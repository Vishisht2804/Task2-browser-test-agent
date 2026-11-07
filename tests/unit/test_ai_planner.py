import pytest

from browser_agent.agent.planner import ActionPlanner, PlannerOutputError
from browser_agent.schemas import (
    ActionType,
    AgentAction,
    AgentDecision,
    AgentDecisionType,
    AssertionType,
    BrowserObservation,
    CompiledTestPlan,
    DataRequirement,
    ElementInfo,
    FrameInfo,
    PlannedStep,
    TestSpec as BrowserTestSpec,
)


class FakeLLMClient:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    async def generate_structured(self, *, system_prompt, user_prompt, response_model):
        self.calls.append((system_prompt, user_prompt, response_model))
        if isinstance(self.response, response_model):
            return self.response
        return response_model.model_validate(self.response)


def sample_plan() -> CompiledTestPlan:
    return CompiledTestPlan(
        name="Customer login",
        objective="Authenticate and show the customer dashboard",
        steps=[
            PlannedStep(
                action="fill",
                target="customer email field",
                value_ref="CUSTOMER_EMAIL",
            ),
            PlannedStep(
                action="fill",
                target="customer password field",
                value_ref="CUSTOMER_PASSWORD",
            ),
            PlannedStep(action="click", target="login button"),
        ],
        assertions=[{"assertion": AssertionType.VISIBLE, "target": "dashboard heading"}],
        required_data=[
            DataRequirement(key="CUSTOMER_EMAIL", purpose="Customer email"),
            DataRequirement(
                key="CUSTOMER_PASSWORD", purpose="Customer password", sensitive=True
            ),
        ],
    )


def sample_observation(*, visible=True, element_id="e1") -> BrowserObservation:
    return BrowserObservation(
        observation_id="obs_12345678901234567890",
        url="https://example.com/login",
        title="Customer Login",
        frames=[FrameInfo(frame_id="f0", url="https://example.com/login", is_main_frame=True)],
        elements=[
            ElementInfo(
                element_id=element_id,
                frame_id="f0",
                tag="input",
                role="textbox",
                name="Email",
                label="Email",
                input_type="email",
                visible=visible,
                enabled=True,
            )
        ],
        visible_text="Customer Login",
    )


def sample_spec(*, refs=("CUSTOMER_EMAIL", "CUSTOMER_PASSWORD")) -> BrowserTestSpec:
    return BrowserTestSpec(
        url="https://example.com/login",
        instruction="Log in as a customer and verify the dashboard",
        test_data_refs=list(refs),
    )


def valid_decision(observation: BrowserObservation) -> AgentDecision:
    return AgentDecision(
        decision=AgentDecisionType.ACT,
        reason="The observed email textbox matches the current step.",
        action=AgentAction(
            action=ActionType.FILL,
            element_id="e1",
            observation_id=observation.observation_id,
            value_ref="CUSTOMER_EMAIL",
        ),
    )


@pytest.mark.asyncio
async def test_planner_maps_semantic_step_to_observed_element():
    observation = sample_observation()
    client = FakeLLMClient(valid_decision(observation))

    decision = await ActionPlanner(client).plan_next(
        spec=sample_spec(), plan=sample_plan(), observation=observation
    )

    assert decision.decision == AgentDecisionType.ACT
    assert decision.action.element_id == "e1"
    assert decision.action.observation_id == observation.observation_id
    prompt = client.calls[0][1]
    assert "CUSTOMER_EMAIL" in prompt
    assert "Customer Login" in prompt


@pytest.mark.asyncio
async def test_planner_blocks_when_required_data_reference_was_not_declared():
    observation = sample_observation()
    client = FakeLLMClient(valid_decision(observation))

    decision = await ActionPlanner(client).plan_next(
        spec=sample_spec(refs=()), plan=sample_plan(), observation=observation
    )

    assert decision.decision == AgentDecisionType.BLOCKED
    assert "CUSTOMER_EMAIL" in decision.reason
    assert client.calls == []


@pytest.mark.asyncio
async def test_planner_rejects_unknown_element_id():
    observation = sample_observation()
    action = valid_decision(observation).action.model_copy(update={"element_id": "e99"})
    decision = valid_decision(observation).model_copy(update={"action": action})
    client = FakeLLMClient(decision)

    with pytest.raises(PlannerOutputError, match="unknown element ID"):
        await ActionPlanner(client).plan_next(
            spec=sample_spec(), plan=sample_plan(), observation=observation
        )


@pytest.mark.asyncio
async def test_planner_rejects_stale_observation_id():
    observation = sample_observation()
    decision = valid_decision(observation).model_copy(
        update={
            "action": valid_decision(observation).action.model_copy(
                update={"observation_id": "obs_00000000000000000000"}
            )
        }
    )
    client = FakeLLMClient(decision)

    with pytest.raises(PlannerOutputError, match="stale observation"):
        await ActionPlanner(client).plan_next(
            spec=sample_spec(), plan=sample_plan(), observation=observation
        )


@pytest.mark.asyncio
async def test_planner_rejects_wrong_action_type_for_current_step():
    observation = sample_observation()
    wrong = AgentDecision(
        decision="act",
        reason="Try clicking the field",
        action=AgentAction(
            action="click",
            element_id="e1",
            observation_id=observation.observation_id,
        ),
    )
    client = FakeLLMClient(wrong)

    with pytest.raises(PlannerOutputError, match="current step requires 'fill'"):
        await ActionPlanner(client).plan_next(
            spec=sample_spec(), plan=sample_plan(), observation=observation
        )


@pytest.mark.asyncio
async def test_planner_rejects_non_fillable_element_for_fill_step():
    observation = sample_observation()
    button = observation.model_copy(
        update={
            "elements": [
                observation.elements[0].model_copy(
                    update={"tag": "button", "role": "button", "input_type": None}
                )
            ]
        }
    )
    client = FakeLLMClient(valid_decision(button))

    with pytest.raises(PlannerOutputError, match="not a fillable control"):
        await ActionPlanner(client).plan_next(
            spec=sample_spec(), plan=sample_plan(), observation=button
        )


@pytest.mark.asyncio
async def test_planner_returns_finish_only_when_all_semantic_steps_are_completed():
    observation = sample_observation()
    client = FakeLLMClient()

    decision = await ActionPlanner(client).plan_next(
        spec=sample_spec(),
        plan=sample_plan(),
        observation=observation,
        completed_step_indices=[0, 1, 2],
    )

    assert decision.decision == AgentDecisionType.FINISH
    assert "assertions" in decision.reason
    assert client.calls == []


@pytest.mark.asyncio
async def test_planner_accepts_blocked_model_decision():
    observation = sample_observation()
    client = FakeLLMClient(
        AgentDecision(decision="blocked", reason="A CAPTCHA requires human intervention.")
    )

    decision = await ActionPlanner(client).plan_next(
        spec=sample_spec(), plan=sample_plan(), observation=observation
    )

    assert decision.decision == AgentDecisionType.BLOCKED
