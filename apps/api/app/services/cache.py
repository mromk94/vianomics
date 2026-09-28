"""Tiny in-process TTL cache for expensive read endpoints.
Bars are daily — a few minutes of staleness is safe; any mutation
endpoint can call invalidate()."""

import time
from typing import Any

_store: dict[str, tuple[float, Any]] = {}


def get(key: str, ttl_s: float) -> Any | None:
    ent = _store.get(key)
    if ent and time.monotonic() - ent[0] < ttl_s:
        return ent[1]
    return None


def put(key: str, value: Any) -> None:
    _store[key] = (time.monotonic(), value)


def invalidate(prefix: str | None = None) -> None:
    if prefix is None:
        _store.clear()
    else:
        for k in [k for k in _store if k.startswith(prefix)]:
            _store.pop(k)
