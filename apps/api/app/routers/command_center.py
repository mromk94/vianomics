from datetime import UTC, datetime

from fastapi import APIRouter

from app.config import get_settings
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


async def _system_health() -> list[ProviderHealth]:
    """Real health of the platform's own infrastructure — never demo data.

    External market-data providers are reported as `unconfigured` until
    their adapters land in M1.
    """
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
        ProviderHealth(name="SEC EDGAR", status="unconfigured"),
        ProviderHealth(name="FRED", status="unconfigured"),
        ProviderHealth(name="Market Data", status="unconfigured"),
        ProviderHealth(name="IBKR", status="unconfigured"),
    ]
    return providers


@router.get("/command-center", response_model=CommandCenterResponse)
async def command_center() -> CommandCenterResponse:
    settings = get_settings()
    providers = await _system_health()

    if settings.demo_fixtures:
        return CommandCenterResponse(
            generated_at=datetime.now(UTC).isoformat(),
            portfolio=demo.DEMO_PORTFOLIO,
            split=demo.DEMO_SPLIT,
            regime=demo.DEMO_REGIME,
            risk=demo.DEMO_RISK,
            watchlist=demo.DEMO_WATCHLIST,
            approvals=demo.DEMO_APPROVALS,
            alerts=demo.DEMO_ALERTS,
            decisions=demo.DEMO_DECISIONS,
            providers=providers,
            demo_sections=[
                "portfolio", "split", "regime",
                "risk", "watchlist", "approvals", "alerts", "decisions",
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
