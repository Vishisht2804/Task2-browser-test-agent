import pytest
from pydantic import ValidationError

from browser_agent.schemas import ManualAction, ManualLocator, ManualTestPlan


def test_manual_plan_accepts_semantic_locator_and_env_reference() -> None:
    plan = ManualTestPlan.model_validate(
        {
            "name": "Customer login",
            "actions": [
                {
                    "action": "fill",
                    "locator": {"strategy": "label", "value": "Email"},
                    "value_ref": "CUSTOMER_EMAIL",
                },
                {
                    "action": "click",
                    "locator": {
                        "strategy": "role",
                        "role": "button",
                        "value": "Login",
                    },
                },
            ],
        }
    )

    assert len(plan.actions) == 2
    assert plan.actions[0].value_ref == "CUSTOMER_EMAIL"


def test_manual_locator_requires_role_for_role_strategy() -> None:
    with pytest.raises(ValidationError, match="require a role"):
        ManualLocator(strategy="role", value="Login")


def test_manual_action_requires_one_value_source_for_fill() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ManualAction(
            action="fill",
            locator={"strategy": "label", "value": "Email"},
        )


def test_wait_state_only_applies_to_wait_action() -> None:
    with pytest.raises(ValidationError, match="only be customized"):
        ManualAction(
            action="click",
            locator={"strategy": "text", "value": "Continue"},
            wait_state="hidden",
        )


def test_manual_plan_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ManualTestPlan(name="Bad plan", actions=[], unexpected=True)
