"""VAIIP ⇄ IBKR bridge — runs on the VPS next to IB Gateway.

Why this exists: the TWS socket API requires a running IB Gateway (or
TWS) — a stateful daemon with daily relogin and a GUI lifecycle — which
a stateless cloud service can't host. This service owns the ib_insync
connection to a localhost Gateway and exposes a minimal HTTPS surface
guarded by a bearer secret. The API's IbkrAdapter maps 1:1 onto these
routes, same shape as AlpacaAdapter maps onto Alpaca's REST.

Safety: IBKR_READONLY=true (the default) connects read-only — order
routes return 403 until explicitly flipped on the VPS, independent of
the API's EXECUTION_ENABLED flag. Two switches, both must open for a
real order to route.

Env (.env / systemd EnvironmentFile):
  IBKR_HOST=127.0.0.1        Gateway listens localhost-only — never expose it
  IBKR_PORT=4002             Gateway paper; 4001 live (TWS: 7497 / 7496)
  IBKR_CLIENT_ID=7           any int; one API session per account
  IBKR_ACCOUNT=              pin to one account id, or blank = default
  IBKR_READONLY=true         'false' only when ready to place orders
  IBKR_BRIDGE_SECRET=        shared bearer secret with the API — required
"""

import asyncio
import os
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from ib_insync import IB, LimitOrder, MarketOrder, Stock
from pydantic import BaseModel

# ib_insync drives asyncio.run_until_complete internally; inside
# uvicorn's already-running loop that fails without nesting. Must
# apply before any IB usage — the sync IB calls below are then fine.
import nest_asyncio
nest_asyncio.apply()

IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "4002"))
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "7"))
IBKR_ACCOUNT = os.environ.get("IBKR_ACCOUNT", "")
IBKR_READONLY = os.environ.get("IBKR_READONLY", "true").lower() != "false"
SECRET = os.environ.get("IBKR_BRIDGE_SECRET", "")

# ib_insync cannot run on uvicorn's loop — connectAsync creates
# futures bound to whatever loop constructed them, and ASGI request
# tasks live on a different one ("attached to a different loop").
# The supported server pattern: IB gets its own event loop in a
# daemon thread; every call is marshalled via run_coroutine_threadsafe.
ib: IB | None = None
_ib_loop: asyncio.AbstractEventLoop | None = None


def _run_ib_loop() -> None:
    # ib_insync.util.getLoop() consults the thread-local loop via the
    # event-loop POLICY — run_forever alone never registers it, so
    # every getLoop() in this thread would raise/attach elsewhere.
    asyncio.set_event_loop(_ib_loop)
    _ib_loop.run_forever()


def _ensure_ib_loop() -> None:
    global _ib_loop
    if _ib_loop is None:
        _ib_loop = asyncio.new_event_loop()
        threading.Thread(target=_run_ib_loop,
                         daemon=True, name="ib-loop").start()


def _wait(coro):
    """Await an ib_insync coroutine ON ib's own loop."""
    return asyncio.wrap_future(
        asyncio.run_coroutine_threadsafe(coro, _ib_loop))


async def _ib_sync(fn, *args, **kw):
    """Run a synchronous ib_insync call on IB's loop — placeOrder/
    cancelOrder write to the transport, which no other thread may touch."""
    async def c():
        return fn(*args, **kw)
    return await _wait(c())


# IB reports unset numerics as ~1.79e308 — treat as missing, never leak
_UNSET = 1e15


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if abs(f) < _UNSET else None
    except (TypeError, ValueError):
        return None


async def ensure_ib() -> None:
    global ib
    _ensure_ib_loop()
    if ib is None:
        async def _mk() -> IB:
            return IB()           # binds to _ib_loop — its running loop
        ib = await _wait(_mk())
    if ib.isConnected():
        return
    try:
        await _wait(ib.connectAsync(
            IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID,
            account=IBKR_ACCOUNT, timeout=15, readonly=IBKR_READONLY))
    except Exception as e:
        raise HTTPException(503, f"gateway unreachable: {e}")


