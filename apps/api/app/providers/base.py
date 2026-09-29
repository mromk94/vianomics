"""Common provider interface (D1.3).

Contract:
- fetch_* returns RAW provider payloads (dict/list) — the ingestion layer
  validates → normalizes → persists; adapters never write to the DB.
- capabilities() declares what the provider can serve so callers never
  assume coverage or redistribution rights.
- credentials come from Settings/secret refs only — never arguments from
  the frontend.
"""

from typing import Any, Protocol

import httpx


class ProviderError(Exception):
    """Retryable upstream failure (network, 5xx, throttle)."""


class ProviderConfigError(Exception):
    """Missing/invalid configuration — do not retry."""


class ProviderAdapter(Protocol):
    key: str  # edgar|fred|tiingo|paper_broker…
    kind: str  # fundamentals|market|macro|news|broker

    def capabilities(self) -> dict[str, Any]: ...

    async def healthcheck(self) -> bool: ...


class HttpAdapter:
    """Shared HTTP plumbing: timeout, UA, throttling hook."""

    key: str = ""
    kind: str = ""
    base_url: str = ""
    # requests per second budget (conservative defaults)
    rps: float = 1.0

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._sem = None  # lazy asyncio.Semaphore if needed

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        client = self._client or httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": "VAIIP/0.1 (internal research)"}
        )
        close = self._client is None
        try:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500:
                raise ProviderError(f"{self.key}: HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise ProviderError(
                    f"{self.key}: HTTP {resp.status_code} {resp.text[:200]}"
                )
            return resp
        except httpx.HTTPError as e:
            raise ProviderError(
                f"{self.key}: {type(e).__name__} {e or ''}".strip()
            ) from e
        finally:
            if close:
                await client.aclose()
