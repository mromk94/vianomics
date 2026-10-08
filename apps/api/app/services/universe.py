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

from app.models.fundamentals import FundamentalObservation
from app.models.instruments import (
    Instrument,
    InstrumentIdentifier,
    Sector,
)
from app.models.market import OhlcvBar
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
        # canonical dash form — Alpaca 'BRK.B' stores as 'BRK-B'
        # (Yahoo/SEC convention); the Alpaca adapter translates back
        # to dot form at call time
        sym = ((a.get("symbol") or "").strip().upper()
               .replace(".", "-"))
        if not sym or len(sym) > 12 or not sym.replace("-", "").isalnum():
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


async def sync_core_universe(
    db: AsyncSession, tenant_id: str = "default",
    hydrate_batch: int = 40,
) -> dict[str, Any]:
    """The desk's working universe — S&P 500 (Wikipedia: real names,
    GICS sectors, SEC CIKs) ∪ Yahoo most-actives top-250 → 'global'
    pool + 'core' universe membership.

    Unlike the Alpaca 10k-name discovery dump, core is the tracked
    book: its members get nightly bars and the fundamentals gapfill
    sweep automatically (the tracked filter admits any non-global
    universe membership). `hydrate_batch` pulls bars+facts for the
    first N uncovered members per call — repeated calls converge to
    full coverage; /backfill does the one-shot mass hydration.
    """
    from app.providers.constituents import (
        sp500_constituents, yahoo_most_actives)

    sources: dict[str, dict] = {}
    errors: list[str] = []
    try:
        for c in await sp500_constituents():
            sources[c["symbol"]] = c
    except Exception as e:
        errors.append(f"sp500: {e}")
    try:
        for c in await yahoo_most_actives(250):
            # S&P rows carry richer metadata — actives fill in the rest
            if c["symbol"] not in sources:
                sources[c["symbol"]] = c
    except Exception as e:
        errors.append(f"most_actives: {e}")
    if not sources:
        return {"status": "failed",
                "error": "; ".join(errors) or "no constituent data"}

    global_u = await universe_by_name(db, "global", tenant_id)
    if global_u is None:
        global_u = Universe(name="global", tier="global",
                            description="All known US-listed securities",
                            tenant_id=tenant_id)
        db.add(global_u)
        await db.flush()
    core_u = await universe_by_name(db, "core", tenant_id)
    if core_u is None:
        core_u = Universe(
            name="core", tier="core",
            description="Working book — S&P 500 ∪ most-actives top-250",
            tenant_id=tenant_id)
        db.add(core_u)
        await db.flush()

    existing = {i.symbol: i for i in
                (await db.execute(select(Instrument))).scalars().all()}
    have_core = {
        m.instrument_id for m in (await db.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe_id == core_u.id))
        ).scalars().all()}
    have_global = {
        m.instrument_id for m in (await db.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe_id == global_u.id))
        ).scalars().all()}
    sectors = {s.name: s for s in
               (await db.execute(select(Sector))).scalars().all()}
    have_cik_iids: set[str] = set()
    have_cik_vals: set[str] = set()
    for r in (await db.execute(
            select(InstrumentIdentifier.instrument_id,
                   InstrumentIdentifier.value)
            .where(InstrumentIdentifier.scheme == "cik"))).all():
        have_cik_iids.add(r[0])
        have_cik_vals.add(r[1])

    created = membered = 0
    new_ids: list[str] = []
    for sym, c in sources.items():
        inst = existing.get(sym)
        if inst is None:
            inst = Instrument(
                symbol=sym, name=c["name"] or sym,
                asset_class=c.get("asset_class") or "equity",
                currency="USD", status="watchlist")
            db.add(inst)
            await db.flush()
            existing[sym] = inst
            created += 1
        else:
            # backfill real name — pool rows from other syncs may only
            # carry the ticker as a placeholder
            if c.get("name") and (not inst.name or inst.name == sym):
                inst.name = c["name"]
        # real GICS sector from the constituents table (don't clobber
        # a human assignment — only fill when unset)
        sec_name = c.get("sector")
        if sec_name and inst.sector_id is None:
            sec = sectors.get(sec_name)
            if sec is None:
                sec = Sector(name=sec_name)
                db.add(sec)
                await db.flush()
                sectors[sec_name] = sec
            inst.sector_id = sec.id
        # CIK → InstrumentIdentifier: EDGAR ingest reads this first,
        # skipping the ticker→CIK map roundtrip. (scheme, value) is
        # unique — share-class siblings (GOOG/GOOGL, BRK-A/BRK-B) file
        # ONE CIK, so only the first claims it; the rest self-heal via
        # EDGAR's ticker map at ingest time
        if (c.get("cik") and inst.id not in have_cik_iids
                and str(c["cik"]) not in have_cik_vals):
            db.add(InstrumentIdentifier(
                instrument_id=inst.id, scheme="cik",
                value=str(c["cik"])))
            have_cik_iids.add(inst.id)
            have_cik_vals.add(str(c["cik"]))
        if inst.id not in have_global:
            db.add(UniverseMembership(
                universe_id=global_u.id, instrument_id=inst.id,
                status="active", reason="core constituent sync"))
            have_global.add(inst.id)
        if inst.id not in have_core:
            db.add(UniverseMembership(
                universe_id=core_u.id, instrument_id=inst.id,
                status="active",
                reason=f"{c['index']} constituent"))
            have_core.add(inst.id)
            membered += 1
            if await coverage_missing(db, inst):
                new_ids.append(inst.id)
    await db.commit()

    # bounded hydration — first-call bootstrap pulls bars+facts for
    # uncovered members; further calls/nightly gapfill finish the job.
    # Ids + db.get per iteration: a failed leg's rollback expires ORM
    # state; re-fetching keeps the loop alive instead of tripping
    # MissingGreenlet on the next expired attribute.
    hydrated = 0
    for iid in new_ids[:hydrate_batch]:
        try:
            live = await db.get(Instrument, iid)
            await hydrate_instrument(db, live)
            hydrated += 1
            await db.commit()
        except Exception:
            await db.rollback()

    return {"status": "success", "constituents": len(sources),
            "instruments_created": created,
            "core_members_added": membered,
            "hydrated": hydrated,
            "uncovered_backlog": max(0, len(new_ids) - hydrated),
            "errors": errors or None}


