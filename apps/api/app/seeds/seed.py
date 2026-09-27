"""Seed script — run: `python -m app.seeds.seed`

Every seeded row is real reference data (roles, exchanges, sectors,
the 24-ticker approved universe from the framework PDF, mandate
defaults, provider registry) — NOT fabricated market data. The admin
password comes from ADMIN_PASSWORD env (default documented below for
local dev only).
"""

import asyncio
import os

from sqlalchemy import select

from app.db.session import SessionFactory
from app.ingestion.upsert import get_or_create
from app.models.identity import Role, User
from app.models.instruments import (
    Exchange,
    Instrument,
    InstrumentIdentifier,
    Sector,
)
from app.models.mandate import Mandate
from app.models.providers import DataProvider
from app.models.universe import Universe, UniverseMembership
from app.security import hash_password

SEED_TICKERS = [
    "AMD", "AMZN", "ANET", "ASML", "AVGO", "BE", "CEG", "CRWD", "DDOG",
    "ECHO", "EOSE", "GOOGL", "MDB", "META", "MRVL", "MSFT", "MU", "NVDA",
    "ORCL", "PLTR", "TSM", "TSLA", "VRT", "ZS",
]

# GICS-style sector assignment for the seed universe
TICKER_SECTOR = {
    "NVDA": "Information Technology", "MSFT": "Information Technology",
    "AVGO": "Information Technology", "ASML": "Information Technology",
    "TSM": "Information Technology", "MU": "Information Technology",
    "AMD": "Information Technology", "MRVL": "Information Technology",
    "ANET": "Information Technology", "VRT": "Information Technology",
    "ORCL": "Information Technology", "PLTR": "Information Technology",
    "MDB": "Information Technology", "CRWD": "Information Technology",
    "DDOG": "Information Technology", "ZS": "Information Technology",
    "GOOGL": "Communication Services", "META": "Communication Services",
    "ECHO": "Communication Services",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "BE": "Industrials", "EOSE": "Industrials",
    "CEG": "Utilities",
}

GICS_SECTORS = [
    "Information Technology", "Health Care", "Financials",
    "Consumer Discretionary", "Communication Services", "Industrials",
    "Consumer Staples", "Energy", "Utilities", "Real Estate", "Materials",
]

PROVIDERS = [
    ("edgar", "SEC EDGAR", "fundamentals", {"requires_credentials": False}),
    ("fred", "FRED", "macro", {"requires_credentials": True}),
    ("tiingo", "Tiingo", "market", {"requires_credentials": True}),
    ("yahoo", "Yahoo Finance (delayed EOD)", "market",
     {"requires_credentials": False, "delayed": True}),
    ("paper_broker", "Paper Broker (simulated)", "broker", {"simulated": True}),
    ("ibkr", "Interactive Brokers", "broker", {"requires_gateway": True}),
]


