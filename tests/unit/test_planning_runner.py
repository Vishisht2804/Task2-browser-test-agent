import json
from pathlib import Path

import pytest

from browser_agent.agent import planning_runner
from browser_agent.config import Settings
from browser_agent.schemas import (
    ActionType,
    AgentAction,
    AgentDecision,
    AssertionType,
    BrowserObservation,
    CompiledTestPlan,
    ElementInfo,
    FrameInfo,
    PlannedStep,
    TestSpec as BrowserTestSpec,
)


class FakeLLMClient:
    def __init__(self, *responses):
        self.responses = list(responses)

    async def generate_structured(self, *, system_prompt, user_prompt, response_model):
        value = self.responses.pop(0)
        if isinstance(value, response_model):
            return value
        return response_model.model_validate(value)


class FakeSession:
    def __init__(self, settings, headless=None):
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def goto(self, url):
        return object()

    async def screenshot(self, path):
        Path(path).write_bytes(b"fake png")
        return path


class FakeObserver:
    def __init__(self, max_elements_per_frame=500):
        self.max_elements_per_frame = max_elements_per_frame

    async def inspect(self, page):
        return BrowserObservation(
            observation_id="obs_12345678901234567890",
            url="https://example.com/login",
            title="Login",
            frames=[FrameInfo(frame_id="f0", url="https://example.com/login", is_main_frame=True)],
            elements=[
                ElementInfo(
                    element_id="e1",
                    frame_id="f0",
                    tag="input",
                    role="textbox",
                    name="Email",
                    label="Email",
                    input_type="email",
                )
            ],
            visible_text="Login",
        )


def sample_compiled_plan():
    return CompiledTestPlan(
        name="Search test",
        objective="Search for a product",
        steps=[
            PlannedStep(
                action=ActionType.FILL,
                target="search input",
                value="wireless keyboard",
            )
        ],
        assertions=[{"assertion": AssertionType.VISIBLE, "target": "search results"}],
        required_data=[],
    )


@pytest.mark.asyncio
async def test_planning_runner_saves_plan_observation_decision_and_screenshot(
    tmp_path, monkeypatch
):
    expected_observation = BrowserObservation(
        observation_id="obs_12345678901234567890",
        url="https://example.com/login",
        title="Login",
        frames=[FrameInfo(frame_id="f0", url="https://example.com/login", is_main_frame=True)],
        elements=[
            ElementInfo(
                element_id="e1",
                frame_id="f0",
                tag="input",
                role="textbox",
                name="Search",
                label="Search",
                input_type="search",
            )
        ],
        visible_text="Search",
    )
    decision = AgentDecision(
        decision="act",
        reason="The search input matches the current step.",
        action=AgentAction(
            action="fill",
            element_id="e1",
            observation_id=expected_observation.observation_id,
            value="wireless keyboard",
        ),
    )

    class MatchingObserver(FakeObserver):
        async def inspect(self, page):
            return expected_observation

    monkeypatch.setattr(planning_runner, "BrowserSession", FakeSession)
    monkeypatch.setattr(planning_runner, "BrowserObserver", MatchingObserver)
    client = FakeLLMClient(sample_compiled_plan(), decision)
    spec = BrowserTestSpec(
        url="https://example.com/login",
        instruction="Search for a wireless keyboard and verify results appear",
    )

    result = await planning_runner.create_planning_artifacts(
        spec=spec,
        settings=Settings(_env_file=None, artifact_dir=tmp_path),
        client=client,
    )

    assert (result.artifact_dir / "compiled-plan.json").is_file()
    assert (result.artifact_dir / "observation.json").is_file()
    assert (result.artifact_dir / "first-decision.json").is_file()
    assert (result.artifact_dir / "page.png").is_file()
    saved = json.loads((result.artifact_dir / "planning-summary.json").read_text())
    assert "does not execute" in saved["note"]
    assert len(result.artifact_paths) == 5
