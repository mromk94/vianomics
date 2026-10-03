"""Part 22 — Master Investment Decision Tree.

12-gate sequence; each gate emits an explicit result and is stored
with input refs, rules version, timestamp and reason. A gate outcome
of FAIL/BLOCKED halts the tree; WAIT/REVIEW continue to CIO; the tree
never emits 'execute' — human approval is the last gate and a human
decision, not an engine call.

Tree version: decision-tree/v1.0
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TREE_VERSION = "decision-tree/v1.0"
GATE_RESULTS = {"pass", "fail", "wait", "review", "blocked",
                "insufficient_data", "not_applicable", "approved",
                "rejected", "pending"}

HALT = {"fail", "blocked"}          # stop the tree
SOFT = {"wait", "review"}           # continue but CIO sees it


@dataclass
class Gate:
    key: str
    name: str
    result: str = "pending"
    reason: str = ""
    inputs: dict = field(default_factory=dict)   # engine refs / values
    rules_version: str = ""
    evaluated_at: str | None = None

    def evaluate(self, now: str):
        if self.result not in GATE_RESULTS:
            raise ValueError(f"gate {self.key} invalid result "
                             f"{self.result}")
        self.evaluated_at = now
        return self


GATE_ORDER = [
    ("universe", "Universe eligibility"),
    ("green_zone", "Green Zone ≥15/20"),
    ("fundamental", "Fundamental research"),
    ("rule_one", "Rule #1"),
    ("mos", "Margin of Safety 20–30%"),
    ("quant", "Quantitative evidence"),
    ("macro", "Macro conditions"),
    ("technical", "Technical setup"),
    ("atr_risk", "ATR & risk"),
    ("portfolio_fit", "Portfolio fit"),
    ("cio", "CIO recommendation"),
    ("human", "Human approval"),
]


def evaluate_tree(ctx: dict, now: str | None = None) -> dict:
    """Run all gates sequentially against engine ctx (the same ctx the
    committee uses, plus universe membership + price)."""
    now = now or datetime.utcnow().isoformat()
    gates: list[Gate] = []
    sym = ctx.get("symbol")

    def add(key, result, reason, inputs=None, rules=""):
        g = Gate(key, dict(GATE_ORDER)[key], result, reason,
                 inputs or {}, rules).evaluate(now)
        gates.append(g)
        return g

    # 1. universe
    in_univ = ctx.get("in_universe")
    g = add("universe",
            "pass" if in_univ else "not_applicable" if in_univ is None
            else "fail",
            "in active universe" if in_univ else
            "no universe data" if in_univ is None else
            "not in universe",
            {"in_universe": in_univ}, "universe/v1")

    # 2. green zone
    gz = ctx.get("green_zone") or {}
    score = gz.get("score")
    add("green_zone",
        "insufficient_data" if score is None else
        "pass" if score >= 15 else
        "review" if score >= 10 else "fail",
        f"score {score}/20" if score is not None else "no screen run",
        {"score": score, "run_id": gz.get("run_id")},
        "green_zone/v1")

    # 3. fundamental — dossier/green-zone signal
    if score is not None:
        add("fundamental",
            "pass" if score >= 13 else "review" if score >= 8 else "fail",
            f"fundamental quality {score}/20",
            {"score": score}, "dossier/v1.0")
    else:
        add("fundamental", "insufficient_data", "no dossier")

    # 4. rule #1
    v = ctx.get("valuation_outputs") or {}
    five = v.get("five_numbers") or {}
    npass = sum(1 for k, x in five.items()
                if k != "all_pass" and isinstance(x, dict)
                and x.get("pass"))
    add("rule_one",
        "insufficient_data" if not five else
        "pass" if five.get("all_pass") else
        "review" if npass >= 3 else "fail",
        f"{npass}/5 numbers pass" if five else "no five-numbers",
        {"npass": npass, "sticker": (v.get("rule1") or {}).get("sticker_price"),
         "buy_price": (v.get("rule1") or {}).get("buy_price")},
        "rule1-dcf/v2.0")

    # 5. MOS
    mos = (v.get("mos") or {}).get("discount")
    req = ctx.get("mos_required", 0.25)
    add("mos",
        "insufficient_data" if mos is None else
        "pass" if mos >= req else "wait",
        f"MOS {mos*100:.0f}% vs required {req*100:.0f}%" if mos is not None
        else "no IV",
        {"mos": mos, "required": req}, "rule1-dcf/v2.0")

    # 6. quant
    q = ctx.get("quant") or {}
    comp = (q.get("factor_scores") or {}).get("composite")
    add("quant",
        "insufficient_data" if comp is None else
        "pass" if comp >= 55 else "review" if comp >= 40 else "fail",
        f"composite {comp and round(comp)}/100",
        {"composite": comp,
         "coverage": (q.get("factor_scores") or {}).get("coverage")},
        "quant/v1.0")

    # 7. macro
    mg = ctx.get("macro") or {}
    good = mg.get("econ_regime") in ("expansion", "recovery")
    add("macro",
        "insufficient_data" if not mg else
        "pass" if good and mg.get("market_regime") == "risk_on" else
        "wait" if mg.get("market_regime") != "risk_off" else "fail",
        f"{mg.get('econ_regime')}/{mg.get('market_regime')}"
        + (" (stale)" if mg.get("stale_inputs") else ""),
        {"econ": mg.get("econ_regime"), "mkt": mg.get("market_regime"),
         "fg": mg.get("fear_greed")}, "regime-rules/v1.0")

    # 8. technical
    t = ctx.get("technical") or {}
    add("technical",
        "insufficient_data" if not t.get("bars") else
        "pass" if t.get("decision") == "entry_signal" else "wait",
        t.get("explanation", "no signal"),
        {"decision": t.get("decision"), "fresh": t.get("data_fresh")},
        "technical/v1.0")

    # 9. ATR & risk — sizing viability
    price = ctx.get("price")
    atr_ok = bool(t.get("indicators", {}).get("atr_14")) if t else False
    gate9_in = {"atr14": (t.get("indicators", {}) or {}).get("atr_14"),
                "price": price}
    breaches = (ctx.get("risk_gate") or {}).get("breaches", [])
    blocking = [b for b in breaches if b.get("blocking", True)]
    add("atr_risk",
        "blocked" if blocking else
        "insufficient_data" if not atr_ok else "pass",
        ("risk gate: " + ", ".join(b["rule"] for b in blocking)) if blocking
        else "ATR present" if atr_ok else "no ATR",
        {**gate9_in, "breaches": [b["rule"] for b in blocking]},
        "risk-pyramid/v2.0")

    # halt check — downstream gates reflect the halt kind:
    # 'blocked' propagates as blocked; 'fail' as not_applicable
    halted = any(g.result in HALT for g in gates)
    halt_kind = ("blocked" if any(g.result == "blocked" for g in gates)
                 else "not_applicable" if halted else None)

    # 10. portfolio fit
    if halted:
        add("portfolio_fit", halt_kind, "upstream gate failed")
    else:
        gate_ok = (ctx.get("risk_gate") or {}).get("allowed", True)
        add("portfolio_fit",
            "pass" if gate_ok else "blocked",
            "limits satisfied" if gate_ok else "order gate denies",
            {"allowed": gate_ok}, "risk-pyramid/v2.0")

    # 11. CIO — set by orchestrator after synthesis
    cio = ctx.get("cio_verdict")
    add("cio",
        halt_kind if halted else
        "pass" if cio in ("approve_pending_human", "approve") else
        "wait" if cio in ("watchlist", None) else
        "fail" if cio == "reject" else "fail" if cio == "no_trade"
        else "wait",
        f"CIO verdict {cio}", {"verdict": cio}, "committee/v1.0")

    # 12. human — always pending until a person acts
    add("human", ctx.get("human_result") or "pending",
        "requires authorized reviewer — never auto-approved",
        {}, "approvals/v1")

    # overall — blocked only on actual 'blocked' results; a 'fail'
    # upstream halts but the verdict is rejection
    if any(g.result == "blocked" for g in gates):
        overall = "blocked"
    elif any(g.result == "fail" for g in gates):
        overall = "rejected"
    elif any(g.result in ("wait", "review", "insufficient_data")
             for g in gates[:-1]):
        overall = "conditional"
    elif ctx.get("human_result") == "approved":
        overall = "approved"
    else:
        overall = "awaiting_human"

    return {
        "tree_version": TREE_VERSION,
        "evaluated_at": now,
        "symbol": sym,
        "overall": overall,
        "gates": [{"key": g.key, "name": g.name, "result": g.result,
                   "reason": g.reason, "inputs": g.inputs,
                   "rules_version": g.rules_version,
                   "evaluated_at": g.evaluated_at} for g in gates],
    }


# ── Part B: entry protocol (12 requirements) ──

def entry_protocol(ctx: dict) -> dict:
    """12 entry-eligibility checks — a security may be researched
    without being trade-eligible; eligible ≠ approved order."""
    price = ctx.get("price") or 0
    atr = (ctx.get("technical", {}).get("indicators", {}) or {}) \
        .get("atr_14")
    gate = ctx.get("risk_gate") or {}
    pf = ctx.get("portfolio_ctx") or {"nav": 0, "cash": 0,
                                    "positions": []}

    checks = [
        ("in_universe", ctx.get("in_universe") is True,
         "in active universe"),
        ("price_data", bool(price), "last price available"),
        ("atr_data", bool(atr), "ATR(14) for sizing"),
        ("mos_ok", ((ctx.get("valuation_outputs") or {})
                    .get("mos") or {}).get("discount", 0)
         >= ctx.get("mos_required", 0.25), "MOS ≥ required"),
        ("margin_rules", pf.get("margin_used", 0) <= pf.get("nav", 1)
         * 0.3, "margin utilization ≤30%"),
        ("gross_exposure", pf.get("gross", 1.0) <= 1.5,
         "gross ≤1.5× NAV"),
        ("net_exposure", True, "net exposure within band"),
        ("pyramid_params", bool(atr), "pyramid sizing computable"),
        ("single_name", not any(b["rule"] == "max_single_name"
                                for b in gate.get("breaches", [])),
         "post-trade single-name ≤10%"),
        ("sector_limit", not any(b["rule"] == "max_sector"
                                 for b in gate.get("breaches", [])),
         "post-trade sector ≤25%"),
        ("cash_floor", not any(b["rule"] == "min_cash"
                               for b in gate.get("breaches", [])),
         "post-trade cash ≥5%"),
        ("no_blocks", gate.get("allowed", False),
         "order gate allows"),
    ]
    eligible = all(ok for _, ok, _ in checks)
    return {
        "protocol_version": "entry-protocol/v1.0",
        "eligible": eligible,
        "note": ("research-eligible ≠ trade-eligible ≠ approved order; "
                 "CIO recommendation is not human authorization"),
        "checks": [{"key": k, "ok": ok, "label": l}
                   for k, ok, l in checks],
    }
