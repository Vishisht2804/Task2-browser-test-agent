from browser_agent.browser.observer import _sanitize_url


def test_sanitize_url_redacts_sensitive_query_parameters() -> None:
    sanitized = _sanitize_url(
        "https://example.com/dashboard?state=abc&access_token=secret&session_id=private"
    )

    assert "state=abc" in sanitized
    assert "access_token=%5BREDACTED%5D" in sanitized
    assert "session_id=%5BREDACTED%5D" in sanitized
    assert "secret" not in sanitized
    assert "private" not in sanitized


def test_sanitize_url_removes_url_user_information() -> None:
    sanitized = _sanitize_url(
        "https://user:password@example.com/path#access_token=fragment-secret"
    )

    assert sanitized == "https://example.com/path#access_token=%5BREDACTED%5D"
    assert "password" not in sanitized
    assert "fragment-secret" not in sanitized
