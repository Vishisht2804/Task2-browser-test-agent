from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from browser_agent.schemas import (
    ActionType,
    AgentAction,
    AgentDecision,
    AgentDecisionType,
    AssertionSpec,
    AssertionType,
    BrowserObservation,
    ElementInfo,
    ExecutionResult,
    FrameInfo,
    TestSpec as BrowserTestSpec,
    TestStatus as BrowserTestStatus,
)


def test_test_spec_accepts_http_url_and_env_refs() -> None:
    spec = BrowserTestSpec(
        url="https://example.com/login",
        instruction="Log in and verify the dashboard appears",
        test_data_refs=["CUSTOMER_EMAIL", "CUSTOMER_PASSWORD"],
    )

    assert str(spec.url) == "https://example.com/login"
    assert spec.max_steps == 25
    assert spec.test_data_refs == ["CUSTOMER_EMAIL", "CUSTOMER_PASSWORD"]


def test_test_spec_rejects_non_http_url() -> None:
    with pytest.raises(ValidationError):
        BrowserTestSpec(url="file:///tmp/page.html", instruction="Open the page")


def test_test_spec_rejects_duplicate_data_references() -> None:
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        BrowserTestSpec(
            url="https://example.com",
            instruction="Open the homepage",
            test_data_refs=["CUSTOMER_EMAIL", "CUSTOMER_EMAIL"],
        )


def test_fill_action_requires_exactly_one_value_source() -> None:
    with pytest.raises(ValidationError):
        AgentAction(
            action=ActionType.FILL,
            element_id="email",
            observation_id="obs_1234567890",
        )

    with pytest.raises(ValidationError):
        AgentAction(
            action=ActionType.FILL,
            element_id="email",
            observation_id="obs_1234567890",
            value="someone@example.com",
            value_ref="CUSTOMER_EMAIL",
        )

    action = AgentAction(
        action=ActionType.FILL,
        element_id="email",
        observation_id="obs_1234567890",
        value_ref="CUSTOMER_EMAIL",
    )
    assert action.value_ref == "CUSTOMER_EMAIL"


def test_observation_rejects_element_with_unknown_frame() -> None:
    with pytest.raises(ValidationError, match="unknown frame"):
        BrowserObservation(
            observation_id="obs_1234567890",
            url="https://example.com",
            title="Example",
            frames=[],
            elements=[
                ElementInfo(
                    element_id="email",
                    frame_id="main",
                    tag="input",
                    role="textbox",
                )
            ],
        )


def test_observation_accepts_main_frame_and_element() -> None:
    observation = BrowserObservation(
        observation_id="obs_1234567890",
        url="https://example.com",
        title="Example",
        frames=[
            FrameInfo(
                frame_id="main",
                url="https://example.com",
                is_main_frame=True,
            )
        ],
        elements=[
            ElementInfo(
                element_id="email",
                frame_id="main",
                tag="input",
                role="textbox",
                label="Email",
            )
        ],
    )

    assert observation.elements[0].label == "Email"


def test_agent_decision_requires_action_when_acting() -> None:
    with pytest.raises(ValidationError):
        AgentDecision(decision=AgentDecisionType.ACT, reason="Need to click login")


def test_assertion_requires_expected_value_for_text_check() -> None:
    with pytest.raises(ValidationError):
        AssertionSpec(assertion=AssertionType.TEXT_CONTAINS, target="page")


def test_execution_result_computes_assertion_counts_and_duration() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
    result = ExecutionResult(
        test_name="Dashboard visible",
        status=BrowserTestStatus.PASS,
        url="https://example.com/dashboard",
        started_at=start,
        finished_at=end,
        assertions=[],
    )

    assert result.duration_seconds == 5.0
    assert result.passed_assertions == 0
    assert result.failed_assertions == 0
