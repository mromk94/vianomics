"""Parts 12–15 — risk engine: sizing, pyramid machine, limits gate.

Hand-calculated. The gate test verifies a 'perfect research' order
still gets blocked by risk — signals never override limits.
"""

import pytest

from app.services import risk_engine as re_
from app.services.risk_engine import PyramidState as S


# ── S1 sizing ──

def test_sizing_hand_calc():
    """equity 1M, risk 0.5% → $5k; entry 100, ATR 4 → stop dist 6,
    raw 833 shares → cash cap 1M/100=10k; shares=833."""
    s = re_.initial_sizing(1_000_000, 0.005, 100, 4, 1_000_000)
    assert s["stop_price"] == pytest.approx(94)
    assert s["shares"] == 833
    assert s["binding"] == "risk"


def test_sizing_cash_binding():
    s = re_.initial_sizing(1_000_000, 0.005, 100, 4, 10_000)
    assert s["shares"] == 100  # 10k cash / 100
    assert s["binding"] == "cash"


def test_sizing_liquidity_binding():
    # ADV 5000 × 10% = 500 share cap
    s = re_.initial_sizing(1_000_000, 0.005, 100, 4, 1_000_000,
                           adv_shares=5000)
    assert s["shares"] == 500
    assert s["binding"] == "liquidity"


def test_sizing_rejects_invalid():
    with pytest.raises(ValueError):
        re_.initial_sizing(0, 0.005, 100, 4, 1e6)
    with pytest.raises(ValueError):
        re_.initial_sizing(1e6, 0.005, 0, 4, 1e6)


# ── pyramid machine ──

def _trade(**kw):
    return re_.create_pyramid("X", 1_000_000, 100, 4, 1_000_000, **kw)


def test_pyramid_full_lifecycle_trailing():
    """Revised adaptive protocol: 1.5×ATR ratchet stop maintained at
    every close; equal-size leg added at each 3×ATR target."""
    t = _trade()
    assert t.state == S.INITIAL and t.target1 == pytest.approx(112)
    assert t.stop == pytest.approx(94) and t.leg_shares == 833

    # close at target → maintenance ratchets stop to 112−1.5×5=104.5,
    # then leg 2 added; next target 112+3×5=127
    r = re_.advance(t, 112, 5.0)
    assert t.state == S.TARGET1 and t.shares == 833 * 2
    assert t.stop == pytest.approx(104.5)
    assert t.target1 == pytest.approx(127)

    # next close: maintenance → 115−7.5=107.5; target not hit
    re_.advance(t, 115, 5.0)
    assert t.stop == pytest.approx(107.5)
    assert t.state == S.TARGET1 and t.shares == 1666

    # target 2 hit at 128 → maintenance 128−7.5=120.5; leg 3, target 143
    re_.advance(t, 128, 5.0)
    assert t.state == S.ADDITION and t.shares == 833 * 3
    assert t.stop == pytest.approx(120.5)
    assert t.target1 == pytest.approx(143)

    # never loosens: 121−7.5=113.5 < 120.5 → stop stays
    re_.advance(t, 121, 5.0)
    assert t.stop == pytest.approx(120.5)

    # close below stop → exit the whole pyramid
    r = re_.advance(t, 119, 5.0)
    assert t.state == S.STOPPED
    assert r["fill"] == pytest.approx(119)


def test_addition_rejected_when_risk_fails():
    """add_ok=False → no leg, but the pyramid keeps trading under the
    trailing stop (v1 dead-ends here — v2 keeps maintaining)."""
    t = _trade()
    r = re_.advance(t, 112, 5.0, add_ok=False)
    assert t.state == S.INITIAL
    assert t.shares == 833
    assert t.stop == pytest.approx(104.5)  # maintenance still ran
    assert "REJECTED" in t.events[-1]

    # retry on a later close — risk may have freed up
    re_.advance(t, 113, 5.0)
    assert t.shares == 833 * 2
    assert t.state == S.TARGET1


def test_gap_through_stop():
    t = _trade()
    # price gaps below stop 94 → fills at 90
    r = re_.advance(t, 92, 5.0, fill_price=90)
    assert t.state == S.STOPPED
    assert r["fill"] == 90
    assert "gapped" in t.events[-1]


def test_stop_never_loosens():
    t = _trade()
    re_.advance(t, 112, 5.0)
    re_.advance(t, 115, 5.0)              # stop → 110
    # price drops to 113, ATR 8 → new = 105 < 110 → keep 110
    r = re_.tighten_stop(113, 8, 110)
    assert r["new_stop"] == 110


