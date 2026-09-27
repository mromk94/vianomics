"""Deterministic agent analyzers — each consumes a bounded ctx of
engine outputs (tool permissions enforced by what the orchestrator
puts in the agent's view) and returns a validated AgentReport dict.
No LLM is configured; model_id records the deterministic generator."""

import uuid
from datetime import datetime

from app.agents.registry import AGENTS, MODEL_ID


def _base(agent_key: str, ticker: str) -> dict:
    a = AGENTS[agent_key]
    return {
        "execution_id": str(uuid.uuid4()),
        "agent_key": agent_key,
        "layer": a["layer"],
        "ticker": ticker,
        "model_id": MODEL_ID,
        "prompt_version": a["prompt_version"],
        "produced_at": datetime.now(),
        "key_evidence": [], "pass_criteria": [], "fail_criteria": [],
        "key_risks": [], "contradictions": [], "assumptions": [],
        "data_gaps": [], "required_followup": [],
    }


def _ev(claim, table=None, ref=None, note=None):
    return {"claim": claim, "ref_table": table, "ref_id": ref,
            "note": note}


# ── research layer ──

def fundamental(ctx: dict) -> dict:
    gz = ctx.get("green_zone") or {}
    score = gz.get("score") or 0
    passed = score >= 13
    r = _base("fundamental", ctx["symbol"])
    r.update({
        "engine_refs": ["green_zone/v1"],
        "data_quality": "high" if score else "insufficient",
        "recommendation": "pass" if passed else "wait",
        "score": min(100, score * 5),
        "confidence": 0.9 if score else 0.3,
        "key_evidence": [_ev(f"Green Zone {score}/20 ("
                             f"{gz.get('status','?')})",
                             "screening_results", gz.get("run_id"))],
        "pass_criteria": ["Green Zone ≥13/20", "moat present or review"],
        "fail_criteria": ["Green Zone <13", "deteriorating balance sheet"],
        "key_risks": ["single data provider (EDGAR)"] if score else
                     ["no fundamental data"],
        "data_gaps": [] if score else ["fundamental observations"],
        "conclusion": (f"Green Zone {score}/20 — "
                       f"{'qualified' if passed else 'below threshold'}"),
    })
    return r


def valuation(ctx: dict) -> dict:
    v = ctx.get("valuation_outputs") or {}
    iv = (v.get("dcf") or {}).get("per_share")
    mos = (v.get("mos") or {}).get("discount")
    price = ctx.get("price")
    r = _base("valuation", ctx["symbol"])
    has = iv is not None
    sens = v.get("sensitivity")
    r.update({
        "engine_refs": ["rule1-dcf/v1.0"],
        "data_quality": "high" if has else "insufficient",
        "recommendation": "buy" if (mos and mos > 0.25) else
                          "wait" if has else "insufficient_data",
        "score": max(0.0, min(100, 50 + (mos or 0) * 100)) if has else 10,
        "confidence": 0.8 if has else 0.2,
        "key_evidence": [_ev(f"DCF IV/share ${iv:.2f}" if has else
                             "no DCF", None, None),
                         _ev(f"sticker ${v.get('rule1', {}).get('sticker_price'):.2f}"
                             if v.get('rule1', {}).get('sticker_price') else
                             "no sticker")],
        "pass_criteria": ["MOS ≥25%", "IV > price"],
        "fail_criteria": ["IV < price", "terminal sensitivity extreme"],
        "assumptions": ["growth & discount rates are user inputs — "
                        "not verified"],
        "data_gaps": [] if has else ["positive FCF for DCF"],
        "conclusion": (f"IV ${iv:.2f} vs ${price} → "
                       f"{mos*100:.0f}% MOS" if has else
                       "insufficient data for DCF"),
    })
    if sens:
        r["key_evidence"].append(_ev(
            f"sensitivity grid {len(sens['growths'])}×"
            f"{len(sens['discount_rates'])}"))
    return r


def rule_one(ctx: dict) -> dict:
    v = ctx.get("valuation_outputs") or {}
    five = v.get("five_numbers") or {}
    r1 = v.get("rule1") or {}
    npass = sum(1 for k, x in five.items()
                if k != "all_pass" and x.get("pass"))
    r = _base("rule_one", ctx["symbol"])
    r.update({
        "engine_refs": ["rule1-dcf/v1.0"],
        "data_quality": "high" if npass >= 4 else "medium",
        "recommendation": "pass" if five.get("all_pass") else "wait",
        "score": npass * 20,
        "confidence": 0.7,
        "key_evidence": [_ev(f"Five Numbers {npass}/5 pass"),
                         _ev(f"sticker {r1.get('sticker_price')}, "
                             f"buy {r1.get('buy_price')}")],
        "pass_criteria": ["all Five Numbers >10%, ROIC>15%",
                          "sticker > price"],
        "fail_criteria": ["any number <10%", "ROIC<15%"],
        "conclusion": f"{npass}/5 numbers pass; "
                      f"sticker ${r1.get('sticker_price', 0):.0f}",
    })
    return r


