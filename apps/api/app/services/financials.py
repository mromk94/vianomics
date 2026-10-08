"""Statement-style fundamentals for the symbol page — the investing.com
layout: Key Ratios + Income Statement / Balance Sheet / Cash Flow
tables with fiscal periods as columns.

Observations are grouped per concept, latest filings first; each
concept keeps up to MAX_PERIODS points tagged FY or Q1-Q4 so the UI
can render an Annual/Quarterly toggle without re-querying.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fundamentals import FundamentalObservation as FO
from app.models.instruments import Instrument

MAX_PERIODS = 6
MAX_CONCEPTS_PER_STATEMENT = 24

# XBRL concept keyword → statement bucket. Compact map — unmatched
# concepts land in "other" rather than being mislabeled. Keywords
# cover us-gaap AND ifrs-full names (CashFlowsFromUsedIn*, PurchaseOf*,
# CurrentAssets…) plus the yahoo: fallback taxonomy.
_CASHFLOW = ("NetCashProvidedByUsedIn", "PaymentsToAcquire",
             "PaymentsOfDividends", "PaymentsForRepurchase",
             "ProceedsFrom", "DepreciationDepletionAndAmortization",
             "ShareBasedCompensation", "PaymentsRelatedTo",
             "PeriodIncreaseDecrease", "IncreaseDecrease",
             "CashFlowsFromUsedIn", "PurchaseOf", "ProceedsFromSalesOf",
             "DividendsPaid", "DividendsReceived",
             "OperatingCashFlow", "CapitalExpenditure",
             "CashDividendsPaid", "FreeCashFlow")
_BALANCE = ("Assets", "Liabilities", "StockholdersEquity",
            "Equity", "Borrowings", "TotalDebt", "CurrentDebt",
            "LongTermDebt", "ShortTermBorrowings", "Debt",
            "CashAndCashEquivalents", "Inventory", "Goodwill",
            "RetainedEarnings", "AccountsReceivable",
            "TradeReceivables", "Receivables",
            "AccountsPayable", "MinorityInterest",
            "CommonStockShares", "EntityCommonStockShares",
            "IntangibleAssets", "PropertyPlantAndEquipment")
_INCOME = ("Revenue", "Income", "Earnings", "CostOf", "Expense",
           "Profit", "Interest", "Tax", "Dividends", "ShareBased",
           "Operating", "Research", "SellingGeneral",
           "WeightedAverage", "AverageShares", "EPS", "PerShare")


def _statement(concept: str) -> str:
    c = concept.split(":")[-1]
    # contra-asset balances contain flow keywords — check first
    if c.startswith("Accumulated"):
        return "balance"
    if any(k in c for k in _CASHFLOW):
        return "cashflow"
    if any(k in c for k in _INCOME):
        return "income"
    if any(k in c for k in _BALANCE):
        return "balance"
    return "other"


# canonical statement row order (investing.com-style) — matched as
# substrings so taxonomy variants (XxxCurrent, …Net) still rank
_PRIORITY: dict[str, tuple[str, ...]] = {
    "income": (
        "RevenueFromContractWithCustomer", "Revenues", "CostOfRevenue",
        "CostOfGoodsAndServicesSold", "GrossProfit",
        "ResearchAndDevelopmentExpense",
        "SellingGeneralAndAdministrativeExpense", "OperatingExpenses",
        "OperatingIncomeLoss", "InterestIncomeExpense",
        "NonoperatingIncomeExpense",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
        "IncomeTaxExpenseBenefit", "NetIncomeLoss", "ProfitLoss",
        "EarningsPerShareBasic", "EarningsPerShareDiluted",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfDilutedSharesOutstanding"),
    "balance": (
        "CashAndCashEquivalents", "AccountsReceivable", "Inventory",
        "AssetsCurrent", "PropertyPlantAndEquipment",
        "Goodwill", "IntangibleAssetsNet", "Assets",
        "AccountsPayable", "LiabilitiesCurrent", "LongTermDebt",
        "Liabilities", "RetainedEarnings", "StockholdersEquity",
        "CommonStockShares", "EntityCommonStockShares"),
    "cashflow": (
        "NetIncomeLoss", "DepreciationDepletionAndAmortization",
        "ShareBasedCompensation",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquire", "ProceedsFrom",
        "NetCashProvidedByUsedInInvestingActivities",
        "PaymentsForRepurchase", "PaymentsOfDividends",
        "PaymentsRelatedTo",
        "NetCashProvidedByUsedInFinancingActivities",
        "CashCashEquivalents"),
}


def _rank(concept: str, bucket: str) -> tuple[int, str]:
    c = concept.split(":")[-1]
    for i, k in enumerate(_PRIORITY.get(bucket, ())):
        if k in c:
            return (i, c)
    return (len(_PRIORITY.get(bucket, ())), c)


# taxonomy renames across eras — same line item, different tag.
# Merged into one display row: primary concept's values win per
# period, aliases only fill missing ends.
_ALIAS: dict[str, str] = {
    "Revenue": "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenues",
    "SalesRevenueNet": "Revenues",
    "CostOfGoodsAndServicesSold": "CostOfRevenue",
    "CostOfGoodsSold": "CostOfRevenue",
    "CostOfServicesRevenue": "CostOfRevenue",
    "StockholdersEquityIncludingPortionAttributableTo"
    "NoncontrollingInterest": "StockholdersEquity",
    "EntityCommonStockSharesOutstanding":
        "CommonStockSharesOutstanding",
    "NetCashProvidedByUsedInOperatingActivitiesContinuing"
    "Operations": "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInInvestingActivitiesContinuing"
    "Operations": "NetCashProvidedByUsedInInvestingActivities",
    "NetCashProvidedByUsedInFinancingActivitiesContinuing"
    "Operations": "NetCashProvidedByUsedInFinancingActivities",
    "ProfitLoss": "NetIncomeLoss",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    "PeriodIncreaseDecreaseIncludingExchangeRateEffect":
        "CashAndCashEquivalentsPeriodIncreaseDecrease",
    # ifrs-full → canonical display names — foreign issuers' filings
    # render as the same statement rows instead of a second taxonomy's
    # vocabulary
    "RevenueFromContractsWithCustomers": "Revenues",
    "ProfitLossAttributableToOwnersOfParent": "NetIncomeLoss",
    "ProfitLossFromOperatingActivities": "OperatingIncomeLoss",
    "OperatingProfitLoss": "OperatingIncomeLoss",
    "Equity": "StockholdersEquity",
    "EquityAttributableToOwnersOfParent": "StockholdersEquity",
    "CashFlowsFromUsedInOperatingActivities":
        "NetCashProvidedByUsedInOperatingActivities",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities":
        "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquirePropertyPlantAndEquipment":
        "PaymentsToAcquirePropertyPlantAndEquipment",
    "LongtermBorrowings": "LongTermDebt",
    "NoncurrentBorrowings": "LongTermDebtNoncurrent",
    "CurrentBorrowings": "DebtCurrent",
    "CurrentPortionOfLongtermBorrowings": "LongTermDebtCurrent",
    "CurrentAssets": "AssetsCurrent",
    "CurrentLiabilities": "LiabilitiesCurrent",
    "CashAndCashEquivalents": "CashAndCashEquivalentsAtCarryingValue",
    "CurrentTradeReceivables": "AccountsReceivable",
    "TradeAndOtherCurrentReceivables": "AccountsReceivable",
    "BasicEarningsLossPerShare": "EarningsPerShareBasic",
    "DilutedEarningsLossPerShare": "EarningsPerShareDiluted",
    "AdjustedWeightedAverageShares":
        "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfOrdinarySharesOutstanding":
        "WeightedAverageNumberOfSharesOutstandingBasic",
    "FinanceCosts": "InterestExpense",
    "InterestExpenseOnBorrowings": "InterestExpense",
    "InterestExpenseOnBonds": "InterestExpense",
    "ProfitLossBeforeTax": "IncomeLossFromContinuingOperations"
        "BeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeTaxExpenseContinuingOperations": "IncomeTaxExpenseBenefit",
    "CurrentTaxExpenseIncome": "IncomeTaxExpenseBenefit",
    "DepreciationAmortisationExpense":
        "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortisationExpense":
        "DepreciationDepletionAndAmortization",
    "DepreciationExpense": "DepreciationDepletionAndAmortization",
    "AmortisationExpense": "DepreciationDepletionAndAmortization",
    "DividendsPaidOrdinarySharesPerShare":
        "CommonStockDividendsPerShareDeclared",
    "DividendsRecognisedAsDistributionsToOwnersPerShare":
        "CommonStockDividendsPerShareDeclared",
    # yahoo: fallback taxonomy → same display rows
    "TotalRevenue": "Revenues",
    "NetIncome": "NetIncomeLoss",
    "OperatingIncome": "OperatingIncomeLoss",
    "OperatingCashFlow": "NetCashProvidedByUsedInOperatingActivities",
    "CapitalExpenditure": "PaymentsToAcquirePropertyPlantAndEquipment",
    "TotalDebt": "LongTermDebt",
    "CurrentDebt": "DebtCurrent",
    "BasicEPS": "EarningsPerShareBasic",
    "DilutedEPS": "EarningsPerShareDiluted",
    "BasicAverageShares": "WeightedAverageNumberOfSharesOutstandingBasic",
    "DilutedAverageShares":
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    "TaxProvision": "IncomeTaxExpenseBenefit",
    "PretaxIncome": "IncomeLossFromContinuingOperations"
        "BeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "DepreciationAndAmortization":
        "DepreciationDepletionAndAmortization",
}


def _canonical(concept: str) -> str:
    tax, _, name = concept.partition(":")
    return f"{tax}:{_ALIAS.get(name, name)}"


# taxonomy prefixes in lookup preference order — "" first so an
# exact caller-specified key always wins; then us-gaap (canonical),
# ifrs-full (foreign private issuers), yahoo (no-SEC fallback),
# dei (cross-issuer facts like EntityCommonStockSharesOutstanding)
_TAXONOMIES = ("", "us-gaap:", "ifrs-full:", "yahoo:", "dei:")


def _find(points: dict[str, list[dict]], name: str):
    """First non-empty point list for a concept name across every
    taxonomy prefix, in preference order."""
    for p in _TAXONOMIES:
        hits = points.get(f"{p}{name}")
        if hits:
            return hits
    return None


def _latest(points: dict[str, list[dict]], *concepts: str) -> float | None:
    """First concept (by alias preference) with any point."""
    for c in concepts:
        hits = _find(points, c)
        if hits:
            return hits[0]["value"]
    return None


def _ttm(points: dict[str, list[dict]], *concepts: str) -> float | None:
    """Trailing-twelve-month for flow concepts: sum of the last 4
    standalone quarters (incl. YTD-derived), else latest FY, else
    latest point."""
    for c in concepts:
        hits = _find(points, c)
        if not hits:
            continue
        q = [p["value"] for p in hits if p["fp"].startswith("Q")]
        if len(q) >= 4:
            return sum(q[:4])
        fy = next((p["value"] for p in hits if p["fp"] == "FY"), None)
        return fy if fy is not None else hits[0]["value"]
    return None


async def statement_map(db: AsyncSession, inst: Instrument,
                        close: float | None = None) -> dict:
    """{statements: {income|balance|cashflow|other: [concept rows]},
        ratios: derived key ratios, periods: column headers}."""
    rows = (await db.execute(
        select(FO.concept, FO.period_start, FO.period_end, FO.fiscal_period,
               FO.value, FO.unit, FO.currency, FO.published_at, FO.quality)
        .where(FO.instrument_id == inst.id,
               FO.quality != "quarantined")
        .order_by(FO.period_end.desc(), FO.published_at.desc().nulls_last())
    )).all()

    # reporting currency — rows are period_end desc, so the first
    # currency-bearing row is the latest filing's unit. When it differs
    # from the listing's trading currency (TSM: TWD filing, USD ADR)
    # every price-linked ratio (PE, PB, div yield) would be garbage —
    # suppress them rather than display a wrong number.
    fund_ccy = next((r.currency for r in rows if r.currency), None)
    ccy_mismatch = (
        fund_ccy is not None
        and fund_ccy != (inst.currency or "USD"))

    # SEC reports the same period_end with multiple durations — a 3-mo
    # quarterly value AND a 6/9-mo YTD value. Pick per fiscal period:
    # FY wants ~12-mo durations, Q* wants ~3-mo. Also build an
    # end→fp map from duration facts so instant facts (balance sheet,
    # period_start=None, usually untagged) inherit the right period.
    # Keep the best-scoring fp per end — a stray mis-tagged duration
    # (score 1) must not outrank a proper FY/3-mo fact (score 0).
    def _score(fp: str | None, dur: int | None) -> int:
        if fp == "FY":
            return 0 if (dur or 0) >= 300 else 1
        if fp and fp.startswith("Q"):
            return 0 if dur is not None and dur <= 120 else 1
        return 0 if dur is None or dur <= 120 else 1

    # fiscal calendar: an end closing a ~12-mo duration is an FY end.
    # Quarter numbers derive from distance after the previous FY end —
    # filing tags can't be trusted here (a Q2 filing restating the Q1
    # end tags that instant "Q2", so voting mislabels comparatives).
    fy_ends = sorted({
        r.period_end for r in rows
        if r.period_start
        and (r.period_end - r.period_start).days >= 300})

    def _cal_fp(end) -> str | None:
        if end in fy_ends:
            return "FY"
        nxt = [f for f in fy_ends if f > end]
        prv = [f for f in fy_ends if f < end]
        if prv:
            m = (end - prv[-1]).days / 30.44
            for lo, hi, q in ((2.5, 4.5, "Q1"), (5.5, 7.5, "Q2"),
                              (8.5, 10.5, "Q3")):
                if lo <= m <= hi:
                    return q
        if nxt:
            m = (nxt[0] - end).days / 30.44
            for lo, hi, q in ((2.5, 4.5, "Q3"), (5.5, 7.5, "Q2"),
                              (8.5, 10.5, "Q1")):
                if lo <= m <= hi:
                    return q
        return None

    by_concept: dict[str, list[dict]] = {}
    best: dict[tuple[str, str], tuple[int, int]] = {}
    units: dict[str, str | None] = {}
    for r in rows:
        key = (r.concept, r.period_end.isoformat())
        dur = ((r.period_end - r.period_start).days
               if r.period_start else None)
        cal = _cal_fp(r.period_end)
        if dur is None:
            fp = cal or r.fiscal_period
        else:
            # durations normalize by length — a 3-mo fact inside a
            # 10-K is tagged "FY" but is a quarter on the table; a
            # 3-mo fact ending at the FY end is Q4
            if dur >= 300:
                fp = "FY"
            else:
                fp = cal or r.fiscal_period or "Q"
                if fp == "FY":
                    fp = "Q4"
        sc = _score(r.fiscal_period or fp, dur)
        pts = by_concept.setdefault(r.concept, [])
        # rows sorted latest-published first — replace only on a
        # strictly better duration match so ties keep freshest filing
        cur = best.get(key)
        if cur is not None and sc >= cur[0]:
            continue
        best[key] = (sc, len(pts))
        pts = [p for p in pts if p["end"] != key[1]]
        pts.append({
            "end": key[1],
            "fp": fp or "?",
            "value": float(r.value),
            "start": (r.period_start.isoformat()
                      if r.period_start else None),
            "dur": dur,
        })
        by_concept[r.concept] = sorted(pts, key=lambda p: p["end"],
                                       reverse=True)
        units.setdefault(r.concept, r.unit)

    # derive standalone quarters from YTD chains — filers commonly
    # tag only cumulative values (6-mo at Q2, 9-mo at Q3) plus the
    # 3-mo prior quarter. Same period_start = same fiscal chain;
    # subtract the immediate predecessor cumulative.
    for pts in by_concept.values():
        for p in pts:
            dur = p.get("dur")
            if dur is None or not (120 < dur < 300):
                continue
            preds = [q for q in pts
                     if q["start"] == p["start"]
                     and q.get("dur") and q["dur"] < dur]
            if preds:
                pred = max(preds, key=lambda q: q["dur"])
                p["value"] = p["value"] - pred["value"]
                p["derived"] = True
            else:
                p["ytd"] = True

    statements: dict[str, list[dict]] = {
        "income": [], "balance": [], "cashflow": [], "other": []}
    for concept, pts in by_concept.items():
        bucket = _statement(concept)
        statements[bucket].append({
            "concept": _canonical(concept),
            "label": (_canonical(concept).split(":")[-1]
                      .replace("ExcludingAssessedTax", "")
                      .replace("FromContractWithCustomer", "")),
            "unit": units[concept],
            "points": [{k: v for k, v in p.items()
                        if k not in ("start", "dur")}
                       for p in pts[:MAX_PERIODS]],
        })
    for k in statements:
        # rank by canonical order, then merge aliases — first row per
        # canonical name keeps its points, later rows only fill ends
        # the primary doesn't cover (old-taxonomy eras)
        ranked = sorted(statements[k],
                        key=lambda c: c["points"][0]["end"]
                        if c["points"] else "", reverse=True)
        ranked = sorted(ranked, key=lambda c: _rank(c["concept"], k))
        merged: dict[str, dict] = {}
        for c in ranked:
            cur = merged.get(c["concept"])
            if cur is None:
                merged[c["concept"]] = c
                continue
            have = {p["end"] for p in cur["points"]}
            cur["points"] += [p for p in c["points"]
                              if p["end"] not in have]
            cur["points"].sort(key=lambda p: p["end"], reverse=True)
            cur["points"] = cur["points"][:MAX_PERIODS]
        statements[k] = list(merged.values())[:MAX_CONCEPTS_PER_STATEMENT]

    # ── derived key ratios (investing.com grid) — only where the
    # facts exist; nulls render as —
    ni = _ttm(by_concept, "NetIncomeLoss", "ProfitLoss", "NetIncome")
    eq = _latest(by_concept, "StockholdersEquity",
                 "StockholdersEquityIncludingPortionAttributableTo"
                 "NoncontrollingInterest",
                 "EquityAttributableToOwnersOfParent", "Equity")
    debt = _latest(by_concept, "LongTermDebt",
                   "LongTermDebtAndCapitalLeaseObligations",
                   "LongTermDebtNoncurrent", "LongtermBorrowings",
                   "NoncurrentBorrowings", "TotalDebt")
    ebitda = _ttm(by_concept, "EBITDA")
    eps = _ttm(by_concept, "EarningsPerShareDiluted",
               "EarningsPerShareBasic", "DilutedEarningsLossPerShare",
               "BasicEarningsLossPerShare", "DilutedEPS", "BasicEPS")
    shares = _latest(by_concept, "EntityCommonStockSharesOutstanding",
                     "CommonStockSharesOutstanding",
                     "WeightedAverageNumberOfDilutedSharesOutstanding",
                     "AdjustedWeightedAverageShares",
                     "DilutedAverageShares", "BasicAverageShares")
    div_ps = _ttm(by_concept,
                  "CommonStockDividendsPerShareDeclared",
                  "CommonStockDividendsPerShareCashPaid",
                  "DividendsPaidOrdinarySharesPerShare",
                  "DividendsRecognisedAsDistributionsToOwnersPerShare")

    ratios = {
        "pe": (close / eps)
        if close and eps and not ccy_mismatch else None,
        "pb": (close / (eq / shares))
        if close and eq and shares and not ccy_mismatch else None,
        "debt_equity": (debt / eq) if debt is not None and eq else None,
        "roe": (ni / eq) if ni is not None and eq else None,
        "ebitda": ebitda,
        "dividend_yield": (div_ps / close)
        if div_ps and close and not ccy_mismatch else None,
        "shares": shares,
        "eps_diluted": eps,
    }
    if ccy_mismatch:
        ratios["currency_mismatch"] = {
            "fundamentals": fund_ccy, "price": inst.currency or "USD",
            "note": ("per-share values are in " + str(fund_ccy) +
                     "; price-linked ratios suppressed — needs "
                     "FX/ADR-ratio normalization")}

    # union of period columns for the table header — newest first;
    # only ends shared by ≥2 concepts so one-off dei document dates
    # (8-K covers etc.) don't create empty noise columns
    usage: dict[str, tuple[str, int]] = {}
    for bucket in statements.values():
        for c in bucket:
            for p in c["points"]:
                fp, n = usage.get(p["end"], (p["fp"], 0))
                usage[p["end"]] = (fp if fp != "?" else p["fp"], n + 1)
    cols = {e: fp for e, (fp, n) in usage.items() if n >= 2}

    return {
        "statements": statements,
        "ratios": ratios,
        "periods": [{"end": e, "fp": fp}
                    for e, fp in sorted(cols.items(), reverse=True)
                    [:MAX_PERIODS * 2]],
        "facts": sum(len(b) for b in statements.values()),
    }