def test_atr_12w_pct():
    assert float(re_.atr_12w_pct(6.0, 200)) == pytest.approx(0.03)
    assert re_.atr_12w_pct(None, 200) is None


# ── Part B holding tests ──

def test_holding_tests_no_mechanical_stops():
    out = re_.holding_tests({"weight": 0.15, "max_weight": 0.10,
                             "price_vs_iv": -0.3})
    assert out["excessive_size"] == "review_reduce"
    assert out["normal_volatility"] == "no_action"
    assert "no mechanical" in out["note"]


# ── Part D order gate ──

def _pf(**over):
    base = {
        "nav": 1_000_000, "cash": 200_000,
        "positions": [
            {"symbol": "AAA", "sector": "Tech", "market_value": 80_000},
            {"symbol": "BBB", "sector": "Health", "market_value": 70_000},
            {"symbol": "CCC", "sector": "Indus", "market_value": 70_000},
            {"symbol": "DDD", "sector": "Energy", "market_value": 60_000},
            {"symbol": "EEE", "sector": "Staples", "market_value": 60_000},
        ],
        "max_dd": -0.05, "avg_correlation": 0.4,
        "vix": 18, "fear_greed": 50,
    }
    base.update(over)
    return base


def test_order_passes_when_limits_ok():
    r = re_.check_order(
        {"symbol": "FFF", "side": "buy", "sector": "Materials",
         "notional": 50_000}, _pf())
    assert r["allowed"] is True


def test_single_name_limit_blocks():
    r = re_.check_order(
        {"symbol": "AAA", "side": "buy", "sector": "Tech",
         "notional": 50_000}, _pf())  # 80k + 50k = 130k > 10% of 1M
    assert r["allowed"] is False
    rules = [b["rule"] for b in r["breaches"]]
    assert "max_single_name" in rules


def test_sector_limit_blocks():
    r = re_.check_order(
        {"symbol": "GGG", "side": "buy", "sector": "Tech",
         "notional": 200_000}, _pf())  # 80k+200k=280k > 25%
    assert not r["allowed"]
    assert "max_sector" in [b["rule"] for b in r["breaches"]]


def test_all_green_signals_but_risk_blocks():
    """Every research signal positive — F&G 85 blocks new positions."""
    pf = _pf(fear_greed=85)
    r = re_.check_order(
        {"symbol": "HOT", "side": "buy", "sector": "Tech",
         "notional": 40_000}, pf)
    assert r["allowed"] is False
    assert "fg_restriction" in [b["rule"] for b in r["breaches"]]


def test_vix_risk_off_blocks():
    r = re_.check_order(
        {"symbol": "X", "side": "buy", "sector": "Tech",
         "notional": 30_000}, _pf(vix=32))
    assert not r["allowed"]
    assert "vix_reduce" in [b["rule"] for b in r["breaches"]]


def test_drawdown_limit_blocks():
    r = re_.check_order(
        {"symbol": "X", "side": "buy", "sector": "Tech",
         "notional": 30_000}, _pf(max_dd=-0.16))
    assert not r["allowed"]
    assert "max_drawdown" in [b["rule"] for b in r["breaches"]]


def test_min_cash_blocks():
    r = re_.check_order(
        {"symbol": "X", "side": "buy", "sector": "Tech",
         "notional": 190_000}, _pf())  # cash 200k→10k = 1% < 5%
    assert not r["allowed"]
    assert "min_cash" in [b["rule"] for b in r["breaches"]]


def test_leverage_blocks():
    r = re_.check_order(
        {"symbol": "X", "side": "buy", "sector": "Materials",
         "notional": 800_000}, _pf())  # gross >1 → breach
    assert not r["allowed"]
    assert "leverage" in [b["rule"] for b in r["breaches"]]


def test_sell_never_blocked():
    r = re_.check_order(
        {"symbol": "AAA", "side": "sell", "sector": "Tech",
         "notional": 50_000}, _pf(fear_greed=95, vix=45, max_dd=-0.5))
    assert r["allowed"] is True  # risk-off never traps exits


def test_stale_unknown_inputs_degrade_not_fail():
    dims = re_.risk_dimensions({"nav": 1e6, "positions": []})
    assert dims["factor_correlation"]["status"] in ("degraded", "ok")
    assert dims["feedback_path"]["status"] == "unknown"


