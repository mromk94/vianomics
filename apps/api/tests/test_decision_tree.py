"""Parts 22–24 — decision tree gates, entry protocol, exit engine,
approval staleness."""

import pytest

from app.services import decision_tree as dt
from app.services import exit_engine as ex
from app.services.approvals import params_hash
from app.services.risk_engine import create_pyramid, PyramidState as S


def _ctx(**over):
    base = {
        "symbol": "TST", "price": 100.0, "in_universe": True,
        "sector": "Tech", "mos_required": 0.25,
        "green_zone": {"score": 16, "run_id": "r1"},
        "valuation_outputs": {
            "dcf": {"per_share": 140.0},
            "rule1": {"sticker_price": 160, "buy_price": 80},
            "mos": {"discount": 0.29},
            "five_numbers": {
                "revenue_growth": {"pass": True},
                "eps_growth": {"pass": True},
                "equity_growth": {"pass": True},
                "fcf_growth": {"pass": True},
                "roic": {"pass": True},
                "all_pass": True}},
        "quant": {"factor_scores": {"composite": 70, "coverage": 1.0}},
        "macro": {"econ_regime": "expansion", "market_regime": "risk_on",
                  "fear_greed": 60, "stale_inputs": []},
        "technical": {"decision": "entry_signal", "data_fresh": True,
                      "bars": 300, "explanation": "Aroon fired",
                      "indicators": {"atr_14": 4.0}},
        "risk_gate": {"allowed": True, "breaches": []},
        "cio_verdict": "approve_pending_human",
        "human_result": "pending",
    }
    base.update(over)
    return base


def _results(tree):
    return {g["key"]: g["result"] for g in tree["gates"]}


# ── gate branches ──

def test_full_tree_all_pass_awaits_human():
    t = dt.evaluate_tree(_ctx())
    r = _results(t)
    assert t["overall"] == "awaiting_human"
    assert r["human"] == "pending"
    for k in ("universe", "green_zone", "fundamental", "rule_one",
              "mos", "quant", "macro", "technical", "atr_risk",
              "portfolio_fit", "cio"):
        assert r[k] == "pass", (k, r[k])


def test_not_in_universe_fails_first_gate():
    t = dt.evaluate_tree(_ctx(in_universe=False))
    assert _results(t)["universe"] == "fail"
    assert t["overall"] == "rejected"


def test_green_zone_boundary_15():
    assert _results(dt.evaluate_tree(
        _ctx(green_zone={"score": 15})))["green_zone"] == "pass"
    assert _results(dt.evaluate_tree(
        _ctx(green_zone={"score": 14})))["green_zone"] == "review"
    assert _results(dt.evaluate_tree(
        _ctx(green_zone={"score": 9})))["green_zone"] == "fail"


def test_missing_data_is_insufficient_not_pass():
    t = dt.evaluate_tree(_ctx(green_zone=None, valuation_outputs={},
                              quant={}, technical={"bars": 0}))
    r = _results(t)
    assert r["green_zone"] == "insufficient_data"
    assert r["mos"] == "insufficient_data"
    assert r["technical"] == "insufficient_data"


def test_risk_block_halts_tree():
    t = dt.evaluate_tree(_ctx(risk_gate={"allowed": False, "breaches": [
        {"rule": "max_sector", "blocking": True}]}))
    r = _results(t)
    assert r["atr_risk"] == "blocked"
    assert r["portfolio_fit"] == "blocked"
    assert t["overall"] == "blocked"


def test_human_gate_never_auto_approved():
    t = dt.evaluate_tree(_ctx(human_result=None))
    assert _results(t)["human"] == "pending"
    # only explicit approval string yields approved
    t2 = dt.evaluate_tree(_ctx(human_result="approved"))
    assert t2["overall"] == "approved"


def test_gate_records_version_and_timestamp():
    t = dt.evaluate_tree(_ctx())
    g = t["gates"][0]
    assert g["rules_version"] and g["evaluated_at"] and g["inputs"]
    assert t["tree_version"] == "decision-tree/v1.0"


# ── entry protocol ──

def test_entry_protocol_12_checks():
    e = dt.entry_protocol(_ctx(portfolio_ctx={
        "nav": 1e6, "cash": 2e5, "positions": [],
        "gross": 1.0, "margin_used": 0}))
    assert len(e["checks"]) == 12
    assert e["eligible"] is True
    assert "not human authorization" in e["note"]


def test_entry_not_eligible_when_gate_blocks():
    e = dt.entry_protocol(_ctx(
        risk_gate={"allowed": False,
                   "breaches": [{"rule": "max_sector",
                                 "blocking": True}]}))
    assert e["eligible"] is False
    failed = [c["key"] for c in e["checks"] if not c["ok"]]
    assert "sector_limit" in failed and "no_blocks" in failed


# ── exit engine ──

def test_six_exit_classes():
    sigs = ex.exit_signals(
        {"stop": 90, "thesis_broken": True,
         "fundamentals_deteriorated": True},
        {"price": 85,
         "valuation_outputs": {"dcf": {"per_share": 80}},
         "macro": {"market_regime": "risk_off"},
         "risk_gate": {"breaches": [{"rule": "max_dd",
                                     "blocking": True}]}})
    classes = {s["cls"] for s in sigs}
    assert classes == {"fundamental", "valuation", "technical",
                       "risk", "market", "thesis"}
    tech = next(s for s in sigs if s["cls"] == "technical")
    assert tech["stage"] == "triggered"  # price < stop


def test_signal_is_not_a_fill():
    o = ex.exit_order({"symbol": "X", "qty": 100, "reason": "stop"})
    assert o["stage"] == "proposed_order"
    assert o["requires_approval"] is True
    assert o["stage"] != "closed"


def test_pyramid_stop_exits_all():
    t = create_pyramid("X", 1_000_000, 100, 4, 1_000_000)
    r = ex.pyramid_exit(t, 92, fill_price=90)
    assert t.state == S.STOPPED  # all out


# ── approval staleness ──

def test_params_hash_detects_changes():
    h1 = params_hash("buy", 100, 150.0, 90.0, 140.0)
    assert h1 == params_hash("buy", 100, 150.0, 90.0, 140.0)
    assert h1 != params_hash("buy", 101, 150.0, 90.0, 140.0)
    assert h1 != params_hash("buy", 100, 151.0, 90.0, 140.0)
    assert h1 != params_hash("sell", 100, 150.0, 90.0, 140.0)