async def auth(authorization: str = Header(default="")) -> None:
    if not SECRET:
        raise HTTPException(500, "IBKR_BRIDGE_SECRET not set on bridge")
    if authorization != f"Bearer {SECRET}":
        raise HTTPException(403, "bad bridge secret")


@asynccontextmanager
async def lifespan(_):
    try:
        await ensure_ib()
    except HTTPException:
        pass        # gateway may not be up yet — endpoints retry per request
    yield
    if ib is not None and ib.isConnected() and _ib_loop is not None:
        _ib_loop.call_soon_threadsafe(ib.disconnect)


app = FastAPI(title="VAIIP IBKR bridge", lifespan=lifespan)

# TWS order status → API's OrderState literals
_STATUS = {
    "PendingSubmit": "submitted", "ApiPending": "submitted",
    "PreSubmitted": "acknowledged", "Submitted": "acknowledged",
    "PendingCancel": "submitted",
    "Filled": "filled",
    "ApiCancelled": "cancelled", "Cancelled": "cancelled",
    "Inactive": "rejected",
}


def _map_trade(t) -> dict:
    st = t.orderStatus
    status = _STATUS.get(st.status, "unknown")
    if status in ("submitted", "acknowledged") and (st.filled or 0) > 0:
        status = "partially_filled"
    reject = None
    if status == "rejected" and t.log:
        reject = str(t.log[-1].message)[:200]
    return {
        "broker_order_id": str(st.permId or t.order.orderId),
        "symbol": t.contract.localSymbol or t.contract.symbol,
        "side": (t.order.action or "").lower(),
        "qty": float(t.order.totalQuantity or 0),
        "order_type": (t.order.orderType or "market").lower(),
        "limit_price": _f(t.order.lmtPrice),
        "status": status,
        "filled_qty": float(st.filled or 0),
        "avg_fill_price": _f(st.avgFillPrice),
        "commission": _f(st.commission) or 0.0,
        "reject_reason": reject,
    }


def _find_trade(oid: str):
    for t in ib.trades():
        if str(t.orderStatus.permId or t.order.orderId) == oid:
            return t
    return None


@app.get("/health")
async def health(_: None = Depends(auth)):
    await ensure_ib()
    return {"connected": True, "accounts": ib.managedAccounts(),
            "account": IBKR_ACCOUNT or "default",
            "readonly": IBKR_READONLY, "port": IBKR_PORT}


@app.get("/account")
async def account(_: None = Depends(auth)):
    await ensure_ib()
    acct = IBKR_ACCOUNT or (ib.managedAccounts() or [""])[0]
    vals = await _wait(ib.accountSummaryAsync(acct))

    def num(tag: str):
        rows = [v for v in vals
                if v.tag == tag and (not acct or v.account == acct)]
        # BASE rows are in account base currency — prefer them over
        # per-currency rows (accounts may be GBP/EUR/USD denominated)
        for pref in ("BASE", "USD", ""):
            for v in rows:
                if v.currency == pref:
                    return _f(v.value)
        return _f(rows[0].value if rows else None)

    return {"equity": num("NetLiquidation"), "cash": num("TotalCashValue"),
            "buying_power": num("BuyingPower"),
            "gross_position_value": num("GrossPositionValue"),
            "unrealized_pl": num("UnrealizedPnL"),
            "account": IBKR_ACCOUNT or (ib.managedAccounts() or [None])[0],
            "readonly": IBKR_READONLY, "port": IBKR_PORT}


@app.get("/positions")
async def positions(_: None = Depends(auth)):
    await ensure_ib()
    return [{
        "symbol": p.contract.localSymbol or p.contract.symbol,
        "sec_type": p.contract.secType,
        "qty": float(p.position),
        "avg_cost": float(p.avgCost),
        "currency": p.contract.currency,
        # market_value intentionally absent — pricing belongs to the
        # data layer (Flex sync / market quotes), not the broker socket
    } for p in (await _ib_sync(ib.positions, account=IBKR_ACCOUNT))]


