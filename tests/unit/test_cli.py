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


def test_plan_subcommand_parses_llm_and_browser_options() -> None:
    from browser_agent.cli import build_parser

    args = build_parser().parse_args(
        [
            "plan",
            "--url",
            "https://example.com/login",
            "--test",
            "Log in and verify the dashboard",
            "--data-ref",
            "CUSTOMER_EMAIL",
            "--data-ref",
            "CUSTOMER_PASSWORD",
            "--headed",
            "--json",
        ]
    )

    assert args.command == "plan"
    assert args.data_ref == ["CUSTOMER_EMAIL", "CUSTOMER_PASSWORD"]
    assert args.headed is True
    assert args.json is True


def test_plan_subcommand_rejects_invalid_url_before_provider_request(capsys) -> None:
    from browser_agent.cli import main

    exit_code = main(
        ["plan", "--url", "not-a-url", "--test", "Log in and verify the dashboard"]
    )

    assert exit_code == 2
    assert "Invalid test request" in capsys.readouterr().err