async def coverage_missing(db: AsyncSession, inst: Instrument) -> bool:
    """True when the instrument has no daily bars or no fundamental
    observations — i.e. it's tracked but would screen 'no data'."""
    bars = (await db.execute(
        select(func.count(OhlcvBar.id)).where(
            OhlcvBar.instrument_id == inst.id,
            OhlcvBar.timeframe == "1d"))).scalar() or 0
    if bars == 0:
        return True
    obs = (await db.execute(
        select(func.count(FundamentalObservation.id)).where(
            FundamentalObservation.instrument_id == inst.id))).scalar() or 0
    return obs == 0


async def hydrate_instrument(db: AsyncSession,
                             inst: Instrument) -> dict[str, Any]:
    """Pull bars + fundamentals + market stats for a tracked
    instrument — the shared hydration path used by first-sight adds
    (ensure_instrument), lazy screener hydration, and the nightly
    gapfill sweep. Per-source failures are isolated; the return dict
    reports exactly which legs landed."""
    from app.ingestion import jobs as ing
    out: dict[str, Any] = {"bars": False, "facts": False,
                           "market_stats": False}
    sym = inst.symbol
    # bars: Alpaca (paid tape) when configured, Yahoo fallback — same
    # preference order as the nightly refresh. 10y of daily bars
    # covers technicals/regime history; the deeper archive pull stays
    # with /backfill, not first-sight hydration.
    #
    # Each leg runs inside a SAVEPOINT (begin_nested) rather than its
    # own commit/rollback: a full rollback expires EVERY ORM object in
    # the session, and callers iterating member lists hit
    # MissingGreenlet on the next attribute access. Savepoint rollback
    # only discards the leg's own writes; the caller's commit persists
    # whatever landed.
    try:
        from app.providers.alpaca import AlpacaAdapter
        from app.providers.base import ProviderConfigError
        async with db.begin_nested():
            try:
                brun = await ing.ingest_alpaca_bars(
                    db, AlpacaAdapter(), sym, start="2015-01-01")
                if brun.status != "success":
                    raise RuntimeError(brun.error or "alpaca bars")
            except ProviderConfigError:
                raise RuntimeError("alpaca unconfigured")
        out["bars"] = True
    except Exception:
        try:
            from app.providers.yahoo import YahooAdapter
            async with db.begin_nested():
                brun = await ing.ingest_stooq_bars(
                    db, YahooAdapter(), sym, start="2015-01-01")
            out["bars"] = brun.status == "success"
        except Exception:
            pass
    try:
        from app.providers.edgar import EdgarAdapter
        async with db.begin_nested():
            frun = await ing.ingest_edgar_facts(db, EdgarAdapter(), sym)
        out["facts"] = frun.status == "success" and frun.records_ok > 0
    except Exception:
        pass
    if not out["facts"]:
        # no SEC XBRL for this issuer — Yahoo annual financials bridge
        # the gap (source='yahoo' keeps provenance distinct)
        try:
            from app.providers.yahoo import YahooAdapter
            async with db.begin_nested():
                yrun = await ing.ingest_yahoo_fundamentals(
                    db, YahooAdapter(), sym)
            out["facts"] = yrun.records_ok > 0
        except Exception:
            pass
    try:
        from app.services.market_context import refresh_market_stats
        async with db.begin_nested():
            out["market_stats"] = await refresh_market_stats(db, inst)
            reasons = eligibility_reasons(inst, await rules_for(db))
            if not reasons:
                await set_membership(
                    db, universe_name="eligible", instrument=inst,
                    status="active", reason="eligibility rules pass")
    except Exception:
        pass
    return out


