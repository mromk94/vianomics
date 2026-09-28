from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import (
    assistant, auth, backtest, command_center, committee, dataops, engines,
    execution, feedback, health, mandate, market, monitoring, portfolio,
    research, risk, screener, technical, universe, valuation,
    webhooks,
)
from app.routers import settings as settings_router

settings = get_settings()

app = FastAPI(title=settings.app_name, version=settings.version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["system"])
app.include_router(auth.router, prefix="/api/v1")
app.include_router(command_center.router, prefix="/api/v1", tags=["command-center"])
app.include_router(mandate.router, prefix="/api/v1")
app.include_router(universe.router, prefix="/api/v1")
app.include_router(screener.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(valuation.router, prefix="/api/v1")
app.include_router(engines.router, prefix="/api/v1")
app.include_router(technical.router, prefix="/api/v1")
app.include_router(risk.router, prefix="/api/v1")
app.include_router(committee.router, prefix="/api/v1")
app.include_router(monitoring.router, prefix="/api/v1")
app.include_router(execution.router, prefix="/api/v1")
app.include_router(backtest.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")
app.include_router(portfolio.router, prefix="/api/v1")
app.include_router(dataops.router, prefix="/api/v1")
app.include_router(settings_router.router, prefix="/api/v1")
app.include_router(assistant.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")


@app.on_event("startup")
async def _load_secrets():
    """DB-stored API keys → process env so providers find them after
    restart."""
    try:
        from app.db.session import SessionFactory
        from app.services.secrets import load_all_into_env
        async with SessionFactory() as db:
            await load_all_into_env(db)
    except Exception:
        pass
