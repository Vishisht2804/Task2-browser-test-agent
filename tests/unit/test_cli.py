import json

from browser_agent.cli import main


def test_cli_accepts_and_prints_json(capsys) -> None:
    exit_code = main(
        [
            "run",
            "--url",
            "https://example.com/login",
            "--test",
            "Log in and verify the dashboard appears",
            "--data-ref",
            "CUSTOMER_EMAIL",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["instruction"] == "Log in and verify the dashboard appears"
    assert payload["test_data_refs"] == ["CUSTOMER_EMAIL"]


def test_cli_rejects_invalid_url(capsys) -> None:
    exit_code = main(
        ["run", "--url", "not-a-url", "--test", "Open the home page"]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Invalid test request" in captured.err
