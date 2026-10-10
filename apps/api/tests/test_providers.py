"""Adapter contract tests — httpx MockTransport, no real network."""

import httpx
import pytest

from app.providers.base import ProviderConfigError, ProviderError
from app.providers.edgar import EdgarAdapter
from app.providers.fred import FredAdapter
from app.providers.market import TiingoAdapter


def mock_client(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_edgar_facts_parse():
    adapter = EdgarAdapter(client=mock_client({"facts": {"us-gaap": {}}}))
    data = await adapter.company_facts("320193")
    assert "facts" in data
    assert adapter.capabilities()["requires_credentials"] is False


async def test_edgar_requires_no_key():
    assert EdgarAdapter().capabilities()["requires_credentials"] is False


async def test_fred_unconfigured_without_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    adapter = FredAdapter()
    assert adapter.capabilities()["configured"] is False
    with pytest.raises(ProviderConfigError):
        await adapter.observations("GDP")


async def test_fred_observations_parse(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    payload = {"observations": [{"date": "2024-01-01", "value": "27000"}]}
    adapter = FredAdapter(client=mock_client(payload))
    obs = await adapter.observations("GDP")
    assert obs[0]["value"] == "27000"


async def test_tiingo_unconfigured(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    adapter = TiingoAdapter()
    assert adapter.capabilities()["configured"] is False
    with pytest.raises(ProviderConfigError):
        await adapter.eod("AAPL", "2024-01-01", "2024-01-31")


async def test_adapter_5xx_is_retryable_provider_error():
    adapter = EdgarAdapter(client=mock_client({}, status=500))
    with pytest.raises(ProviderError):
        await adapter.company_facts("320193")


def test_paper_broker_is_honest():
    from app.providers.broker import PaperBroker

    caps = PaperBroker().capabilities()
    assert caps["mode"] == "simulated"
    assert caps["orders"] is True      # paper lane only


async def test_ibkr_refuses_until_configured():
    from app.providers.broker import IbkrAdapter

    with pytest.raises(ProviderConfigError):
        IbkrAdapter()


async def test_stockrow_unconfigured_without_key(monkeypatch):
    from app.providers.stockrow import StockRowAdapter

    monkeypatch.delenv("STOCKROW_API_KEY", raising=False)
    adapter = StockRowAdapter()
    assert adapter.capabilities()["configured"] is False
    with pytest.raises(ProviderConfigError):
        await adapter.metric_history("LULU", "revenue")


async def test_stockrow_annual_series_parse(monkeypatch):
    from app.providers.stockrow import StockRowAdapter

    monkeypatch.delenv("STOCKROW_API_KEY", raising=False)
    payload = {"data": [
        {"period_end": "2025-01-31", "value": 1200},
        {"period_end": "2024-01-31", "value": 1000},
        {"period_end": "2023-01-31", "value": 800},
    ]}
    adapter = StockRowAdapter(api_key="k", client=mock_client(payload))
    series = await adapter.annual_series("LULU", "revenue")
    # oldest→newest date-keyed dict, ready for first→last CAGR
    import datetime as _dt
    assert series == {
        _dt.date(2023, 1, 31): 800.0,
        _dt.date(2024, 1, 31): 1000.0,
        _dt.date(2025, 1, 31): 1200.0,
    }


async def test_stockrow_unknown_ticker_returns_empty(monkeypatch):
    """404/no coverage → empty series, never a fabricated zero —
    callers treat it as 'provider doesn't cover this name'."""
    from app.providers.stockrow import StockRowAdapter

    adapter = StockRowAdapter(api_key="k",
                              client=mock_client({}, status=404))
    assert await adapter.metric_history("ZZZZ", "revenue") == []
