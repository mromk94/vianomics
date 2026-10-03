"""Valuation run orchestration — gathers fundamentals, runs the
deterministic engine, persists versioned inputs+outputs."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.instruments import Instrument, Sector
from app.models.valuation import ValuationRun
from app.services import finmetrics as fm
from app.services import valuation as sector_val
from app.services import valuation_engine as ve
from app.services.fundamentals_query import fy_series, last_two, latest_instant
from app.services.green_zone import (
    C_CASH, C_CAPEXC, C_DEBT, C_EBIT, C_EQUITY, C_NI, C_OCF, C_PRETAX,
    C_REV, C_SHARES, C_TAX,
)

D = Decimal


def _cagr(series: dict, years: int | None = None) -> Decimal | None:
    items = sorted(series.items())
    if years:
        items = items[-(years + 1):]
    if len(items) < 2 or items[0][1] <= 0:
        return None
    n = len(items) - 1
    return (D(str(items[-1][1])) / D(str(items[0][1]))) ** (D(1) / n) - 1


async def gather_inputs(
    db: AsyncSession, inst: Instrument, as_of: datetime
) -> dict:
    """Fundamental anchors for valuation assumptions."""
    rev = await fy_series(db, inst.id, C_REV, as_of)
    ni = await fy_series(db, inst.id, C_NI, as_of)
    ocf = await fy_series(db, inst.id, C_OCF, as_of)
    capex = await fy_series(db, inst.id, C_CAPEXC, as_of)
    ebit = await fy_series(db, inst.id, C_EBIT, as_of)
    eq_s = await fy_series(db, inst.id, C_EQUITY, as_of)
    shares_s = await fy_series(db, inst.id, C_SHARES, as_of)
    debt = await latest_instant(db, inst.id, C_DEBT, as_of)
    cash = await latest_instant(db, inst.id, C_CASH, as_of)
    ebit_l = last_two(ebit)[1]
    tax_l = last_two((await fy_series(db, inst.id, C_TAX, as_of)))[1]
    pretax_l = last_two((await fy_series(db, inst.id, C_PRETAX, as_of)))[1]
    equity = await latest_instant(db, inst.id, C_EQUITY, as_of)

    sh = last_two(shares_s)[1]
    eps = fm.per_share(last_two(ni)[1], sh)
    fcf_vals = {
        pe: float(fm.fcf(ocf_v, capex.get(pe)))
        for pe, ocf_v in ocf.items()
    }
    fcf = fcf_vals.get(sorted(fcf_vals)[-1]) if fcf_vals else None
    roic = fm.roic(ebit_l, tax_l, pretax_l, debt, equity, cash)

    sector = None
    if inst.sector_id:
        sector = (
            await db.execute(select(Sector).where(Sector.id == inst.sector_id))
        ).scalar_one_or_none()

    return {
        "symbol": inst.symbol,
        "sector": sector.name if sector else None,
        "archetype": sector_val.archetype_for(
            sector.name if sector else None),
        "eps": float(eps) if eps is not None else None,
        "fcf": fcf,
        "shares": sh,
        "equity": equity,
        "net_debt": float(D(str(debt or 0)) - D(str(cash or 0))),
        "cash": float(cash) if cash is not None else 0.0,
        "debt": float(debt) if debt is not None else 0.0,
        "ni_latest": last_two(ni)[1],
        "revenue_latest": last_two(rev)[1],
        "bvps": (float(D(str(equity)) / D(str(sh)))
                 if equity is not None and sh else None),
        "growth": {
            "revenue": float(_cagr(rev)) if _cagr(rev) else None,
            "eps": None,  # EPS series requires share-adjusted EPS obs
            "ni": float(_cagr(ni)) if _cagr(ni) else None,
            "equity": float(_cagr(eq_s)) if _cagr(eq_s) else None,
            "fcf": float(_cagr(fcf_vals)) if _cagr(fcf_vals) else None,
        },
        "roic": float(roic) if roic is not None else None,
        "history": {
            "revenue": {k.isoformat(): v for k, v in sorted(rev.items())},
            "net_income": {k.isoformat(): v for k, v in sorted(ni.items())},
            "fcf": {k.isoformat(): v for k, v in sorted(fcf_vals.items())},
        },
    }


async def run_valuation(
    db: AsyncSession,
    inst: Instrument,
    *,
    price: float,
    growth: float,
    years: int = 10,
    discount_rate: float = 0.10,
    terminal_growth: float = 0.025,
    mos: float | None = None,
    future_pe: float | None = None,
    actor_id: str | None = None,
    mandate=None,
    scenario: str = "base",
) -> ValuationRun:
    as_of = utcnow()
    anchors = await gather_inputs(db, inst, as_of)

    mos_v = mos if mos is not None else (
        (mandate.margin_of_safety_min_pct / 100) if mandate else 0.50)
    missing = []

    inputs = {
        "price": price, "growth": growth, "years": years,
        "discount_rate": discount_rate,
        "terminal_growth": terminal_growth,
        "mos": mos_v,
        "future_pe": future_pe,
        "scenario": scenario,
        "anchors": anchors,
    }

    outputs: dict = {"methodology": ve.METHODOLOGY_VERSION}

    # Five Numbers
    outputs["five_numbers"] = ve.five_numbers(
        anchors["growth"]["revenue"], anchors["growth"]["eps"],
        anchors["growth"]["equity"], anchors["growth"]["fcf"],
        anchors["roic"] and Decimal(str(anchors["roic"])),
    )

    # Rule #1 sticker — needs positive EPS
    if anchors["eps"] and anchors["eps"] > 0:
        pe = future_pe or float(
            ve.default_future_pe(growth))
        try:
            outputs["rule1"] = ve.sticker_price(
                anchors["eps"], growth, years, pe,
                required_return=0.15, mos=mos_v)
        except ValueError as e:
            outputs["rule1"] = {"error": str(e)}
            missing.append("rule1")
    else:
        outputs["rule1"] = {"error": "no positive EPS"}
        missing.append("rule1 — EPS ≤ 0 or missing")

    # DCF — needs positive base FCF
    if anchors["fcf"] and anchors["fcf"] > 0 and anchors["shares"]:
        try:
            outputs["dcf"] = ve.dcf(
                anchors["fcf"], growth, years, discount_rate,
                terminal_growth, anchors["net_debt"], anchors["shares"])
            outputs["mos"] = ve.margin_of_safety(
                price, outputs["dcf"]["per_share"])
            outputs["discount_premium"] = ve.discount_premium(
                price, outputs["dcf"]["per_share"])
        except ValueError as e:
            outputs["dcf"] = {"error": str(e)}
            missing.append("dcf")
        # multi-stage DCF (doc-canonical): stage 1 = caller growth,
        # stage 2 = 50% fade, stage 3 = terminal growth — the CPRT
        # workbook "stage" terminal (PV of every year, no Gordon TV)
        try:
            g2 = growth * 0.5
            stages = ([(5, growth), (5, g2),
                       (years - 10, terminal_growth)]
                      if years > 10 else [(years, growth)])
            outputs["dcf_multistage"] = ve.dcf_multistage(
                anchors["fcf"], stages, discount_rate,
                anchors["cash"], anchors["debt"], anchors["shares"],
                terminal_method="stage")
        except ValueError as e:
            outputs["dcf_multistage"] = {"error": str(e)}
        # reverse DCF
        try:
            outputs["reverse_dcf"] = ve.reverse_dcf(
                price, anchors["fcf"], years, discount_rate,
                terminal_growth, anchors["net_debt"], anchors["shares"])
        except ValueError as e:
            outputs["reverse_dcf"] = {"error": str(e)}
        # sensitivity ±20% around g, ±2pp around r
        gs = [growth * f for f in (0.6, 0.8, 1.0, 1.2, 1.4)]
        rs = [discount_rate - 0.02, discount_rate - 0.01,
              discount_rate, discount_rate + 0.01, discount_rate + 0.02]
        outputs["sensitivity"] = ve.sensitivity(
            anchors["fcf"], gs, [r for r in rs if r > terminal_growth],
            years, terminal_growth, anchors["net_debt"], anchors["shares"])
    else:
        outputs["dcf"] = {"error": "no positive base FCF"}
        outputs["missing"] = missing + ["dcf — FCF ≤ 0 or missing"]

    # financials: DNI (NI base) + P/B intrinsic — doc method
    # selection for banks/finance cos
    if anchors["archetype"] == "financial":
        if anchors["ni_latest"] and anchors["ni_latest"] > 0 \
                and anchors["shares"]:
            try:
                g2 = growth * 0.5
                stages = ([(5, growth), (5, g2),
                           (years - 10, terminal_growth)]
                          if years > 10 else [(years, growth)])
                outputs["dni"] = ve.dni(
                    anchors["ni_latest"], stages, discount_rate,
                    anchors["cash"], anchors["debt"],
                    anchors["shares"], terminal_method="stage")
            except ValueError as e:
                outputs["dni"] = {"error": str(e)}
        if anchors["bvps"]:
            try:
                outputs["pb_intrinsic"] = ve.pb_intrinsic(
                    anchors["bvps"], fair_pb=1.10)
            except ValueError as e:
                outputs["pb_intrinsic"] = {"error": str(e)}

    # quick checks — PEG (consistent earnings growth) / PSG
    # (unprofitable hyper-growth)
    eps = anchors["eps"]
    ni_g = anchors["growth"]["ni"]
    if eps and eps > 0 and ni_g and ni_g > 0:
        try:
            outputs["peg"] = ve.peg(price / eps, ni_g * 100)
        except (ValueError, ZeroDivisionError):
            pass
    rev = anchors["revenue_latest"]
    if rev and rev > 0 and anchors["shares"] \
            and anchors["growth"]["revenue"]:
        try:
            mcap = price * anchors["shares"]
            outputs["psg"] = ve.psg(
                mcap / rev, anchors["growth"]["revenue"] * 100)
        except (ValueError, ZeroDivisionError):
            pass

    # sector-specific extras
    outputs["sector_valuation"] = sector_val.compute(
        anchors["sector"], price=price, shares=anchors["shares"],
        equity=anchors["equity"], eps=anchors["eps"],
        ni_series=list(anchors["history"]["net_income"].values()),
        fcf_ps=(anchors["fcf"] / anchors["shares"]
                if anchors["fcf"] and anchors["shares"] else None),
    )
    outputs["missing"] = missing

    prev = (
        await db.execute(
            select(ValuationRun)
            .where(ValuationRun.instrument_id == inst.id)
            .order_by(ValuationRun.created_at.desc())
        )
    ).scalars().first()
    run = ValuationRun(
        group_id=prev.group_id if prev else f"{inst.id}-v",
        instrument_id=inst.id,
        version=(prev.version + 1) if prev else 1,
        methodology=ve.METHODOLOGY_VERSION,
        mandate_version=mandate.version if mandate else None,
        created_by=actor_id,
        inputs=inputs,
        outputs=outputs,
    )
    db.add(run)
    return run
