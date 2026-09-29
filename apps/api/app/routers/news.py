"""News feed — Alpaca market news (keyed) + SEC EDGAR filings
(keyless, real). Cached 5min per symbol; empty list is honest."""

import os
from datetime import datetime
from datetime import timezone as tz

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.instruments import Instrument, InstrumentIdentifier
from app.services import cache
from app.services.secrets import get_secret

router = APIRouter(prefix="/news", tags=["news"])

UA = {"User-Agent": "VAIIP/0.1 research contact@vianomics"}


async def _alpaca_news(db: AsyncSession, sym: str) -> list[dict]:
    """Alpaca data news API — needs keys from env or secret store."""
    key = os.environ.get("ALPACA_API_KEY") \
        or await get_secret(db, "ALPACA_API_KEY")
    sec = os.environ.get("ALPACA_SECRET_KEY") \
        or await get_secret(db, "ALPACA_SECRET_KEY")
    if not (key and sec):
        return []
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(
                "https://data.alpaca.markets/v1beta1/news",
                params={"symbols": sym, "limit": 12},
                headers={"APCA-API-KEY-ID": key,
                         "APCA-API-SECRET-KEY": sec})
            if r.status_code != 200:
                return []
            return [{"source": a.get("source") or "alpaca",
                     "title": a.get("headline", ""),
                     "url": a.get("url"),
                     "at": a.get("created_at"),
                     "summary": a.get("summary")}
                    for a in r.json().get("news", [])]
    except Exception:
        return []


async def _edgar_news(db: AsyncSession, sym: str) -> list[dict]:
    """Recent SEC filings — real 'news' from the source of truth."""
    inst = (await db.execute(
        select(Instrument).where(Instrument.symbol == sym))
    ).scalar_one_or_none()
    if not inst:
        return []
    cik = (await db.execute(
        select(InstrumentIdentifier.value)
        .where(InstrumentIdentifier.instrument_id == inst.id,
               InstrumentIdentifier.scheme == "cik"))
    ).scalar_one_or_none()
    if not cik:
        return []
    try:
        async with httpx.AsyncClient(timeout=12, headers=UA) as c:
            r = await c.get(
                f"https://data.sec.gov/submissions/"
                f"CIK{int(cik):010d}.json")
            if r.status_code != 200:
                return []
            recent = r.json().get("filings", {}).get("recent", {})
            forms = recent.get("form", [])[:8]
            dates = recent.get("filingDate", [])
            accs = recent.get("accessionNumber", [])
            docs = recent.get("primaryDocument", [])
            return [{
                "source": "SEC EDGAR",
                "title": f"{f} filing",
                "url": f"https://www.sec.gov/cgi-bin/browse-edgar?"
                       f"action=getcompany&CIK={cik}&type={f}"
                       f"&dateb=&owner=include&count=10",
                "at": dates[i] if i < len(dates) else None,
                "summary": f"Accession {accs[i]} · {docs[i]}"
                if i < len(accs) else None}
                for i, f in enumerate(forms)]
    except Exception:
        return []


@router.get("/{symbol}")
async def news(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    sym = symbol.upper().split(".")[0]
    hit = cache.get(f"news:{sym}", 300)
    if hit is not None:
        return hit
    items = await _alpaca_news(db, sym)
    items += await _edgar_news(db, sym)
    items.sort(key=lambda x: x.get("at") or "", reverse=True)
    out = {"symbol": sym, "items": items[:16],
           "sources": sorted({i["source"] for i in items})}
    cache.put(f"news:{sym}", out)
    return out
