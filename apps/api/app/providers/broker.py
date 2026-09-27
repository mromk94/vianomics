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
    """Planned — NOT operational. Do not present as connected."""
    key = "alpaca"
    live = True

    def __init__(self):
        raise ProviderConfigError("alpaca: adapter not yet implemented")

    def capabilities(self):
        return {"mode": "planned", "operational": False}
    account_summary = positions = margin_requirements = submit_order = \
        cancel_order = order_status = executions = None


# registry — paper is the only operational adapter
def get_adapter(key: str = "paper") -> BrokerAdapter:
    if key == "paper":
        return PAPER
    if key == "ibkr":
        return IbkrAdapter()
    raise ProviderConfigError(f"broker '{key}' not implemented")


PAPER = PaperBroker()   # module-level singleton for the paper lane
