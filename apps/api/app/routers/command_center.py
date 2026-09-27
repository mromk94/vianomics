from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.models.ops import SyncStatus
from app.models.providers import DataProvider
from app.routers.health import _check_postgres, _check_redis
from app.schemas.command_center import (
    CommandCenterResponse,
    PortfolioSplit,
    PortfolioSummary,
    ProviderHealth,
    RegimeSnapshot,
    RiskUtilization,
)
from app.services import demo_fixtures as demo

router = APIRouter()


async def _provider_health(db: AsyncSession) -> list[ProviderHealth]:
    """Registered providers + last successful sync — real DB state."""
    providers = (await db.execute(select(DataProvider))).scalars().all()
    latest = dict(
        (await db.execute(
            select(
                SyncStatus.provider_id,
                func.max(SyncStatus.last_success_at),
            ).group_by(SyncStatus.provider_id)
        )).all()
    )
    return [
        ProviderHealth(
            name=p.name,
            status=(
                "up" if p.status == "connected"
                else "down" if p.status == "down"
                else "degraded" if p.status == "degraded"
                else "unconfigured"
            ),
            last_sync=(
                latest[p.id].isoformat() if latest.get(p.id) else None
            ),
        )
        for p in providers
    ]


async def _system_health(db: AsyncSession | None) -> list[ProviderHealth]:
    """Real health — infrastructure checks + provider registry rows.
    External providers only ever say 'up' after a real successful sync."""
    pg = await _check_postgres()
    rd = await _check_redis()
    now = datetime.now(UTC).isoformat()
    providers = [
        ProviderHealth(
            name="PostgreSQL",
            status="up" if pg["status"] == "up" else "down",
            last_sync=now if pg["status"] == "up" else None,
            detail=pg.get("error"),
        ),
        ProviderHealth(
            name="Redis",
            status="up" if rd["status"] == "up" else "down",
            last_sync=now if rd["status"] == "up" else None,
            detail=rd.get("error"),
        ),
    ]
    if db is not None:
        try:
            providers.extend(await _provider_health(db))
        except Exception:  # noqa: BLE001 — DB must be reachable for this anyway
            pass
    return providers


@router.get("/command-center", response_model=CommandCenterResponse)
async def command_center(db: AsyncSession = Depends(get_db)) -> CommandCenterResponse:
    settings = get_settings()
    providers = await _system_health(db)

    if settings.demo_fixtures:
        return CommandCenterResponse(
            generated_at=datetime.now(UTC).isoformat(),
            portfolio=demo.DEMO_PORTFOLIO,
            split=demo.DEMO_SPLIT,
            regime=demo.DEMO_REGIME,
            risk=demo.DEMO_RISK,
            cio=demo.DEMO_CIO,
            agents=demo.DEMO_AGENTS,
            sectors=demo.DEMO_SECTORS,
            calendar=demo.DEMO_CALENDAR,
            signals=demo.DEMO_SIGNALS,
            watchlist=demo.DEMO_WATCHLIST,
            approvals=demo.DEMO_APPROVALS,
            alerts=demo.DEMO_ALERTS,
            decisions=demo.DEMO_DECISIONS,
            providers=providers,
            demo_sections=[
                "portfolio", "split", "regime", "risk", "cio",
                "agents", "sectors", "calendar", "signals",
                "watchlist", "approvals", "alerts", "decisions",
            ],
        )

    # Real (currently unpopulated) state — honest empty sections.
    return CommandCenterResponse(
        generated_at=datetime.now(UTC).isoformat(),
        portfolio=PortfolioSummary(),
        split=PortfolioSplit(),
        regime=RegimeSnapshot(),
        risk=RiskUtilization(),
        providers=providers,
    )
