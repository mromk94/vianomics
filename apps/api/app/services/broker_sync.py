"""Broker account sync — pull-based equivalent of the MT4 push EA.

MT4 has no REST API so the terminal pushes snapshots; Alpaca and
IBKR(Flex) are pulled server-side into the same ExternalAccount row
shape the Command Center portfolio strip already reads.

- sync_alpaca_account: REST /v2/account + /v2/positions (+ held-name
  snapshots → market_quotes so external positions mark to a real tape)
- sync_ibkr_flex:    Flex Web Service — read-only statement pull, the
  only IBKR path that needs no running gateway daemon
"""

import os
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.instruments import Instrument
from app.models.market import MarketQuote
from app.models.portfolio import ExternalAccount
from app.services.secrets import get_secret


async def _ext_account(db: AsyncSession, source: str) -> ExternalAccount:
    acc = (await db.execute(
        select(ExternalAccount).where(ExternalAccount.source == source))
    ).scalars().first()
    if acc is None:
        acc = ExternalAccount(source=source, label="")
        db.add(acc)
    return acc


def _roll_equity(acc: ExternalAccount, equity: float | None) -> None:
    hist = list(acc.equity_history or [])
    hist.append({"t": utcnow().isoformat(), "equity": equity})
    acc.equity_history = hist[-400:]


async def sync_alpaca_account(db: AsyncSession) -> dict:
    """Alpaca paper/live account → ExternalAccount(source='alpaca').
    Positions normalize to the MT4 shape:
    {symbol, qty, price, profit, stop?, target?}"""
    from app.providers.alpaca import AlpacaAdapter

    base = os.environ.get("ALPACA_BASE_URL",
                          "https://paper-api.alpaca.markets")
    key = (await get_secret(db, "ALPACA_API_KEY")
           or os.environ.get("ALPACA_API_KEY"))
    secret = (await get_secret(db, "ALPACA_SECRET_KEY")
              or os.environ.get("ALPACA_SECRET_KEY"))
    if not (key and secret):
        return {"status": "skipped",
                "error": "ALPACA_API_KEY + ALPACA_SECRET_KEY not set"}
    h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    async with httpx.AsyncClient(timeout=20) as c:
        ra = await c.get(f"{base}/v2/account", headers=h)
        if ra.status_code != 200:
            acc = await _ext_account(db, "alpaca")
            acc.connected = False
            return {"status": "failed",
                    "error": f"account {ra.status_code}"}
        a = ra.json()
        rp = await c.get(f"{base}/v2/positions", headers=h)
        raw_pos = rp.json() if rp.status_code == 200 else []

    positions = [{
        "symbol": p["symbol"],
        "qty": float(p.get("qty") or 0),
        "price": float(p.get("avg_entry_price") or 0),
        "current_price": float(p.get("current_price") or 0),
        "market_value": float(p.get("market_value") or 0),
        "profit": float(p.get("unrealized_pl") or 0),
        "side": p.get("side"),
    } for p in raw_pos]

    equity = float(a.get("equity") or 0)
    acc = await _ext_account(db, "alpaca")
    acct_no = a.get("account_number") or ""
    acc.label = (f"Alpaca {'paper' if 'paper' in base else 'live'} "
                 f"#{acct_no[-4:]}" if acct_no else "Alpaca")
    acc.balance = float(a.get("cash") or 0)
    acc.equity = equity
    acc.currency = a.get("currency") or "USD"
    acc.positions = positions
    _roll_equity(acc, equity)
    acc.synced_at = utcnow()
    acc.connected = True

    # mark held names to Alpaca tape (quote rows, source='alpaca')
    try:
        ad = AlpacaAdapter(api_key=key, secret_key=secret)
        nq = 0
        inst_map = dict((await db.execute(
            select(Instrument.symbol, Instrument.id))).all())
        for p in positions:
            try:
                snap = await ad.snapshot(p["symbol"])
            except Exception:
                continue
            if not snap:
                continue
            q = snap.get("latestQuote") or {}
            t = snap.get("latestTrade") or {}
            bid, ask = q.get("bp"), q.get("ap")
            mid = ((bid + ask) / 2
                   if bid is not None and ask is not None
                   else t.get("p"))
            if mid is None:
                continue
            row = (await db.execute(
                select(MarketQuote).where(
                    MarketQuote.source == "alpaca",
                    MarketQuote.symbol == p["symbol"]))
            ).scalar_one_or_none()
            if row is None:
                row = MarketQuote(source="alpaca",
                                  symbol=p["symbol"], ts=utcnow())
                db.add(row)
            row.instrument_id = inst_map.get(p["symbol"])
            row.bid, row.ask, row.mid = bid, ask, mid
            row.day_open = (snap.get("dailyBar") or {}).get("o")
            row.prev_close = (snap.get("prevDailyBar") or {}).get("c")
            row.ts = utcnow()
            nq += 1
    except Exception:
        nq = 0  # quote marking is best-effort — positions already synced
    return {"status": "success", "positions": len(positions),
            "equity": equity, "quotes": nq}


