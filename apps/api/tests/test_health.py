from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_is_alive() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["execution_enabled"] is False
    assert "uptime_seconds" in body


def test_readyz_reports_check_structure() -> None:
    r = client.get("/readyz")
    assert r.status_code in (200, 503)
    body = r.json()
    assert set(body["checks"]) == {"postgres", "redis"}
    for check in body["checks"].values():
        assert check["status"] in ("up", "down")
