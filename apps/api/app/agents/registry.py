"""Agent registry — explicit tool permissions per agent. An agent may
only consume engine outputs listed in `tools`; it cannot call the
order gate, modify the mandate, or approve anything."""

AGENTS: dict[str, dict] = {
    "fundamental": {
        "layer": "research", "name": "Fundamental Agent",
        "tools": ["green_zone", "finmetrics", "fundamentals_query"],
        "question": "Is this a good business?",
        "prompt_version": "fundamental/v1.0",
    },
    "valuation": {
        "layer": "research", "name": "Valuation Agent",
        "tools": ["valuation_engine", "sector_valuation"],
        "question": "What is it worth?",
        "prompt_version": "valuation/v1.0",
    },
    "rule_one": {
        "layer": "research", "name": "Rule #1 Agent",
        "tools": ["valuation_engine.five_numbers", "valuation_engine.sticker_price"],
        "question": "Does it pass the Four Ms + Five Numbers?",
        "prompt_version": "rule_one/v1.0",
    },
    "quant": {
        "layer": "research", "name": "Quant Agent",
        "tools": ["quant_service.instrument_metrics", "quant.correlation"],
        "question": "What does the statistical evidence show?",
        "prompt_version": "quant/v1.0",
    },
    "macro": {
        "layer": "research", "name": "Macro Agent",
        "tools": ["macro_regime.classify", "macro.sector_rotation"],
        "question": "What regime are we in?",
        "prompt_version": "macro/v1.0",
    },
    "technical": {
        "layer": "research", "name": "Technical Agent",
        "tools": ["technical_engine.evaluate"],
        "question": "Is the timing right?",
        "prompt_version": "technical/v1.0",
    },
    "risk": {
        "layer": "control", "name": "Risk Manager Agent",
        "tools": ["risk_engine.check_order", "risk_engine.risk_dimensions"],
        "question": "What could go wrong? (absolute veto)",
        "prompt_version": "risk/v1.0",
    },
    "pm": {
        "layer": "capital", "name": "Portfolio Manager Agent",
        "tools": ["risk_engine.check_order", "quant.portfolio_exposure"],
        "question": "Is this a good allocation?",
        "prompt_version": "pm/v1.0",
    },
    "cio": {
        "layer": "executive", "name": "CIO Agent",
        "tools": ["committee.synthesize"],  # consumes reports only
        "question": "What is the decision?",
        "prompt_version": "cio/v1.0",
    },
}

MODEL_ID = "deterministic/vaiip-engines-1.0"  # no LLM configured;
# structured reports generated from engine outputs only
