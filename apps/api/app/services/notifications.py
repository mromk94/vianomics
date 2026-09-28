"""Outbound notification dispatch — Telegram/WhatsApp/SMS/email.

Credentials live in SecretStore; targets in notification_channels.
Fail-open for monitoring (a broken channel logs, doesn't crash the
alert pipeline). version notify/v1.0"""

import smtplib
from email.mime.text import MIMEText
import os

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import NotificationChannel, SecretStore

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


async def _secret(db, key):
    r = (await db.execute(
        select(SecretStore).where(SecretStore.key == key))
    ).scalar_one_or_none()
    return r.value if r else os.environ.get(key)


async def dispatch_alert(db: AsyncSession, severity: str, text: str):
    """Send to every enabled channel whose min_severity ≤ severity."""
    rows = (await db.execute(
        select(NotificationChannel)
        .where(NotificationChannel.enabled))).scalars().all()
    sent, failed = [], []
    for ch in rows:
        if SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK.get(
                ch.min_severity, 0):
            continue
        try:
            await _send(db, ch, text)
            sent.append(ch.channel)
        except Exception as e:
            failed.append({"channel": ch.channel, "error": str(e)[:120]})
    return {"sent": sent, "failed": failed}


async def _send(db, ch: NotificationChannel, text: str):
    if ch.channel == "telegram":
        tok = await _secret(db, "TELEGRAM_BOT_TOKEN")
        if not tok:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                json={"chat_id": ch.target, "text": text})
            r.raise_for_status()
    elif ch.channel in ("whatsapp", "sms"):
        sid = await _secret(db, "TWILIO_ACCOUNT_SID")
        auth = await _secret(db, "TWILIO_AUTH_TOKEN")
        from_num = (await _secret(db, "TWILIO_FROM")
                    or ch.extra.get("from"))
        if not (sid and auth and from_num):
            raise RuntimeError("TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM not set")
        to = (f"whatsapp:{ch.target}" if ch.channel == "whatsapp"
              else ch.target)
        frm = (f"whatsapp:{from_num}" if ch.channel == "whatsapp"
               else from_num)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, auth),
                data={"To": to, "From": frm, "Body": text})
            r.raise_for_status()
    elif ch.channel == "email_resend":
        key = await _secret(db, "RESEND_API_KEY")
        if not key:
            raise RuntimeError("RESEND_API_KEY not set")
        frm = ch.extra.get("from") or "onboarding@resend.dev"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}"},
                json={"from": frm, "to": [ch.target],
                      "subject": "VAIIP alert", "text": text})
            r.raise_for_status()
    elif ch.channel == "email_smtp":
        host = (await _secret(db, "SMTP_HOST")
                or os.environ.get("SMTP_HOST"))
        port = int(await _secret(db, "SMTP_PORT")
                   or os.environ.get("SMTP_PORT") or 587)
        user = await _secret(db, "SMTP_USER")
        pw = await _secret(db, "SMTP_PASSWORD")
        frm = (await _secret(db, "SMTP_FROM")
               or ch.extra.get("from") or user or "vaiip@localhost")
        if not host:
            raise RuntimeError("SMTP_HOST not set")
        msg = MIMEText(text)
        msg["Subject"] = "VAIIP alert"
        msg["From"], msg["To"] = frm, ch.target
        s = smtplib.SMTP(host, port, timeout=10)
        s.starttls()
        if user and pw:
            s.login(user, pw)
        s.send_message(msg)
        s.quit()
    elif ch.channel == "email":
        # legacy channel id → try resend then smtp
        if await _secret(db, "RESEND_API_KEY"):
            c2 = NotificationChannel(
                channel="email_resend", target=ch.target,
                extra=ch.extra)
            await _send(db, c2, text)
        else:
            c2 = NotificationChannel(
                channel="email_smtp", target=ch.target,
                extra=ch.extra)
            await _send(db, c2, text)
    else:
        raise RuntimeError(f"unknown channel {ch.channel}")