# ── IBKR Flex Web Service — read-only statement pull ──
#
# IBKR connectivity reality (why this path exists):
#   * TWS API / IB Gateway (ib_insync): full trading + streaming, but
#     requires a running TWS/IB Gateway process reachable over TCP —
#     a daemon that must live on a VPS or the user's machine, with
#     API enabled + port open (7497 paper / 7496 live). A stateless
#     Render service cannot host it.
#   * Client Portal Web API: REST but still a self-hosted Java
#     gateway + daily re-auth — same daemon problem.
#   * Flex Web Service: pure HTTPS pull of a saved "Activity Flex
#     Query" report — positions, cash, trades. Read-only, works
#     anywhere. That's what this implements for portfolio sync; live
#     IBKR execution still needs the gateway path.
#
# Setup: IBKR portal → Performance & Reports → Flex Queries →
# create an Activity Flex Query including Open Positions + Cash
# Report → generate a Flex Web Service token → set
# IBKR_FLEX_TOKEN + IBKR_FLEX_QUERY_ID.

_FLEX_BASE = ("https://ndcdyn.interactivebrokers.com"
              "/AccountManagement/FlexWebService")


async def sync_ibkr_flex(db: AsyncSession,
                         max_polls: int = 6) -> dict:
    """Flex statement → ExternalAccount(source='ibkr')."""
    token = (await get_secret(db, "IBKR_FLEX_TOKEN")
             or os.environ.get("IBKR_FLEX_TOKEN"))
    qid = (await get_secret(db, "IBKR_FLEX_QUERY_ID")
           or os.environ.get("IBKR_FLEX_QUERY_ID"))
    if not (token and qid):
        return {"status": "skipped",
                "error": "IBKR_FLEX_TOKEN + IBKR_FLEX_QUERY_ID "
                         "not set — see services/broker_sync docstring"}

    import asyncio

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{_FLEX_BASE}/SendRequest",
                        params={"t": token, "q": qid, "v": "3"})
        root = ET.fromstring(r.text)
        status = (root.findtext("Status") or "").strip()
        if status != "Success":
            err = (root.findtext("ErrorMessage")
                   or f"SendRequest status={status}")
            return {"status": "failed", "error": err[:200]}
        ref = root.findtext("ReferenceCode")

        # report generates async — poll GetStatement until ready
        stmt = None
        for _ in range(max_polls):
            await asyncio.sleep(2.5)
            r2 = await c.get(f"{_FLEX_BASE}/GetStatement",
                             params={"t": token, "q": ref, "v": "3"})
            body = r2.text
            if "<FlexQueryResponse" in body:
                stmt = ET.fromstring(body)
                break
        if stmt is None:
            return {"status": "failed",
                    "error": "statement not ready after polling"}

    positions = []
    for p in stmt.iter("OpenPosition"):
        try:
            positions.append({
                "symbol": p.get("symbol"),
                "qty": float(p.get("position") or 0),
                "price": float(p.get("costBasisPrice") or 0),
                "current_price": float(p.get("markPrice") or 0),
                "market_value": float(p.get("positionValue") or 0),
                "profit": float(p.get("fifoPnlUnrealized") or 0),
                "side": ("long" if float(p.get("position") or 0) >= 0
                         else "short"),
                "currency": p.get("currency"),
            })
        except (TypeError, ValueError):
            continue

    cash = equity = None
    currency = "USD"
    for cnode in stmt.iter("CashReportCurrency"):
        if cnode.get("currency") == "USD" or cash is None:
            currency = cnode.get("currency") or "USD"
            cash = float(cnode.get("endingCash") or 0)
    # account-level equity from <EquitySummaryInBase> when present
    for e in stmt.iter("EquitySummaryByReportDateInBase"):
        if e.get("total"):
            equity = float(e.get("total") or 0)

    acc = await _ext_account(db, "ibkr")
    acct = next(iter(stmt.iter("FlexStatement")), None)
    acct_id = acct.get("accountId") if acct is not None else None
    acc.label = f"IBKR #{acct_id}" if acct_id else "IBKR (Flex)"
    acc.balance = cash
    acc.equity = equity or cash
    acc.currency = currency
    acc.positions = positions
    _roll_equity(acc, acc.equity)
    acc.synced_at = utcnow()
    acc.connected = True
    return {"status": "success", "positions": len(positions),
            "equity": acc.equity}


