from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    secret_key: str
    verification_token_expire_minutes: int = 120
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

@lru_cache
def get_settings() -> Settings:
    return Settings()
