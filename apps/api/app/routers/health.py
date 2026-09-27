import asyncio
import time

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings

router = APIRouter()

_STARTED = time.monotonic()


async def _check_postgres() -> dict:
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(get_settings().database_url), timeout=2.0
        )
        await conn.fetchval("SELECT 1")
        await conn.close()
        return {"status": "up"}
    except Exception as exc:  # noqa: BLE001 - surfaced as health detail, never secrets
        return {"status": "down", "error": type(exc).__name__}


async def _check_redis() -> dict:
    try:
        client = aioredis.from_url(get_settings().redis_url, socket_timeout=2.0)
        await client.ping()
        await client.aclose()
        return {"status": "up"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "down", "error": type(exc).__name__}


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness: process is up. Never fails."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.version,
        "uptime_seconds": round(time.monotonic() - _STARTED, 1),
        "execution_enabled": settings.execution_enabled,
    }


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness: dependencies reachable. 503 if any check fails."""
    checks = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(),
    }
    degraded = any(c["status"] != "up" for c in checks.values())
    return JSONResponse(
        status_code=503 if degraded else 200,
        content={"status": "degraded" if degraded else "ready", "checks": checks},
    )
