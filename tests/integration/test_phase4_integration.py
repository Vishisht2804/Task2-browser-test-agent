from pathlib import Path

import pytest

from browser_agent.agent import runner
from browser_agent.browser.observer import BrowserObserver
from browser_agent.browser.session import BrowserSession, BrowserStartupError
from browser_agent.config import Settings
from browser_agent.schemas import (
    ActionType,
    AgentAction,
    AgentDecision,
    BrowserObservation,
    CompiledTestPlan,
    PlannedStep,
    AssertionSpec,
    TestStatus as BrowserTestStatus,
)


HTML = """<!doctype html>
<html><head><title>Local Search</title></head><body>
  <main>
    <h1>Search</h1>
    <form id="search-form">
      <label for="query">Search products</label>
      <input id="query" type="search">
      <button type="submit">Search</button>
    </form>
    <p id="results" hidden></p>
  </main>
  <script>
    document.querySelector('#search-form').addEventListener('submit', (event) => {
      event.preventDefault();
      const query = document.querySelector('#query').value;
      const results = document.querySelector('#results');
      results.hidden = false;
      results.textContent = `Results for ${query}`;
    });
  </script>
</body></html>"""


class LocalPageSession(BrowserSession):
    async def goto(self, url: str, *, timeout_ms=None):
        assert self.page is not None
        await self.page.set_content(HTML)
        return self.page


class StaticInterpreter:
    def __init__(self, client):
        pass

    async def compile(self, spec):
        return CompiledTestPlan(
            name="Product search",
            objective="Search for a product and show result text",
            steps=[
                PlannedStep(
                    action=ActionType.FILL,
                    target="product search field",
                    value="wireless keyboard",
                ),
                PlannedStep(action=ActionType.CLICK, target="Search button"),
            ],
            assertions=[
                AssertionSpec(
                    assertion="text_contains",
                    target="page text",
                    expected="wireless keyboard",
                ),
                AssertionSpec(
                    assertion="visible",
                    target="Results for wireless keyboard",
                ),
                AssertionSpec(
                    assertion="value_equals",
                    target="Search products searchbox",
                    expected="wireless keyboard",
                ),
            ],
        )


class ObservationDrivenPlanner:
    def __init__(self, client, **kwargs):
        pass

    async def plan_next(
        self,
        *,
        spec,
        plan,
        observation: BrowserObservation,
        completed_step_indices=(),
        last_action_feedback=None,
    ):
        next_index = next(
            index for index in range(len(plan.steps)) if index not in completed_step_indices
        )
        step = plan.steps[next_index]
        if step.action == ActionType.FILL:
            element = next(
                item for item in observation.elements
                if item.tag == "input" and item.input_type == "search"
            )
        else:
            element = next(
                item for item in observation.elements
                if item.role == "button" and item.name == "Search"
            )
        return AgentDecision(
            decision="act",
            reason=f"The live observation contains the target for step {next_index + 1}.",
            action=AgentAction(
                action=step.action,
                element_id=element.element_id,
                observation_id=observation.observation_id,
                value=step.value,
                value_ref=step.value_ref,
            ),
        )


class NoopClient:
    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_phase4_full_loop_with_real_chromium_and_local_page(monkeypatch, tmp_path):
    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path,
        browser_executable_path=executable if executable.exists() else None,
        browser_action_timeout_ms=2_000,
        browser_navigation_timeout_ms=5_000,
    )
    monkeypatch.setattr(runner, "BrowserSession", LocalPageSession)
    monkeypatch.setattr(runner, "BrowserObserver", BrowserObserver)
    monkeypatch.setattr(runner, "TestInterpreter", StaticInterpreter)
    monkeypatch.setattr(runner, "ActionPlanner", ObservationDrivenPlanner)

    try:
        result = await runner.create_agent_run(
            spec=runner.TestSpec(
                url="https://example.com/search",
                instruction="Search for wireless keyboard and show the results",
                timeout_seconds=30,
            ),
            settings=settings,
            client=NoopClient(),
        )
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")

    assert result.status.value == "ACTIONS_COMPLETED"
    assert result.successful_steps == 2
    run_dir = Path(result.artifact_paths[0]).parent
    observation_files = list((run_dir / "observations").glob("*.json"))
    assert len(observation_files) == 2
    assert any(path.endswith("step-001.png") for path in result.artifact_paths)
    assert any(path.endswith("step-002.png") for path in result.artifact_paths)
    assert result.assertions_evaluated is True
    assert result.test_status == BrowserTestStatus.PASS
    assert len(result.assertions) == 3
    assert all(item.passed for item in result.assertions)


class FailingAssertionInterpreter(StaticInterpreter):
    async def compile(self, spec):
        plan = await super().compile(spec)
        return plan.model_copy(
            update={
                "assertions": [
                    AssertionSpec(
                        assertion="text_contains",
                        target="page text",
                        expected="text-that-is-not-on-the-page",
                    )
                ]
            }
        )


@pytest.mark.asyncio
async def test_phase5_failed_assertion_produces_test_fail(monkeypatch, tmp_path):
    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path,
        browser_executable_path=executable if executable.exists() else None,
        browser_action_timeout_ms=2_000,
        browser_navigation_timeout_ms=5_000,
    )
    monkeypatch.setattr(runner, "BrowserSession", LocalPageSession)
    monkeypatch.setattr(runner, "BrowserObserver", BrowserObserver)
    monkeypatch.setattr(runner, "TestInterpreter", FailingAssertionInterpreter)
    monkeypatch.setattr(runner, "ActionPlanner", ObservationDrivenPlanner)

    try:
        result = await runner.create_agent_run(
            spec=runner.TestSpec(
                url="https://example.com/search",
                instruction="Search for wireless keyboard and show the results",
                timeout_seconds=30,
            ),
            settings=settings,
            client=NoopClient(),
        )
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")

    assert result.status.value == "ACTIONS_COMPLETED"
    assert result.assertions_evaluated is True
    assert result.test_status == BrowserTestStatus.FAIL
    assert len(result.assertions) == 1
    assert result.assertions[0].passed is False
    assert "Failed assertions" in result.failure_reason
