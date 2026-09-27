"""AuthN/AuthZ — server-side sessions, argon2 hashes, RBAC dependency."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.identity import AuditEvent, Session, User

_ph = PasswordHasher()
SESSION_TTL = timedelta(hours=12)


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerifyMismatchError:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(
    db: AsyncSession, user: User, request: Request | None = None
) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        Session(
            user_id=user.id,
            token_hash=_hash_token(token),
            expires_at=datetime.now(UTC) + SESSION_TTL,
            ip=request.client.host if request and request.client else None,
            user_agent=(
                request.headers.get("user-agent") if request else None
            ),
        )
    )
    return token


async def current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token_hash = _hash_token(auth[7:])
    row = (
        await db.execute(
            select(Session).where(Session.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if (
        row is None
        or row.revoked_at is not None
        or row.expires_at <= datetime.now(UTC)
    ):
        raise HTTPException(401, "invalid or expired session")
    user = (
        await db.execute(
            select(User)
            .where(User.id == row.user_id)
            .options(selectinload(User.roles))
        )
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(401, "user inactive")
    return user


def user_permissions(user: User) -> set[str]:
    return {p for r in user.roles for p in (r.permissions or [])}


def require(permission: str):
    """RBAC dependency: require('admin:*'), require('universe:write'), …
    'admin:*' grants everything."""

    async def dep(user: User = Depends(current_user)) -> User:
        perms = user_permissions(user)
        if "admin:*" in perms or permission in perms:
            return user
        # prefix match: 'universe:*' covers 'universe:write'
        domain = permission.split(":")[0]
        if f"{domain}:*" in perms:
            return user
        raise HTTPException(403, f"missing permission '{permission}'")

    return dep


async def audit(
    db: AsyncSession,
    *,
    action: str,
    actor: User | None = None,
    entity_type: str = "",
    entity_id: str | None = None,
    detail: dict | None = None,
    tenant_id: str = "default",
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail or {},
            tenant_id=tenant_id,
        )
    )
