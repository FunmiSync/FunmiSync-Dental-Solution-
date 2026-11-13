"""Send plain-text email through any provider's SMTP server (settings.smtp_*)."""
import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings


class EmailNotConfigured(RuntimeError):
    pass


def email_configured() -> bool:
    return bool(settings.smtp_host and settings.alert_email_from)


def _send(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.alert_email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=context)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
    with server:
        if settings.smtp_port != 465:
            server.starttls(context=context)
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password or "")
        server.send_message(message)


async def send_email(*, to: str, subject: str, body: str) -> None:
    """One recipient per message, so colleagues' addresses are not shared. SMTP is
    blocking, so it runs in a thread and never freezes the event loop."""
    if not email_configured():
        raise EmailNotConfigured("Email is not set up: SMTP_HOST and ALERT_EMAIL_FROM are empty")
    await asyncio.to_thread(_send, to, subject, body)
