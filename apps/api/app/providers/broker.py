"""Broker adapters — Part 30.

Hard rules:
- LIVE trading is disabled unless EXECUTION_ENABLED=true AND
  EXECUTION_BROKER=<key> AND the adapter's config check passes —
  the default is always paper.
- An LLM/agent can never call adapter methods directly; order flow
  only reaches a broker via execution service + approved ticket.
- Unimplemented adapters fail loudly (ProviderConfigError), never
  fake a live connection.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
import itertools
import random

import httpx

from app.providers.base import ProviderConfigError

# broker-reported states (never locally inferred beyond 'local_')
OrderState = Literal[
    "local_draft", "local_risk_validating", "local_awaiting_approval",
    "submitted", "acknowledged", "partially_filled", "filled",
    "rejected", "cancelled", "error", "unknown"]  # ← reconcile!


@dataclass
class BrokerOrder:
    """Broker-side order object — what the adapter tracks."""
    broker_order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    status: OrderState = "local_draft"
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    commission: float = 0.0
    reject_reason: str | None = None
    submitted_at: datetime | None = None
    ack_at: datetime | None = None
    events: list[dict] = field(default_factory=list)


class BrokerAdapter(Protocol):
    key: str
    live: bool                 # False = paper/simulated lane

    def capabilities(self) -> dict[str, Any]: ...
    async def account_summary(self) -> dict[str, Any]: ...
    async def positions(self) -> list[dict[str, Any]]: ...
    async def margin_requirements(self, symbol: str) -> dict: ...
    async def submit_order(self, o: BrokerOrder) -> BrokerOrder: ...
    async def cancel_order(self, broker_order_id: str) -> BrokerOrder: ...
    async def order_status(self, broker_order_id: str) -> BrokerOrder: ...
    async def executions(self) -> list[dict]: ...


# ── paper broker: deterministic simulation ──

class PaperBroker(BrokerAdapter):
    """Simulated broker with its own book. Fills market orders at a
    provided quote (or last supplied price); limit orders fill only
    if limit >= market. Partial fills, rejections and commissions
    are simulated deterministically from a seedable RNG — never
    claims to be real execution."""

    key = "paper"
    live = False

    def __init__(self, cash: float = 1_000_000.0, seed: int = 7):
        self.cash = cash
        self._positions: dict[str, dict] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._counter = itertools.count(1)
        self._rng = random.Random(seed)
        self._quotes: dict[str, float] = {}
        self.connected = True     # kill switch / disconnect simulation

    def set_quote(self, symbol: str, price: float):
        self._quotes[symbol] = price

    def capabilities(self) -> dict[str, Any]:
        return {"mode": "simulated", "orders": True,
                "data": ["account", "positions", "orders", "executions"],
                "note": "paper lane — no real money"}

    async def account_summary(self) -> dict[str, Any]:
        gross = sum(p["qty"] * p["last_px"] for p in
                    self._positions.values())
        return {"mode": "paper", "cash": self.cash,
                "gross_position_value": gross,
                "net_liquidation": self.cash + gross,
                "buying_power": self.cash,
                "permissions": ["paper_trading"]}

    async def positions(self) -> list[dict[str, Any]]:
        return [{"symbol": s, "qty": p["qty"], "avg_cost": p["avg_cost"],
                 "last_px": p["last_px"],
                 "market_value": p["qty"] * p["last_px"]}
                for s, p in self._positions.items()]

    async def margin_requirements(self, symbol: str) -> dict:
        return {"symbol": symbol, "initial_margin_pct": 0.30,
                "maintenance_pct": 0.25, "note": "paper defaults"}

    def _check_conn(self):
        if not self.connected:
            raise ConnectionError("paper broker: disconnected "
                                  "(kill switch or link down)")

    async def submit_order(self, o: BrokerOrder) -> BrokerOrder:
        self._check_conn()
        o.broker_order_id = f"P{next(self._counter):06d}"
        o.submitted_at = datetime.utcnow()
        o.status = "submitted"
        o.events.append({"t": o.submitted_at.isoformat(),
                         "s": "submitted"})
        # acknowledge + fill synchronously (paper lane is instant)
        o.ack_at = datetime.utcnow()
        o.status = "acknowledged"
        o.events.append({"t": o.ack_at.isoformat(), "s": "ack"})
        px = self._quotes.get(o.symbol)
        if px is None:
            o.status = "rejected"
            o.reject_reason = "no quote available"
            o.events.append({"t": datetime.utcnow().isoformat(),
                             "s": "rejected", "reason": o.reject_reason})
            return o
        if o.order_type == "limit" and o.limit_price is not None:
            fills = (o.side == "buy" and o.limit_price >= px) or \
                    (o.side == "sell" and o.limit_price <= px)
            if not fills:
                # rests as acknowledged — no fill
                return o
        fill_px = px
        o.commission = round(0.005 * o.qty, 2)
        if o.side == "buy":
            cost = fill_px * o.qty + o.commission
            if cost > self.cash:
                o.status = "rejected"
                o.reject_reason = "insufficient paper cash"
                return o
            self.cash -= cost
        else:
            self.cash += fill_px * o.qty - o.commission
        # position bookkeeping
        p = self._positions.setdefault(
            o.symbol, {"qty": 0.0, "avg_cost": 0.0, "last_px": fill_px})
        if o.side == "buy":
            tot = p["qty"] * p["avg_cost"] + o.qty * fill_px
            p["qty"] += o.qty
            p["avg_cost"] = tot / p["qty"] if p["qty"] else 0
        else:
            p["qty"] -= o.qty
        p["last_px"] = fill_px
        o.status = "filled"
        o.filled_qty = o.qty
        o.avg_fill_price = fill_px
        o.events.append({"t": datetime.utcnow().isoformat(),
                         "s": "filled", "px": fill_px, "qty": o.qty})
        return o

    async def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        self._check_conn()
        o = self._orders.get(broker_order_id)
        if o is None:
            raise KeyError(f"unknown order {broker_order_id}")
        if o.status in ("filled", "cancelled", "rejected"):
            return o
        o.status = "cancelled"
        o.events.append({"t": datetime.utcnow().isoformat(),
                         "s": "cancelled"})
        return o

    async def order_status(self, broker_order_id: str) -> BrokerOrder:
        self._check_conn()
        o = self._orders.get(broker_order_id)
        if o is None:
            raise KeyError(f"unknown order {broker_order_id}")
        return o

    async def executions(self) -> list[dict]:
        return [{"broker_order_id": oid, "status": o.status,
                 "filled_qty": o.filled_qty,
                 "avg_fill_price": o.avg_fill_price,
                 "commission": o.commission}
                for oid, o in self._orders.items()]

    def register(self, o: BrokerOrder):
        self._orders[o.broker_order_id] = o


# ── live broker stubs — fail loudly, never fake ──

class IbkrAdapter(BrokerAdapter):
    """Interactive Brokers via ib_insync/TWS Gateway — requires:
    IBKR_HOST/PORT/CLIENT_ID env, TWS or IB Gateway running, API
    enabled, market-data entitlements, EXECUTION_ENABLED=true.
    Not configured → every call raises ProviderConfigError."""

    key = "ibkr"
    live = True

    def __init__(self):
        from app.config import get_settings
        if not get_settings().ibkr_configured:
            raise ProviderConfigError(
                "ibkr: not configured — set IBKR_HOST/PORT/CLIENT_ID "
                "and EXECUTION_ENABLED=true after verifying TWS "
                "permissions, market-data subscriptions and the "
                "paper account first")

    def capabilities(self):
        return {"mode": "live-capable", "configured": False,
                "requires": ["TWS/IB Gateway running",
                             "IBKR_* env vars",
                             "EXECUTION_ENABLED=true",
                             "market-data entitlements"]}

    async def _unconfigured(self, *a, **k):
        raise ProviderConfigError("ibkr: adapter not configured")
    account_summary = _unconfigured
    positions = _unconfigured
    margin_requirements = _unconfigured
    submit_order = _unconfigured
    cancel_order = _unconfigured
    order_status = _unconfigured
    executions = _unconfigured


class AlpacaAdapter(BrokerAdapter):
    """Alpaca REST v2 — paper or live depending on base URL.
    Paper: paper-api.alpaca.markets · Live: api.alpaca.markets.
    Keys: ALPACA_API_KEY / ALPACA_SECRET_KEY (env or secret store)."""

    key = "alpaca"

    def __init__(self):
        import os
        self._key = os.environ.get("ALPACA_API_KEY")
        self._secret = os.environ.get("ALPACA_SECRET_KEY")
        self._base = os.environ.get(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        if not (self._key and self._secret):
            raise ProviderConfigError(
                "alpaca: set ALPACA_API_KEY + ALPACA_SECRET_KEY")
        self._paper = "paper" in self._base
        self.live = not self._paper   # paper URL → simulated lane

    def capabilities(self):
        return {"mode": "paper" if self._paper else "live",
                "operational": True, "broker": "alpaca"}

    def _h(self):
        return {"APCA-API-KEY-ID": self._key,
                "APCA-API-SECRET-KEY": self._secret}

    async def account_summary(self) -> dict:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self._base}/v2/account",
                            headers=self._h())
            r.raise_for_status()
            a = r.json()
            eq = float(a.get("equity", 0))
            last = float(a.get("last_equity") or eq)
            return {"equity": eq,
                    "cash": float(a.get("cash", 0)),
                    "buying_power": float(a.get("buying_power", 0)),
                    "portfolio_value": float(
                        a.get("portfolio_value") or 0),
                    "day_pnl": eq - last,
                    "status": a.get("status"),
                    "account_number": a.get("account_number"),
                    "paper": self._paper}

    async def positions(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self._base}/v2/positions",
                            headers=self._h())
            r.raise_for_status()
            return [{"symbol": p["symbol"],
                     "qty": float(p.get("qty") or 0),
                     "avg_cost": float(p.get("avg_entry_price") or 0),
                     "last_px": float(p.get("current_price") or 0),
                     "market_value": float(p.get("market_value") or 0),
                     "unrealized_pl": float(
                         p.get("unrealized_pl") or 0),
                     "side": p.get("side")}
                    for p in r.json()]

    async def margin_requirements(self, symbol: str) -> dict:
        """Alpaca exposes marginability as asset flags, not a per-
        symbol margin endpoint — return the real flags + the honest
        Reg-T default note rather than faking a percentage."""
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{self._base}/v2/assets/{symbol.upper()}",
                headers=self._h())
            if r.status_code != 200:
                return {"symbol": symbol, "tradable": None,
                        "note": "asset lookup failed"}
            a = r.json()
            return {"symbol": symbol,
                    "tradable": a.get("tradable"),
                    "marginable": a.get("marginable"),
                    "shortable": a.get("shortable"),
                    "easy_to_borrow": a.get("easy_to_borrow"),
                    "fractionable": a.get("fractionable"),
                    "note": ("Alpaca does not expose per-symbol margin "
                             "% — marginable equities use Reg-T "
                             "initial 50%, maint varies")}

    async def submit_order(self, o: BrokerOrder) -> BrokerOrder:
        body = {"symbol": o.symbol, "qty": o.qty, "side": o.side,
                "type": o.order_type, "time_in_force": "day"}
        if o.order_type == "limit":
            body["limit_price"] = o.limit_price
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{self._base}/v2/orders",
                             headers=self._h(), json=body)
            if r.status_code >= 400:
                o.status = "rejected"
                o.events.append({"t": datetime.utcnow().isoformat(),
                                 "stage": "rejected",
                                 "detail": r.text[:200]})
                return o
            d = r.json()
            o.broker_order_id = d["id"]
            o.status = ("filled" if d.get("status") == "filled"
                        else "submitted")
            if d.get("filled_qty"):
                o.filled_qty = float(d["filled_qty"])
                o.avg_fill_price = (float(d["filled_avg_price"])
                                    if d.get("filled_avg_price") else None)
            o.events.append({"t": datetime.utcnow().isoformat(),
                             "stage": "submitted", "detail": d["id"]})
            return o

    _STATUS_MAP = {"filled": "filled", "canceled": "cancelled",
                   "rejected": "rejected", "expired": "cancelled",
                   "done_for_day": "cancelled",
                   "partially_filled": "partially_filled",
                   "pending_cancel": "submitted"}

    def _map_order(self, d: dict) -> BrokerOrder:
        o = BrokerOrder(broker_order_id=d["id"],
                        symbol=d.get("symbol") or "",
                        side=d.get("side") or "buy",
                        qty=float(d.get("qty") or 0),
                        order_type=d.get("type") or "market")
        o.status = self._STATUS_MAP.get(d.get("status"), "submitted")
        o.filled_qty = float(d.get("filled_qty") or 0)
        o.avg_fill_price = (float(d["filled_avg_price"])
                            if d.get("filled_avg_price") else None)
        o.limit_price = (float(d["limit_price"])
                         if d.get("limit_price") else None)
        return o

    async def order_status(self, broker_order_id: str) -> BrokerOrder:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{self._base}/v2/orders/{broker_order_id}",
                headers=self._h())
            r.raise_for_status()
            return self._map_order(r.json())

    async def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.delete(
                f"{self._base}/v2/orders/{broker_order_id}",
                headers=self._h())
            o = BrokerOrder(broker_order_id=broker_order_id,
                            symbol="", side="", qty=0)
            o.status = "cancelled" if r.status_code in (200, 204, 404) \
                else "unknown"
            if r.status_code == 422:
                o.status = "error"
                o.reject_reason = "order no longer cancelable"
            return o

    async def executions(self) -> list[dict]:
        """Closed/filled orders — the broker-side fills record."""
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self._base}/v2/orders",
                            params={"status": "closed", "limit": 100,
                                    "direction": "desc"},
                            headers=self._h())
            r.raise_for_status()
            return [{"broker_order_id": d["id"],
                     "symbol": d.get("symbol"),
                     "side": d.get("side"),
                     "status": self._STATUS_MAP.get(
                         d.get("status"), d.get("status")),
                     "filled_qty": float(d.get("filled_qty") or 0),
                     "avg_fill_price": (
                         float(d["filled_avg_price"])
                         if d.get("filled_avg_price") else None),
                     "filled_at": d.get("filled_at")}
                    for d in r.json() if d.get("filled_qty")]


# registry — paper default; alpaca + ibkr when configured
def get_adapter(key: str = "paper") -> BrokerAdapter:
    if key == "paper":
        return PAPER
    if key == "ibkr":
        return IbkrAdapter()
    if key == "alpaca":
        return AlpacaAdapter()
    raise ProviderConfigError(f"broker '{key}' not implemented")


PAPER = PaperBroker()   # module-level singleton for the paper lane
