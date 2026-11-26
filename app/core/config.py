from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DEV_SECRET_KEY = "dev-only-insecure-change-me"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128  # caps the work a single request can force on the password hasher


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MOVIEHUB_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    secret_key: str = DEV_SECRET_KEY
    # SQLite is the local fallback; set MOVIEHUB_DATABASE_URL to postgresql+asyncpg://... for PostgreSQL.
    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'moviehub.db').as_posix()}"
    site_name: str = "MovieHub"
    uploads_dir: Path = BASE_DIR / "uploads"
    max_upload_bytes: int = 10 * 1024 * 1024

    # ---- Email / Gmail SMTP ----
    mail_enabled: bool = False
    mail_from_name: str = "MovieHub"
    mail_from_address: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout: int = 20
    # Public base URL used to build absolute links in emails. Never read from the request Host header.
    app_base_url: str = "http://localhost:8000"
    password_reset_token_expire_minutes: int = 30
    email_verification_token_expire_hours: int = 24
    email_max_retries: int = 3
    email_worker_interval_seconds: int = 10

    # ---- Points / community ----
    # How many points each accepted invitation grants, and what the inviter pays.
    points_per_invited_member: int = 1000
    invitation_expiry_hours: int = 72
    points_per_approved_contribution: int = 25
    # Resend-verification cooldown, in seconds.
    resend_verification_cooldown_seconds: int = 60

    @property
    def email_configured(self) -> bool:
        """True when email can actually be sent (enabled, and host + auth + from address are set)."""
        return bool(self.mail_enabled and self.smtp_host and self.smtp_username and self.mail_from_address)

    @model_validator(mode="after")
    def _require_real_secret_in_production(self) -> "Settings":
        if self.environment == "production" and self.secret_key == DEV_SECRET_KEY:
            raise ValueError("MOVIEHUB_SECRET_KEY must be set when MOVIEHUB_ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
