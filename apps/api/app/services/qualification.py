"""Phase-1 Fundamental Qualification Gate (V2 doc §Phase 1 / V1 Steps
4–7).

Produces the gate object:

    four_ms          — Meaning / Moat / Management machine-proxy scores
                       (MOS is deferred to the valuation engine)
    five_numbers     — per-metric 10/5/3/1y CAGR + consistency
    initial_screen   — the doc's six hard thresholds
    valuation        — status zones + consensus (never averaged)
    verdict          — PASS / WATCH / FAIL → eligibility candidate flag

The Four M's are inherently qualitative — these are machine proxies
over computed fundamentals, explicitly labeled so a human review can
override. No gate is ever a trade order.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument, Sector
from app.models.valuation import ValuationRun
from app.services import finmetrics as fm
from app.services.fundamentals_query import fy_series, last_two, latest_instant
from app.services.green_zone import (
    C_CA, C_CAPEXC, C_CL, C_CASH, C_DEBT, C_EBIT, C_EQUITY, C_NI, C_OCF,
    C_PRETAX, C_REV, C_SHARES, C_TAX,
)

D = Decimal
HORIZONS = (10, 5, 3, 1)


def _cagr_window(series: dict, years: int) -> Decimal | None:
    """CAGR (RATE semantics) over the last `years` of a {date: value}
    series. None when the window isn't actually covered — never fake a
    10y CAGR from 3y of data."""
    items = sorted(series.items())
    if len(items) < 2:
        return None
    end = items[-1][0]
    win = [(d, v) for d, v in items
           if d >= end - timedelta(days=365 * years + 30)]
    if len(win) < 2 or win[0][1] <= 0 or win[-1][1] <= 0:
        return None
    n = (win[-1][0] - win[0][0]).days / 365.25
    if n < years * 0.6:                    # coverage guard
        return None
    return (D(str(win[-1][1])) / D(str(win[0][1]))) ** (D(1) / D(str(n))) - 1


def _consistency(series: dict) -> float | None:
    """Share of YoY deltas ≥ 0 — growth that survives inspection."""
    vals = [v for _, v in sorted(series.items())]
    if len(vals) < 3:
        return None
    diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
    return sum(1 for d in diffs if d >= 0) / len(diffs)


def _metric_row(series: dict, growth_min=D("0.10")) -> dict:
    row = {f"{y}y": (float(_cagr_window(series, y))
                     if _cagr_window(series, y) is not None else None)
           for y in HORIZONS}
    row["consistency"] = _consistency(series)
    # pass on the deepest available horizon — a 10y company is judged
    # on 10y; a young IPO on what it has, honestly labeled
    primary = next((row[f"{y}y"] for y in HORIZONS
                    if row[f"{y}y"] is not None), None)
    row["horizon_used"] = next((f"{y}y" for y in HORIZONS
                                if row[f"{y}y"] is not None), None)
    row["pass"] = (primary is not None
                   and primary > float(growth_min)
                   and (row["consistency"] is None
                        or row["consistency"] >= 0.6))
    return row


def _per_share(flow: dict, shares: dict) -> dict:
    """{period_end: flow/shares} on the aligned FY intersection."""
    return {pe: v / shares[pe] for pe, v in flow.items()
            if shares.get(pe)}


def _roic_series(ebit: dict, tax: dict, pretax: dict, debt: dict,
                 equity: dict, cash: dict) -> dict:
    """Per-FY ROIC where all inputs resolve for that period."""
    out = {}
    for pe, ebit_v in ebit.items():
        r = fm.roic(ebit_v, tax.get(pe), pretax.get(pe),
                    debt.get(pe), equity.get(pe), cash.get(pe))
        if r is not None:
            out[pe] = float(r)
    return out


def _split_distorted(shares: dict) -> bool:
    """A >2.5× step in the share series is a stock split, not real
    issuance — per-share ratios computed against as-filed counts are
    meaningless across it (pre-split shares vs post-split shares)."""
    vals = [v for _, v in sorted(shares.items()) if v and v > 0]
    return any(vals[i] / vals[i - 1] > 2.5 or vals[i - 1] / vals[i] > 2.5
               for i in range(1, len(vals)))


def _status_from_discount(discount: float | None) -> str:
    """discount = (IV − price) / IV — delegates to the canonical ladder
    in finmetrics so the gate and the valuation lab agree."""
    return fm.valuation_status(discount)


async def qualification_gate(
    db: AsyncSession, inst: Instrument,
    price: float | None = None, as_of: datetime | None = None,
    valuation_mode: str = "both",
) -> dict:
    from app.db.base import utcnow
    as_of = as_of or utcnow()

    # ── series (12y pull for the 10y windows) ──
    rev = await fy_series(db, inst.id, C_REV, as_of, years=12)
    ni = await fy_series(db, inst.id, C_NI, as_of, years=12)
    ocf = await fy_series(db, inst.id, C_OCF, as_of, years=12)
    capex = await fy_series(db, inst.id, C_CAPEXC, as_of, years=12)
    ebit = await fy_series(db, inst.id, C_EBIT, as_of, years=12)
    eq_s = await fy_series(db, inst.id, C_EQUITY, as_of, years=12)
    debt_s = await fy_series(db, inst.id, C_DEBT, as_of, years=12)
    cash_s = await fy_series(db, inst.id, C_CASH, as_of, years=12)
    sh_s = await fy_series(db, inst.id, C_SHARES, as_of, years=12)
    tax_s = await fy_series(db, inst.id, C_TAX, as_of, years=12)
    pretax_s = await fy_series(db, inst.id, C_PRETAX, as_of, years=12)
    # filed EPS is restated by the company after splits — split-safe,
    # unlike NI ÷ as-filed share counts
    eps_filed = await fy_series(
        db, inst.id,
        ["us-gaap:EarningsPerShareDiluted",
         "us-gaap:EarningsPerShareBasic",
         "ifrs-full:DilutedEarningsLossPerShare",
         "ifrs-full:BasicEarningsLossPerShare",
         "yahoo:DilutedEPS", "yahoo:BasicEPS",
         "stockrow:DilutedEPS"], as_of, years=12)

    # StockRow depth-extension — when local XBRL covers fewer than ~8
    # fiscal years, merge the provider's ~10y annual series UNDER the
    # local one (local wins each period_end; StockRow only fills ends
    # the local series lacks — same extend-don't-compete rule as
    # fy_series alias merge). Unconfigured/absent → local-only.
    _sr_used: dict[str, int] = {}
    try:
        from app.providers.stockrow import StockRowAdapter
        _a = StockRowAdapter()
        _sr = _a if _a.api_key else None
    except Exception:
        _sr = None

    async def _ext(series: dict, slug: str, min_years: float = 8.0):
        if _sr is None:
            return
        items = sorted(series.items())
        span = ((items[-1][0] - items[0][0]).days / 365.25
                if len(items) >= 2 else 0.0)
        if span >= min_years:
            return
        try:
            ext = await _sr.annual_series(inst.symbol, slug)
        except Exception:
            return
        added = 0
        for pe, v in ext.items():
            if pe not in series:
                series[pe] = v
                added += 1
        if added:
            _sr_used[slug] = _sr_used.get(slug, 0) + added

    await _ext(rev, "revenue"); await _ext(ni, "netinc")
    await _ext(ocf, "opcf"); await _ext(ebit, "ebit")
    await _ext(eq_s, "equityt"); await _ext(sh_s, "shsdila")
    await _ext(eps_filed, "epsd")

    split = _split_distorted(sh_s)
    eps_s = eps_filed or _per_share(ni, sh_s)
    # FCF needs BOTH ocf and capex — a missing capex must not read as
    # FCF=OCF (silently inflated), it is simply absent
    fcf_ps = _per_share(
        {pe: float(f) for pe, o in ocf.items()
         if capex.get(pe) is not None
         and (f := fm.fcf(o, capex.get(pe))) is not None}, sh_s)
    bvps_s = (_per_share(eq_s, sh_s) if not split else {})
    bvps_basis = "per_share"
    if split or not bvps_s:
        # split-distorted share series → equity level carries the same
        # compounding signal (book value ≈ equity); dilution is still
        # measured separately by the Management M's share-count check
        bvps_s = dict(eq_s)
        bvps_basis = "equity_level"
    roic_s = _roic_series(ebit, tax_s, pretax_s, debt_s, eq_s, cash_s)

    # ── five numbers table ──
    roic_vals = [v for _, v in sorted(roic_s.items())]
    five = {
        "revenue": _metric_row(rev),
        "eps": {**_metric_row(eps_s),
                "basis": "filed_eps" if eps_filed else "ni_per_share"},
        "fcf_per_share": _metric_row(fcf_ps),
        "bvps": {**_metric_row(bvps_s), "basis": bvps_basis},
        "roic": {
            "latest": roic_vals[-1] if roic_vals else None,
            "avg_3y": (sum(roic_vals[-3:]) / len(roic_vals[-3:])
                       if len(roic_vals) >= 3 else None),
            "min_5y": (min(roic_vals[-5:])
                       if len(roic_vals) >= 5 else None),
            "pass": bool(roic_vals and roic_vals[-1] > 0.15),
        },
    }
    five["all_pass"] = all(v["pass"] for v in five.values()
                           if isinstance(v, dict))
    five["growth_min"] = 0.10
    five["roic_min"] = 0.15

    # ── initial screen (doc Step 1.2 thresholds) ──
    sh_l = last_two(sh_s)[1]
    eps_l = last_two(eps_s)[1]
    debt_l = await latest_instant(db, inst.id, C_DEBT, as_of)
    equity_l = await latest_instant(db, inst.id, C_EQUITY, as_of)
    ca = await latest_instant(db, inst.id, C_CA, as_of)
    cl = await latest_instant(db, inst.id, C_CL, as_of)
    eps_g5 = five["eps"]["5y"] or five["eps"]["3y"]
    screen = {
        "market_cap_gt_10b": {
            "value": (price * sh_l) if price and sh_l else None,
            "pass": bool(price and sh_l and price * sh_l > 10e9)},
        "pe_lt_20": {
            "value": (price / eps_l) if price and eps_l else None,
            "pass": bool(price and eps_l and eps_l > 0
                         and price / eps_l < 20)},
        "roic_gt_15": {"value": five["roic"]["latest"],
                       "pass": five["roic"]["pass"]},
        "de_lt_0_5": {
            "value": (float(D(str(debt_l)) / D(str(equity_l)))
                      if debt_l and equity_l else None),
            "pass": bool(debt_l is not None and equity_l
                         and equity_l > 0
                         and debt_l / equity_l < 0.5)},
        "eps_growth_5y_gt_10": {
            "value": eps_g5, "pass": bool(eps_g5 and eps_g5 > 0.10)},
        "current_ratio_gt_1": {
            "value": (float(D(str(ca)) / D(str(cl)))
                      if ca is not None and cl else None),
            "pass": bool(ca is not None and cl and cl > 0
                         and ca / cl > 1)},
    }
    # Step 1.2 — the screen is part of the qualification chain, not
    # a decoration: any failed threshold (or missing input, which
    # reads fail) blocks TRADE_ELIGIBLE
    screen["all_pass"] = all(v["pass"] for v in screen.values())

    # ── sector/architype — hoisted: method routing AND the Four-M
    # proxies both need it ──
    sec = None
    if inst.sector_id:
        s = await db.get(Sector, inst.sector_id)
        sec = s.name if s else None

    # ── valuation status + consensus (never averaged) ──
    vrun = (await db.execute(
        select(ValuationRun)
        .where(ValuationRun.instrument_id == inst.id)
        .order_by(ValuationRun.created_at.desc()).limit(1))
    ).scalars().first()
    o = (vrun.outputs or {}) if vrun else {}
    sticker = (o.get("rule1") or {}).get("sticker_price") or o.get("sticker")
    mos_px = (o.get("rule1") or {}).get("buy_price") or o.get("buy")
    dcf_iv = (o.get("dcf") or {}).get("per_share") \
        or (o.get("dcf_multistage") or {}).get("per_share")
    dni_iv = (o.get("dni") or {}).get("per_share")
    pb_iv = (o.get("pb_intrinsic") or {}).get("per_share")

    # Step 1.3/§5 + §9 — the company type picks the PRIMARY valuation
    # method (financials → DNI/P-B, operating cos → Rule-1/DCF) and
    # VALUATION_MODE filters which frameworks may set the bar. The
    # other methods still display side-by-side — never averaged.
    from app.services import valuation as sector_val
    arch = sector_val.archetype_for(sec)
    mode = (valuation_mode or "both").lower()
    method_order: list[str] = []
    if mode != "institutional_dcf":
        method_order += (["rule1"] if arch != "financial"
                         else ["dni", "pb", "rule1"])
    if mode != "rule1_classic":
        method_order += (["dni", "pb", "dcf"] if arch == "financial"
                         else ["dcf", "dni", "pb"])
    iv_map = {"rule1": sticker, "dcf": dcf_iv,
              "dni": dni_iv, "pb": pb_iv}
    method_used = next((m for m in method_order if iv_map.get(m)),
                       None)
    iv_best = iv_map.get(method_used) if method_used else None

    zone_rule1 = None
    if price and mos_px:
        zone_rule1 = ("BUY_ZONE" if price <= mos_px
                      else "WATCH" if sticker and price < sticker
                      else "VALUATION_EXIT")
    discount = fm.intrinsic_discount(price, iv_best) \
        if price and iv_best else None
    valuation = {
        "status": _status_from_discount(
            float(discount) if discount is not None else None),
        "valuation_mode": mode,
        "archetype": arch,
        "method_used": method_used,
        "rule1": {"sticker_price": sticker, "mos_price": mos_px,
                  "zone": zone_rule1},
        "dcf": {"intrinsic_value": dcf_iv},
        "financial_methods": {"dni": dni_iv, "pb": pb_iv},
        "discount_to_iv": (float(discount)
                           if discount is not None else None),
        "consensus": {
            "methods_compared": [m for m, v in
                                 (("rule1", sticker), ("dcf", dcf_iv),
                                  ("dni", dni_iv), ("pb", pb_iv))
                                 if v],
            "note": ("methods shown side-by-side, never averaged — "
                     "differences trace to required return (15% Rule-1 "
                     "vs CAPM discount) and the 50% MOS floor"),
        },
        "run_at": vrun.created_at.isoformat() if vrun else None,
    }

    # ── Four M's — machine proxies, explicitly labeled ──
    yrs = len(rev)
    # share-count growth is meaningless across a split — skip the
    # dilution check rather than read a 10:1 as 900% issuance
    shares_g5 = None if split else _cagr_window(sh_s, 5)
    roic_latest = five["roic"]["latest"]
    roic_avg3 = five["roic"]["avg_3y"]

    four_ms = {
        "meaning": {
            "pass": yrs >= 3,
            "proxy": True,
            "evidence": ([f"sector: {sec}" if sec
                          else "sector unmapped — review",
                          f"{yrs}y of filed fundamentals"]),
            "note": "business-understanding is qualitative — proxy is "
                    "sector identification + data depth"},
        "moat": {
            "pass": bool(roic_latest and roic_latest > 0.15
                         and (roic_avg3 or 0) > 0.15),
            "proxy": True,
            "evidence": ([f"ROIC {roic_latest:.1%}" if roic_latest
                          else "ROIC n/a",
                          f"3y avg {roic_avg3:.1%}" if roic_avg3
                          else "insufficient history"]),
            "note": "durable-advantage proxy: sustained ROIC > 15%"},
        "management": {
            "pass": (bool(shares_g5 is None or shares_g5 <= 0.05)
                     and bool(five["bvps"]["pass"]
                              or five["revenue"]["pass"])),
            "proxy": True,
            "evidence": ([f"5y share CAGR {shares_g5:.1%}"
                          if shares_g5 is not None
                          else ("share series split-distorted — "
                                "dilution check skipped" if split
                                else "share count stable/n/a")]),
            "note": "shareholder-alignment proxy: dilution ≤5%/yr + "
                    "compounding book value"},
        "margin_of_safety": {
            "deferred": True,
            "value": mos_px,
            "note": "computed by the valuation engine — see "
                    "valuation.rule1.mos_price"},
    }
    four_ms["pass"] = all(four_ms[k]["pass"] for k in
                          ("meaning", "moat", "management"))

    # ── verdict (doc Step 1.7) — the chain is ordered: Four M's →
    # initial screen → five numbers → price ≤ MOS bar. Rule-1's
    # buy_price is the bar when the routed method is Rule-1; for
    # DCF-style methods the bar is IV × (1−50%). ──
    elig_bar = (mos_px if method_used == "rule1"
                else (iv_best * 0.5 if iv_best else None))
    if not four_ms["pass"]:
        verdict = "REJECTED"
    elif not screen["all_pass"] or not five["all_pass"]:
        verdict = "WATCH"
    elif price and elig_bar and price <= elig_bar:
        verdict = "TRADE_ELIGIBLE"
    else:
        verdict = "WATCHLIST"

    return {
        "symbol": inst.symbol,
        "as_of": as_of.isoformat(),
        "price": price,
        "verdict": verdict,
        "four_ms": four_ms,
        "five_numbers": five,
        "initial_screen": screen,
        "valuation": valuation,
        # provenance — which series StockRow extended past the local
        # XBRL history (metric slug → points added). Absent → local
        # filings only.
        "data_sources": ({"xbrl": "filed fundamentals",
                          "stockrow": _sr_used}
                         if _sr_used else {"xbrl": "filed fundamentals"}),
        "note": ("Four M's are machine proxies over filed fundamentals "
                 "— a human qualitative review may override. This gate "
                 "never creates an order."),
    }


async def gate_with_price(db, inst, auto_baseline: bool = True,
                          valuation_mode: str = "both") -> dict:
    """Convenience — resolves the latest close for the gate. When no
    ValuationRun exists and a price resolves, seeds a conservative
    auto_baseline run so the eligibility bar is always computable —
    the run is versioned and auditable like any other."""
    from app.db.base import utcnow
    from app.models.market import OhlcvBar
    px = (await db.execute(
        select(OhlcvBar.close)
        .where(OhlcvBar.instrument_id == inst.id,
               OhlcvBar.timeframe == "1d")
        .order_by(OhlcvBar.time.desc()).limit(1))).scalar()
    price = float(px) if px else None
    if auto_baseline and price:
        last = (await db.execute(
            select(ValuationRun)
            .where(ValuationRun.instrument_id == inst.id)
            .order_by(ValuationRun.created_at.desc()).limit(1))
        ).scalars().first()
        # Re-run when the latest run produced no usable IV (error-only
        # outputs — e.g. created before a fundamentals fix) — but never
        # churn a fresh auto_baseline within 24h of the last attempt.
        o = (last.outputs or {}) if last else {}
        usable = bool((o.get("rule1") or {}).get("sticker_price")
                      or (o.get("dcf") or {}).get("per_share")
                      or (o.get("dni") or {}).get("per_share")
                      or (o.get("pb_intrinsic") or {}).get("per_share"))
        stale_auto = (last is not None
                      and (last.inputs or {}).get("scenario")
                          == "auto_baseline"
                      and (utcnow() - last.created_at).days < 1)
        if last is None or (not usable and not stale_auto):
            iid = inst.id            # snapshot before commit expires it
            try:
                from app.services import mandate as mandate_svc
                from app.services import valuation_service as vs
                await vs.auto_valuation(
                    db, inst, price=price,
                    mandate=await mandate_svc.get_active(db))
                await db.commit()
            except Exception:
                await db.rollback()
            # commit and rollback both expire ORM state — re-load so
            # the gate below never fires a lazy attribute load
            inst = await db.get(Instrument, iid)
    return await qualification_gate(db, inst, price=price,
                                    valuation_mode=valuation_mode)