class OrderIn(BaseModel):
    symbol: str
    side: str                    # buy | sell
    qty: float
    order_type: str = "market"   # market | limit
    limit_price: float | None = None
    tif: str = "DAY"


def _readonly() -> None:
    if IBKR_READONLY:
        raise HTTPException(
            403, "bridge read-only — set IBKR_READONLY=false on the VPS")


@app.post("/orders", status_code=201)
async def submit(body: OrderIn, _: None = Depends(auth)):
    _readonly()
    await ensure_ib()
    contract = Stock(body.symbol.upper(), "SMART", "USD")
    await _wait(ib.qualifyContractsAsync(contract))
    if not contract.conId:
        raise HTTPException(400, f"cannot qualify '{body.symbol}'")
    if body.order_type == "limit":
        if body.limit_price is None:
            raise HTTPException(400, "limit_price required for limit orders")
        order = LimitOrder(body.side.upper(), body.qty, body.limit_price)
    else:
        order = MarketOrder(body.side.upper(), body.qty)
    order.tif = body.tif
    trade = await _ib_sync(ib.placeOrder, contract, order)
    await asyncio.sleep(1.5)            # let TWS acknowledge
    return _map_trade(trade)


@app.post("/orders/{oid}/cancel")
async def cancel(oid: str, _: None = Depends(auth)):
    _readonly()
    await ensure_ib()
    t = _find_trade(oid)
    if t is None:
        raise HTTPException(404, "unknown order")
    await _ib_sync(ib.cancelOrder, t.order)
    await asyncio.sleep(1.0)
    return _map_trade(t)


@app.get("/orders/{oid}")
async def order_status(oid: str, _: None = Depends(auth)):
    await ensure_ib()
    t = _find_trade(oid)
    if t is None:
        # completed orders drop out of trades() — query TWS directly
        try:
            completed = await _wait(
                ib.reqCompletedOrdersAsync(apiOnly=True))
        except Exception:
            completed = []
        for ct in completed:
            if str(ct.orderStatus.permId or ct.order.orderId) == oid:
                t = ct
                break
    if t is None:
        raise HTTPException(404, "unknown order")
    return _map_trade(t)


@app.get("/executions")
async def executions(_: None = Depends(auth)):
    await ensure_ib()
    out = []
    for fill in ib.fills():
        e = fill.execution
        cr = fill.commissionReport
        out.append({
            "broker_order_id": str(e.permId or e.orderId),
            "symbol": fill.contract.localSymbol or fill.contract.symbol,
            "side": (e.side or "").lower(),
            "qty": float(e.shares),
            "price": _f(e.price),
            "commission": _f(cr.commission) if cr else 0.0,
            "exec_id": e.execId,
            "at": fill.time.isoformat() if fill.time else None,
        })
    return out


@app.get("/margin/{symbol}")
async def margin(symbol: str, qty: float = 100, _: None = Depends(auth)):
    """What-if margin check — a hypothetical BUY, never placed."""
    await ensure_ib()
    contract = Stock(symbol.upper(), "SMART", "USD")
    await _wait(ib.qualifyContractsAsync(contract))
    try:
        st = await _wait(
            ib.whatIfOrderAsync(contract, MarketOrder("BUY", qty)))
    except Exception as e:
        raise HTTPException(502, f"whatif failed: {e}")
    return {
        "symbol": symbol.upper(), "hypothetical_qty": qty,
        "init_margin": {"before": _f(st.initMarginBefore),
                        "after": _f(st.initMarginAfter),
                        "change": _f(st.initMarginChange)},
        "maint_margin": {"before": _f(st.maintMarginBefore),
                         "after": _f(st.maintMarginAfter),
                         "change": _f(st.maintMarginChange)},
        "equity_with_loan": {"before": _f(st.equityWithLoanBefore),
                             "after": _f(st.equityWithLoanAfter),
                             "change": _f(st.equityWithLoanChange)},
    }
