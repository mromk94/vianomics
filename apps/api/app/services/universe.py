"""Part 2 — Investment Universe service.

Eligibility ladder (never conflated):
  known      — in the security master
  eligible   — passes the eligible-universe rules (liquidity, mcap,
               asset class, listing status)
  approved   — membership in the 'approved' universe → tradeable
"""

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument
from app.models.universe import (
    Universe,
    UniverseMembership,
    Watchlist,
    WatchlistItem,
)

# Defaults for the 'eligible' tier — configurable via universe.rules.
DEFAULT_RULES: dict[str, Any] = {
    "asset_classes": ["equity", "etf"],
    "listing_status": ["active"],
    "min_market_cap": 300_000_000,
    "min_avg_dollar_volume": 5_000_000,
}

# Asset classes exempt from the market-cap gate — ETFs/indices have
# AUM, not market cap, which we don't track. Liquidity still applies.
NO_MCAP_CLASSES = {"etf", "index"}

# Alpaca exchanges we admit to the discovery pool — the main US
# listing venues; OTC/CRYPTO/etc. stay out
_MAJOR_EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "AMEX", "NYSEAMERICAN",
                    "BATS", "NYSEArca", "NYSEAmerican"}


async def sync_alpaca_universe(
    db: AsyncSession, tenant_id: str = "default",
) -> dict[str, Any]:
    """Alpaca /v2/assets → the 'global' discovery universe.

    This is the *discovery pool*, not the book — everything lands in
    'global'; 'eligible'/'approved' stay gated by mandate rules + the
    Four-M's engine. Bars/quotes for global-only names hydrate on
    view (the bars endpoint self-heals) instead of a 10k-symbol
    nightly pull."""
    from app.services.secrets import get_secret
    from app.providers.alpaca import AlpacaAdapter

    key = await get_secret(db, "ALPACA_API_KEY")      # adapter falls
    secret = await get_secret(db, "ALPACA_SECRET_KEY")  # back to env
    ad = AlpacaAdapter(api_key=key, secret_key=secret)
    try:
        assets = await ad.assets()
    except Exception as e:
        return {"status": "failed", "error": f"alpaca assets: {e}"}

    uni = await universe_by_name(db, "global", tenant_id)
    if uni is None:
        uni = Universe(name="global", tier="global",
                       description="Alpaca tradable-asset discovery pool",
                       tenant_id=tenant_id)
        db.add(uni)
        await db.flush()

    existing = {i.symbol: i for i in
                (await db.execute(select(Instrument))).scalars().all()}
    have_membership = {
        m.instrument_id for m in (await db.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe_id == uni.id))
        ).scalars().all()}

    created = added = 0
    for a in assets:
        if not a.get("tradable"):
            continue
        if a.get("exchange") not in _MAJOR_EXCHANGES:
            continue
        sym = (a.get("symbol") or "").strip().upper()
        if not sym or len(sym) > 12 or not sym.replace(".", "").isalnum():
            continue        # skip pairs/ODD lots/test tickers
        inst = existing.get(sym)
        if inst is None:
            inst = Instrument(
                symbol=sym, name=a.get("name") or sym,
                asset_class="equity",
                currency="USD", status="watchlist")
            db.add(inst)
            await db.flush()
            existing[sym] = inst
            created += 1
        if inst.id not in have_membership:
            db.add(UniverseMembership(
                universe_id=uni.id, instrument_id=inst.id,
                status="active", reason="alpaca universe sync"))
            have_membership.add(inst.id)
            added += 1
    await db.flush()
    return {"status": "success", "assets_seen": len(assets),
            "instruments_created": created, "members_added": added}


async def universe_by_name(db: AsyncSession, name: str, tenant_id: str = "default") -> Universe | None:
    return (
        await db.execute(
            select(Universe).where(
                Universe.tenant_id == tenant_id, Universe.name == name
            )
        )
    ).scalar_one_or_none()


async def rules_for(db: AsyncSession, tenant_id: str = "default") -> dict[str, Any]:
    eligible = await universe_by_name(db, "eligible", tenant_id)
    rules = dict(DEFAULT_RULES)
    if eligible and eligible.rules:
        rules.update(eligible.rules)
    return rules


def eligibility_reasons(inst: Instrument, rules: dict[str, Any]) -> list[str]:
    """Deterministic eligibility gate — returns blocking reasons.
    Empty list = eligible for research."""
    reasons: list[str] = []
    # `or "active"`/`or "equity"`: column defaults only apply at INSERT;
    # unsaved objects report None — treat as the schema default.
    if (inst.listing_status or "active") not in rules.get("listing_status", ["active"]):
        reasons.append(f"listing_status={inst.listing_status}")
    if (inst.asset_class or "equity") not in rules.get("asset_classes", ["equity"]):
        reasons.append(f"asset_class={inst.asset_class} not in {rules['asset_classes']}")
    min_cap = rules.get("min_market_cap")
    if min_cap is not None and (inst.asset_class or "equity") \
            not in NO_MCAP_CLASSES:
        if inst.market_cap is None:
            reasons.append("market_cap unknown")
        elif float(inst.market_cap) < min_cap:
            reasons.append(f"market_cap {inst.market_cap:,.0f} < {min_cap:,.0f}")
    min_adv = rules.get("min_avg_dollar_volume")
    if min_adv is not None:
        if inst.avg_dollar_volume_30d is None:
            reasons.append("liquidity unknown")
        elif float(inst.avg_dollar_volume_30d) < min_adv:
            reasons.append(
                f"adv30 {inst.avg_dollar_volume_30d:,.0f} < {min_adv:,.0f}"
            )
    return reasons


