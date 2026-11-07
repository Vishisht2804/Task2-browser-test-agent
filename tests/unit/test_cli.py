import json

from browser_agent.cli import main


def test_cli_accepts_and_prints_json(capsys) -> None:
    exit_code = main(
        [
            "validate",
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
        ["validate", "--url", "not-a-url", "--test", "Open the home page"]
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


def test_run_subcommand_parses_agent_execution_options():
    from browser_agent.cli import build_parser

    args = build_parser().parse_args(
        [
            "run",
            "--url",
            "https://example.com/login",
            "--test",
            "Log in and verify the dashboard",
            "--data-ref",
            "CUSTOMER_EMAIL",
            "--headed",
            "--max-steps",
            "12",
            "--max-retries",
            "1",
            "--json",
        ]
    )

    assert args.command == "run"
    assert args.data_ref == ["CUSTOMER_EMAIL"]
    assert args.headed is True
    assert args.max_steps == 12
    assert args.max_retries == 1
    assert args.json is True


def test_run_rejects_invalid_url_without_calling_provider(capsys):
    exit_code = main(
        ["run", "--url", "not-a-url", "--test", "Open the customer dashboard"]
    )

    assert exit_code == 2
    assert "Invalid test request" in capsys.readouterr().err


def test_run_rejects_invalid_retry_limit(capsys):
    exit_code = main(
        [
            "run",
            "--url",
            "https://example.com",
            "--test",
            "Open the customer dashboard",
            "--max-retries",
            "9",
        ]
    )

    assert exit_code == 2
    assert "between 0 and 5" in capsys.readouterr().err


def test_run_command_returns_zero_only_for_assertion_pass(monkeypatch, capsys):
    from datetime import datetime, timezone

    from browser_agent.agent import runner
    from browser_agent.schemas import (
        AgentRunResult,
        AgentRunStatus,
        AssertionResult,
        AssertionSpec,
        TestStatus as BrowserTestStatus,
    )

    now = datetime.now(timezone.utc)
    result = AgentRunResult(
        test_name="Customer login",
        status=AgentRunStatus.ACTIONS_COMPLETED,
        test_status=BrowserTestStatus.PASS,
        url="https://example.com/dashboard",
        started_at=now,
        finished_at=now,
        assertions=[
            AssertionResult(
                assertion=AssertionSpec(assertion="visible", target="Dashboard heading"),
                passed=True,
                actual=True,
                message="Visible",
            )
        ],
        assertions_evaluated=True,
    )
    monkeypatch.setattr(runner, "run_agent", lambda **kwargs: result)

    exit_code = main(
        [
            "run",
            "--url",
            "https://example.com/login",
            "--test",
            "Log in and verify the Dashboard heading",
        ]
    )

    assert exit_code == 0
    assert "Test result:      PASS" in capsys.readouterr().out


def test_run_command_returns_nonzero_for_assertion_fail(monkeypatch, capsys):
    from datetime import datetime, timezone

    from browser_agent.agent import runner
    from browser_agent.schemas import (
        AgentRunResult,
        AgentRunStatus,
        AssertionResult,
        AssertionSpec,
        TestStatus as BrowserTestStatus,
    )

    now = datetime.now(timezone.utc)
    result = AgentRunResult(
        test_name="Customer login",
        status=AgentRunStatus.ACTIONS_COMPLETED,
        test_status=BrowserTestStatus.FAIL,
        url="https://example.com/login",
        started_at=now,
        finished_at=now,
        assertions=[
            AssertionResult(
                assertion=AssertionSpec(assertion="visible", target="Dashboard heading"),
                passed=False,
                actual=False,
                message="Not visible",
            )
        ],
        assertions_evaluated=True,
        failure_reason="Failed assertions: Dashboard heading",
    )
    monkeypatch.setattr(runner, "run_agent", lambda **kwargs: result)

    exit_code = main(
        [
            "run",
            "--url",
            "https://example.com/login",
            "--test",
            "Log in and verify the Dashboard heading",
        ]
    )

    assert exit_code == 1
    assert "Test result:      FAIL" in capsys.readouterr().out