def test_stress_ladder():
    pf = _pf()
    dims = re_.risk_dimensions(pf)
    assert set(dims["stress_tests"]) == {-0.10, -0.20, -0.30, -0.40}
    assert dims["stress_tests"][-0.30]["pnl"] == pytest.approx(
        -0.3 * (80e3 + 70e3 + 70e3 + 60e3 + 60e3))


def test_breach_carries_remediation():
    r = re_.check_order(
        {"symbol": "AAA", "side": "buy", "sector": "Tech",
         "notional": 50_000}, _pf())
    b = next(b for b in r["breaches"] if b["rule"] == "max_single_name")
    assert b["observed"] and b["required"] and b["remediation"]


# ── new-docs: Trade Risk Sheet / Ledger / Dashboard / PM ──

def test_trade_risk_sheet_hand_calc():
    """Workbook cols R–AB: entry 380, stop 350, target 610, FV 610,
    qty 33, equity 50k."""
    s = re_.trade_risk_sheet(entry=380, stop=350, target=610,
                             fair_value=610, qty=33, equity=50_000,
                             margin_rate=0.20)
    assert s["initial_notional"] == pytest.approx(380 * 33)
    assert s["initial_margin"] == pytest.approx(380 * 33 * 0.20)
    assert s["risk_dollars"] == pytest.approx(30 * 33)      # always ≥0
    assert s["risk_pct_equity"] == pytest.approx(990 / 50_000)
    assert s["reward_dollars"] == pytest.approx(230 * 33)
    assert s["rr"] == pytest.approx(230 / 30)
    assert s["mos_pct"] == pytest.approx((610 - 380) / 610)
    assert s["upside_pct"] == pytest.approx((610 - 380) / 380)


def test_trade_risk_never_negative():
    """The old workbook's -$874 bug — risk uses ABS."""
    assert re_.calculate_risk(300, 280, 43.7) == pytest.approx(874)
    assert re_.calculate_rr(3885, 874) == pytest.approx(4.44, abs=0.01)


def test_position_size_for_risk():
    """equity 50k × 2% = $1000 budget; risk/unit $20 → 50 units."""
    r = re_.position_size_for_risk(50_000, 0.02, 300, 280)
    assert r["qty"] == 50
    # invalid stop distance → no size
    assert re_.position_size_for_risk(50_000, 0.02, 300, 300)["qty"] == 0


def test_trade_risk_decision_rules():
    eq = 50_000
    # risk > 2% equity → BLOCK
    s = {"risk_dollars": 1100, "risk_capacity_after": 10_000,
         "initial_margin": 5000, "rr": 3.0}
    assert re_.trade_risk_decision(s, eq)["decision"] == "BLOCK"
    # risk > remaining portfolio capacity → BLOCK even if 1R trade
    s = {"risk_dollars": 800, "risk_capacity_after": 500,
         "initial_margin": 100, "rr": 4.0}
    d = re_.trade_risk_decision(s, eq)
    assert d["decision"] == "BLOCK" and "capacity" in d["reason"]
    # R/R < 2 → REVIEW
    s = {"risk_dollars": 500, "risk_capacity_after": 5000,
         "initial_margin": 100, "rr": 1.5}
    assert re_.trade_risk_decision(s, eq)["decision"] == "REVIEW"
    # margin utilisation > 50% → BLOCK
    s = {"risk_dollars": 500, "risk_capacity_after": 5000,
         "initial_margin": 26_000, "rr": 3.0}
    assert re_.trade_risk_decision(s, eq)["decision"] == "BLOCK"
    # clean → PASS
    s = {"risk_dollars": 800, "risk_capacity_after": 5000,
         "initial_margin": 5000, "rr": 3.0}
    assert re_.trade_risk_decision(s, eq)["decision"] == "PASS"


def test_position_ledger_row_long():
    """Entry 300, current 380, stop 350, qty 10 → open risk $300,
    locked-in $500, remaining reward $2300."""
    r = re_.position_ledger_row(qty=10, avg_entry=300, current_price=380,
                                stop=350, target=610, fair_value=610,
                                equity=50_000, direction="long")
    assert r["open_risk"] == pytest.approx(30 * 10)
    assert r["locked_in_profit"] == pytest.approx(50 * 10)
    assert r["remaining_reward"] == pytest.approx(230 * 10)
    assert r["gross_pnl"] == pytest.approx(80 * 10)
    assert r["rr"] == pytest.approx(230 / 30)
    assert r["unstopped"] is False


