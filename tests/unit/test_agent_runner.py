import json
from pathlib import Path

import pytest

from browser_agent.agent import runner
from browser_agent.config import Settings
from browser_agent.schemas import (
    ActionType,
    AgentAction,
    AgentDecision,
    AgentRunStatus,
    AssertionResult,
    AssertionType,
    TestStatus as BrowserTestStatus,
    BrowserObservation,
    CompiledTestPlan,
    ElementInfo,
    FrameInfo,
    PlannedStep,
    TestSpec as BrowserTestSpec,
)


class FakePage:
    def __init__(self):
        self.closed = False
        self.screenshots = []

    def is_closed(self):
        return self.closed

    async def screenshot(self, *, path, **kwargs):
        Path(path).write_bytes(b"fake png")
        self.screenshots.append(path)


class FakeSession:
    instances = []

    def __init__(self, settings, headless=None):
        self.settings = settings
        self.page = FakePage()
        self.pages = [self.page]
        self.popups = []
        self.console_errors = ["console warning for test"]
        self.page_errors = []
        FakeSession.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def goto(self, url):
        return self.page


class FakeObserver:
    instances = []

    def __init__(self, max_elements_per_frame=500):
        self.index = 0
        self.max_elements_per_frame = max_elements_per_frame
        FakeObserver.instances.append(self)

    async def inspect(self, page):
        self.index += 1
        return BrowserObservation(
            observation_id=f"obs_{self.index:020d}",
            url="https://example.com/login",
            title="Example",
            frames=[
                FrameInfo(frame_id="f0", url="https://example.com/login", is_main_frame=True)
            ],
            elements=[
                ElementInfo(
                    element_id="e1",
                    frame_id="f0",
                    tag="input",
                    role="textbox",
                    name="Email",
                    label="Email",
                    input_type="email",
                ),
                ElementInfo(
                    element_id="e2",
                    frame_id="f0",
                    tag="button",
                    role="button",
                    name="Login",
                ),
            ],
            visible_text="Example login",
        )

    def locator_resolver(self):
        return object()


class FakeAssertionEvaluator:
    calls = []

    async def evaluate_all(
        self, *, page, plan, observation, resolver, historical_observations=()
    ):
        self.calls.append(
            {
                "assertions": len(plan.assertions),
                "observation_id": observation.observation_id,
                "history_count": len(historical_observations),
            }
        )
        return [
            AssertionResult(
                assertion=assertion,
                passed=True,
                actual=True,
                message="Fake assertion passed.",
            )
            for assertion in plan.assertions
        ]


class FakeInterpreter:
    plan = None

    def __init__(self, client):
        self.client = client

    async def compile(self, spec):
        return self.plan


class FakePlanner:
    decisions = []
    calls = []

    def __init__(self, client, **kwargs):
        self.client = client

    async def plan_next(
        self,
        *,
        spec,
        plan,
        observation,
        completed_step_indices=(),
        last_action_feedback=None,
    ):
        self.calls.append(
            {
                "completed": sorted(completed_step_indices),
                "feedback": last_action_feedback,
                "observation_id": observation.observation_id,
            }
        )
        if self.decisions:
            item = self.decisions.pop(0)
            if callable(item):
                return item(observation, plan, completed_step_indices)
            return item
        next_index = next(
            index for index in range(len(plan.steps)) if index not in completed_step_indices
        )
        step = plan.steps[next_index]
        return AgentDecision(
            decision="act",
            reason=f"Execute step {next_index + 1}",
            action=AgentAction(
                action=step.action,
                element_id="e1" if step.action == ActionType.FILL else "e2",
                observation_id=observation.observation_id,
                value=step.value,
                value_ref=step.value_ref,
                wait_state=step.wait_state,
            ),
        )


class FakeExecutor:
    outcomes = []
    calls = []

    def __init__(self, resolver, settings):
        pass

    async def execute(self, action):
        self.calls.append(action)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return "executed"


def sample_plan(step_count=2):
    steps = [
        PlannedStep(
            action="fill",
            target="customer email field",
            value="customer@example.com",
        ),
        PlannedStep(action="click", target="login button"),
    ][:step_count]
    return CompiledTestPlan(
        name="Customer login",
        objective="Log in and show the dashboard",
        steps=steps,
        assertions=[{"assertion": AssertionType.VISIBLE, "target": "dashboard"}],
    )


def sample_spec(max_steps=10):
    return BrowserTestSpec(
        url="https://example.com/login",
        instruction="Log in and verify the dashboard appears",
        max_steps=max_steps,
        timeout_seconds=30,
    )


def patch_runner(monkeypatch, tmp_path, plan=None):
    FakeSession.instances.clear()
    FakeObserver.instances.clear()
    FakePlanner.calls.clear()
    FakePlanner.decisions = []
    FakeExecutor.calls = []
    FakeExecutor.outcomes = []
    FakeAssertionEvaluator.calls = []
    FakeInterpreter.plan = plan or sample_plan()
    monkeypatch.setattr(runner, "BrowserSession", FakeSession)
    monkeypatch.setattr(runner, "BrowserObserver", FakeObserver)
    monkeypatch.setattr(runner, "TestInterpreter", FakeInterpreter)
    monkeypatch.setattr(runner, "ActionPlanner", FakePlanner)
    monkeypatch.setattr(runner, "ObservedActionExecutor", FakeExecutor)
    monkeypatch.setattr(runner, "AssertionEvaluator", FakeAssertionEvaluator)
    return Settings(_env_file=None, artifact_dir=tmp_path)


