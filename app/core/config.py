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

    @model_validator(mode="after")
    def _require_real_secret_in_production(self) -> "Settings":
        if self.environment == "production" and self.secret_key == DEV_SECRET_KEY:
            raise ValueError("MOVIEHUB_SECRET_KEY must be set when MOVIEHUB_ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