def test_portfolio_dashboard_workbook():
    """3 stopped positions + 1 unstopped — spec Panel 5: open stop
    risk counts only losses-to-active-stops; unstopped notional is
    flagged separately, never silently added to the risk budget."""
    pos = [
        {"notional": 10_000, "margin": 2_000, "open_risk": 1_000,
         "net_pnl": 500, "remaining_reward": 3_000, "direction": "long",
         "strategy": "rule1"},
        {"notional": 20_000, "margin": 4_000, "open_risk": 2_000,
         "net_pnl": -100, "remaining_reward": 4_000, "direction": "long",
         "strategy": "rule1"},
        {"notional": 30_000, "margin": 6_000, "open_risk": None,
         "net_pnl": 0, "remaining_reward": 0, "direction": "long",
         "strategy": "swing"},
    ]
    d = re_.portfolio_dashboard(pos, 100_000, 40_000)
    assert d["gross_notional"] == pytest.approx(60_000)
    assert d["gross_leverage"] == pytest.approx(0.6)
    assert d["current_margin"] == pytest.approx(12_000)
    assert d["margin_utilisation"] == pytest.approx(0.12)
    assert d["open_stop_risk"] == pytest.approx(3_000)
    assert d["unstopped_notional"] == pytest.approx(30_000)
    # spec-literal: open risk = losses if active stops hit = $3k
    assert d["open_risk"] == pytest.approx(3_000)
    # 3k < 15% of 100k (15k), but unstopped book → WATCH + warning
    assert d["status"] == "WATCH"
    assert d["warnings"]
    assert d["risk_capacity"] == pytest.approx(15_000 - 3_000)
    assert d["risk_by_strategy"]["rule1"] == pytest.approx(3_000)


def test_margin_call_buffer_check():
    """Exposure doc: $30k equity, 5x = $150k gross, maint 16% → call
    at $24k — same equity level as the 20% stop → call first."""
    r = re_.margin_call_check(30_000, 150_000,
                              margin_requirement=0.20,
                              maintenance_margin=0.16,
                              portfolio_stop_pct=0.20)
    assert r["margin_call_equity_threshold"] == pytest.approx(24_000)
    assert r["equity_at_stop"] == pytest.approx(24_000)
    # doc: M% ≥ 16% → margin call before the stop (zero buffer)
    assert r["margin_call_before_stop"] is True
    # maint 17% → threshold $25.5k > $24k → margin call first
    r = re_.margin_call_check(30_000, 150_000,
                              maintenance_margin=0.17)
    assert r["margin_call_before_stop"] is True
    # maint 10% → threshold $15k < $24k → stop hits first, safe
    r = re_.margin_call_check(30_000, 150_000,
                              maintenance_margin=0.10)
    assert r["margin_call_before_stop"] is False


def test_drawdown_escalation_ladder():
    assert re_.drawdown_escalation(0.02)["level"] == "NORMAL"
    assert re_.drawdown_escalation(0.06)["level"] == "WATCH"
    assert re_.drawdown_escalation(0.12)["level"] == "RISK_REDUCTION"
    assert re_.drawdown_escalation(0.20)["level"] == "TRADING_HALT"


def test_named_stress_scenarios():
    pos = [
        {"symbol": "NVDA", "sector": "Information Technology",
         "market_value": 10_000},
        {"symbol": "JNJ", "sector": "Health Care",
         "market_value": 10_000},
    ]
    out = re_.named_stress(pos, 50_000)
    by_key = {s["key"]: s for s in out}
    # semis −20% hits only NVDA
    assert by_key["semi_shock"]["loss"] == pytest.approx(2_000)
    # tech −15% hits only NVDA
    assert by_key["tech_shock"]["loss"] == pytest.approx(1_500)
    # broad −10% hits both
    assert by_key["broad_market"]["loss"] == pytest.approx(2_000)
    assert by_key["broad_market"]["status"] in (
        "OK", "WATCH", "REDUCE", "HALT")


