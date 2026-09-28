"""Floating assistant — answers from live system state (deterministic
intents) and, when a model config with a key exists, relays to that
LLM with a system context built from real data. Never fabricates."""

import re

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.ops import ModelConfig
from app.models.macro import RegimeRun
from app.models.ops import Alert
from app.models.governance import DecisionRecord
from app.models.execution import BrokerOrderRec
from app.models.instruments import Instrument

router = APIRouter(prefix="/assistant", tags=["assistant"])

NAV = {
    "portfolio": "/portfolio", "risk": "/risk", "order": "/trading-desk",
    "trade": "/trading-desk", "backtest": "/backtesting",
    "alert": "/monitoring", "monitor": "/monitoring",
    "macro": "/macro", "regime": "/macro", "decision": "/journal",
    "approval": "/committee", "committee": "/committee",
    "valuat": "/valuation", "screen": "/screener", "search": "/screener",
    "research": "/research", "key": "/settings", "setting": "/settings",
    "doc": "/docs", "help": "/docs", "data": "/data-ops",
    "universe": "/universe", "market": "/market", "alloc": "/allocation",
    "technical": "/technical", "journal": "/journal", "quant": "/quant",
}


async def _context(db: AsyncSession) -> dict:
    regime = (await db.execute(
        select(RegimeRun).order_by(RegimeRun.as_of.desc()))
    ).scalars().first()
    alerts = (await db.execute(
        select(Alert).where(Alert.status == "active"))).scalars().all()
    decisions = (await db.execute(
        select(DecisionRecord, Instrument.symbol)
        .join(Instrument, DecisionRecord.instrument_id == Instrument.id)
        .order_by(DecisionRecord.at.desc()).limit(5))).all()
    open_orders = (await db.execute(
        select(func.count(BrokerOrderRec.id))
        .where(BrokerOrderRec.status.not_in(
            ["filled", "rejected", "cancelled"])))).scalar()
    return {
        "regime": {"econ": regime.econ_regime,
                   "market": regime.market_regime,
                   "fear_greed": regime.fear_greed,
                   "vix": regime.vix} if regime else None,
        "active_alerts": len(alerts),
        "alert_examples": [a.message for a in alerts[:3]],
        "open_orders": open_orders,
        "recent_decisions": [
            {"symbol": s, "verdict": d.verdict,
             "at": d.at.isoformat()[:16]} for d, s in decisions],
    }


async def _det(db: AsyncSession, msg: str) -> str | None:
    """Deterministic answers from real state — returns None when the
    question needs a model."""
    m = msg.lower()
    ctx = await _context(db)
    if any(w in m for w in ("regime", "macro", "market mood",
                            "economy")):
        r = ctx["regime"]
        return (f"Current regime: economy **{r['econ']}**, market "
                f"**{r['market']}**, Fear & Greed **{r['fear_greed']}**, "
                f"VIX **{r['vix']}**."
                if r else
                "No regime computed yet — run macro ingestion first.")
    if "alert" in m or "warning" in m or "problem" in m:
        n = ctx["active_alerts"]
        head = "; ".join(ctx["alert_examples"]) if ctx[
            "alert_examples"] else "none right now"
        return f"{n} active alert(s). Latest: {head}. Full list → /monitoring"
    if "order" in m or "trade" in m or "fill" in m:
        return (f"{ctx['open_orders']} open order(s). The blotter and "
                "chart live at /trading-desk.")
    if "decision" in m or "committee" in m:
        ds = ", ".join(f"{d['symbol']}={d['verdict']}"
                       for d in ctx["recent_decisions"]) or "none yet"
        return f"Recent decisions: {ds}. Full trail → /journal."
    if "where" in m or "how do i" in m or "go to" in m or "find" in m:
        for k, url in NAV.items():
            if k in m:
                return f"That lives at **{url}** — sidebar or ⌘K."
        return "Try ⌘K to jump anywhere, or /docs for the full guide."
    return None


async def _llm(db: AsyncSession, msg: str, history: list) -> str | None:
    """Call the default model config if one has a key."""
    cfg = (await db.execute(
        select(ModelConfig).where(ModelConfig.is_default,
                                  ModelConfig.enabled))
    ).scalar_one_or_none()
    if cfg is None or not (cfg.api_key or cfg.base_url):
        return None
    ctx = await _context(db)
    system = ("You are the VAIIP assistant inside a trading platform. "
              "Answer concisely using this live context; never claim "
              "orders executed that didn't. Context: "
              f"{ctx}. Page map: {NAV}.")
    messages = ([{"role": h["role"], "content": h["content"]}
                 for h in history[-6:]]
                + [{"role": "user", "content": msg}])
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            if cfg.provider == "anthropic":
                r = await c.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": cfg.api_key,
                             "anthropic-version": "2023-06-01"},
                    json={"model": cfg.model, "max_tokens": 400,
                          "system": system,
                          "messages": [{"role": m["role"],
                                        "content": m["content"]}
                                       for m in messages
                                       if m["role"] != "system"]})
                return r.json()["content"][0]["text"]
            url = (cfg.base_url or
                   {"openai": "https://api.openai.com/v1",
                    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
                    "deepseek": "https://api.deepseek.com/v1",
                    "kimi": "https://api.moonshot.ai/v1",
                    "ollama": "http://localhost:11434/v1",
                    "custom": None}.get(cfg.provider))
            if url is None:
                return None
            r = await c.post(
                f"{url}/chat/completions",
                headers=({"Authorization": f"Bearer {cfg.api_key}"}
                         if cfg.api_key else {}),
                json={"model": cfg.model, "max_tokens": 400,
                      "messages": [{"role": "system",
                                    "content": system}] + messages})
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"(model call failed: {e} — showing local answers)"


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []


@router.post("/chat")
async def chat(body: ChatIn, db: AsyncSession = Depends(get_db)):
    det = await _det(db, body.message)
    if det:
        return {"reply": det, "source": "deterministic"}
    llm = await _llm(db, body.message, body.history)
    if llm:
        return {"reply": llm, "source": "model"}
    return {"reply": (
        "I can answer from the system state — try "
        "\"what's the regime?\", \"any alerts?\", \"open orders?\", "
        "\"where is the screener?\" — or add an AI model in "
        "Settings → AI models for free-form questions."),
        "source": "fallback"}

