import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from jinja2 import Environment, FileSystemLoader, select_autoescape

from auth import VERIFICATION_CODE_EXPIRE_MINUTES
import config,database, models

settings = config.get_settings()

template_dir = os.path.join(os.path.dirname(__file__), "email_templates")
_jinja = Environment(
    loader=FileSystemLoader(template_dir), autoescape=select_autoescape(["html"])
)
url = settings.url.rstrip("/")
tech_email = settings.tech_email
support_email = settings.support_email
logo_url = url + "/static/logo.jpg"
if "localhost" in url or "127.0.0.1" in url:
    logging.getLogger("uvicorn.error").warning(
        "URL is %s: reset links and the logo in emails will not load for recipients. "
        "Set URL to the public https address of this API.", url,
    )


def _recipients(delegate: models.Delegate) -> list[str]:
    """The delegate's email, plus their backup email if they set one."""
    emails = [delegate.email]
    backup = getattr(delegate, "backup_email", "")
    if backup and backup != delegate.email:
        emails.append(backup)
    return emails

conf = ConnectionConfig(
    MAIL_USERNAME=settings.mail_username,
    MAIL_PASSWORD=settings.mail_password,
    MAIL_FROM=settings.mail_from,
    MAIL_FROM_NAME=settings.mail_from_name,
    MAIL_PORT=settings.mail_port,
    MAIL_SERVER=settings.mail_server,
    MAIL_STARTTLS=settings.mail_starttls,
    MAIL_SSL_TLS=settings.mail_ssl_tls,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
    TEMPLATE_FOLDER=Path(template_dir),
)

async def _send(subject: str, recipients: list[str], template_name: str, context: dict) -> None:
    """Render the template and send it. Prefers Brevo's HTTPS API (works on hosts that
    block SMTP, such as Render); falls back to SMTP when no API key is configured."""
    html = _jinja.get_template(template_name).render(year=datetime.now(timezone.utc).year, **context)
    if settings.brevo_api_key:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": settings.brevo_api_key,
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                json={
                    "sender": {"email": settings.mail_from, "name": settings.mail_from_name},
                    "to": [{"email": e} for e in recipients],
                    "subject": subject,
                    "htmlContent": html,
                },
            )
            resp.raise_for_status()
    else:
        message = MessageSchema(
            subject=subject, recipients=recipients, body=html, subtype=MessageType.html
        )
        await FastMail(conf).send_message(message)


async def send_verification_email(delegate: models.Delegate, code: str) -> None:
    await _send(
        "Verify your email - MUNSociety MPSTME",
        _recipients(delegate),
        "email_verification.html",
        {"logo_url": logo_url, "firstname": delegate.firstname, "code": code, "expiry": VERIFICATION_CODE_EXPIRE_MINUTES, "support_email": support_email, "tech_email": tech_email},
    )

async def send_password_reset_email(delegate: models.Delegate, link: str) -> None:
    await _send(
        "Reset your password - MUNSociety MPSTME",
        _recipients(delegate),
        "password_reset.html",
        {"logo_url": logo_url, "firstname": delegate.firstname, "link": link, "support_email": support_email, "tech_email": tech_email},
    )
