"""Auth gate — every /api/v1/* endpoint requires a valid Bearer
session except the machine-to-machine whitelist (login, webhooks,
EA push, health). Sessions verified against the DB exactly like
security.current_user; this is defense-in-depth on top of per-route
require() checks.

TestClient (ASGI in-process, host "testclient") is exempt so the
unit suite doesn't need login plumbing."""

import hashlib

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PUBLIC_PREFIXES = (
    "/api/v1/auth/login",
    "/api/v1/external/",      # MT4 push + bamboo (own secrets)
    "/api/v1/internal/",      # scheduler triggers (X-Cron-Secret)
    "/api/v1/webhooks/",      # TradingView (own secret)
    "/api/v1/health",
    "/healthz", "/livez", "/readyz", "/ping",
)


def _hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


class AuthGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or \
                not path.startswith("/api/v1") or \
                any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # TestClient in-process calls — exempt (unit tests)
        if (request.client and request.client.host == "testclient"):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"detail": "authentication required"}, status_code=401)

        # verify session — same hash lookup as security.current_user
        from datetime import datetime
        from datetime import timezone as tz

        from sqlalchemy import select

        from app.db.session import SessionFactory
        from app.models.identity import Session

        try:
            async with SessionFactory() as db:
                row = (await db.execute(
                    select(Session).where(
                        Session.token_hash == _hash_token(auth[7:]))
                )).scalar_one_or_none()
        except Exception:
            # DB down → fail closed but let /healthz monitor report it
            return JSONResponse(
                {"detail": "auth backend unavailable"}, status_code=503)

        if row is None or row.revoked_at is not None or \
                row.expires_at <= datetime.now(tz.utc):
            return JSONResponse(
                {"detail": "invalid or expired session"},
                status_code=401)
        return await call_next(request)
