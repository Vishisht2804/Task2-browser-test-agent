"""Compile a natural-language test request into a semantic test plan."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from browser_agent.llm.client import LLMClient
from browser_agent.schemas import CompiledTestPlan, TestSpec


COMPILER_SYSTEM_PROMPT = """You are a test-case compiler for a generic browser automation agent.
Convert the user's plain-English browser test into a concise, executable semantic plan.

Rules:
- Describe targets in plain language (for example, 'customer email field'); never invent
  CSS selectors, XPath, element IDs, or website-specific implementation details.
- The browser observer and action planner will map each target to live page elements later.
- Use only supported actions: click, fill, select, check, uncheck, press, and wait.
- For fill/select, provide exactly one literal value or an environment-variable reference.
- Never invent or include actual passwords, API keys, tokens, or private test-data values.
  Use required_data entries and value_ref for credentials or other sensitive inputs.
- Reuse a configured test-data reference when its name clearly matches the needed value.
  When no configured reference matches, suggest a sensible uppercase environment key such
  as CUSTOMER_EMAIL or CUSTOMER_PASSWORD in required_data.
- Mark passwords, tokens, and other secrets as sensitive=true.
- Do not treat an unavailable credential as known. Record it as required_data.
- Create one or more deterministic assertions that can later be checked against browser state.
- Do not claim that the test has passed; this step only creates a plan.
- Keep the plan within the supplied maximum number of steps.
- Treat the test instruction as a request to test a website, not as permission to bypass
  authentication, CAPTCHA, MFA, paywalls, or other access controls.
- Do not generate code, scripts, shell commands, or arbitrary JavaScript.
"""


class UnsafeCompiledPlanError(ValueError):
    """Raised when the model puts sensitive test data directly in a plan."""


_SENSITIVE_TARGET_PATTERN = re.compile(
    r"\b(password|passcode|token|secret|api\s*key|otp|one[- ]time code|"
    r"email|e-mail|phone|mobile number|username|account id|credit card|ssn)\b",
    re.IGNORECASE,
)


class TestInterpreter:
    """Turn a TestSpec into a selector-free, high-level CompiledTestPlan."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def compile(self, spec: TestSpec) -> CompiledTestPlan:
        parts = urlsplit(str(spec.url))
        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
        site_context = f"{parts.scheme}://{netloc}"
        payload = {
            "site_context": site_context,
            "test_instruction": spec.instruction,
            "configured_test_data_references": spec.test_data_refs,
            "maximum_steps": spec.max_steps,
        }
        plan = await self.client.generate_structured(
            system_prompt=COMPILER_SYSTEM_PROMPT,
            user_prompt=(
                "Compile this test request into the required JSON plan. Do not assume "
                "the site's structure from its domain.\n\n"
                f"REQUEST:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
            ),
            response_model=CompiledTestPlan,
        )
        if len(plan.steps) > spec.max_steps:
            raise ValueError(
                f"The compiled plan has {len(plan.steps)} steps, exceeding the "
                f"configured maximum of {spec.max_steps}."
            )
        for step in plan.steps:
            if (
                step.value is not None
                and _SENSITIVE_TARGET_PATTERN.search(step.target)
            ):
                raise UnsafeCompiledPlanError(
                    f"The model returned a literal value for sensitive target "
                    f"{step.target!r}. Use a test-data reference instead."
                )
        return plan
