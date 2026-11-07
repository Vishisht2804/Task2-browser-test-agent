"""Application settings loaded from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    browser_navigation_timeout_ms: int = 30_000
    browser_executable_path: Path | None = None
    artifact_dir: Path = Path("artifacts")

    # Provider selection is configuration, not a browser-engine dependency.
    llm_provider: str = "openai"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: int = Field(default=45, ge=1, le=300)
    llm_max_observed_elements: int = Field(default=250, ge=1, le=2_000)
    llm_max_visible_text_chars: int = Field(default=8_000, ge=1, le=50_000)
    agent_max_retries_per_step: int = Field(default=2, ge=0, le=5)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings. Useful for dependency injection in later phases."""

    return Settings()


def clear_settings_cache() -> None:
    """Clear cached settings, primarily for tests that change environment variables."""

    get_settings.cache_clear()
