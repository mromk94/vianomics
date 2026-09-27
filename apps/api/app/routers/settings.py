"""Configuration status — shows WHICH keys are set, never values."""

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])

# label, env var, where to get it, what it unlocks
KEY_SPECS = [
    ("Tiingo API key", "TIINGO_API_KEY",
     "tiingo.com → free account → API token",
     "Daily bars for all instruments"),
    ("FRED API key", "FRED_API_KEY",
     "fred.stlouisfed.org → My Account → API Keys",
     "Macro series: CPI, rates, PMI, unemployment"),
    ("SEC EDGAR email", "EDGAR_USER_AGENT / SEC_EDGAR_EMAIL",
     "no key — SEC asks for a contact email",
     "Company fundamentals (10-K/10-Q XBRL)"),
    ("Interactive Brokers host", "IBKR_HOST",
     "TWS/Gateway → API settings → trusted IP",
     "Live broker connection (read-only until enabled)"),
    ("IBKR port", "IBKR_PORT", "TWS: 7497 paper / 7496 live",
     "Live broker connection"),
    ("IBKR client id", "IBKR_CLIENT_ID", "any integer, e.g. 7",
     "Live broker connection"),
    ("Execution enabled", "EXECUTION_ENABLED",
     "set 'true' only after TWS verified + approval flow tested",
     "Real order submission — keep false for paper"),
    ("Admin password", "ADMIN_PASSWORD",
     "set at first seed",
     "Admin sign-in"),
]

MODEL_SPECS = [
    ("Agents", "deterministic",
     "rule-based today — an LLM provider (e.g. ANTHROPIC_API_KEY) can "
     "be added later; agents stay inside the same evidence schema"),
    ("CIO synthesis", "deterministic",
     "weighted blend — no external model required"),
    ("Backtest engine", "backtest/v1.0",
     "deterministic event-driven"),
]


@router.get("/env")
async def env_status():
    """Which env keys exist — booleans only, values never leave the
    server."""
    import os
    s = get_settings()
    out = []
    for label, var, where, unlocks in KEY_SPECS:
        if var == "EXECUTION_ENABLED":
            is_set = s.execution_enabled
        elif var == "ADMIN_PASSWORD":
            is_set = bool(os.environ.get("ADMIN_PASSWORD"))
        else:
            is_set = any(os.environ.get(v) for v in var.split(" / "))
        out.append({"label": label, "var": var, "set": is_set,
                    "where": where, "unlocks": unlocks})
    return {
        "env": out,
        "models": [{"area": a, "current": c, "notes": n}
                   for a, c, n in MODEL_SPECS],
        "execution_enabled": s.execution_enabled,
        "execution_broker": s.execution_broker,
        "demo_fixtures": s.demo_fixtures,
    }
