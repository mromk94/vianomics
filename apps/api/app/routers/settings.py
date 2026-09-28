"""Configuration status — shows WHICH keys are set, never values."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.identity import User
from app.models.ops import ModelConfig, RuntimeFlag, SecretStore
from app.security import audit, require

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
async def env_status(db: AsyncSession = Depends(get_db)):
    """Which env keys exist — booleans only, values never leave the
    server."""
    import os
    s = get_settings()
    out = []
    for label, var, where, unlocks in KEY_SPECS:
        is_set = False
        if var == "EXECUTION_ENABLED":
            is_set = s.execution_enabled
        elif var == "ADMIN_PASSWORD":
            is_set = bool(os.environ.get("ADMIN_PASSWORD"))
        else:
            for v in var.split(" / "):
                st = (await db.execute(
                    select(SecretStore).where(SecretStore.key == v))
                ).scalar_one_or_none()
                if st or os.environ.get(v):
                    is_set = True
                    break
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


# ── model configs (Part E: user-selectable AI providers) ──


PROVIDERS = [
    {"id": "anthropic", "label": "Anthropic", "models": [
        "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
     "where": "console.anthropic.com → API Keys"},
    {"id": "openai", "label": "OpenAI", "models": [
        "gpt-5", "gpt-5-mini", "o4-mini"],
     "where": "platform.openai.com → API keys"},
    {"id": "gemini", "label": "Google Gemini", "models": [
        "gemini-2.5-pro", "gemini-2.5-flash"],
     "where": "aistudio.google.com → Get API key"},
    {"id": "deepseek", "label": "DeepSeek", "models": [
        "deepseek-chat", "deepseek-reasoner"],
     "where": "platform.deepseek.com → API keys"},
    {"id": "kimi", "label": "Moonshot Kimi", "models": [
        "kimi-k2", "moonshot-v1-128k"],
     "where": "platform.moonshot.ai → API keys"},
    {"id": "ollama", "label": "Local (Ollama / open models)", "models": [
        "llama3.3", "qwen3", "deepseek-r1"],
     "where": "ollama.com — runs on your machine, no key",
     "needs_base_url": True},
    {"id": "custom", "label": "Custom / Meta AI compatible endpoint",
     "models": [], "where": "any OpenAI-compatible base URL",
     "needs_base_url": True},
]


@router.get("/models")
async def model_catalog(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ModelConfig))).scalars().all()
    return {
        "providers": PROVIDERS,
        "configs": [
            {"id": c.id, "provider": c.provider, "label": c.label,
             "model": c.model,
             "key_set": bool(c.api_key),
             "key_masked": ("••••" + c.api_key[-4:]) if c.api_key else None,
             "base_url": c.base_url, "enabled": c.enabled,
             "is_default": c.is_default}
            for c in rows],
        "note": "keys are stored server-side and masked — never "
                "returned in full"}


class ModelIn(BaseModel):
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    label: str = ""
    is_default: bool = False


@router.post("/models", status_code=201)
async def upsert_model(body: ModelIn,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("admin:*"))):
    if body.provider not in {p["id"] for p in PROVIDERS}:
        raise HTTPException(400, f"unknown provider {body.provider}")
    c = ModelConfig(
        provider=body.provider, model=body.model,
        api_key=body.api_key, base_url=body.base_url,
        label=body.label or f"{body.provider}/{body.model}",
        is_default=body.is_default)
    if c.is_default:
        for r in (await db.execute(
                select(ModelConfig).where(ModelConfig.is_default))).scalars():
            r.is_default = False
    db.add(c)
    await db.flush()
    await audit(db, action="model.config.add", actor=user,
                entity_type="model_config", entity_id=c.id,
                detail={"provider": c.provider, "model": c.model})
    await db.commit()
    return {"id": c.id, "ok": True}


@router.post("/models/{config_id}/default")
async def set_default(config_id: str,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(require("admin:*"))):
    c = await db.get(ModelConfig, config_id)
    if c is None:
        raise HTTPException(404, "config not found")
    for r in (await db.execute(
            select(ModelConfig).where(ModelConfig.is_default))).scalars():
        r.is_default = False
    c.is_default = True
    await db.commit()
    return {"ok": True, "default": c.id}


@router.delete("/models/{config_id}")
async def delete_model(config_id: str,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("admin:*"))):
    c = await db.get(ModelConfig, config_id)
    if c is None:
        raise HTTPException(404, "config not found")
    await db.delete(c)
    await db.commit()
    return {"ok": True}


# ── runtime flags: demo/live switch without restart ──

async def runtime_flag(db: AsyncSession, key: str,
                       default: bool) -> bool:
    """DB-backed runtime switch; on any read failure fall back to the
    env default — a flag lookup must never break the endpoint."""
    try:
        r = (await db.execute(
            select(RuntimeFlag).where(RuntimeFlag.key == key))
        ).scalar_one_or_none()
        return r.value if r else default
    except Exception:
        return default


@router.post("/flags/{key}")
async def set_flag(key: str, value: bool,
                   db: AsyncSession = Depends(get_db),
                   user: User = Depends(require("admin:*"))):
    if key not in {"demo_fixtures"}:
        raise HTTPException(400, f"unknown flag {key}")
    r = (await db.execute(
        select(RuntimeFlag).where(RuntimeFlag.key == key))
    ).scalar_one_or_none()
    if r is None:
        r = RuntimeFlag(key=key, value=value)
        db.add(r)
    else:
        r.value = value
    await audit(db, action=f"flag.{key}", actor=user,
                detail={"value": value})
    await db.commit()
    return {"key": key, "value": value}


# ── editable data-source keys (DB-backed, masked on read) ──

from app.services.secrets import set_secret


class KeyIn(BaseModel):
    key: str
    value: str


@router.post("/keys", status_code=201)
async def set_key(body: KeyIn,
                  db: AsyncSession = Depends(get_db),
                  user: User = Depends(require("admin:*"))):
    """Store a data-source key server-side. Never logged, never
    returned — only status+masked tail appear anywhere."""
    allowed = {k["var"] for spec in KEY_SPECS
               for k in [{"var": v} for v in spec[1].split(" / ")]}
    if body.key not in allowed:
        raise HTTPException(400, f"unknown key {body.key}")
    await set_secret(db, body.key, body.value.strip())
    await audit(db, action="secret.set", actor=user,
                detail={"key": body.key})
    await db.commit()
    return {"key": body.key, "set": True}
