from pathlib import Path

import pytest

from browser_agent.browser.executor import TestDataError as MissingTestDataError, resolve_test_data


def test_resolve_test_data_prefers_process_environment(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CUSTOMER_EMAIL=from-file@example.com\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOMER_EMAIL", "from-process@example.com")

    assert resolve_test_data("CUSTOMER_EMAIL", env_file=env_file) == "from-process@example.com"


def test_resolve_test_data_reads_env_file_when_not_in_process(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CUSTOMER_EMAIL=from-file@example.com\n", encoding="utf-8")
    monkeypatch.delenv("CUSTOMER_EMAIL", raising=False)

    assert resolve_test_data("CUSTOMER_EMAIL", env_file=env_file) == "from-file@example.com"


def test_resolve_test_data_does_not_return_missing_value(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MISSING_VALUE", raising=False)

    with pytest.raises(MissingTestDataError, match="MISSING_VALUE"):
        resolve_test_data("MISSING_VALUE", env_file=tmp_path / "absent.env")