# ── IBKR bridge — live read path via the VPS sidecar ──
#
# When IBKR_BRIDGE_URL + IBKR_BRIDGE_SECRET are set this is preferred
# over Flex: real-time account values + open positions straight from
# the running Gateway — no 24h-batch statement delay. Read-only and
# bearer-gated, same external account row.


async def sync_ibkr_bridge(db: AsyncSession) -> dict:
    """Bridge /account + /positions → ExternalAccount(source='ibkr')."""
    url = (await get_secret(db, "IBKR_BRIDGE_URL")
           or os.environ.get("IBKR_BRIDGE_URL") or "").rstrip("/")
    secret = (await get_secret(db, "IBKR_BRIDGE_SECRET")
              or os.environ.get("IBKR_BRIDGE_SECRET"))
    if not (url and secret):
        return {"status": "skipped",
                "error": "IBKR_BRIDGE_URL + IBKR_BRIDGE_SECRET not set"}
    h = {"Authorization": f"Bearer {secret}"}
    async with httpx.AsyncClient(timeout=30) as c:
        ra = await c.get(f"{url}/account", headers=h)
        if ra.status_code != 200:
            acc = await _ext_account(db, "ibkr")
            acc.connected = False
            acc.synced_at = utcnow()
            return {"status": "failed",
                    "error": f"bridge /account {ra.status_code}"}
        a = ra.json()
        rp = await c.get(f"{url}/positions", headers=h)
        raw_pos = rp.json() if rp.status_code == 200 else []

    # mark held names to our own tape when a stored quote exists —
    # the bridge deliberately doesn't price positions (data layer's job)
    quote_map = dict((await db.execute(
        select(MarketQuote.symbol, MarketQuote.mid))).all())
    positions = []
    for p in raw_pos:
        if p.get("sec_type") not in (None, "STK"):
            continue            # equities only — matches the UI shape
        qty = float(p.get("qty") or 0)
        cur = quote_map.get(p["symbol"])
        positions.append({
            "symbol": p["symbol"], "qty": qty,
            "price": float(p.get("avg_cost") or 0),
            "current_price": cur,
            "market_value": cur * qty if cur and qty else None,
            "side": "long" if qty >= 0 else "short",
            "currency": p.get("currency"),
        })

    acc = await _ext_account(db, "ibkr")
    acct = a.get("account") or ""
    kind = "paper" if acct.upper().startswith("DU") else "live"
    acc.label = f"IBKR {kind} {acct}" if acct else "IBKR bridge"
    acc.balance = a.get("cash")
    acc.equity = a.get("equity") or a.get("cash")
    acc.currency = a.get("currency") or "USD"
    acc.positions = positions
    _roll_equity(acc, acc.equity)
    acc.synced_at = utcnow()
    acc.connected = True
    return {"status": "success", "positions": len(positions),
            "equity": acc.equity}
