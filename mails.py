import os
from pathlib import Path
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from auth import create_verification_token, generate_password, hash_password, VERIFICATION_TOKEN_EXPIRE_MINUTES
import config,database, models

settings = config.get_settings()

template_dir = os.path.join(os.path.dirname(__file__), "email_templates") 
url = settings.url
tech_email = settings.tech_email
support_email = settings.support_email
logo_url = url + "/static/logo.jpg"

conf = ConnectionConfig(
    MAIL_USERNAME=settings.mail_username,
    MAIL_PASSWORD=settings.mail_password,
    MAIL_FROM=settings.mail_from,
    MAIL_FROM_NAME=settings.mail_from_name,
    MAIL_PORT=settings.mail_port,
    MAIL_SERVER=settings.mail_server,
    MAIL_STARTTLS=False,
    MAIL_SSL_TLS=True,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
    TEMPLATE_FOLDER=Path(template_dir),
)

async def send_verification_email(delegate: models.Delegate) -> None:

    token = create_verification_token(data={"sub": delegate.email})
    link = f"{url}/verify_email?token={token}"
    expiration = VERIFICATION_TOKEN_EXPIRE_MINUTES // 60

    message = MessageSchema(
        subject="Verify your email - MUNSociety MPSTME",
        recipients=[delegate.email],
        template_body={"logo_url": logo_url, "firstname": delegate.firstname, "verification_url": link, "expiry": expiration, "support_email": support_email, "tech_email": tech_email},
        subtype=MessageType.html,
    )
    fm = FastMail(conf)
    await fm.send_message(message, template_name="email_verification.html")

async def send_password_reset_email(delegate: models.Delegate, link: str) -> None:

    message = MessageSchema(
        subject="Reset your password - MUNSociety MPSTME",
        recipients=[delegate.email],
        template_body={"logo_url": logo_url, "firstname": delegate.firstname, "link": link, "support_email": support_email, "tech_email": tech_email},
        subtype=MessageType.html,
    )
    fm = FastMail(conf)
    await fm.send_message(message, template_name="password_reset.html")