def test_pm_decide_matrix():
    # hard veto first — new idea BLOCKed pre-trade
    assert re_.pm_decide(thesis="VALID", valuation="ATTRACTIVE",
                         technical="CONFIRMED", risk_check="BLOCK",
                         portfolio_check="PASS")["decision"] == "BLOCK"
    # spec §25 matrix: same hard breach on a HELD position → REDUCE,
    # not BLOCK (you can't block what you already hold — you de-risk)
    assert re_.pm_decide(thesis="VALID", valuation="ATTRACTIVE",
                         technical="CONFIRMED", risk_check="PASS",
                         portfolio_check="BLOCK",
                         has_position=True)["decision"] == "REDUCE"
    # all green + no position → ENTER
    assert re_.pm_decide(thesis="VALID", valuation="ATTRACTIVE",
                         technical="CONFIRMED", risk_check="PASS",
                         portfolio_check="PASS")["decision"] == "ENTER"
    # thesis invalid → EXIT regardless of P&L
    assert re_.pm_decide(thesis="INVALID", valuation="ATTRACTIVE",
                         technical="CONFIRMED", risk_check="PASS",
                         portfolio_check="PASS",
                         has_position=True)["decision"] == "EXIT"
    # position + valid + capacity + target hit → ADD
    assert re_.pm_decide(thesis="VALID", valuation="ATTRACTIVE",
                         technical="CONFIRMED", risk_check="PASS",
                         portfolio_check="PASS", has_position=True,
                         target_hit=True)["decision"] == "ADD"
    # position + valid + normal → HOLD
    assert re_.pm_decide(thesis="VALID", valuation="REVIEW",
                         technical="CONFIRMED", risk_check="PASS",
                         portfolio_check="PASS",
                         has_position=True)["decision"] == "HOLD"
    # concentration → REDUCE
    assert re_.pm_decide(thesis="VALID", valuation="ATTRACTIVE",
                         technical="CONFIRMED", risk_check="PASS",
                         portfolio_check="PASS", has_position=True,
                         concentration_high=True)["decision"] == "REDUCE"
    # ambiguous → REVIEW
    assert re_.pm_decide(thesis="REVIEW", valuation="REVIEW",
                         technical="WEAKENING", risk_check="REVIEW",
                         portfolio_check="PASS")["decision"] == "REVIEW"


def test_check_order_new_hard_rules():
    """spec §24 hard rules: stop_invalid, insufficient margin,
    strategy risk, asset risk — all block."""
    pf = {"nav": 100_000, "cash": 50_000,
          "positions": [{"symbol": "AMD", "market_value": 10_000,
                         "sector": "Tech", "open_risk": 4_000,
                         "strategy": "swing"}],
          "open_risk": 4_000, "margin_used": 10_000}
    # stop on wrong side of entry → stop_invalid BLOCK
    r = re_.check_order(
        {"symbol": "NVDA", "side": "buy", "sector": "Tech",
         "notional": 1_000, "stop_invalid": True}, pf)
    rules = [b["rule"] for b in r["breaches"]]
    assert "stop_invalid" in rules and not r["allowed"]
    # margin required > free margin (equity − used = 90k) → BLOCK
    r = re_.check_order(
        {"symbol": "NVDA", "side": "buy", "sector": "Tech",
         "notional": 1_000, "margin": 95_000}, pf)
    assert "insufficient_margin" in [b["rule"] for b in r["breaches"]]
    # asset open risk 4k + new 2k = 6k > 5% of 100k → max_asset_risk
    r = re_.check_order(
        {"symbol": "AMD", "side": "buy", "sector": "Tech",
         "notional": 1_000, "risk_dollars": 2_000}, pf)
    assert "max_asset_risk" in [b["rule"] for b in r["breaches"]]
    # same-strategy risk 4k + 7k > 10% → max_strategy_risk
    r = re_.check_order(
        {"symbol": "NVDA", "side": "buy", "sector": "Tech",
         "notional": 1_000, "risk_dollars": 7_000,
         "strategy": "swing"}, pf)
    assert "max_strategy_risk" in [b["rule"] for b in r["breaches"]]


def test_trade_risk_decision_input_and_stop_invalid():
    eq = 50_000
    # missing input → INPUT (workbook rule)
    s = re_.trade_risk_sheet(entry=None, stop=100, qty=10, equity=eq)
    assert re_.trade_risk_decision(s, eq)["decision"] == "INPUT"
    # long stop above entry → BLOCK (hard rule, not silent PASS)
    s = re_.trade_risk_sheet(entry=100, stop=110, qty=10, equity=eq)
    assert s["stop_invalid"] is True
    assert re_.trade_risk_decision(s, eq)["decision"] == "BLOCK"


