from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.identity import Role, Session, User
from app.security import (
    audit,
    create_session,
    current_user,
    user_permissions,
    hash_password,
    require,
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


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password")
async def change_password(body: PasswordChange,
                          db: AsyncSession = Depends(get_db),
                          user: User = Depends(current_user)):
    if len(body.new_password) < 10:
        raise HTTPException(400, "min 10 chars")
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "current password wrong")
    user.password_hash = hash_password(body.new_password)
    await audit(db, action="user.password_change", actor=user)
    await db.commit()
    return {"ok": True}


class UserIn(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    role: str = "viewer"   # admin can grant admin


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db),
                     _admin: User = Depends(require("admin:*"))):
    rows = (await db.execute(select(User))).scalars().all()
    return [{"id": u.id, "email": u.email,
             "display_name": u.display_name,
             "roles": [r.name for r in u.roles]} for u in rows]


@router.post("/users", status_code=201)
async def create_user(body: UserIn,
                      db: AsyncSession = Depends(get_db),
                      admin: User = Depends(require("admin:*"))):
    if len(body.password) < 10:
        raise HTTPException(400, "min 10 chars")
    exists = (await db.execute(
        select(User).where(User.email == body.email))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "email already registered")
    role = (await db.execute(
        select(Role).where(Role.name == body.role))).scalar_one_or_none()
    u = User(email=body.email, display_name=body.display_name,
             password_hash=hash_password(body.password))
    if role:
        u.roles.append(role)
    db.add(u)
    await db.flush()
    await audit(db, action="user.create", actor=admin,
                entity_type="user", entity_id=u.id,
                detail={"email": u.email, "role": body.role})
    await db.commit()
    return {"id": u.id, "email": u.email}
