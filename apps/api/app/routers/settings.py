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
    ("IBKR bridge URL", "IBKR_BRIDGE_URL",
     "https://gw.<your-domain> — the VPS bridge service (apps/bridge)",
     "Live broker connection (read-only until enabled)"),
    ("IBKR bridge secret", "IBKR_BRIDGE_SECRET",
     "openssl rand -hex 32 — same value in the bridge .env on the VPS",
     "Live broker connection"),
    ("Execution enabled", "EXECUTION_ENABLED",
     "set 'true' only after bridge verified + approval flow tested",
     "Real order submission — keep false for paper"),
    ("Admin password", "ADMIN_PASSWORD",
     "set at first seed",
     "Admin sign-in"),
    ("Alpaca API key", "ALPACA_API_KEY",
     "alpaca.markets → Paper Trading → View Keys (the PK… key id)",
     "Alpaca market data + paper/live trading + account sync"),
    ("Alpaca secret", "ALPACA_SECRET_KEY", "same page — the SK… key",
     "Alpaca market data + paper/live trading + account sync"),
    ("Alpaca mode", "ALPACA_BASE_URL",
     "paper-api.alpaca.markets (default) or api.alpaca.markets",
     "paper vs live Alpaca"),
    ("IBKR Flex token", "IBKR_FLEX_TOKEN",
     "IBKR portal → Performance & Reports → Flex Queries → "
     "Flex Web Service token",
     "IBKR read-only portfolio sync (no gateway needed)"),
    ("IBKR Flex query id", "IBKR_FLEX_QUERY_ID",
     "Activity Flex Query including Open Positions + Cash Report",
     "IBKR read-only portfolio sync"),
    ("StockRow API key", "STOCKROW_API_KEY",
     "stockrow.com → API dashboard → sr_live_… key",
     "10-year fundamentals + CAGR metrics — extends screener/research history"),
    ("MT4 push secret", "MT4_PUSH_SECRET",
     "any string — goes in the VAIIP_Push.mq4 EA input field",
     "MetaTrader 4 → platform portfolio sync"),
    ("Bamboo client ID", "BAMBOO_CLIENT_ID",
     "investbamboo.com partner dashboard → API credentials",
     "Bamboo brokerage sync"),
    ("Bamboo client secret", "BAMBOO_CLIENT_SECRET", "same page",
     "Bamboo brokerage sync"),
    ("Bamboo API base", "BAMBOO_BASE_URL",
     "default https://api.investbamboo.com — sandbox differs",
     "Bamboo brokerage sync"),
    ("TradingView webhook secret", "TRADINGVIEW_WEBHOOK_SECRET",
     "any string you choose — paste the same string in the TV alert "
     "webhook body field",
     "TradingView alerts → platform signals"),
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
    allowed = {v for spec in KEY_SPECS
               for v in spec[1].split(" / ")} | NOTIF_KEY_NAMES
    if body.key not in allowed:
        raise HTTPException(400, f"unknown key {body.key}")
    await set_secret(db, body.key, body.value.strip())
    await audit(db, action="secret.set", actor=user,
                detail={"key": body.key})
    await db.commit()
    return {"key": body.key, "set": True}


# ── notification channels (email/whatsapp/telegram/sms) ──

from app.models.ops import NotificationChannel
from app.services.notifications import dispatch_alert

NOTIF_KEY_NAMES = {
    "RESEND_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
    "SMTP_PASSWORD", "SMTP_FROM", "TELEGRAM_BOT_TOKEN",
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL",
    "TRADINGVIEW_WEBHOOK_SECRET",
    "MT4_PUSH_SECRET",
    "BAMBOO_CLIENT_ID", "BAMBOO_CLIENT_SECRET", "BAMBOO_BASE_URL",
    "STOCKROW_API_KEY",
}

