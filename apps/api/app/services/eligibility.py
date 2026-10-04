"""Trade Eligibility Object — the doc's unified gate.

Composes the five gates a pyramid entry must survive:

    quality    — Four M's pass + five-numbers (qualification gate)
    valuation  — price <= MOS (or IV x 50% for non-Rule1 methods)
    technical  — the timing engine produced an entry_signal
    margin     — sleeve free margin covers the starter leg
    portfolio  — sleeve capacity: position count, per-asset and gross
                 headroom (incl. the maintenance-margin buffer)

The object is machine-readable and persistable — every rejection
carries its reasons so the audit trail shows WHY the gate refused,
not just that it did. CIO/committee output may inform conviction;
it can never flip a failing gate.

Verdict ladder:
    TRADE_ELIGIBLE  — all gates pass; sizing may proceed
    BLOCKED         — fundamentals + timing pass but sleeve capacity
                      is exhausted (positions/gross/margin full)
    WATCHLIST       — quality + valuation pass; waiting on timing or
                      price above MOS
    WATCH           — quality passes, growth/valuation incomplete
    REJECTED        — quality fails or valuation unusable
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument
from app.services import risk_engine as re_
from app.services import technical_engine as te
from app.services import qualification as qual
from app.db.base import utcnow

D = Decimal

_TL = "TRADE_ELIGIBLE"


async def trade_eligibility(
    db: AsyncSession,
    inst: Instrument,
    pf_ctx: dict,
    *,
    entry: float | None = None,
    atr: float | None = None,
    auto_baseline: bool = True,
) -> dict:
    """The unified gate. `pf_ctx` is the risk router's _portfolio_ctx
    (carries nav/cash/positions/sleeve). `entry`/`atr` default to the
    last close and its ATR-14 so margin/portfolio gates size a real
    starter leg instead of a hypothetical."""

    gate = await qual.gate_with_price(
        db, inst, auto_baseline=auto_baseline)
    price = entry or gate["price"]

    # ── technical timing ──
    try:
        t = await te.evaluate(db, inst, utcnow())
        tech_decision = t.get("decision")
        tech = {
            "pass": tech_decision == "entry_signal",
            "decision": tech_decision,
            "mean_reversion": (t.get("mean_reversion") or {})
                              .get("decision"),
            "trend_following": (t.get("trend_following") or {})
                               .get("decision"),
            "data_fresh": t.get("data_fresh"),
            "last_close": t.get("last_close"),
            "atr_14": (t.get("indicators") or {}).get("atr_14"),
        }
    except Exception as e:
        tech = {"pass": False, "decision": "invalid_data",
                "error": str(e)[:200]}

    if atr is None:
        atr = tech.get("atr_14")
    if entry is None:
        entry = tech.get("last_close") or price

    # ── sleeve margin + portfolio capacity ──
    sleeve = pf_ctx.get("sleeve") or {}
    margin_gate = {"pass": True, "skipped": True}
    portfolio_gate = {"pass": True, "skipped": True}
    sizing = None
    if sleeve.get("enabled"):
        st, cfg = sleeve["state"], sleeve["config"]
        asset_gross = sum(
            p["market_value"] for p in sleeve["positions"]
            if p["symbol"] == inst.symbol)
        already_held = asset_gross > 0

        if entry and atr:
            sizing = re_.sleeve_sizing(
                entry, atr, st, cfg,
                asset_gross=asset_gross,
                adv_shares=(
                    float(inst.avg_dollar_volume_30d) / entry
                    if inst.avg_dollar_volume_30d else None))
        margin_gate = {
            "pass": bool(sizing and sizing["shares"] > 0
                         and sizing["margin_required"]
                         <= st["free_margin"]),
            "free_margin": st["free_margin"],
            "margin_required": (sizing["margin_required"]
                                if sizing else None),
            "maint_margin_used": st["maint_margin_used"],
            "buffer_capped": st["buffer_capped"],
            "skipped": False,
        }
        portfolio_gate = {
            "pass": bool(
                (already_held
                 or st["open_positions"] < cfg["max_positions"])
                and st["gross"] < st["effective_gross_cap"]
                and asset_gross < st["max_asset_notional"]),
            "open_positions": st["open_positions"],
            "max_positions": cfg["max_positions"],
            "gross": st["gross"],
            "effective_gross_cap": st["effective_gross_cap"],
            "asset_gross": asset_gross,
            "max_asset_notional": st["max_asset_notional"],
            "buffer_capped": st["buffer_capped"],
            "skipped": False,
        }

    # ── compose the verdict ──
    qv = gate["verdict"]
    quality_pass = bool(gate["four_ms"].get("pass"))
    growth_pass = bool(gate["five_numbers"].get("all_pass"))
    valuation_pass = qv == _TL
    # valuation usable but price above the bar → soft gate (WATCHLIST),
    # valuation unusable/missing → hard blocker alongside quality
    val_status = gate["valuation"]["status"]
    val_usable = val_status != "INSUFFICIENT_DATA"

    gates = {
        "quality": {"pass": quality_pass,
                    "four_ms": gate["four_ms"],
                    "five_numbers_pass": growth_pass},
        "valuation": {"pass": valuation_pass,
                      "status": val_status,
                      "zone": gate["valuation"]["rule1"]["zone"],
                      "mos_price": gate["valuation"]["rule1"]["mos_price"],
                      "price": price},
        "technical": tech,
        "margin": margin_gate,
        "portfolio": portfolio_gate,
    }

    blocking = []
    if not quality_pass:
        blocking.append("four_ms_failed")
    if not val_usable:
        blocking.append("valuation_insufficient_data")
    if tech["decision"] == "invalid_data":
        blocking.append("technical_invalid_data")
    if sleeve.get("enabled"):
        if not margin_gate["pass"]:
            blocking.append("no_margin_headroom")
        if not portfolio_gate["pass"]:
            blocking.append("sleeve_capacity_exhausted")

    capacity_blocked = (sleeve.get("enabled")
                        and (not margin_gate["pass"]
                             or not portfolio_gate["pass"]))
    if not quality_pass:
        verdict = "REJECTED"
    elif not growth_pass or not val_usable:
        verdict = "WATCH"      # quality ok, proof incomplete
    elif not valuation_pass:
        verdict = "WATCHLIST"  # qualified company, price above MOS
    elif not tech["pass"]:
        verdict = "WATCHLIST"  # at/below MOS, waiting on timing
    elif capacity_blocked:
        # every idea-gate passed — the sleeve itself is full
        verdict = "BLOCKED"
    else:
        verdict = _TL

    return {
        "symbol": inst.symbol,
        "as_of": utcnow().isoformat(),
        "verdict": verdict,
        "gates": gates,
        "blocking": blocking,
        "qualification_verdict": qv,
        "price": price,
        "entry_assumed": entry,
        "atr_used": atr,
        "sizing": sizing,
        "sleeve_enabled": bool(sleeve.get("enabled")),
        "note": ("gate order is fixed: quality → valuation → technical "
                 "→ margin → portfolio. AI and CIO conviction can never "
                 "override a failing gate — they only annotate it."),
    }
