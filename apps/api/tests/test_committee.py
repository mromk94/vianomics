"""Parts 16–21 — agent schema, conflict protocol, CIO synthesis."""

import pytest

from app.agents import analyzers
from app.agents.report import validate_report
from app.services import committee as cs


def _ctx(**over):
    """Realistic ctx with everything present."""
    base = {
        "symbol": "TST", "sector": "Tech", "price": 100.0,
        "mandate_version": 1,
        "valuation_outputs": {
            "dcf": {"per_share": 140.0},
            "rule1": {"sticker_price": 160, "buy_price": 80},
            "mos": {"discount": 0.29},
            "five_numbers": {
                "revenue_growth": {"value": 0.2, "pass": True},
                "eps_growth": {"value": 0.15, "pass": True},
                "equity_growth": {"value": 0.12, "pass": True},
                "fcf_growth": {"value": 0.18, "pass": True},
                "roic": {"value": 0.25, "pass": True},
                "all_pass": True},
            "sensitivity": {"growths": [0.1], "discount_rates": [0.1],
                            "grid": [[140]]},
            "sector_valuation": {"metrics": {}},
        },
        "quant": {"metrics": {"sharpe": 1.1, "beta": {"beta": 1.1}},
                  "factor_scores": {"composite": 70, "coverage": 1.0}},
        "macro": {"econ_regime": "expansion", "market_regime": "risk_on",
                  "fear_greed": 60, "overlay": "neutral",
                  "stale_inputs": []},
        "technical": {"decision": "wait", "data_fresh": True,
                      "mean_reversion": {"decision": "wait"},
                      "trend_following": {"decision": "wait"},
                      "explanation": "RSI 55 — wait"},
        "risk_gate": {"allowed": True, "breaches": []},
        "risk_check_id": "chk-1",
        "green_zone": {"score": 15, "status": "pass", "run_id": "r1"},
    }
    base.update(over)
    return base


def _reports(ctx):
    return {k: validate_report(fn(ctx)) for k, fn in
            cs.ANALYZER_FN.items()}


# ── schema ──

def test_report_schema_validates():
    r = validate_report(analyzers.fundamental(_ctx()))
    assert r.agent_key == "fundamental" and r.layer == "research"
    assert r.schema_version and r.execution_id


def test_report_rejects_missing_fields():
    with pytest.raises(Exception):
        validate_report({"agent_key": "x"})          # everything missing
    bad = analyzers.fundamental(_ctx())
    bad.pop("data_quality")
    with pytest.raises(Exception):
        validate_report(bad)


def test_report_rejects_bad_recommendation():
    bad = analyzers.fundamental(_ctx())
    bad["recommendation"] = "to the moon!!"
    with pytest.raises(ValueError):
        validate_report(bad)


def test_report_rejects_empty_conclusion():
    bad = analyzers.fundamental(_ctx())
    bad["conclusion"] = "   "
    with pytest.raises(ValueError):
        validate_report(bad)


def test_all_agents_produce_valid_reports():
    reports = _reports(_ctx())
    assert len(reports) == 8
    for k, r in reports.items():
        assert r.ticker == "TST" and r.conclusion


# ── conflict protocol ──

def test_missing_risk_manager_is_no_trade():
    reports = _reports(_ctx())
    reports["risk"] = None
    c = cs.resolve_conflict(reports)
    assert c["verdict"] == "no_trade"
    assert "never a pass" in c["reason"]


def test_risk_block_is_absolute_veto():
    ctx = _ctx(risk_gate={"allowed": False, "breaches": [
        {"rule": "max_sector", "observed": 0.3, "required": 0.25,
         "blocking": True, "remediation": "trim"}]})
    reports = _reports(ctx)
    assert reports["risk"].recommendation == "block"
    c = cs.resolve_conflict(reports)
    assert c["verdict"] == "no_trade"


def test_disagreements_surfaced_not_averaged():
    reports = _reports(_ctx())
    # force split: technical bullish, others wait
    reports["technical"].recommendation = "buy"
    reports["fundamental"].recommendation = "pass"
    c = cs.resolve_conflict(reports)
    assert c["verdict"] is None  # no veto → proceeds to CIO
    assert c["disagreements"]  # split recorded


# ── CIO synthesis ──

def test_cio_verdict_and_confidence():
    reports = _reports(_ctx())
    c = cs.resolve_conflict(reports)
    cio = cs.cio_synthesize(reports, _ctx(), c)
    assert cio["verdict"] == "approve_pending_human"
    assert 0 <= cio["confidence"] <= 1
    assert "NOT a probability" in cio["confidence_note"]
    assert cio["required_human_approval"] is True
    assert cio["margin_of_safety"] == pytest.approx(0.29)


def test_cio_weak_scores_reject():
    ctx = _ctx(
        green_zone={"score": 2, "status": "fail"},
        valuation_outputs={"dcf": {"per_share": 50},
                           "rule1": {},
                           "mos": {"discount": -0.5},
                           "five_numbers": {k: {"pass": False} for k in
                                            ["revenue_growth", "eps_growth",
                                             "equity_growth", "fcf_growth",
                                             "roic"]},
                           "sector_valuation": {"metrics": {}}},
        quant={"metrics": {}, "factor_scores": {"composite": 10,
                                                "coverage": 0.2}},
        macro={"econ_regime": "recession", "market_regime": "risk_off",
               "stale_inputs": ["X"]},
        technical={"decision": "wait", "data_fresh": False,
                   "mean_reversion": {}, "trend_following": {},
                   "explanation": "stale"})
    reports = _reports(ctx)
    c = cs.resolve_conflict(reports)
    cio = cs.cio_synthesize(reports, ctx, c)
    assert cio["verdict"] in ("reject", "watchlist")
    assert cio["confidence"] < 0.6


def test_risk_veto_overrides_everything():
    """All research green + RM block → no_trade regardless."""
    ctx = _ctx(risk_gate={"allowed": False, "breaches": [
        {"rule": "max_single_name", "observed": 0.15, "required": 0.10,
         "blocking": True, "remediation": "smaller"}]})
    reports = _reports(ctx)
    for r in reports.values():
        if r.agent_key != "risk":
            r.recommendation = "strong_buy"
            r.score = 95
    c = cs.resolve_conflict(reports)
    cio = cs.cio_synthesize(reports, ctx, c)
    assert cio["verdict"] == "no_trade"
    assert cio["veto_reason"]
