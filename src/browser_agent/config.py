"""Application settings loaded from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration; provider-specific settings stay outside browser code."""

    model_config = SettingsConfigDict(
        env_prefix="BA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    default_max_steps: int = 25
    default_timeout_seconds: int = 120
    browser_headless: bool = True
    browser_action_timeout_ms: int = 10_000
    artifact_dir: Path = Path("artifacts")

    # Provider selection is configuration, not a browser-engine dependency.
    llm_provider: str = "openai"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings. Useful for dependency injection in later phases."""

    return Settings()


def clear_settings_cache() -> None:
    """Clear cached settings, primarily for tests that change environment variables."""

    get_settings.cache_clear()