@pytest.mark.asyncio
async def test_agent_runner_executes_all_steps_and_saves_history(monkeypatch, tmp_path):
    settings = patch_runner(monkeypatch, tmp_path)

    result = await runner.create_agent_run(
        spec=sample_spec(), settings=settings, client=object()
    )

    assert result.status == AgentRunStatus.ACTIONS_COMPLETED
    assert result.completed_step_indices == [0, 1]
    assert result.successful_steps == 2
    assert result.failed_attempts == 0
    assert result.assertions_evaluated is True
    assert result.test_status == BrowserTestStatus.PASS
    assert len(result.assertions) == 1 and result.assertions[0].passed
    assert len(FakeAssertionEvaluator.calls) == 1
    assert FakeExecutor.calls[0].value == "customer@example.com"
    assert len(FakePlanner.calls) == 2
    assert FakePlanner.calls[1]["completed"] == [0]
    assert (Path(result.artifact_paths[0])).name == "compiled-plan.json"
    report_path = next(
        Path(path) for path in result.artifact_paths if path.endswith("execution.json")
    )
    report = json.loads(report_path.read_text())
    assert report["status"] == "ACTIONS_COMPLETED"
    assert report["steps"][0]["action"]["value"] == "[REDACTED]"
    events_path = next(Path(path) for path in result.artifact_paths if path.endswith("events.json"))
    events = json.loads(events_path.read_text())
    assert len(events) == 3
    assert events[0]["decision"]["action"]["value"] == "[REDACTED]"
    assert any(path.endswith("final.png") for path in result.artifact_paths)
    assert result.console_errors == ["console warning for test"]


@pytest.mark.asyncio
async def test_agent_runner_reobserves_and_retries_failed_action(monkeypatch, tmp_path):
    settings = patch_runner(monkeypatch, tmp_path)
    FakeExecutor.outcomes = [RuntimeError("element detached"), "retry succeeded", "login clicked"]

    result = await runner.create_agent_run(
        spec=sample_spec(), settings=settings, max_retries_per_step=1, client=object()
    )

    assert result.status == AgentRunStatus.ACTIONS_COMPLETED
    assert len(result.steps) == 3
    assert result.failed_attempts == 1
    assert len(FakeObserver.instances) == 1
    assert FakeObserver.instances[0].index == 4
    assert FakePlanner.calls[1]["feedback"]
    assert "element detached" in FakePlanner.calls[1]["feedback"]


@pytest.mark.asyncio
async def test_agent_runner_stops_after_retry_limit(monkeypatch, tmp_path):
    settings = patch_runner(monkeypatch, tmp_path, plan=sample_plan(step_count=1))
    FakeExecutor.outcomes = [RuntimeError("not clickable"), RuntimeError("still not clickable")]

    result = await runner.create_agent_run(
        spec=sample_spec(), settings=settings, max_retries_per_step=1, client=object()
    )

    assert result.status == AgentRunStatus.FAILED
    assert len(result.steps) == 2
    assert result.failed_attempts == 2
    assert "failed after 1 retries" in result.failure_reason


@pytest.mark.asyncio
async def test_agent_runner_respects_action_attempt_limit(monkeypatch, tmp_path):
    settings = patch_runner(monkeypatch, tmp_path)

    result = await runner.create_agent_run(
        spec=sample_spec(max_steps=1), settings=settings, client=object()
    )

    assert result.status == AgentRunStatus.STEP_LIMIT
    assert len(result.steps) == 1
    assert result.completed_step_indices == [0]


@pytest.mark.asyncio
async def test_agent_runner_stops_when_planner_blocks(monkeypatch, tmp_path):
    settings = patch_runner(monkeypatch, tmp_path)
    FakePlanner.decisions = [
        AgentDecision(decision="blocked", reason="CAPTCHA requires human intervention.")
    ]

    result = await runner.create_agent_run(
        spec=sample_spec(), settings=settings, client=object()
    )

    assert result.status == AgentRunStatus.BLOCKED
    assert "CAPTCHA" in result.failure_reason
    assert result.steps == []
    assert FakeExecutor.calls == []


def test_runner_refuses_more_than_five_retries(monkeypatch, tmp_path):
    settings = patch_runner(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="between 0 and 5"):
        import asyncio

        asyncio.run(
            runner.create_agent_run(
                spec=sample_spec(),
                settings=settings,
                max_retries_per_step=6,
                client=object(),
            )
        )


def test_persisted_plan_and_assertion_results_redact_private_values():
    from browser_agent.agent.runner import _safe_assertion_result, _safe_plan_payload
    from browser_agent.schemas import AssertionResult, AssertionSpec

    private_email = "customer.private@example.com"
    plan = CompiledTestPlan(
        name="Email verification",
        objective="Verify the email value",
        steps=[
            PlannedStep(
                action="fill",
                target="customer email field",
                value=private_email,
            )
        ],
        assertions=[
            AssertionSpec(
                assertion="value_equals",
                target="customer email field",
                expected=private_email,
            )
        ],
    )
    payload = _safe_plan_payload(plan)
    assert payload["steps"][0]["value"] == "[REDACTED]"
    assert payload["assertions"][0]["expected"] == "[REDACTED]"

    raw_result = AssertionResult(
        assertion=plan.assertions[0],
        passed=True,
        actual=private_email,
        message="Value matches.",
    )
    safe_result = _safe_assertion_result(raw_result)
    assert safe_result.assertion.expected == "[REDACTED]"
    assert safe_result.actual == "[REDACTED]"
