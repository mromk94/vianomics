from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.identity import Session, User
from app.security import (
    audit,
    create_session,
    current_user,
    user_permissions,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/login")
async def login(
    body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    user = (
        await db.execute(
            select(User)
            .where(User.email == body.email.lower())
            .options(selectinload(User.roles))
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid credentials")
    if not user.is_active:
        raise HTTPException(403, "account disabled")
    token = await create_session(db, user, request)
    await audit(
        db, action="auth.login", actor=user, entity_type="user", entity_id=user.id
    )
    await db.commit()
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.display_name,
            "permissions": sorted(user_permissions(user)),
        },
    }


@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    auth = request.headers["authorization"]
    import hashlib

    token_hash = hashlib.sha256(auth[7:].encode()).hexdigest()
    row = (
        await db.execute(select(Session).where(Session.token_hash == token_hash))
    ).scalar_one_or_none()
    if row:
        row.revoked_at = datetime.now(UTC)
    await audit(db, action="auth.logout", actor=user, entity_type="user", entity_id=user.id)
    await db.commit()
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.display_name,
        "permissions": sorted(user_permissions(user)),
    }
