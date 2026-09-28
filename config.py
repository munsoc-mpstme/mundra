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
    mail_username: str = "technology@munsocietympstme.com"
    mail_password: str = ""
    mail_from: str = "technology@munsocietympstme.com"
    mail_from_name: str = "Tech - MUNSociety MPSTME"
    mail_port: int = 465
    mail_server: str
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
