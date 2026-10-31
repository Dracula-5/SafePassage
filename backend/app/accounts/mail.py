"""Email over SMTP, which every provider offers (Resend, SendGrid, Amazon SES, a Gmail app password).

Off until SMTP_HOST and MAIL_FROM are set. A failure is logged and never reaches the person who asked:
a mail outage must not stop anyone signing up, and "forgot password" must not reveal who has an account.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import CommonSettings, get_common_settings
from app.core.logging import get_logger

log = get_logger(__name__)
SMTP_TIMEOUT_S = 10
IMPLICIT_TLS_PORT = 465


def enabled(settings: CommonSettings) -> bool:
    return bool(settings.smtp_host and settings.mail_from)


def _deliver(settings: CommonSettings, message: EmailMessage) -> None:
    secure = ssl.create_default_context()
    if settings.smtp_port == IMPLICIT_TLS_PORT:
        connection = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S, context=secure)
    else:
        connection = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S)
    with connection as smtp:
        smtp.ehlo()
        if settings.smtp_port != IMPLICIT_TLS_PORT and smtp.has_extn("starttls"):
            smtp.starttls(context=secure)
            smtp.ehlo()
        elif settings.smtp_user and settings.smtp_port != IMPLICIT_TLS_PORT:
            raise smtplib.SMTPNotSupportedError("the server offers no TLS, so the password stays unsent")
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(message)


async def send(to: str, subject: str, body: str) -> None:
    settings = get_common_settings()
    if not enabled(settings):
        return
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings.mail_from, to, subject
    message.set_content(body)
    try:
        await asyncio.to_thread(_deliver, settings, message)
        log.info("mail_sent", subject=subject)
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("mail_not_sent", subject=subject, error=type(exc).__name__)