CHANNEL_SPECS = [
    {"id": "email_resend", "label": "Email via Resend", "group": "email",
     "target_label": "Recipient email",
     "fields": [
         {"name": "to", "label": "Recipient email", "kind": "target"},
         {"name": "from", "label": "From address (must be a verified sender/domain)",
          "kind": "extra", "default": "alerts@yourdomain.com"},
         {"name": "RESEND_API_KEY", "label": "Resend API key",
          "kind": "secret"}],
     "setup": ["resend.com → sign up → API Keys → create",
               "verify your domain (or use onboarding@resend.dev for tests)",
               "paste the key + from address here"],
     "note": "simplest email path — HTTPS API, no SMTP setup"},
    {"id": "email_smtp", "label": "Email via SMTP", "group": "email",
     "target_label": "Recipient email",
     "fields": [
         {"name": "to", "label": "Recipient email", "kind": "target"},
         {"name": "SMTP_HOST", "label": "SMTP host", "kind": "secret",
          "default": "smtp.gmail.com"},
         {"name": "SMTP_PORT", "label": "SMTP port", "kind": "secret",
          "default": "587"},
         {"name": "SMTP_USER", "label": "SMTP username (usually your email)",
          "kind": "secret"},
         {"name": "SMTP_PASSWORD", "label": "SMTP password / app password",
          "kind": "secret"},
         {"name": "SMTP_FROM", "label": "From address", "kind": "secret"}],
     "setup": ["Gmail: enable 2FA → App passwords → paste here (port 587, TLS auto)",
               "Outlook: smtp.office365.com:587",
               "self-hosted: your relay host/port"],
     "note": "full SMTP — works with Gmail/Outlook/your own relay"},
    {"id": "telegram", "label": "Telegram", "group": "telegram",
     "target_label": "Chat ID (numeric, e.g. 123456789)",
     "fields": [
         {"name": "chat_id", "label": "Chat ID", "kind": "target"},
         {"name": "TELEGRAM_BOT_TOKEN", "label": "Bot token",
          "kind": "secret"}],
     "setup": ["Telegram → @BotFather → /newbot → copy the token",
               "message your bot once, then visit "
               "api.telegram.org/bot<TOKEN>/getUpdates → copy chat.id",
               "paste both here"],
     "note": "fastest to set up — 2 minutes"},
    {"id": "whatsapp", "label": "WhatsApp via Twilio", "group": "twilio",
     "target_label": "Recipient phone (+15551234567)",
     "fields": [
         {"name": "to", "label": "Recipient phone (E.164)", "kind": "target"},
         {"name": "TWILIO_ACCOUNT_SID", "label": "Twilio Account SID",
          "kind": "secret"},
         {"name": "TWILIO_AUTH_TOKEN", "label": "Twilio Auth Token",
          "kind": "secret"},
         {"name": "TWILIO_FROM", "label": "Twilio WhatsApp sender "
          "(e.g. +14155238886 sandbox)", "kind": "secret"}],
     "setup": ["twilio.com → console → Account SID + Auth Token",
               "Messaging → Try WhatsApp → join sandbox (or approved sender)",
               "recipient must join the sandbox first in test mode"],
     "note": "production WhatsApp needs an approved Twilio sender"},
    {"id": "sms", "label": "SMS via Twilio", "group": "twilio",
     "target_label": "Recipient phone (+15551234567)",
     "fields": [
         {"name": "to", "label": "Recipient phone (E.164)", "kind": "target"},
         {"name": "TWILIO_ACCOUNT_SID", "label": "Twilio Account SID",
          "kind": "secret"},
         {"name": "TWILIO_AUTH_TOKEN", "label": "Twilio Auth Token",
          "kind": "secret"},
         {"name": "TWILIO_FROM", "label": "Twilio SMS number", "kind": "secret"}],
     "setup": ["twilio.com → console → Account SID + Auth Token",
               "Phone Numbers → buy a number → paste as TWILIO_FROM"],
     "note": "any Twilio SMS-capable number works"},
]


class ChannelIn(BaseModel):
    channel: str            # email_resend | email_smtp | telegram | whatsapp | sms
    target: str
    min_severity: str = "critical"
    extra: dict = {}        # {from: ...} + per-channel config


@router.get("/notifications")
async def notif_list(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(NotificationChannel))).scalars().all()
    return {
        "channels": [
            {"id": c.id, "channel": c.channel, "target": c.target,
             "enabled": c.enabled, "min_severity": c.min_severity}
            for c in rows],
        "specs": CHANNEL_SPECS,
        "note": "credentials (bot token / Twilio keys / SMTP) go in "
                "API keys section or .env"}


@router.post("/notifications", status_code=201)
async def notif_add(body: ChannelIn,
                    db: AsyncSession = Depends(get_db),
                    user: User = Depends(require("admin:*"))):
    if body.channel not in {s["id"] for s in CHANNEL_SPECS}:
        raise HTTPException(400, "unknown channel")
    if body.min_severity not in {"info", "warning", "critical"}:
        raise HTTPException(400, "severity must be info|warning|critical")
    c = NotificationChannel(
        channel=body.channel, target=body.target,
        min_severity=body.min_severity,
        extra=body.extra or {})
    db.add(c)
    await db.flush()
    await audit(db, action="notification.add", actor=user,
                detail={"channel": c.channel, "target": c.target})
    await db.commit()
    return {"id": c.id}


@router.post("/notifications/{cid}/toggle")
async def notif_toggle(cid: str, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("admin:*"))):
    c = await db.get(NotificationChannel, cid)
    if c is None:
        raise HTTPException(404, "channel not found")
    c.enabled = not c.enabled
    await db.commit()
    return {"id": c.id, "enabled": c.enabled}


@router.delete("/notifications/{cid}")
async def notif_del(cid: str, db: AsyncSession = Depends(get_db),
                    user: User = Depends(require("admin:*"))):
    c = await db.get(NotificationChannel, cid)
    if c is None:
        raise HTTPException(404, "channel not found")
    await db.delete(c)
    await db.commit()
    return {"ok": True}


@router.post("/notifications/{cid}/test")
async def notif_test(cid: str, db: AsyncSession = Depends(get_db),
                     user: User = Depends(require("admin:*"))):
    c = await db.get(NotificationChannel, cid)
    if c is None:
        raise HTTPException(404, "channel not found")
    try:
        from app.services.notifications import _send
        await _send(db, c, "VAIIP test — notifications working")
        return {"ok": True, "channel": c.channel}
    except Exception as e:
        return {"ok": False, "channel": c.channel, "error": str(e)[:200]}
