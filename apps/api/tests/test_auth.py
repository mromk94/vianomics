import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.identity import Role, User
from app.security import (
    hash_password,
    user_permissions,
    verify_password,
)


async def _user(db, perms=None) -> User:
    role = Role(tenant_id="default", name="t", permissions=perms or [])
    db.add(role)
    user = User(
        email="t@t.dev",
        display_name="T",
        password_hash=hash_password("secret-pw"),
    )
    user.roles.append(role)
    db.add(user)
    await db.commit()
    return user


def test_password_hash_roundtrip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert h != "correct horse"  # never stored plaintext


async def test_permissions_aggregate_from_roles(db):
    user = await _user(db, ["universe:read", "approve:trade"])
    assert {"universe:read", "approve:trade"} <= user_permissions(user)


async def test_admin_wildcard_covers_everything(db):
    from fastapi import Depends
    from app.security import require

    user = await _user(db, ["admin:*"])
    dep = require("anything:at_all")
    assert await dep(user=user) is user


async def test_missing_permission_raises_403(db):
    from fastapi import HTTPException
    from app.security import require

    user = await _user(db, ["data:read"])
    dep = require("approve:trade")
    with pytest.raises(HTTPException) as e:
        await dep(user=user)
    assert e.value.status_code == 403
