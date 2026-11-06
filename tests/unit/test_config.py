from browser_agent.config import Settings


def test_settings_default_values() -> None:
    settings = Settings(_env_file=None)

    assert settings.default_max_steps == 25
    assert settings.browser_headless is True
    assert settings.llm_provider == "openai"


def test_llm_provider_can_be_changed_without_changing_browser_settings() -> None:
    openai_settings = Settings(_env_file=None, llm_provider="openai", browser_headless=True)
    anthropic_settings = Settings(
        _env_file=None,
        llm_provider="anthropic",
        browser_headless=True,
    )

    assert openai_settings.llm_provider == "openai"
    assert anthropic_settings.llm_provider == "anthropic"
    assert openai_settings.browser_headless == anthropic_settings.browser_headless
