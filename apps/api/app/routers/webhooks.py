"""TradingView webhook — alerts hit this endpoint and become platform
signals/alerts. Never auto-trades: a TV alert becomes an Alert +
optional decision intent that still needs human approval.

Setup (in TradingView):
  Alert → Notifications → Webhook URL:
    https://<api>/api/v1/webhooks/tradingview
  Message body must include: {"secret": "<TRADINGVIEW_WEBHOOK_SECRET>",
  "ticker": "{{ticker}}", "action": "buy|sell|alert", ...}
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.secrets import get_secret

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/tradingview", status_code=202)
async def tradingview(request: Request,
                      db: AsyncSession = Depends(get_db)):
    """Ingest a TradingView alert. Shared-secret auth — the secret
    lives in the alert's message body, not a header."""
    try:
        payload = await request.json()
    except Exception:
        # TV 'strategy.order.action' etc. sometimes arrive as text
        raw = (await request.body()).decode("utf-8", "replace")
        payload = {"raw": raw}

    secret = await get_secret(db, "TRADINGVIEW_WEBHOOK_SECRET")
    if secret and payload.get("secret") != secret:
        raise HTTPException(403, "bad webhook secret")

    ticker = (payload.get("ticker") or payload.get("symbol")
              or "UNKNOWN")
    action = (payload.get("action") or payload.get("side")
              or payload.get("strategy.order.action") or "alert")

    # lands in Monitoring as a signal alert — visible, auditable,
    # never an order
    import hashlib
    from app.services.monitoring import emit_alert
    await emit_alert(
        db,
        source="tradingview",
        severity="info",
        dedup_key=f"tv:{ticker}:{hashlib.md5(str(payload).encode()).hexdigest()[:10]}",
        message=f"TV alert {ticker}: {action} — "
                f"{payload.get('message', payload.get('raw', ''))[:200]}",
        observed=payload,
        action="review",
    )
    await db.commit()
    return {"accepted": True, "ticker": ticker,
            "note": "alert recorded — no order placed; approval "
                    "still required"}
