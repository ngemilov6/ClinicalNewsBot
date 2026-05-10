"""Gmail SMTP delivery via app password."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from clinical_news.config import Settings

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def send_brief(settings: Settings, markdown: str, filename: str) -> None:
    if not (settings.gmail_from and settings.gmail_to and settings.gmail_app_password):
        log.warning("gmail not configured; skipping send")
        return

    msg = EmailMessage()
    msg["Subject"] = f"Clinical-trials weekly brief — {filename}"
    msg["From"] = settings.gmail_from
    msg["To"] = settings.gmail_to
    msg.set_content(markdown)

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.login(settings.gmail_from, settings.gmail_app_password)
        smtp.send_message(msg)
    log.info("brief sent", extra={"to": settings.gmail_to})
