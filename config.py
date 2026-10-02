from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from sqlalchemy.engine import URL, make_url

class Settings(BaseSettings):
    secret_key: str
    # A full connection string (e.g. Supabase or Render). When set, it wins over the
    # POSTGRES_* parts below, so a managed host needs only this one variable. Local dev
    # leaves it unset and uses the POSTGRES_* parts with docker-compose.
    database_url_raw: str | None = Field(default=None, alias="DATABASE_URL")
    postgres_password: str = ""
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
    docs_url: str | None = "/docs"  # Swagger UI
    redoc_url: str | None = "/redoc"  # ReDoc

    model_config = SettingsConfigDict(env_file=".env", populate_by_name=True)

    @property
    def database_url(self) -> URL:
        if self.database_url_raw:
            # Normalise the driver to asyncpg and drop libpq-only query args (sslmode,
            # pgbouncer, ...) that asyncpg cannot parse; SSL is handled in db_connect_args.
            raw = self.database_url_raw
            for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
                if raw.startswith(prefix):
                    raw = "postgresql+asyncpg://" + raw[len(prefix):]
                    break
            url = make_url(raw)
            return url.set(query={}) if url.query else url
        # URL.create escapes the password, so special characters are safe.
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def db_connect_args(self) -> dict:
        """Extra asyncpg connect args. A managed host (DATABASE_URL set) needs SSL;
        statement_cache_size=0 keeps it working behind a transaction pooler (pgbouncer),
        which cannot reuse prepared statements. Local docker Postgres needs neither."""
        if self.database_url_raw:
            return {"ssl": "require", "statement_cache_size": 0}
        return {}

@lru_cache
def get_settings() -> Settings:
    return Settings()
