"""Layer-IV trading sleeve — V2 doc worked examples.

Doc config: $30k sleeve equity (30% of $100k), 5X target leverage,
20% initial margin, 5 positions, 20% gross per asset, starter = ¼ of
max, portfolio stop = 20% of sleeve equity = $6,000.
"""
import pytest

from app.services import risk_engine as re_


@pytest.fixture
def cfg():
    lim = re_.Limits(values={
        "sleeve_enabled": True, "sleeve_pct": 0.30,
        "sleeve_target_leverage": 5.0, "sleeve_initial_margin": 0.20,
        "sleeve_maint_margin": 0.16, "sleeve_max_positions": 5,
        "sleeve_max_asset_gross_pct": 0.20,
        "sleeve_starter_fraction": 0.25,
        "sleeve_portfolio_stop_pct": 0.20})
    return re_.sleeve_config(lim)


def test_sleeve_state_doc_numbers(cfg):
    st = re_.sleeve_state(100_000, 0, cfg)
    assert st["sleeve_equity"] == pytest.approx(30_000)
    assert st["gross_cap"] == pytest.approx(150_000)
    assert st["max_asset_notional"] == pytest.approx(30_000)
    assert st["starter_notional"] == pytest.approx(7_500)
    assert st["portfolio_stop_usd"] == pytest.approx(6_000)
    # per-trade risk budget = stop ÷ max positions
    assert st["per_trade_risk_budget"] == pytest.approx(1_200)
    # 16% maint → buffer exactly at 5X: (30k − 6k) / 0.16 = 150k —
    # the doc's boundary case: call and stop coincide → flagged
    assert st["max_gross_safe"] == pytest.approx(150_000)
    assert st["buffer_capped"] is True
    # strictly inside (15%) → real headroom → not capped
    st = re_.sleeve_state(100_000, 0, cfg, maint_margin=0.15)
    assert st["max_gross_safe"] == pytest.approx(160_000)
    assert st["buffer_capped"] is False


def test_margin_buffer_caps_leverage(cfg):
    # doc Step 2.3: maint ≥ ~16% means the 4% stop fires too late —
    # the cap must shrink. 20% maint → (30k−6k)/0.20 = $120k = 4X.
    st = re_.sleeve_state(100_000, 0, cfg, maint_margin=0.20)
    assert st["max_gross_safe"] == pytest.approx(120_000)
    assert st["effective_gross_cap"] == pytest.approx(120_000)
    assert st["buffer_capped"] is True
    # a 25% maint rate caps even harder — $96k = 3.2X
    st = re_.sleeve_state(100_000, 0, cfg, maint_margin=0.25)
    assert st["effective_gross_cap"] == pytest.approx(96_000)


def test_full_deployment_consumes_equity_as_margin(cfg):
    st = re_.sleeve_state(100_000, 150_000, cfg)
    assert st["used_margin"] == pytest.approx(30_000)   # 150k × 20%
    assert st["free_margin"] == pytest.approx(0)
    assert st["effective_leverage"] == pytest.approx(5.0)
    assert st["margin_utilisation"] == pytest.approx(1.0)


def test_sizing_starter_value_binds(cfg):
    # price 100, ATR 10 → risk shares 1200/15 = 80 > starter 75
    st = re_.sleeve_state(100_000, 0, cfg)
    sz = re_.sleeve_sizing(100, 10, st, cfg)
    assert sz["shares"] == 75
    assert sz["binding"] == "starter_value"
    assert sz["notional"] == pytest.approx(7_500)
    assert sz["stop_price"] == pytest.approx(85)          # 1.5×ATR
    assert sz["dollar_risk"] == pytest.approx(75 * 15)


def test_sizing_risk_budget_binds(cfg):
    # volatile: ATR 40 → risk shares 1200/60 = 20 < starter 75
    st = re_.sleeve_state(100_000, 0, cfg)
    sz = re_.sleeve_sizing(100, 40, st, cfg)
    assert sz["shares"] == 20
    assert sz["binding"] == "risk_budget"
    assert sz["dollar_risk"] == pytest.approx(20 * 60)    # = $1,200


