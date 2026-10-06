"""Part 5 — Green Zone Screening Engine (20 criteria).

Design rules implemented:
- Missing data → status "insufficient_data", score 0 — never a pass.
- Scores: pass=1, review=0.5, fail/insufficient/not_applicable=0.
- Sector variants are explicit: Financials/REITs get N/A on
  current_ratio, interest_coverage, DSCR (capital-structure ratios are
  meaningless for financials) — flagged, not silently substituted.
- Moat is deterministic evidence (ROIC persistence + margin stability);
  every moat pass is flagged requires_review for human confirmation.
- Screen records policy_version + mandate_version + as_of for PIT
  reconstruction.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.instruments import Instrument, Sector
from app.models.market import OhlcvBar
from app.models.screening import (
    ScreeningPolicy,
    ScreeningResult,
    ScreeningRun,
)
from app.services import finmetrics as fm
from app.services.fundamentals_query import (
    fy_series, last_two, latest_instant, series_values,
)
from app.services.universe import universe_by_name

FINANCIAL_SECTORS = {"Financials", "Real Estate"}  # ratio-variant sectors

# EDGAR concept aliases per metric
C_REV = ["us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:SalesRevenueNet"]
C_NI = ["us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"]
C_OCF = ["us-gaap:NetCashProvidedByUsedInOperatingActivities"]
C_EBIT = ["us-gaap:OperatingIncomeLoss"]
C_EQUITY = ["us-gaap:StockholdersEquity", "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
C_DEBT = ["us-gaap:LongTermDebt", "us-gaap:LongTermDebtNoncurrent"]
C_DEBT_CURRENT = ["us-gaap:LongTermDebtCurrent", "us-gaap:DebtCurrent"]
C_CASH = ["us-gaap:CashAndCashEquivalentsAtCarryingValue", "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
C_INTEREST = ["us-gaap:InterestExpense", "us-gaap:InterestExpenseNonoperating", "us-gaap:InterestIncomeExpenseNet"]
C_SHARES = ["us-gaap:WeightedAverageNumberOfSharesOutstandingBasic", "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"]
C_CAPEXC = ["us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", "us-gaap:PaymentsToAcquireProductiveAssets"]
C_DPS = ["us-gaap:CommonStockDividendsPerShareDeclared", "us-gaap:CommonStockDividendsPerShareCashPaid"]
C_TAX = ["us-gaap:IncomeTaxExpenseBenefit"]
C_PRETAX = ["us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"]
C_DA = ["us-gaap:DepreciationDepletionAndAmortization", "us-gaap:DepreciationAmortizationAndAccretionNet", "us-gaap:Depreciation"]
C_AR = ["us-gaap:AccountsReceivableNetCurrent", "us-gaap:ReceivablesNetCurrent"]
C_CA = ["us-gaap:AssetsCurrent"]
C_CL = ["us-gaap:LiabilitiesCurrent"]

POLICY_DEFAULTS: dict[str, Any] = {
    "growth_min": 0.0,               # any positive YoY growth passes
    "margin_expansion_min_pp": 0.0,  # Δ margin ≥ 0 passes
    "roe_min": 0.15,
    "roic_min": 0.12,
    "cash_conversion_min": 0.9,      # OCF / NI
    "fcf_ps_min": 0.0,
    "p_fcf_max": 30.0,
    "current_ratio_min": 1.5,
    "debt_ebitda_max": 3.0,
    "interest_coverage_min": 5.0,
    "dscr_min": 1.5,
    "moat_roic_min": 0.12,
    "moat_roic_years": 3,
    "moat_margin_min": 0.15,
    "sma_window": 200,
    "min_history_years": 2,
}


@dataclass
class CritResult:
    key: str
    name: str
    status: str          # pass|fail|review|insufficient_data|not_applicable
    score: float
    formula: str
    evidence: dict = field(default_factory=dict)
    industry_variant: bool = False
    review_required: bool = False

    def to_dict(self):
        return {
            "key": self.key, "name": self.name, "status": self.status,
            "score": self.score, "formula": self.formula,
            "evidence": self.evidence,
            "industry_variant": self.industry_variant,
            "review_required": self.review_required,
        }


def _insuf(key, name, formula, missing: list[str]) -> CritResult:
    return CritResult(key, name, "insufficient_data", 0.0, formula,
                      evidence={"missing": missing})


async def _series_all(db, inst_id, as_of, concepts) -> dict:
    return {
        tag: await fy_series(db, inst_id, con, as_of)
        for tag, con in concepts.items()
    }


# Asset classes with no issuer fundamentals — their thesis comes from
# the docs' Macro + Technical channels, not the Green Zone quality screen.
NON_FUNDAMENTAL_CLASSES = {"etf", "index"}

# Total criteria in the equity Green Zone — the mandate's
# green_zone_pass_score is denominated against this. Non-equity
# screening judges the same bar as a FRACTION of applicable criteria.
EQUITY_SCREEN_CRITERIA = 20

# Doc gate (Part 4): <18/20 → REJECT/WATCHLIST, ≥15/20 → DEEP
# RESEARCH. Requirements C1 resolves the 15–17 band as "conditional
# → watchlist pending review" — i.e. only ≥90% of applicable
# criteria earns an outright PASS; clearing the mandate floor with
# less is REVIEW, never silent qualification.
STRONG_PASS_FRACTION = 0.9


def aggregate_verdict(crits: list, threshold: float,
                      listing_status: str = "active",
                      non_equity: bool = False) -> dict:
    """Shared verdict aggregation — used by screen_instrument AND the
    human-confirm endpoint (which recomputes from stored criterion
    dicts). Accepts CritResult objects or stored dicts."""
    def _st(c): return c.status if hasattr(c, "status") else c.get("status")
    def _sc(c): return c.score if hasattr(c, "score") else c.get("score", 0)
    def _key(c): return c.key if hasattr(c, "key") else c.get("key")

    applicable = [c for c in crits if _st(c) != "not_applicable"]
    score = sum(_sc(c) for c in crits)
    insufficient = sum(1 for c in applicable
                       if _st(c) == "insufficient_data")
    review_n = sum(1 for c in applicable if _st(c) == "review")

    blocked: list[str] = []
    if listing_status != "active":
        blocked.append(f"listing_status={listing_status}")

    if non_equity:
        max_pts = max(1, len(applicable))
        frac = score / max_pts
        bar = threshold / EQUITY_SCREEN_CRITERIA
        macro = next((c for c in applicable
                      if _key(c) == "macro_sector_alignment"), None)
        thesis_ok = macro is None or _st(macro) == "pass"
        passed = frac >= STRONG_PASS_FRACTION and review_n == 0 \
            and thesis_ok
        conditional = frac >= bar
    else:
        frac = None
        bar = None
        strong_bar = STRONG_PASS_FRACTION * len(applicable)
        passed = score >= strong_bar and review_n == 0
        conditional = score >= threshold

    if blocked:
        verdict = "blocked_by_risk"
    elif insufficient >= max(1, len(applicable) // 2):
        verdict = "insufficient_data"
    elif passed:
        verdict = "pass"
    elif conditional:
        # hits the floor but is either in the 15–17 conditional band
        # or waiting on human confirmation — deep-research path, not
        # qualified
        verdict = "review"
    else:
        verdict = "fail"

    out = {
        "score": score,
        "applicable": len(applicable),
        "verdict": verdict,
        "qualified": verdict == "pass",
        "blocked_reasons": blocked,
        "threshold": threshold,
        "band": ("strong" if passed else
                 "conditional" if conditional else "below"),
    }
    if non_equity:
        out["score_fraction"] = frac
        out["pass_fraction"] = bar
    return out


async def screen_instrument(
    db: AsyncSession,
    inst: Instrument,
    policy: dict,
    mandate,
    as_of: datetime,
    price: float | None = None,
    intrinsic_value: float | None = None,
    sector_name: str | None = None,
) -> dict:
    """Screen one instrument. Returns criteria + verdict dicts."""
    if (inst.asset_class or "equity") in NON_FUNDAMENTAL_CLASSES:
        return await _screen_non_equity(
            db, inst, policy, mandate, as_of, sector_name)

    S = await _series_all(
        db, inst.id, as_of,
        {"rev": C_REV, "ni": C_NI, "ocf": C_OCF, "ebit": C_EBIT,
         "shares": C_SHARES, "capex": C_CAPEXC, "dps": C_DPS,
         "ar": C_AR, "tax": C_TAX, "pretax": C_PRETAX, "da": C_DA},
    )
    equity = await latest_instant(db, inst.id, C_EQUITY, as_of)
    debt = await latest_instant(db, inst.id, C_DEBT, as_of)
    debt_cur = await latest_instant(db, inst.id, C_DEBT_CURRENT, as_of)
    cash = await latest_instant(db, inst.id, C_CASH, as_of)
    interest = await latest_instant(db, inst.id, C_INTEREST, as_of)
    ca = await latest_instant(db, inst.id, C_CA, as_of)
    cl = await latest_instant(db, inst.id, C_CL, as_of)

    fin = (sector_name or "") in FINANCIAL_SECTORS
    crits: list[CritResult] = []

    def growth_crit(key, name, series_key, formula):
        prev, cur = last_two(S[series_key])
        if prev is None or cur is None:
            return _insuf(key, name, formula, [f"{series_key} history"])
        g = fm.growth(prev, cur)
        if g is None:
            return CritResult(key, name, "insufficient_data", 0.0, formula,
                              {"prev": prev, "curr": cur,
                               "note": "base ≤ 0 — growth undefined"})
        ok = g >= Decimal(str(policy["growth_min"]))
        return CritResult(key, name, "pass" if ok else "fail",
                          1.0 if ok else 0.0, formula,
                          {"prev": prev, "curr": cur, "growth": float(g)})

    # 1-3: growth criteria
    crits.append(growth_crit("revenue_growth", "Revenue growth", "rev",
                             "(rev_t − rev_{t−1}) / rev_{t−1} > 0"))
    crits.append(growth_crit("net_income_growth", "Net income growth", "ni",
                             "(NI_t − NI_{t−1}) / |NI_{t−1}| > 0"))
    crits.append(growth_crit("ocf_growth", "Operating cash flow growth", "ocf",
                             "(OCF_t − OCF_{t−1}) / |OCF_{t−1}| > 0"))

    # 4: margin expansion (operating margin Δ)
    prev_r, cur_r = last_two(S["rev"])
    prev_e, cur_e = last_two(S["ebit"])
    if None in (prev_r, cur_r, prev_e, cur_e):
        crits.append(_insuf("margin_expansion", "Margin expansion",
                            "Δ(EBIT/rev) ≥ 0", ["ebit or rev history"]))
    else:
        m_prev = fm.margin(prev_e, prev_r)
        m_cur = fm.margin(cur_e, cur_r)
        if m_prev is None or m_cur is None:
            crits.append(_insuf("margin_expansion", "Margin expansion",
                                "Δ(EBIT/rev) ≥ 0", ["zero revenue"]))
        else:
            delta = m_cur - m_prev
            ok = delta >= Decimal(str(policy["margin_expansion_min_pp"])) / 100
            crits.append(CritResult(
                "margin_expansion", "Margin expansion",
                "pass" if ok else "fail", 1.0 if ok else 0.0,
                "margin_t − margin_{t−1} ≥ 0pp",
                {"margin_prev": float(m_prev), "margin_curr": float(m_cur),
                 "delta_pp": float(delta * 100)}))

    # 5: ROE — equity is instant; compare NI FY / latest equity
    _, ni_latest = last_two(S["ni"])
    roe_v = fm.roe(ni_latest, equity) if equity else None
    if roe_v is None:
        crits.append(_insuf("roe", "Return on equity", "NI / equity ≥ 15%",
                            ["ni" if ni_latest is None else "equity"]))
    else:
        ok = roe_v >= Decimal(str(policy["roe_min"]))
        crits.append(CritResult("roe", "Return on equity",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0, "NI / equity ≥ 15%",
                                {"roe": float(roe_v), "equity": equity}))

    # 6: ROIC
    ebit_l = last_two(S["ebit"])[1]
    tax_l = last_two(S["tax"])[1]
    pretax_l = last_two(S["pretax"])[1]
    roic_v = fm.roic(ebit_l, tax_l, pretax_l, debt, equity, cash)
    if roic_v is None:
        crits.append(_insuf("roic", "Return on invested capital",
                            "NOPAT / (debt + equity − cash) ≥ 12%",
                            ["ebit|ic components"]))
    else:
        ok = roic_v >= Decimal(str(policy["roic_min"]))
        crits.append(CritResult("roic", "Return on invested capital",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0,
                                "NOPAT / invested capital ≥ 12%",
                                {"roic": float(roic_v)}))

    # 7: revenue vs receivables — revenue growth ≥ AR growth
    prev_ar, cur_ar = last_two(S["ar"])
    if prev_r is None or cur_r is None or prev_ar is None or cur_ar is None:
        crits.append(_insuf("revenue_receivables", "Revenue vs receivables",
                            "rev_growth ≥ AR_growth", ["rev or AR history"]))
    else:
        rg, ag = fm.growth(prev_r, cur_r), fm.growth(prev_ar, cur_ar)
        if rg is None or ag is None:
            crits.append(CritResult("revenue_receivables",
                                    "Revenue vs receivables",
                                    "insufficient_data", 0.0,
                                    "rev_growth ≥ AR_growth",
                                    {"note": "undefined growth base"}))
        else:
            ok = rg >= ag
            crits.append(CritResult("revenue_receivables",
                                    "Revenue vs receivables",
                                    "pass" if ok else "fail",
                                    1.0 if ok else 0.0,
                                    "rev_growth ≥ AR_growth",
                                    {"rev_growth": float(rg),
                                     "ar_growth": float(ag)}))

    # 8: cash conversion OCF / NI ≥ 0.9
    _, ocf_l = last_two(S["ocf"])
    if ocf_l is None or ni_latest is None:
        crits.append(_insuf("cash_conversion", "Cash conversion",
                            "OCF / NI ≥ 0.9", ["ocf or ni"]))
    elif ni_latest <= 0:
        crits.append(CritResult("cash_conversion", "Cash conversion",
                                "insufficient_data", 0.0, "OCF / NI ≥ 0.9",
                                {"ni": ni_latest,
                                 "note": "negative NI — ratio undefined"}))
    else:
        cc = fm.margin(ocf_l, ni_latest)
        ok = cc is not None and cc >= Decimal(str(policy["cash_conversion_min"]))
        crits.append(CritResult("cash_conversion", "Cash conversion",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0, "OCF / NI ≥ 0.9",
                                {"ocf": ocf_l, "ni": ni_latest,
                                 "ratio": float(cc) if cc is not None else None}))

    # 9: share-count trend (declining = pass; rising >2% = fail)
    shares = series_values(S["shares"], 3)
    if len(shares) < 2:
        crits.append(_insuf("share_count", "Share-count trend",
                            "shares_t ≤ shares_{t−1} (+2% tol)",
                            ["shares history"]))
    else:
        ch = fm.shares_change(shares[-2], shares[-1])
        if ch is None:
            crits.append(_insuf("share_count", "Share-count trend",
                                "Δ shares", ["shares"]))
        else:
            ok = ch <= Decimal("0.02")
            crits.append(CritResult("share_count", "Share-count trend",
                                    "pass" if ok else "fail",
                                    1.0 if ok else 0.0,
                                    "Δshares ≤ +2%",
                                    {"shares": shares, "change": float(ch)}))

    # 10: FCF per share > 0
    capex_l = last_two(S["capex"])[1]
    sh_l = last_two(S["shares"])[1]
    fcf_v = fm.fcf(ocf_l, capex_l) if ocf_l is not None else None
    fcfps = fm.per_share(fcf_v, sh_l) if fcf_v is not None else None
    if fcfps is None:
        crits.append(_insuf("fcf_per_share", "FCF per share",
                            "(OCF − capex) / shares > 0",
                            ["ocf|capex|shares"]))
    else:
        ok = fcfps > Decimal(str(policy["fcf_ps_min"]))
        crits.append(CritResult("fcf_per_share", "FCF per share",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0,
                                "FCF/share > 0",
                                {"fcf": float(fcf_v), "fcf_ps": float(fcfps)}))

    # 11: P/FCF ≤ policy
    pfcf = fm.price_ratio(price, fcfps) if fcfps is not None else None
    if pfcf is None:
        crits.append(_insuf("p_fcf", "Price / FCF",
                            f"price / FCFps ≤ {policy['p_fcf_max']}",
                            ["price" if price is None else "fcf_ps"]))
    else:
        ok = pfcf <= Decimal(str(policy["p_fcf_max"]))
        crits.append(CritResult("p_fcf", "Price / FCF",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0,
                                f"P/FCF ≤ {policy['p_fcf_max']}",
                                {"p_fcf": float(pfcf)}))

    # 12: book value per share > 0 and growing (2y)
    eq_series = await fy_series(db, inst.id, C_EQUITY, as_of, years=4)
    bvps_vals = []
    for i, eq in enumerate(sorted(eq_series.items())):
        s = series_values(S["shares"], 2)
        sh = s[min(i, len(s) - 1)] if s else None
        v = fm.per_share(eq[1], sh) if sh else None
        if v is not None:
            bvps_vals.append(v)
    if not bvps_vals:
        crits.append(_insuf("bvps", "Book value per share",
                            "equity / shares > 0, non-declining",
                            ["equity series"]))
    else:
        ok = bvps_vals[-1] > 0 and (
            len(bvps_vals) == 1 or fm.trend_ok(bvps_vals[-2:], increasing=True, min_points=2)
        )
        crits.append(CritResult("bvps", "Book value per share",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0,
                                "BVPS > 0 and not declining",
                                {"bvps": [float(v) for v in bvps_vals]}))

    # 13: dividend growth — non-payers are N/A, not auto-fail
    dps = S["dps"]
    prev_d, cur_d = last_two(dps)
    if cur_d in (None, 0) and prev_d in (None, 0):
        crits.append(CritResult("dividend_growth", "Dividend growth",
                                "not_applicable", 0.0,
                                "DPS_t > DPS_{t−1}",
                                {"note": "non-dividend payer"}))
    elif prev_d is None or cur_d is None:
        crits.append(_insuf("dividend_growth", "Dividend growth",
                            "DPS_t > DPS_{t−1}", ["dps history"]))
    else:
        g = fm.growth(prev_d, cur_d)
        ok = g is not None and g > 0
        crits.append(CritResult("dividend_growth", "Dividend growth",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0, "DPS growth > 0",
                                {"prev": prev_d, "curr": cur_d}))

    # 14: moat — deterministic evidence: ROIC ≥ min for N yrs + margin ≥ min.
    # Any pass is flagged for human review; never LLM-derived.
    roic_hist = []
    tax_s, pretax_s, ebit_s = S["tax"], S["pretax"], S["ebit"]
    for pe, ebit_v in sorted(ebit_s.items())[-policy["moat_roic_years"]:]:
        rv = fm.roic(ebit_v, tax_s.get(pe), pretax_s.get(pe), debt, equity, cash)
        if rv is not None:
            roic_hist.append(float(rv))
    margins_hist = []
    for pe, ebit_v in sorted(ebit_s.items())[-3:]:
        rv = S["rev"].get(pe)
        m = fm.margin(ebit_v, rv) if rv else None
        if m is not None:
            margins_hist.append(float(m))
    enough_hist = len(roic_hist) >= policy["moat_roic_years"]
    roic_ok = enough_hist and all(
        r >= policy["moat_roic_min"] for r in roic_hist)
    margin_ok = len(margins_hist) >= 3 and all(
        m >= policy["moat_margin_min"] for m in margins_hist)
    if not enough_hist and not margins_hist:
        crits.append(_insuf("moat", "Economic moat",
                            "ROIC ≥ 12% × 3y AND EBIT margin ≥ 15% × 3y",
                            ["3y roic/margin history"]))
    elif roic_ok and margin_ok:
        crits.append(CritResult(
            "moat", "Economic moat", "review", 0.5,
            "ROIC ≥ 12% ×3y AND margin ≥ 15% ×3y (evidence-based; human review required)",
            {"roic_history": roic_hist, "margin_history": margins_hist},
            review_required=True))
    else:
        crits.append(CritResult("moat", "Economic moat", "fail", 0.0,
                                "ROIC ≥ 12% ×3y AND margin ≥ 15% ×3y",
                                {"roic_history": roic_hist,
                                 "margin_history": margins_hist},
                                review_required=True))

    # 15-17: financial-sector variants — conventional ratios N/A
    def na(key, name, formula):
        return CritResult(key, name, "not_applicable", 0.0, formula,
                          {"sector": sector_name}, industry_variant=True)

    # 15: current ratio
    if fin:
        crits.append(na("current_ratio", "Current ratio",
                        "CA/CL — N/A for financial-sector balance sheets"))
    elif ca is None or cl is None:
        crits.append(_insuf("current_ratio", "Current ratio",
                            f"CA/CL ≥ {policy['current_ratio_min']}",
                            ["current assets|liabilities"]))
    else:
        cr = fm.current_ratio(ca, cl)
        ok = cr is not None and cr >= Decimal(str(policy["current_ratio_min"]))
        crits.append(CritResult("current_ratio", "Current ratio",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0,
                                f"CA/CL ≥ {policy['current_ratio_min']}",
                                {"current_ratio": float(cr) if cr else None}))

    # 16: debt/EBITDA
    if fin:
        crits.append(na("debt_ebitda", "Debt-to-EBITDA",
                        "debt/EBITDA — N/A: debt is operational for financials"))
    else:
        da_l = last_two(S["da"])[1]
        d2e = fm.debt_to_ebitda(debt, ebit_l, da_l)
        if d2e is None:
            crits.append(_insuf("debt_ebitda", "Debt-to-EBITDA",
                                f"debt/EBITDA ≤ {policy['debt_ebitda_max']}",
                                ["debt|ebit|da" if debt is None else "ebit≤0"]))
        else:
            ok = d2e <= Decimal(str(policy["debt_ebitda_max"]))
            crits.append(CritResult("debt_ebitda", "Debt-to-EBITDA",
                                    "pass" if ok else "fail",
                                    1.0 if ok else 0.0,
                                    f"debt/EBITDA ≤ {policy['debt_ebitda_max']}",
                                    {"debt_ebitda": float(d2e)}))

    # 17: interest coverage
    if fin:
        crits.append(na("interest_coverage", "Interest coverage",
                        "EBIT/interest — N/A for financials"))
    elif interest is None or interest <= 0:
        crits.append(CritResult("interest_coverage", "Interest coverage",
                                "not_applicable", 0.0,
                                "EBIT/interest — no meaningful interest expense",
                                {"interest": interest}))
    elif ebit_l is None:
        crits.append(_insuf("interest_coverage", "Interest coverage",
                            f"EBIT/interest ≥ {policy['interest_coverage_min']}",
                            ["ebit"]))
    else:
        ic = fm.interest_coverage(ebit_l, interest)
        ok = ic is not None and ic >= Decimal(str(policy["interest_coverage_min"]))
        crits.append(CritResult("interest_coverage", "Interest coverage",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0,
                                f"EBIT/interest ≥ {policy['interest_coverage_min']}",
                                {"interest_coverage": float(ic) if ic else None}))

    # 18: DSCR
    if fin:
        crits.append(na("dscr", "Debt service coverage",
                        "OCF/(interest+curr debt) — N/A for financials"))
    elif ocf_l is None:
        crits.append(_insuf("dscr", "Debt service coverage",
                            f"OCF/debt service ≥ {policy['dscr_min']}", ["ocf"]))
    else:
        dscr = fm.debt_service_coverage(ocf_l, interest, debt_cur)
        if dscr is None:
            crits.append(CritResult("dscr", "Debt service coverage",
                                    "not_applicable", 0.0,
                                    "OCF/(interest + current debt) — no debt service",
                                    {"interest": interest,
                                     "current_debt": debt_cur}))
        else:
            ok = dscr >= Decimal(str(policy["dscr_min"]))
            crits.append(CritResult("dscr", "Debt service coverage",
                                    "pass" if ok else "fail",
                                    1.0 if ok else 0.0,
                                    f"DSCR ≥ {policy['dscr_min']}",
                                    {"dscr": float(dscr)}))

    # 19: intrinsic-value discount (needs Part-6 valuation input)
    if intrinsic_value is None or price is None:
        crits.append(_insuf(
            "iv_discount", "Intrinsic-value discount",
            f"(IV − P)/IV ≥ {mandate.margin_of_safety_min_pct if mandate else 20}%",
            ["intrinsic_value" if intrinsic_value is None else "price"]))
    else:
        disc = fm.intrinsic_discount(price, intrinsic_value)
        mos = Decimal(str(
            (mandate.margin_of_safety_min_pct if mandate else 20.0) / 100))
        ok = disc is not None and disc >= mos
        crits.append(CritResult("iv_discount", "Intrinsic-value discount",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0,
                                f"discount ≥ {float(mos)*100:.0f}%",
                                {"price": price, "iv": intrinsic_value,
                                 "discount": float(disc) if disc else None}))

    # 20: technical setup — close > SMA200 proxy
    window = int(policy["sma_window"])
    bars = (
        await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d",
                   OhlcvBar.time <= as_of)
            .order_by(OhlcvBar.time.desc())
            .limit(window)
        )
    ).scalars().all()
    if len(bars) < window // 2:
        crits.append(_insuf("technical_setup", "Technical setup",
                            "close > SMA200", [f"price bars ({len(bars)})"]))
    else:
        closes = [float(b.close) for b in bars]
        sma = sum(closes) / len(closes)
        latest = closes[0]
        ok = latest > sma
        crits.append(CritResult("technical_setup", "Technical setup",
                                "pass" if ok else "fail",
                                1.0 if ok else 0.0,
                                f"close > SMA{window}",
                                {"close": latest, "sma": sma,
                                 "bars": len(closes)}))

    # ── aggregate ──
    threshold = mandate.green_zone_pass_score if mandate else 15
    out = aggregate_verdict(crits, threshold, inst.listing_status)
    return {"criteria": [c.to_dict() for c in crits], **out}


async def _screen_non_equity(
    db: AsyncSession,
    inst: Instrument,
    policy: dict,
    mandate,
    as_of: datetime,
    sector_name: str | None = None,
) -> dict:
    """ETF/index screening — the Green Zone's fundamental channel does
    not exist for funds (no 10-K facts, no moat). Per the docs' CIO
    summary (THESIS = Fundamental + Technical + Macro), the fund's
    thesis comes from the two channels that DO apply:

      Macro     — sector ∈ RegimeRun.sector_preferences
                  (SECTOR_ROTATION map, SOW Part 11)
      Technical — trend (SMA200) + 63d relative strength vs SPY
      Liquidity — avg_dollar_volume_30d vs policy floor

    The mandate's pass bar is judged as a FRACTION of applicable
    criteria — same rigor, no fake fundamentals.
    """
    from app.models.macro import RegimeRun

    crits: list[CritResult] = []

    async def _closes(inst_id, limit):
        rows = (await db.execute(
            select(OhlcvBar.close)
            .where(OhlcvBar.instrument_id == inst_id,
                   OhlcvBar.timeframe == "1d",
                   OhlcvBar.time <= as_of)
            .order_by(OhlcvBar.time.desc())
            .limit(limit))).scalars().all()
        return [float(c) for c in rows]

    window = int(policy["sma_window"])
    closes = await _closes(inst.id, window)

    # 1: macro sector alignment — the fund's thesis channel
    run = (await db.execute(
        select(RegimeRun).order_by(RegimeRun.as_of.desc()).limit(1))
    ).scalar_one_or_none()
    if run is None:
        crits.append(_insuf(
            "macro_sector_alignment", "Macro sector alignment",
            "sector ∈ regime sector_preferences", ["regime run"]))
    elif sector_name is None:
        crits.append(CritResult(
            "macro_sector_alignment", "Macro sector alignment",
            "not_applicable", 0.0,
            "sector ∈ regime sector_preferences",
            {"note": "no GICS sector — broad/macro fund"}))
    else:
        favored = sector_name in (run.sector_preferences or {})
        crits.append(CritResult(
            "macro_sector_alignment", "Macro sector alignment",
            "pass" if favored else "fail",
            1.0 if favored else 0.0,
            "sector ∈ regime sector_preferences",
            {"sector": sector_name,
             "econ_regime": run.econ_regime,
             "favored": favored,
             "preferences": run.sector_preferences}))

    # 2: relative strength — 63d return ≥ SPY 63d return (rotation lead)
    spy = (await db.execute(
        select(Instrument.id).where(Instrument.symbol == "SPY"))
    ).scalar_one_or_none()
    spy_closes = await _closes(spy, 64) if spy else []
    if len(closes) < 64 or len(spy_closes) < 64:
        crits.append(_insuf(
            "relative_strength", "Relative strength vs SPY",
            "r63(ETF) ≥ r63(SPY)",
            [f"bars ({len(closes)}/{len(spy_closes)})"]))
    else:
        r_etf = closes[0] / closes[63] - 1
        r_spy = spy_closes[0] / spy_closes[63] - 1
        ok = r_etf >= r_spy
        crits.append(CritResult(
            "relative_strength", "Relative strength vs SPY",
            "pass" if ok else "fail",
            1.0 if ok else 0.0,
            "r63(ETF) ≥ r63(SPY)",
            {"etf_r63": r_etf, "spy_r63": r_spy,
             "spread": r_etf - r_spy}))

    # 3: technical setup — close > SMA200 (same bar as equities)
    if len(closes) < window // 2:
        crits.append(_insuf("technical_setup", "Technical setup",
                            "close > SMA200", [f"price bars ({len(closes)})"]))
    else:
        sma = sum(closes) / len(closes)
        latest = closes[0]
        ok = latest > sma
        crits.append(CritResult(
            "technical_setup", "Technical setup",
            "pass" if ok else "fail",
            1.0 if ok else 0.0,
            f"close > SMA{window}",
            {"close": latest, "sma": sma, "bars": len(closes)}))

    # 4: liquidity — ADV30 ≥ universe floor
    min_adv = float(policy.get("min_avg_dollar_volume", 5_000_000))
    adv = inst.avg_dollar_volume_30d
    if adv is None:
        crits.append(_insuf("liquidity", "Liquidity",
                            f"ADV30 ≥ {min_adv:,.0f}", ["avg_dollar_volume"]))
    else:
        ok = float(adv) >= min_adv
        crits.append(CritResult(
            "liquidity", "Liquidity",
            "pass" if ok else "fail",
            1.0 if ok else 0.0,
            f"ADV30 ≥ {min_adv:,.0f}",
            {"adv30": float(adv)}))

    # ── aggregate — pass bar as a fraction of applicable criteria ──
    threshold = mandate.green_zone_pass_score if mandate else 15
    out = aggregate_verdict(crits, threshold, inst.listing_status,
                            non_equity=True)
    return {"criteria": [c.to_dict() for c in crits], **out}


async def _latest_close(db: AsyncSession, inst_id: str,
                        as_of: datetime) -> float | None:
    px = (await db.execute(
        select(OhlcvBar.close)
        .where(OhlcvBar.instrument_id == inst_id,
               OhlcvBar.timeframe == "1d",
               OhlcvBar.time <= as_of)
        .order_by(OhlcvBar.time.desc()).limit(1))).scalar_one_or_none()
    return float(px) if px is not None else None


async def _latest_iv(db: AsyncSession, inst: Instrument,
                     sector_name: str | None) -> float | None:
    """Latest ValuationRun's intrinsic value, resolved by the same
    company-type precedence as the qualification gate (financials →
    DNI/P-B, operating cos → Rule-1/DCF)."""
    from app.models.valuation import ValuationRun
    from app.services import valuation as sector_val

    vrun = (await db.execute(
        select(ValuationRun)
        .where(ValuationRun.instrument_id == inst.id)
        .order_by(ValuationRun.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    if vrun is None:
        return None
    o = vrun.outputs or {}
    vals = {
        "rule1": (o.get("rule1") or {}).get("sticker_price")
                 or o.get("sticker"),
        "dcf": (o.get("dcf") or {}).get("per_share")
               or (o.get("dcf_multistage") or {}).get("per_share"),
        "dni": (o.get("dni") or {}).get("per_share"),
        "pb": (o.get("pb_intrinsic") or {}).get("per_share"),
    }
    order = (["dni", "pb", "rule1", "dcf"]
             if sector_val.archetype_for(sector_name or "") == "financial"
             else ["rule1", "dcf", "dni", "pb"])
    for m in order:
        if vals.get(m):
            return float(vals[m])
    return None


async def run_screen(
    db: AsyncSession,
    policy: ScreeningPolicy,
    mandate,
    as_of: datetime | None = None,
    universe_name: str = "approved",
) -> ScreeningRun:
    """Screen every active member of the universe — one ScreeningRun +
    ScreeningResult rows (idempotent per run)."""
    from app.models.universe import UniverseMembership

    as_of = as_of or utcnow()
    run = ScreeningRun(
        universe=universe_name,
        policy_version=policy.version,
        mandate_version=mandate.version if mandate else None,
    )
    db.add(run)
    await db.flush()

    universe = await universe_by_name(db, universe_name)
    if universe is None:
        run.status, run.error = "failed", f"universe '{universe_name}' missing"
        return run

    members = (
        await db.execute(
            select(Instrument, UniverseMembership)
            .join(UniverseMembership,
                  UniverseMembership.instrument_id == Instrument.id)
            .where(
                UniverseMembership.universe_id == universe.id,
                UniverseMembership.status == "active",
            )
        )
    ).all()

    sector_names = dict(
        (await db.execute(select(Sector.id, Sector.name))).all()
    )

    for inst, _m in members:
        sector_name = sector_names.get(inst.sector_id)
        res = await screen_instrument(
            db, inst, policy.params or POLICY_DEFAULTS, mandate, as_of,
            price=await _latest_close(db, inst.id, as_of),
            intrinsic_value=await _latest_iv(db, inst, sector_name),
            sector_name=sector_name,
        )
        db.add(
            ScreeningResult(
                run_id=run.id,
                instrument_id=inst.id,
                score=res["score"],
                applicable=res["applicable"],
                qualified=res["qualified"],
                verdict=res["verdict"],
                blocked_reasons=res["blocked_reasons"],
                criteria=res["criteria"],
                as_of=as_of,
            )
        )
        run.instruments += 1

    run.status = "complete"
    run.finished_at = utcnow()
    return run
