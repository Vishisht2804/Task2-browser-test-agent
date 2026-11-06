import json
from datetime import datetime, timezone

from browser_agent.cli import main
from browser_agent.schemas import ManualExecutionReport, TestStatus as ExecutionStatus


def test_execute_cli_loads_and_validates_manual_plan(tmp_path, monkeypatch, capsys) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "name": "Smoke test",
                "actions": [
                    {
                        "action": "click",
                        "locator": {
                            "strategy": "role",
                            "role": "button",
                            "value": "Continue",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)

    def fake_run_manual_plan(*, url, plan, settings, headless):
        assert url == "https://example.com/"
        assert plan.name == "Smoke test"
        assert headless is None
        return ManualExecutionReport(
            test_name=plan.name,
            status=ExecutionStatus.PASS,
            url=url,
            started_at=now,
            finished_at=now,
        )

    monkeypatch.setattr("browser_agent.browser.runner.run_manual_plan", fake_run_manual_plan)

    exit_code = main(
        [
            "execute",
            "--url",
            "https://example.com",
            "--plan",
            str(plan_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Action sequence: Smoke test" in output
    assert "Status:          PASS" in output
    assert "does not evaluate test assertions" in output


def test_execute_cli_rejects_invalid_plan(tmp_path, capsys) -> None:
    plan_path = tmp_path / "invalid.json"
    plan_path.write_text('{"name": "Missing actions", "actions": []}', encoding="utf-8")

    exit_code = main(
        ["execute", "--url", "https://example.com", "--plan", str(plan_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Invalid action plan" in captured.err
