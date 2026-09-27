"""Broker interface — read-only / simulated.

Hard rules (per SOW + goals.md):
- No order path exists here. Execution lands in `execution/` (D1.30)
  behind EXECUTION_ENABLED + recorded approvals.
- IBKR requires TWS/Gateway + entitlements; until a real integration
  test passes the provider reports 'unconfigured'.
"""

from typing import Any, Protocol

from app.providers.base import ProviderConfigError


class BrokerAdapter(Protocol):
    key: str

    def capabilities(self) -> dict[str, Any]: ...

    async def account_summary(self) -> dict[str, Any]: ...

    async def positions(self) -> list[dict[str, Any]]: ...


class PaperBrokerAdapter:
    """Local simulated broker — reads portfolio state from nothing external.
    Returns empty/derived data honestly; used for the Phase-1 paper lane."""

    key = "paper_broker"

    def capabilities(self) -> dict[str, Any]:
        return {
            "mode": "simulated",
            "orders": False,  # order API arrives with D1.30
            "data": ["positions", "account_summary"],
        }

    async def account_summary(self) -> dict[str, Any]:
        return {"mode": "simulated", "net_liquidation": None, "cash": None}

    async def positions(self) -> list[dict[str, Any]]:
        return []


class IbkrAdapter:
    """Interactive Brokers — stub until TWS/Gateway integration + a real
    integration test exists. Deliberately refuses rather than faking."""

    key = "ibkr"

    def capabilities(self) -> dict[str, Any]:
        return {
            "mode": "live-capable",
            "orders": False,
            "configured": False,
            "requires": ["TWS/IB Gateway", "market-data subscriptions"],
        }

    async def account_summary(self) -> dict[str, Any]:
        raise ProviderConfigError("ibkr: adapter not configured")

    async def positions(self) -> list[dict[str, Any]]:
        raise ProviderConfigError("ibkr: adapter not configured")