async def seed() -> None:
    async with SessionFactory() as db:
        # ── Roles ──
        admin_role, _ = await get_or_create(
            db, Role, {"tenant_id": "default", "name": "admin"},
            {"permissions": ["admin:*"]},
        )
        trader_role, _ = await get_or_create(
            db, Role, {"tenant_id": "default", "name": "trader"},
            {"permissions": [
                "universe:read", "universe:write", "mandate:read",
                "research:*", "approve:trade", "data:read",
            ]},
        )
        await get_or_create(
            db, Role, {"tenant_id": "default", "name": "viewer"},
            {"permissions": ["data:read", "universe:read", "mandate:read"]},
        )

        # ── Admin user (dev password — override via ADMIN_PASSWORD) ──
        pw = os.environ.get("ADMIN_PASSWORD", "vianomics-dev")
        admin = (
            await db.execute(select(User).where(User.email == "admin@vianomics.io"))
        ).scalar_one_or_none()
        if admin is None:
            admin = User(
                email="admin@vianomics.io",
                display_name="VAIIP Admin",
                password_hash=hash_password(pw),
            )
            admin.roles.append(admin_role)
            db.add(admin)

        # ── Exchanges ──
        nasdaq, _ = await get_or_create(
            db, Exchange, {"code": "NASDAQ"},
            {"name": "Nasdaq Stock Market"},
        )
        nyse, _ = await get_or_create(
            db, Exchange, {"code": "NYSE"},
            {"name": "New York Stock Exchange"},
        )
        exch = {"NASDAQ": nasdaq, "NYSE": nyse}
        # NYSE-listed among the seed tickers
        nyse_tickers = {"ACN", "BE", "IBM", "ORCL", "TSM", "VRT", "ECHO"}

        # ── Sectors ──
        sectors = {}
        for name in GICS_SECTORS:
            s, _ = await get_or_create(db, Sector, {"name": name})
            sectors[name] = s

        # ── Universe hierarchy: global → eligible → approved ──
        global_u, _ = await get_or_create(
            db, Universe,
            {"tenant_id": "default", "name": "global"},
            {"tier": "global", "description": "All known US-listed securities"},
        )
        eligible_u, _ = await get_or_create(
            db, Universe,
            {"tenant_id": "default", "name": "eligible"},
            {"tier": "eligible", "description": "Passes liquidity/mcap/asset-class gates",
             "rules": {
                 "asset_classes": ["equity"],
                 "listing_status": ["active"],
                 "min_market_cap": 300_000_000,
                 "min_avg_dollar_volume": 5_000_000,
             }},
        )
        approved, _ = await get_or_create(
            db, Universe,
            {"tenant_id": "default", "name": "approved"},
            {"tier": "approved", "description": "VAIIP approved universe (24 seed tickers)"},
        )

        for sym in SEED_TICKERS:
            exchange = exch["NYSE"] if sym in nyse_tickers else exch["NASDAQ"]
            inst, created = await get_or_create(
                db, Instrument,
                {"exchange_id": exchange.id, "symbol": sym},
                {"name": sym, "asset_class": "equity"},
            )
            sec_name = TICKER_SECTOR.get(sym)
            if sec_name and inst.sector_id is None:
                inst.sector_id = sectors[sec_name].id
            await get_or_create(
                db, InstrumentIdentifier,
                {"scheme": "ticker", "value": sym},
                {"instrument_id": inst.id, "is_primary": True},
            )
            for u in (global_u, eligible_u, approved):
                await get_or_create(
                    db, UniverseMembership,
                    {"universe_id": u.id, "instrument_id": inst.id},
                )

        # ── Mandate v1 (framework Part 1 + Part 15 limits, C9 ported) ──
        existing = (
            await db.execute(
                select(Mandate).where(
                    Mandate.tenant_id == "default", Mandate.version == 1
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                Mandate(
                    version=1,
                    is_active=True,
                    change_note="Initial mandate — framework Part 1 defaults",
                    extra={
                        "philosophy": [
                            "fundamentals-first", "high-quality compounders",
                            "margin of safety", "technical confirmation",
                            "mean reversion", "trend following",
                            "strict risk management",
                        ],
                        "risk_limits": {
                            "max_capital_per_trade_pct": 20,
                            "risk_per_trade_pct": [1, 2],
                            "daily_loss_pct": 3,
                            "weekly_loss_pct": 6,
                            "position_allocation_pct": [10, 15],
                            "position_drawdown_pct": 20,
                            "correlated_exposure_pct": 30,
                            "sector_limit_pct": 25,
                            "single_stock_pct": 10,
                            "min_sectors": 5,
                        },
                    },
                )
            )

        # ── Screening policy v1 (Green Zone thresholds) ──
        from app.models.screening import ScreeningPolicy
        from app.services.green_zone import POLICY_DEFAULTS
        sp = (
            await db.execute(
                select(ScreeningPolicy).where(
                    ScreeningPolicy.tenant_id == "default",
                    ScreeningPolicy.version == 1,
                )
            )
        ).scalar_one_or_none()
        if sp is None:
            db.add(
                ScreeningPolicy(
                    version=1, is_active=True,
                    change_note="Default Green Zone thresholds (Part 5)",
                    params=POLICY_DEFAULTS,
                )
            )

        # ── Provider registry (status 'unconfigured' until a real
        # integration test proves connectivity — per spec, never claimed) ──
        for key, name, kind, caps in PROVIDERS:
            await get_or_create(
                db, DataProvider, {"key": key},
                {"name": name, "kind": kind, "capabilities": caps,
                 "status": "unconfigured"},
            )

        await db.commit()
        print("seed complete: roles=3 admin=admin@vianomics.io "
              f"instruments={len(SEED_TICKERS)} sectors={len(GICS_SECTORS)} "
              f"providers={len(PROVIDERS)} mandate=v1")


if __name__ == "__main__":
    asyncio.run(seed())