async def ensure_instrument(db: AsyncSession, symbol: str,
                            hydrate: bool = True) -> dict[str, Any]:
    """On-demand security-master add — validate the ticker against a
    real quote source, create the instrument, join global+eligible,
    then pull bars + EDGAR facts so screens/technical work on first
    sight. Returns {"instrument", "created"} or {"error"}."""
    import httpx
    sym = symbol.upper().strip()
    inst = (await db.execute(
        select(Instrument).where(Instrument.symbol == sym))
    ).scalar_one_or_none()
    if inst is not None:
        # already known — but an Alpaca-pool member may carry zero
        # bars/facts; hydrate on first demand so it can actually screen
        facts_ok = None
        if hydrate and await coverage_missing(db, inst):
            res = await hydrate_instrument(db, inst)
            facts_ok = res["facts"]
            await db.commit()
        return {"instrument": inst, "created": False,
                "fundamentals": facts_ok}

    from app.providers.yahoo import YahooAdapter
    try:
        async with httpx.AsyncClient(
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"range": "5d", "interval": "1d"})
        meta = r.json()["chart"]["result"][0]["meta"]
        name = meta.get("shortName") or meta.get("longName") or sym
    except Exception:
        return {"error": f"{sym}: not a real ticker per yahoo"}

    inst = Instrument(symbol=sym, name=name, asset_class="equity",
                      currency=meta.get("currency", "USD"),
                      exchange_id=None)
    db.add(inst)
    await db.flush()
    # 'global' is unconditional — known to the security master.
    # 'eligible' is EARNED (doc Step 2): it requires the rules to
    # actually pass, which needs the hydrated market stats below.
    try:
        await set_membership(
            db, universe_name="global", instrument=inst,
            status="active", reason="added on-demand")
    except ValueError:
        pass
    await db.commit()

    facts_ok: bool | None = None
    if hydrate:
        # bars + fundamentals (EDGAR → Yahoo fallback) + market stats;
        # eligibility is earned by the rules gate inside
        # hydrate_instrument — never granted by default
        res = await hydrate_instrument(db, inst)
        facts_ok = res["facts"]
        # hydrate's legs are savepoints — the caller-level commit is
        # what actually persists the bars/facts that landed
        await db.commit()
    return {"instrument": inst, "created": True, "name": name,
            "fundamentals": facts_ok}


async def fundamentals_gapfill(db: AsyncSession, limit: int = 40,
                               stale_days: int = 400
                               ) -> dict[str, Any]:
    """Coverage sweep — tracked instruments (bar-covered or tiered)
    with zero facts or facts older than `stale_days` get re-ingested:
    EDGAR first, Yahoo fallback for issuers SEC doesn't carry. Bounded
    per run — nightly cadence converges coverage without melting the
    job. The 10k-name global pool hydrates lazily on screen instead."""
    from datetime import timedelta
    from app.db.base import utcnow

    covered = select(OhlcvBar.instrument_id).distinct()
    tiered = (select(UniverseMembership.instrument_id)
              .join(Universe,
                    UniverseMembership.universe_id == Universe.id)
              .where(Universe.name != "global",
                     UniverseMembership.status == "active"))
    tracked = (await db.execute(
        select(Instrument).where(
            or_(Instrument.id.in_(covered),
                Instrument.id.in_(tiered)),
            Instrument.asset_class == "equity"))).scalars().all()
    ids = [i.id for i in tracked]
    if not ids:
        return {"status": "success", "checked": 0, "hydrated": 0}

    latest_pub = dict((await db.execute(
        select(FundamentalObservation.instrument_id,
               func.max(FundamentalObservation.published_at))
        .where(FundamentalObservation.instrument_id.in_(ids))
        .group_by(FundamentalObservation.instrument_id))).all())
    cutoff = utcnow() - timedelta(days=stale_days)
    needs = [(i.id, i.market_cap or 0) for i in tracked
             if latest_pub.get(i.id) is None
             or latest_pub[i.id] < cutoff]
    needs.sort(key=lambda t: t[1], reverse=True)

    hydrated = failed = 0
    for iid, _mc in needs[:limit]:
        try:
            # fresh object per iteration — a rollback expires ORM
            # state wholesale; db.get re-loads in the async context
            # instead of tripping MissingGreenlet on lazy refresh
            live = await db.get(Instrument, iid)
            res = await hydrate_instrument(db, live)
            if res.get("facts") or res.get("bars"):
                hydrated += 1
            await db.commit()
        except Exception:
            await db.rollback()
            failed += 1
    return {"status": "success", "checked": len(needs),
            "hydrated": hydrated, "failed": failed,
            "backlog": max(0, len(needs) - limit)}


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
    universe: str | None = None,
    tenant_id: str = "default",
    limit: int = 50,
) -> list[dict]:
    stmt = select(Instrument)
    if universe:
        stmt = (
            stmt.join(
                UniverseMembership,
                UniverseMembership.instrument_id == Instrument.id)
            .join(Universe,
                  UniverseMembership.universe_id == Universe.id)
            .where(Universe.name == universe,
                   Universe.tenant_id == tenant_id,
                   UniverseMembership.status == "active")
        )
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
    for name in ("global", "core", "eligible", "approved"):
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
