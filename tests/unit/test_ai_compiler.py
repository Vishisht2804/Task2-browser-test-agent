import pytest
from pydantic import ValidationError

from browser_agent.agent.compiler import (
    TestInterpreter as Interpreter,
    UnsafeCompiledPlanError,
)
from browser_agent.schemas import (
    ActionType,
    AssertionType,
    CompiledTestPlan,
    DataRequirement,
    PlannedStep,
    TestSpec as BrowserTestSpec,
)


class FakeLLMClient:
    def __init__(self, response):
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
        objective="Authenticate as a customer and display the dashboard",
        preconditions=["A customer test account is available"],
        steps=[
            PlannedStep(
                action=ActionType.FILL,
                target="customer email field",
                value_ref="CUSTOMER_EMAIL",
            ),
            PlannedStep(
                action=ActionType.FILL,
                target="customer password field",
                value_ref="CUSTOMER_PASSWORD",
            ),
            PlannedStep(action=ActionType.CLICK, target="login button"),
        ],
        assertions=[
            {
                "assertion": AssertionType.VISIBLE,
                "target": "customer dashboard heading",
            }
        ],
        required_data=[
            DataRequirement(
                key="CUSTOMER_EMAIL",
                purpose="Email address for the customer test account",
            ),
            DataRequirement(
                key="CUSTOMER_PASSWORD",
                purpose="Password for the customer test account",
                sensitive=True,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_interpreter_compiles_plain_english_without_selectors_or_secret_values(
    monkeypatch,
):
    monkeypatch.setenv("CUSTOMER_PASSWORD", "local-secret-value-never-send")
    client = FakeLLMClient(sample_plan())
    spec = BrowserTestSpec(
        url="https://alice:secret-url-password@example.com/login?token=should-not-be-included",
        instruction="Log in as a customer and verify the dashboard is shown",
        test_data_refs=["CUSTOMER_EMAIL", "CUSTOMER_PASSWORD"],
    )

    plan = await Interpreter(client).compile(spec)

    assert plan.name == "Customer login"
    assert len(plan.steps) == 3
    assert plan.assertions[0].assertion == AssertionType.VISIBLE
    assert "CUSTOMER_PASSWORD" in client.calls[0][1]
    assert "should-not-be-included" not in client.calls[0][1]
    assert "alice" not in client.calls[0][1]
    assert "secret-url-password" not in client.calls[0][1]
    assert "local-secret-value-never-send" not in client.calls[0][1]
    assert "CSS selector" in client.calls[0][0]


@pytest.mark.asyncio
async def test_interpreter_rejects_compiled_plan_over_max_steps():
    plan = sample_plan()
    too_many_steps = plan.model_copy(
        update={"steps": [plan.steps[0], plan.steps[1], plan.steps[2], plan.steps[2]]}
    )
    client = FakeLLMClient(too_many_steps)
    spec = BrowserTestSpec(
        url="https://example.com",
        instruction="Log in and verify the dashboard",
        max_steps=3,
    )

    with pytest.raises(ValueError, match="exceeding the configured maximum"):
        await Interpreter(client).compile(spec)


def test_compiled_plan_rejects_step_ref_not_declared_as_required_data():
    with pytest.raises(ValidationError, match="missing from required_data"):
        CompiledTestPlan(
            name="Search",
            objective="Search for an item",
            steps=[
                {
                    "action": "fill",
                    "target": "search field",
                    "value_ref": "SEARCH_TERM",
                }
            ],
            assertions=[{"assertion": "visible", "target": "results list"}],
            required_data=[],
        )


@pytest.mark.asyncio
async def test_interpreter_rejects_literal_sensitive_values():
    unsafe = CompiledTestPlan(
        name="Login",
        objective="Log in to a test account",
        steps=[
            PlannedStep(
                action=ActionType.FILL,
                target="customer password field",
                value="do-not-send-this-password",
            )
        ],
        assertions=[{"assertion": "visible", "target": "dashboard"}],
        required_data=[],
    )
    client = FakeLLMClient(unsafe)
    spec = BrowserTestSpec(
        url="https://example.com/login",
        instruction="Log in and verify the dashboard",
    )

    with pytest.raises(UnsafeCompiledPlanError, match="Use a test-data reference"):
        await Interpreter(client).compile(spec)
