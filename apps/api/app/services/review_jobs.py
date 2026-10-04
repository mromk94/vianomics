"""Phase-6 operational cadence — the doc's scheduled reviews:

    review:weekly     → watchlist/valuation/Four-M's review
    review:monthly    → recalculate MOS, re-rank candidates
    review:quarterly  → broker margin / leverage / performance review

These are reviews, not trades — they re-run the deterministic gates,
persist what they find, and alert. No job creates an order and none
closes a position (closes are human-approved).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument
from app.services import qualification as qual
from app.services.monitoring import emit_alert

REVIEW_STATES = ("watchlist", "under_research", "valuation_pending",
                 "trade_eligible", "active_position")


async def weekly_review(db: AsyncSession, limit: int = 200) -> dict:
    """Re-run the qualification gate over the live universe; flag any
    name whose Four-M proxy newly fails (fundamental deterioration —
    doc Step 20B is a review outcome, not an auto-exit)."""
    insts = (await db.execute(
        select(Instrument)
        .where(Instrument.status.in_(REVIEW_STATES),
               Instrument.is_active.is_(True),
               Instrument.listing_status == "active")
        .limit(limit))).scalars().all()
    out = {"reviewed": 0, "verdicts": {}, "deteriorated": [],
           "errors": 0}
    for inst in insts:
        try:
            gate = await qual.gate_with_price(
                db, inst, auto_baseline=False)
            out["reviewed"] += 1
            v = gate["verdict"]
            out["verdicts"][v] = out["verdicts"].get(v, 0) + 1
            has_fund = any(
                isinstance(row, dict)
                and any(row.get(h) is not None
                        for h in ("10y", "5y", "3y", "1y", "latest"))
                for row in gate["five_numbers"].values())
            if has_fund and not gate["four_ms"]["pass"]:
                out["deteriorated"].append(inst.symbol)
                await emit_alert(
                    db, severity="warning", source="monitor:review",
                    message=(f"{inst.symbol}: Four-M proxy failing on "
                             "weekly review — fundamental "
                             "deterioration"),
                    dedup_key=f"review_fund:{inst.symbol}",
                    instrument_id=inst.id,
                    action="review thesis — Step 20B exit consideration")
        except Exception:
            out["errors"] += 1
    return out


async def monthly_rerank(db: AsyncSession, limit: int = 100) -> dict:
    """Recalculate MOS for watchlist-grade names and re-rank by
    discount to intrinsic value — doc monthly cadence."""
    from app.services import valuation_service as vs
    insts = (await db.execute(
        select(Instrument)
        .where(Instrument.status.in_(
            ("watchlist", "under_research", "valuation_pending",
             "trade_eligible")),
               Instrument.is_active.is_(True),
               Instrument.listing_status == "active")
        .limit(limit))).scalars().all()
    out = {"repriced": 0, "errors": 0, "ranked": []}
    for inst in insts:
        try:
            gate = await qual.gate_with_price(db, inst)
            disc = gate["valuation"]["discount_to_iv"]
            if disc is not None:
                out["ranked"].append(
                    {"symbol": inst.symbol,
                     "discount_to_iv": round(disc, 4),
                     "verdict": gate["verdict"],
                     "method": gate["valuation"]["method_used"]})
        except Exception:
            out["errors"] += 1
    out["ranked"].sort(key=lambda r: -(r["discount_to_iv"] or 0))
    return out


async def quarterly_margin_review(db: AsyncSession) -> dict:
    """Broker-margin review — doc Phase 6 quarterly + Step 2.3: if
    maintenance margin ≥ 16% the 4% gross stop is too late; the
    sleeve must cap exposure. Alert when the buffer math is tight."""
    from app.routers.risk import _portfolio_ctx
    from app.services import risk_engine as re_
    pf = await _portfolio_ctx(db)
    sleeve = pf.get("sleeve") or {}
    st = sleeve.get("state") or {}
    cfg = sleeve.get("config") or {}
    maint = st.get("maint_margin_used")
    out = {
        "sleeve_equity": st.get("sleeve_equity"),
        "maint_margin_used": maint,
        "margin_call_at_gross": st.get("margin_call_at_gross"),
        "margin_call_distance": st.get("margin_call_distance"),
        "margin_utilisation": st.get("margin_utilisation"),
        "effective_leverage": st.get("effective_leverage"),
        "buffer_capped": st.get("buffer_capped"),
    }
    if not sleeve.get("enabled"):
        out["skipped"] = "sleeve disabled"
        return out
    if maint is not None and maint >= 0.16:
        # doc Step 2.3 — at maint ≥16% the broker call lands before
        # the 4% stop; the cap must already have tightened exposure
        await emit_alert(
            db, severity="critical", source="monitor:review",
            message=(f"maintenance margin {maint:.0%} ≥ 16% — the 4% "
                     "gross stop is too late; exposure cap must bind "
                     f"(buffer_capped={st.get('buffer_capped')})"),
            dedup_key="review_margin_16",
            action="cap exposure below 5X or raise sleeve equity")
    elif st.get("margin_utilisation") and st["margin_utilisation"] > 0.75:
        await emit_alert(
            db, severity="warning", source="monitor:review",
            message=(f"sleeve margin utilisation "
                     f"{st['margin_utilisation']:.0%} on quarterly "
                     "review"),
            dedup_key="review_margin_util",
            action="review leverage headroom")
    out["gross_stop_pct"] = cfg.get("gross_stop_pct")
    out["portfolio_stop_pct"] = cfg.get("portfolio_stop_pct")
    return out


REVIEW_JOBS = {
    "review:weekly": weekly_review,
    "review:monthly": monthly_rerank,
    "review:quarterly": quarterly_margin_review,
}
