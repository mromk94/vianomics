"""SEC EDGAR adapter — public endpoints, no credentials required.

Uses data.sec.gov:
  /submissions/CIK##########.json  — filing index + recent facts
  /api/xbrl/companyfacts/CIK##########.json — extracted XBRL fundamentals

SEC fair-access rules require a descriptive User-Agent; we send one and
stay under the 10 req/s guideline via conservative throttling upstream.
"""

from typing import Any

from app.providers.base import HttpAdapter, ProviderError


class EdgarAdapter(HttpAdapter):
    key = "edgar"
    kind = "fundamentals"
    base_url = "https://data.sec.gov"
    rps = 2.0  # well under SEC's 10/s fair-access cap

    def capabilities(self) -> dict[str, Any]:
        return {
            "asset_classes": ["equity"],
            "data": ["submissions", "xbrl_facts", "filings"],
            "requires_credentials": False,
            "redistribution": "public domain",
        }

    async def healthcheck(self) -> bool:
        resp = await self._get(f"{self.base_url}/submissions/CIK0000320193.json")
        return resp.status_code == 200

    async def submissions(self, cik: str | int) -> dict:
        cik10 = str(cik).zfill(10)
        return (
            await self._get(f"{self.base_url}/submissions/CIK{cik10}.json")
        ).json()

    async def company_facts(self, cik: str | int) -> dict:
        cik10 = str(cik).zfill(10)
        return (
            await self._get(
                f"{self.base_url}/api/xbrl/companyfacts/CIK{cik10}.json"
            )
        ).json()

    async def ticker_map(self) -> dict:
        """SEC's official ticker→CIK map."""
        resp = await self._get("https://www.sec.gov/files/company_tickers.json")
        if resp.status_code != 200:
            raise ProviderError("edgar: ticker map fetch failed")
        return resp.json()
