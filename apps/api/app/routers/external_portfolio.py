"""External portfolio sources — MT4 terminal pushes, Bamboo sync.

MT4 has no REST API: the user's terminal runs a small EA/script
(docs/mt4/VAIIP_Push.mq4) that POSTs a snapshot here on a timer or on
account change. Bamboo syncs server-to-server via its API creds.

Security: pushes authenticate with MT4_PUSH_SECRET in the body —
same pattern as the TradingView webhook.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.session import get_db
from app.models.portfolio import ExternalAccount
from app.services.secrets import get_secret

router = APIRouter(prefix="/external", tags=["external-portfolio"])


class Mt4PushIn(BaseModel):
    secret: str
    account: str = Field(max_length=120)
    balance: float | None = None
    equity: float | None = None
    currency: str = Field(default="USD", max_length=3)
    positions: list[dict] = []          # [{symbol, qty, price, profit, bid?, ask?, stop?, target?}]
    quotes: list[dict] = []             # [{symbol, bid, ask, ts?}] —
                                        # MT4 Market Watch live tape


@router.post("/mt4/push", status_code=202)
async def mt4_push(body: Mt4PushIn, db: AsyncSession = Depends(get_db)):
    secret = await get_secret(db, "MT4_PUSH_SECRET")
    if not secret or body.secret != secret:
        raise HTTPException(403, "bad push secret")
    acc = (await db.execute(
        select(ExternalAccount).where(ExternalAccount.source == "mt4"))
    ).scalars().first() or ExternalAccount(source="mt4", label="")
    acc.label = f"MT4 #{body.account}"
    acc.balance, acc.equity = body.balance, body.equity
    acc.currency = body.currency.upper()
    acc.positions = body.positions
    hist = list(acc.equity_history or [])
    hist.append({"t": utcnow().isoformat(), "equity": body.equity})
    acc.equity_history = hist[-400:]   # ~6h at 60s pushes
    acc.synced_at = utcnow()
    acc.connected = True
    db.add(acc)

    # live tape — MT4 is a market-data source until real brokers
    # connect: upsert Market Watch quotes into market_quotes so the
    # book can be marked to live bid/ask, not the open price
    nq = 0
    if body.quotes:
        from datetime import datetime
        from app.models.instruments import Instrument
        from app.models.market import MarketQuote
        now = utcnow()
        # symbol → instrument resolution (best-effort; provider-native
        # symbol is kept so unmatched symbols still mark positions)
        inst_map = dict(
            (await db.execute(
                select(Instrument.symbol, Instrument.id))).all())
        existing = {q.symbol: q for q in (await db.execute(
            select(MarketQuote).where(MarketQuote.source == "mt4"))
        ).scalars().all()}
        for q in body.quotes:
            sym = str(q.get("symbol") or "").strip().upper()
            bid, ask = q.get("bid"), q.get("ask")
            if not sym or bid is None:
                continue
            row = existing.get(sym)
            if row is None:
                row = MarketQuote(source="mt4", symbol=sym, ts=now)
                db.add(row)
                existing[sym] = row
            row.instrument_id = inst_map.get(sym)
            row.bid, row.ask = bid, ask
            row.mid = ((float(bid) + float(ask)) / 2
                       if ask is not None else float(bid))
            raw_ts = q.get("ts")
            try:
                row.ts = (datetime.fromisoformat(
                    str(raw_ts).replace("Z", "+00:00"))
                    if raw_ts else now)
            except (ValueError, TypeError):
                row.ts = now
            nq += 1
    await db.commit()
    return {"accepted": True, "positions": len(body.positions),
            "quotes": nq}


@router.get("/sources")
async def sources(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ExternalAccount).where(ExternalAccount.connected))
    ).scalars().all()
    return [
        {"source": a.source, "label": a.label,
         "equity": float(a.equity) if a.equity is not None else None,
         "balance": float(a.balance) if a.balance is not None else None,
         "currency": a.currency,
         "positions": len(a.positions or []),
         "synced_at": a.synced_at.isoformat() if a.synced_at else None,
         "stale": (a.synced_at is None or
                   (utcnow() - a.synced_at).total_seconds() > 3600)}
        for a in rows]


@router.post("/bamboo/sync")
async def bamboo_sync(db: AsyncSession = Depends(get_db)):
    """Pull balances + positions from Bamboo's API using the stored
    client credentials. OAuth client_credentials → Bearer."""
    cid = await get_secret(db, "BAMBOO_CLIENT_ID")
    csec = await get_secret(db, "BAMBOO_CLIENT_SECRET")
    base = (await get_secret(db, "BAMBOO_BASE_URL")
            or "https://api.investbamboo.com")
    if not (cid and csec):
        raise HTTPException(400, "BAMBOO_CLIENT_ID/SECRET not set")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            tok = await c.post(f"{base}/oauth/token",
                               json={"grant_type": "client_credentials",
                                     "client_id": cid,
                                     "client_secret": csec})
            tok.raise_for_status()
            access = tok.json().get("access_token")
            h = {"Authorization": f"Bearer {access}"}
            acct = (await c.get(f"{base}/v1/account",
                                headers=h)).json()
            pos = (await c.get(f"{base}/v1/positions",
                              headers=h)).json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"bamboo unreachable: {e}")

    acc = (await db.execute(
        select(ExternalAccount).where(ExternalAccount.source == "bamboo"))
    ).scalars().first() or ExternalAccount(source="bamboo", label="")
    acc.label = acct.get("account_number", "Bamboo account")
    acc.balance = float(acct.get("cash", acct.get("cash_balance", 0)) or 0)
    acc.equity = float(acct.get("equity", acct.get("net_liquidation", 0))
                       or 0)
    acc.currency = acct.get("currency", "USD")
    acc.positions = [
        {"symbol": p.get("symbol"), "qty": p.get("quantity"),
         "price": p.get("current_price"),
         "market_value": p.get("market_value")}
        for p in (pos if isinstance(pos, list)
                  else pos.get("positions", []))]
    hist = list(acc.equity_history or [])
    hist.append({"t": utcnow().isoformat(), "equity": acc.equity})
    acc.equity_history = hist[-400:]
    acc.synced_at = utcnow()
    acc.connected = True
    db.add(acc)
    await db.commit()
    return {"synced": True, "equity": acc.equity,
            "positions": len(acc.positions)}
