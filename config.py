from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from sqlalchemy.engine import URL

class Settings(BaseSettings):
    secret_key: str
    postgres_password: str
    postgres_user: str = "mundra"
    postgres_db: str = "mundra"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    verification_token_expire_minutes: int = 120
    access_token_expire_minutes: int = 720  # 12h: one event day (docs/adr/0003)
    tech_email: str = "technology@munsocietympstme.com"
    support_email: str = "contact@munsocietympstme.com"
    url: str = "http://localhost:8000"
    # Email is sent through Brevo's SMTP relay. MAIL_USERNAME is the Brevo SMTP login
    # (e.g. 8xxxxxx@smtp-brevo.com), MAIL_PASSWORD is a Brevo SMTP key (not the account
    # password), and MAIL_FROM must be a sender/domain verified in Brevo.
    mail_username: str = "technology@munsocietympstme.com"
    mail_password: str = ""
    mail_from: str = "technology@munsocietympstme.com"
    mail_from_name: str = "Tech - MUNSociety MPSTME"
    mail_port: int = 587
    mail_server: str = "smtp-relay.brevo.com"
    # Brevo relay: port 587 uses STARTTLS. For port 465 instead, set MAIL_STARTTLS=false
    # and MAIL_SSL_TLS=true.
    mail_starttls: bool = True
    mail_ssl_tls: bool = False
    docs_url: str | None = None
    redoc_url: str = "/docs"

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def database_url(self) -> URL:
        # URL.create escapes the password, so special characters are safe.
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

@lru_cache
def get_settings() -> Settings:
    return Settings()