def quant(ctx: dict) -> dict:
    m = (ctx.get("quant") or {}).get("metrics") or {}
    fs = (ctx.get("quant") or {}).get("factor_scores") or {}
    r = _base("quant", ctx["symbol"])
    comp = fs.get("composite")
    r.update({
        "engine_refs": ["quant/v1.0"],
        "data_quality": "high" if comp is not None else "low",
        "recommendation": "pass" if (comp or 0) >= 55 else "wait",
        "score": comp or 0,
        "confidence": fs.get("coverage", 0) or 0.3,
        "key_evidence": [
            _ev(f"Sharpe {m.get('sharpe')}", "ohlcv_bars"),
            _ev(f"β {m.get('beta', {}).get('beta') if m.get('beta') else None}"),
            _ev(f"factor composite {comp}", None)],
        "assumptions": ["rf=4%, daily simple returns, SPY benchmark"],
        "key_risks": ["delayed EOD data — not for intraday signals"],
        "conclusion": f"composite {comp and round(comp)}/100, "
                      f"coverage {fs.get('coverage', 0)*100:.0f}%",
    })
    return r


def macro(ctx: dict) -> dict:
    mg = ctx.get("macro") or {}
    econ, mkt = mg.get("econ_regime"), mg.get("market_regime")
    r = _base("macro", ctx["symbol"])
    good = econ in ("expansion", "recovery") and mkt == "risk_on"
    r.update({
        "engine_refs": ["regime-rules/v1.0"],
        "data_quality": "low" if mg.get("stale_inputs") else "medium",
        "recommendation": "pass" if good else "wait",
        "score": 70 if good else 40,
        "confidence": 0.6 if mg.get("stale_inputs") else 0.8,
        "key_evidence": [_ev(f"econ {econ} / market {mkt}"),
                         _ev(f"F&G {mg.get('fear_greed')} → "
                             f"{mg.get('overlay')}")],
        "key_risks": mg.get("stale_inputs") and
                     [f"stale: {', '.join(mg['stale_inputs'])}"] or [],
        "assumptions": ["fredgraph latest-vintage", "F&G proxy composite"],
        "conclusion": f"{econ} + {mkt}; overlay {mg.get('overlay')}",
    })
    return r


def technical(ctx: dict) -> dict:
    t = ctx.get("technical") or {}
    dec = t.get("decision")
    r = _base("technical", ctx["symbol"])
    r.update({
        "engine_refs": ["technical/v1.0"],
        "data_quality": "high" if t.get("data_fresh") else "low",
        "recommendation": {"entry_signal": "buy", "wait": "wait",
                           "invalid_data": "insufficient_data"}.get(dec, "wait"),
        "score": {"entry_signal": 80, "wait": 45,
                  "invalid_data": 0}.get(dec, 0),
        "confidence": 0.7 if t.get("data_fresh") else 0.3,
        "key_evidence": [
            _ev(f"MR: {t.get('mean_reversion', {}).get('decision')}"),
            _ev(f"TF: {t.get('trend_following', {}).get('decision')}")],
        "key_risks": [] if t.get("data_fresh") else
                     [t.get("freshness_note", "stale bars")],
        "conclusion": t.get("explanation", ""),
    })
    return r


# ── control layer ──

def risk_manager(ctx: dict) -> dict:
    """Independent veto. check_order result + dimensions — BLOCK on
    any blocking breach. Never approves, only gates."""
    gate = ctx.get("risk_gate") or {}
    breaches = gate.get("breaches", [])
    blocking = [b for b in breaches if b.get("blocking", True)]
    blocked = bool(blocking) or not gate.get("allowed", True)
    r = _base("risk", ctx["symbol"])
    r.update({
        "engine_refs": ["risk-pyramid/v1.0"],
        "data_quality": "high",
        "recommendation": "block" if blocked else "pass",
        "score": 0 if blocked else 80,
        "confidence": 1.0,   # deterministic gate — certain
        "key_evidence": [
            _ev(f"{b['rule']}: {b.get('observed')} vs {b.get('required')}",
                "risk_checks", ctx.get("risk_check_id"))
            for b in breaches],
        "fail_criteria": [b["rule"] for b in breaches],
        "key_risks": [b.get("remediation", "") for b in blocking],
        "conclusion": ("BLOCK — " + "; ".join(b["rule"] for b in blocking))
                      if blocked else "PASS — within limits",
    })
    return r


# ── capital layer ──

def portfolio_manager(ctx: dict) -> dict:
    """Allocation lens: new candidate vs marginal existing holding —
    opportunity cost, sector fit, concentration, liquidity."""
    gate = ctx.get("risk_gate") or {}
    valuation = ctx.get("valuation_outputs") or {}
    mos = (valuation.get("mos") or {}).get("discount")
    exp_ret = mos  # expected return proxy = discount to IV
    sector = ctx.get("sector")
    sector_w = None  # would come from portfolio ctx
    r = _base("pm", ctx["symbol"])
    opp_cost = "marginal existing holding comparison — no positions loaded"
    r.update({
        "engine_refs": ["risk-pyramid/v1.0", "rule1-dcf/v1.0"],
        "data_quality": "medium",
        "recommendation": "pass" if (mos or 0) > 0.2 and
                          gate.get("allowed") else "wait",
        "score": max(0.0, min(100, 40 + (mos or 0) * 100)),
        "confidence": 0.6,
        "key_evidence": [
            _ev(f"expected return proxy (MOS) {mos and round(mos*100,1)}%"),
            _ev("portfolio fit: sector/gross constrained by gate")],
        "pass_criteria": ["candidate return > marginal holding return",
                          "within sector/name limits"],
        "fail_criteria": ["better alternative exists", "concentration breach"],
        "assumptions": [opp_cost],
        "conclusion": (f"allocation lens: MOS {mos and round(mos*100,0)}% "
                       f"{'favorable' if (mos or 0)>0.2 else 'thin'}"),
    })
    return r
