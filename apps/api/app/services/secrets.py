"""Secret resolution: DB store first, env second. Values never leave
the API — only 'set' status and masked tails."""

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import SecretStore


async def get_secret(db: AsyncSession, key: str) -> str | None:
    r = (await db.execute(
        select(SecretStore).where(SecretStore.key == key))
    ).scalar_one_or_none()
    return r.value if r else os.environ.get(key)


async def set_secret(db: AsyncSession, key: str, value: str) -> None:
    r = (await db.execute(
        select(SecretStore).where(SecretStore.key == key))
    ).scalar_one_or_none()
    if r is None:
        db.add(SecretStore(key=key, value=value))
    else:
        r.value = value
    os.environ[key] = value          # live for this process too


async def load_all_into_env(db: AsyncSession) -> int:
    rows = (await db.execute(select(SecretStore))).scalars().all()
    for r in rows:
        os.environ.setdefault(r.key, r.value)
    return len(rows)