async def instrument_detail(
    db: AsyncSession, inst: Instrument, tenant_id: str = "default"
) -> dict:
    rules = await rules_for(db, tenant_id)
    reasons = eligibility_reasons(inst, rules)

    memberships = (
        await db.execute(
            select(UniverseMembership, Universe)
            .join(Universe, UniverseMembership.universe_id == Universe.id)
            .where(UniverseMembership.instrument_id == inst.id)
        )
    ).all()

    return {
        "id": inst.id,
        "symbol": inst.symbol,
        "name": inst.name,
        "asset_class": inst.asset_class,
        "listing_status": inst.listing_status,
        "currency": inst.currency,
        "market_cap": float(inst.market_cap) if inst.market_cap else None,
        "avg_dollar_volume_30d": float(inst.avg_dollar_volume_30d)
        if inst.avg_dollar_volume_30d else None,
        "identifiers": [
            {"scheme": i.scheme, "value": i.value, "primary": i.is_primary}
            for i in inst.identifiers
        ],
        "eligibility": {
            "known": True,
            "research_eligible": len(reasons) == 0,
            "reasons": reasons,
        },
        "universes": [
            {"name": u.name, "tier": u.tier, "status": m.status, "reason": m.reason}
            for m, u in memberships
        ],
        "approved_for_trading": any(
            u.tier == "approved" and m.status == "active" for m, u in memberships
        ),
    }


async def search_instruments(
    db: AsyncSession,
    q: str | None = None,
    asset_class: str | None = None,
    include_inactive: bool = False,
    eligible_only: bool = False,
    tenant_id: str = "default",
    limit: int = 50,
) -> list[dict]:
    stmt = select(Instrument)
    if q:
        like = f"%{q.upper()}%"
        stmt = stmt.where(
            or_(Instrument.symbol.ilike(like), Instrument.name.ilike(like))
        )
    if asset_class:
        stmt = stmt.where(Instrument.asset_class == asset_class)
    if not include_inactive:
        stmt = stmt.where(Instrument.listing_status == "active")
    stmt = stmt.order_by(Instrument.symbol).limit(limit)

    rules = await rules_for(db, tenant_id)
    rows = (await db.execute(stmt)).scalars().all()

    out = []
    for inst in rows:
        reasons = eligibility_reasons(inst, rules)
        if eligible_only and reasons:
            continue
        out.append(
            {
                "id": inst.id,
                "symbol": inst.symbol,
                "name": inst.name,
                "asset_class": inst.asset_class,
                "listing_status": inst.listing_status,
                "research_eligible": len(reasons) == 0,
                "reasons": reasons,
                "market_cap": float(inst.market_cap) if inst.market_cap else None,
            }
        )
    return out


async def set_membership(
    db: AsyncSession,
    *,
    universe_name: str,
    instrument: Instrument,
    status: str,
    reason: str | None = None,
    tenant_id: str = "default",
) -> UniverseMembership:
    universe = await universe_by_name(db, universe_name, tenant_id)
    if universe is None:
        raise ValueError(f"universe '{universe_name}' not found")
    m = (
        await db.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe_id == universe.id,
                UniverseMembership.instrument_id == instrument.id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        m = UniverseMembership(
            universe_id=universe.id, instrument_id=instrument.id
        )
        db.add(m)
    m.status = status
    m.reason = reason
    await db.flush()
    return m


async def watchlists(db: AsyncSession, tenant_id: str = "default") -> list[dict]:
    rows = (
        await db.execute(
            select(Watchlist).where(Watchlist.tenant_id == tenant_id)
        )
    ).scalars().all()
    out = []
    for w in rows:
        items = (
            await db.execute(
                select(WatchlistItem, Instrument)
                .join(Instrument, WatchlistItem.instrument_id == Instrument.id)
                .where(WatchlistItem.watchlist_id == w.id)
            )
        ).all()
        out.append(
            {
                "id": w.id,
                "name": w.name,
                "items": [
                    {
                        "symbol": i.symbol,
                        "name": i.name,
                        "note": it.note,
                        "reason_code": it.reason_code,
                    }
                    for it, i in items
                ],
            }
        )
    return out


async def universe_stats(db: AsyncSession, tenant_id: str = "default") -> dict:
    """Counts per tier for the hierarchy header."""
    tiers = {}
    for name in ("global", "eligible", "approved"):
        u = await universe_by_name(db, name, tenant_id)
        if u is None:
            tiers[name] = 0
            continue
        n = (
            await db.execute(
                select(func.count())
                .select_from(UniverseMembership)
                .where(
                    UniverseMembership.universe_id == u.id,
                    UniverseMembership.status == "active",
                )
            )
        ).scalar()
        tiers[name] = n
    total = (
        await db.execute(select(func.count()).select_from(Instrument))
    ).scalar()
    return {**tiers, "securities": total}