def test_equity_stats_drawdown_panel():
    """spec §20 Panel 8: peak/current/daily/weekly/monthly/max DD."""
    hist = [{"t": "2025-09-01T00:00:00", "equity": 50_000},
            {"t": "2025-09-20T00:00:00", "equity": 55_000},
            {"t": "2025-10-01T00:00:00", "equity": 60_000}]
    s = re_.equity_stats(hist, 57_000)
    assert s["peak_equity"] == 60_000
    assert s["current_dd_pct"] == pytest.approx(57_000/60_000 - 1)
    assert s["mdd_pct"] is not None
    assert "weekly_dd_pct" in s and "monthly_dd_pct" in s


# ── direction reversal — doc: "for shorts, direction must be
# reversed" ──

def test_short_pyramid_levels_reversed():
    t = re_.create_pyramid("S", 100_000, 100, 10, 100_000,
                           direction="short")
    assert t.stop == pytest.approx(100 + 15)      # stop ABOVE entry
    assert t.target1 == pytest.approx(100 - 30)   # target BELOW
    assert t.direction == "short"


def test_short_stop_ratchet_and_breach():
    t = re_.create_pyramid("S", 100_000, 100, 10, 100_000,
                           direction="short")
    # price falls → stop ratchets DOWN (95+15=110 < 115)
    re_.advance(t, 80, 10)
    assert t.stop == pytest.approx(95)
    # a later rally must NOT widen it
    re_.advance(t, 88, 10)
    assert t.stop == pytest.approx(95)
    # close above the stop → STOPPED, loss per share = fill − entry
    r = re_.advance(t, 97, 10)
    assert r["state"] == re_.PyramidState.STOPPED
    assert r["loss_per_share"] == pytest.approx(-3.0)


def test_short_target_adds_leg_downward():
    t = re_.create_pyramid("S", 100_000, 100, 10, 100_000,
                           direction="short")
    r = re_.advance(t, 68, 10)          # ≤ target1 70 → add
    assert r["state"] in (re_.PyramidState.TARGET1,
                          re_.PyramidState.ADDITION)
    assert t.additions == 1
    assert t.target1 == pytest.approx(68 - 30)   # ladder walks DOWN
    assert t.leg_fills[-1]["fill"] == pytest.approx(68)


def test_sleeve_gates_apply_to_sell_side():
    """A sleeve-tagged sell is a SHORT ENTRY — still new exposure; the
    capacity gates can't be dodged by flipping the side."""
    pf = _pf()
    pf["sleeve"] = {
        "enabled": True, "cooldown": True,
        "state": re_.sleeve_state(100_000, 0,
                                re_.sleeve_config(
                                    re_.Limits(values={
                                        "sleeve_enabled": True}))),
        "config": re_.sleeve_config(
            re_.Limits(values={"sleeve_enabled": True})),
        "positions": []}
    r = re_.check_order(
        {"symbol": "X", "side": "sell", "sector": "Tech",
         "notional": 10_000, "sleeve": True}, pf)
    assert not r["allowed"]
    assert "sleeve_cooldown" in [b["rule"] for b in r["breaches"]]


def test_sleeve_sector_cap_blocks():
    cfg = re_.sleeve_config(re_.Limits(values={
        "sleeve_enabled": True, "sleeve_max_sector_pct": 0.40}))
    st = re_.sleeve_state(100_000, 30_000, cfg)   # eff cap 150k → 60k
    pf = _pf()
    pf["sleeve"] = {"enabled": True, "state": st, "config": cfg,
                    "positions": [
                        {"symbol": "A", "sector": "Tech",
                         "market_value": 30_000}]}
    # 30k Tech + 35k order = 65k > 60k cap
    r = re_.check_order(
        {"symbol": "B", "side": "buy", "sector": "Tech",
         "notional": 35_000, "sleeve": True}, pf)
    assert not r["allowed"]
    assert "sleeve_sector" in [b["rule"] for b in r["breaches"]]
    # a different sector clears the cap
    r2 = re_.check_order(
        {"symbol": "C", "side": "buy", "sector": "Energy",
         "notional": 35_000, "sleeve": True}, pf)
    assert "sleeve_sector" not in [b["rule"] for b in r2["breaches"]]


def test_sleeve_sizing_sector_cap_binds():
    cfg = re_.sleeve_config(re_.Limits(values={
        "sleeve_enabled": True, "sleeve_max_sector_pct": 0.40}))
    st = re_.sleeve_state(100_000, 30_000, cfg)
    sz = re_.sleeve_sizing(100, 10, st, cfg, sector_gross=60_000)
    # sector room is 60k − 60k = 0 → no shares
    assert sz["shares"] == 0
    assert sz["binding"] == "sector"