def test_sizing_gross_and_margin_headroom(cfg):
    # sleeve already holds $120k gross at 20% maint → cap $120k,
    # free margin = 30k − 24k = 6k → margin allows 6k/(100×0.2)=300
    st = re_.sleeve_state(100_000, 120_000, cfg, maint_margin=0.20)
    sz = re_.sleeve_sizing(100, 10, st, cfg)
    assert sz["binding"] == "gross_cap"   # 0 headroom → 0 shares
    assert sz["shares"] == 0


def test_sizing_asset_cap(cfg):
    # already $28k gross in the asset → $2k headroom → 20 shares
    st = re_.sleeve_state(100_000, 28_000, cfg)
    sz = re_.sleeve_sizing(100, 10, st, cfg, asset_gross=28_000)
    assert sz["binding"] == "asset_gross"
    assert sz["shares"] == 20


def _pf_with_sleeve(cfg):
    st = re_.sleeve_state(100_000, 0, cfg)
    return {
        "nav": 100_000, "cash": 100_000, "positions": [],
        "open_risk": 0, "margin_used": 0,
        "sleeve": {"enabled": True, "config": cfg, "state": st,
                   "positions": []},
    }


def test_check_order_sleeve_gross_cap(cfg):
    pf = _pf_with_sleeve(cfg)
    st = pf["sleeve"]["state"]
    st["gross"] = 140_000   # $10k headroom under $150k cap
    order = {"side": "buy", "symbol": "NVDA", "sector": "tech",
             "notional": 20_000, "sleeve": True}
    out = re_.check_order(order, pf,
                          limits=re_.Limits(values={
                              **re_.DEFAULT_LIMITS,
                              "max_gross_leverage": 99,
                              "min_sectors": 0}))
    rules = {b["rule"] for b in out["breaches"]}
    assert "sleeve_gross_cap" in rules
    assert out["allowed"] is False


def test_check_order_sleeve_max_positions(cfg):
    pf = _pf_with_sleeve(cfg)
    st = pf["sleeve"]["state"]
    st["open_positions"] = 5
    pf["sleeve"]["positions"] = [
        {"symbol": s, "market_value": 1000}
        for s in ("A", "B", "C", "D", "E")]
    order = {"side": "buy", "symbol": "NEW", "sector": "tech",
             "notional": 5_000, "sleeve": True}
    out = re_.check_order(order, pf,
                          limits=re_.Limits(values={
                              **re_.DEFAULT_LIMITS,
                              "max_gross_leverage": 99,
                              "min_sectors": 0}))
    rules = {b["rule"] for b in out["breaches"]}
    assert "sleeve_max_positions" in rules


def test_check_order_sleeve_margin(cfg):
    pf = _pf_with_sleeve(cfg)
    st = pf["sleeve"]["state"]
    st["gross"] = 120_000                    # used margin 24k
    st["used_margin"] = 24_000
    st["free_margin"] = 6_000                # order needs 20% × notional
    st["effective_gross_cap"] = 150_000      # pretend buffer ok
    order = {"side": "buy", "symbol": "NVDA", "sector": "tech",
             "notional": 50_000, "sleeve": True}
    # order margin = 10k > free 6k → sleeve_margin breach
    out = re_.check_order(order, pf,
                          limits=re_.Limits(values={
                              **re_.DEFAULT_LIMITS,
                              "max_gross_leverage": 99,
                              "min_sectors": 0}))
    rules = {b["rule"] for b in out["breaches"]}
    assert "sleeve_margin" in rules


def test_non_sleeve_order_unaffected(cfg):
    pf = _pf_with_sleeve(cfg)
    order = {"side": "buy", "symbol": "NVDA", "sector": "tech",
             "notional": 1_000}              # no "sleeve" flag
    out = re_.check_order(order, pf,
                          limits=re_.Limits(values={
                              **re_.DEFAULT_LIMITS,
                              "min_sectors": 0}))
    assert not any(b["rule"].startswith("sleeve_")
                   for b in out["breaches"])
