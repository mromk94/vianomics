"""FRED (Federal Reserve Economic Data) adapter.

Requires FRED_API_KEY (server-side env). Without it the adapter reports
unconfigured rather than failing mysteriously.
"""

import os
from typing import Any

from app.providers.base import (
    HttpAdapter,
    ProviderConfigError,
    ProviderError,
)


class FredAdapter(HttpAdapter):
    key = "fred"
    kind = "macro"
    base_url = "https://api.stlouisfed.org/fred"
    rps = 2.0

    def __init__(self, api_key: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.api_key = api_key or os.environ.get("FRED_API_KEY")

    def _require_key(self) -> str:
        if not self.api_key:
            raise ProviderConfigError("fred: FRED_API_KEY not configured")
        return self.api_key

    def capabilities(self) -> dict[str, Any]:
        return {
            "data": ["macro_series", "observations", "release_calendar"],
            "requires_credentials": True,
            "configured": bool(self.api_key),
        }

    async def healthcheck(self) -> bool:
        resp = await self.series("GDP")
        return "seriess" in resp or "series" in resp

    async def series(self, code: str) -> dict:
        resp = await self._get(
            f"{self.base_url}/series",
            params={
                "series_id": code,
                "api_key": self._require_key(),
                "file_type": "json",
            },
        )
        return resp.json()

    async def observations(
        self, code: str, start: str | None = None
    ) -> list[dict]:
        resp = await self._get(
            f"{self.base_url}/series/observations",
            params={
                "series_id": code,
                "api_key": self._require_key(),
                "file_type": "json",
                **({"observation_start": start} if start else {}),
            },
        )
        data = resp.json()
        if "observations" not in data:
            raise ProviderError(f"fred: bad response for {code}")
        return data["observations"]

    async def observations_csv(self, code: str) -> list[dict]:
        """Public fredgraph CSV fallback — no key needed. Returns
        latest-vintage values (FRED revisions not tracked without
        ALFRED; caller must treat published_at as fetch time and note
        the limitation in provenance)."""
        import csv as csvmod
        import io

        resp = await self._get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": code},
        )
        text = resp.text
        if "Error" in text[:200] or "observation" not in text:
            raise ProviderError(f"fred: fredgraph error for {code}")
        rows = list(csvmod.DictReader(io.StringIO(text)))
        return [
            {"date": r["observation_date"], "value": r.get(code, ".")}
            for r in rows
            if r.get(code) not in (None, ".")
        ]

    async def release_dates(self, start: str, end: str,
                            limit: int = 1000) -> list[dict]:
        """Upcoming release calendar — /releases/dates windowed to
        [start, end]. Requires the API key (all /fred/* endpoints do).
        Paginates: the daily-frequency releases alone overflow one
        page, which silently truncated future macro events.
        Returns [{release_id, release_name, date}]."""
        out: list[dict] = []
        offset = 0
        while True:
            resp = await self._get(
                f"{self.base_url}/releases/dates",
                params={
                    "api_key": self._require_key(),
                    "file_type": "json",
                    "realtime_start": start,
                    "realtime_end": end,
                    "include_release_dates_with_no_data": "true",
                    "order_by": "release_date",
                    "sort_order": "asc",
                    "limit": limit,
                    "offset": offset,
                },
            )
            batch = (resp.json().get("release_dates") or [])
            out.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
        return out
