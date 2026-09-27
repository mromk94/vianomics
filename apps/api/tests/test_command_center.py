from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)


def test_command_center_shape() -> None:
    r = client.get("/api/v1/command-center")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "generated_at", "portfolio", "split", "regime", "risk",
        "watchlist", "approvals", "alerts", "decisions",
        "providers", "demo_sections",
    ):
        assert key in body


def test_command_center_demo_flagging() -> None:
    r = client.get("/api/v1/command-center")
    body = r.json()
    if get_settings().demo_fixtures:
        # Demo sections must be explicitly flagged — never implied live.
        assert "portfolio" in body["demo_sections"]
        assert body["portfolio"]["total_value"] is not None
    else:
        assert body["demo_sections"] == []
        assert body["portfolio"]["total_value"] is None


def test_provider_health_is_never_demo() -> None:
    r = client.get("/api/v1/command-center")
    body = r.json()
    names = {p["name"] for p in body["providers"]}
    assert {"PostgreSQL", "Redis"} <= names
    assert "providers" not in body["demo_sections"]
