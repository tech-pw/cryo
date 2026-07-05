"""Optional SMTP email delivery for reports."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from cryo.config import get_settings

logger = logging.getLogger(__name__)


def send_report_email(subject: str, body_html: str) -> bool:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from or not settings.report_email_recipients:
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(settings.report_email_recipients)
    message.set_content(body_html, subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
        return True
    except Exception as exc:
        logger.warning("Failed to send report email: %s", exc)
        return False
