from dataclasses import dataclass

import pytest

from browser_agent.assertions.evaluator import AssertionEvaluator
from browser_agent.schemas import (
    AssertionSpec,
    AssertionType,
    BrowserObservation,
    CompiledTestPlan,
    ElementInfo,
    FrameInfo,
)


class FakeLocator:
    def __init__(self, *, visible=True, enabled=True, checked=False, value=""):
        self.visible = visible
        self.enabled = enabled
        self.checked = checked
        self.value = value

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def is_checked(self):
        return self.checked

    async def input_value(self):
        return self.value

    async def inner_text(self):
        return self.value


class FakeResolver:
    def __init__(self, values):
        self.values = values

    async def resolve_unique(self, element_id, *, observation_id):
        return self.values[element_id]


class FakeBody:
    def __init__(self, text):
        self.text = text

    async def count(self):
        return 1

    async def inner_text(self, timeout=None):
        return self.text


class FakeFrame:
    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        assert selector == "body"
        return FakeBody(self.text)


class FakePage:
    def __init__(self, *, url="https://example.com/dashboard", text="Welcome to Dashboard"):
        self.url = url
        self.frames = [FakeFrame(text)]


def observation(*elements, visible_text="Welcome to Dashboard", suffix="1"):
    return BrowserObservation(
        observation_id=f"obs_{suffix.zfill(20)}",
        url="https://example.com/dashboard",
        title="Dashboard",
        frames=[FrameInfo(frame_id="f0", url="https://example.com/dashboard", is_main_frame=True)],
        elements=list(elements),
        visible_text=visible_text,
    )


def heading(element_id="e1", *, visible=True, name="Dashboard"):
    return ElementInfo(
        element_id=element_id,
        frame_id="f0",
        tag="h1",
        role="heading",
        name=name,
        text=name,
        visible=visible,
    )


def plan(assertion):
    from browser_agent.schemas import PlannedStep

    return CompiledTestPlan(
        name="Test",
        objective="Verify a state",
        steps=[PlannedStep(action="wait", target="page ready")],
        assertions=[assertion],
    )


@pytest.mark.asyncio
async def test_visible_assertion_uses_observed_element_and_live_locator():
    snapshot = observation(heading())
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(),
        assertion=AssertionSpec(assertion="visible", target="Dashboard heading"),
        observation=snapshot,
        resolver=FakeResolver({"e1": FakeLocator(visible=True)}),
    )

    assert result.passed is True
    assert result.actual is True


@pytest.mark.asyncio
async def test_hidden_assertion_passes_for_currently_hidden_element():
    snapshot = observation(heading(visible=False))
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(),
        assertion=AssertionSpec(assertion="hidden", target="Dashboard heading"),
        observation=snapshot,
        resolver=FakeResolver({"e1": FakeLocator(visible=False)}),
    )

    assert result.passed is True
    assert result.actual is True


@pytest.mark.asyncio
async def test_hidden_assertion_passes_when_previously_visible_target_was_removed():
    prior = observation(heading(), suffix="2")
    final = observation(suffix="3")
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(),
        assertion=AssertionSpec(assertion="hidden", target="Dashboard heading"),
        observation=final,
        resolver=FakeResolver({}),
        historical_observations=[prior],
    )

    assert result.passed is True
    assert "previously observed" in result.message


@pytest.mark.asyncio
async def test_hidden_assertion_does_not_pass_when_target_was_never_seen():
    final = observation(suffix="4")
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(),
        assertion=AssertionSpec(assertion="hidden", target="Dashboard heading"),
        observation=final,
        resolver=FakeResolver({}),
    )

    assert result.passed is False
    assert "Could not identify" in result.message


@pytest.mark.asyncio
async def test_text_contains_checks_rendered_page_text_case_insensitively():
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(text="Your search results include Wireless Keyboard"),
        assertion=AssertionSpec(
            assertion="text_contains", target="page text", expected="wireless keyboard"
        ),
        observation=observation(),
        resolver=FakeResolver({}),
    )

    assert result.passed is True
    assert result.actual is True


@pytest.mark.asyncio
async def test_url_contains_checks_current_url():
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(url="https://example.com/account/dashboard?tab=orders"),
        assertion=AssertionSpec(
            assertion="url_contains", target="current URL", expected="/dashboard"
        ),
        observation=observation(),
        resolver=FakeResolver({}),
    )

    assert result.passed is True
    assert "/dashboard" in result.actual


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("assertion", "target", "locator", "expected", "passed"),
    [
        ("checked", "Remember me checkbox", FakeLocator(checked=True), True, True),
        ("checked", "Remember me checkbox", FakeLocator(checked=False), True, False),
        ("enabled", "Continue button", FakeLocator(enabled=False), False, True),
        ("enabled", "Continue button", FakeLocator(enabled=True), False, False),
        ("value_equals", "Search field", FakeLocator(value="keyboard"), "keyboard", True),
        ("value_equals", "Search field", FakeLocator(value="mouse"), "keyboard", False),
    ],
)
async def test_state_and_value_assertions(assertion, target, locator, expected, passed):
    if assertion == "checked":
        element = ElementInfo(
            element_id="e1", frame_id="f0", tag="input", role="checkbox",
            name="Remember me", checked=locator.checked,
        )
    elif assertion == "enabled":
        element = ElementInfo(
            element_id="e1", frame_id="f0", tag="button", role="button",
            name="Continue", enabled=locator.enabled,
        )
    else:
        element = ElementInfo(
            element_id="e1", frame_id="f0", tag="input", role="textbox",
            name="Search", input_type="search",
        )
    snapshot = observation(element)
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(),
        assertion=AssertionSpec(assertion=assertion, target=target, expected=expected),
        observation=snapshot,
        resolver=FakeResolver({"e1": locator}),
    )

    assert result.passed is passed


@pytest.mark.asyncio
async def test_ambiguous_semantic_target_fails_closed():
    snapshot = observation(
        heading("e1"),
        ElementInfo(
            element_id="e2", frame_id="f0", tag="a", role="link",
            name="Dashboard", text="Dashboard",
        ),
    )
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(),
        assertion=AssertionSpec(assertion="visible", target="Dashboard"),
        observation=snapshot,
        resolver=FakeResolver({"e1": FakeLocator(), "e2": FakeLocator()}),
    )

    assert result.passed is False
    assert "ambiguous" in result.message


@pytest.mark.asyncio
async def test_evaluate_all_returns_one_result_per_assertion():
    snapshot = observation(heading())
    compiled = plan(
        AssertionSpec(assertion="visible", target="Dashboard heading")
    )
    compiled = compiled.model_copy(
        update={
            "assertions": [
                AssertionSpec(assertion="visible", target="Dashboard heading"),
                AssertionSpec(assertion="text_contains", target="page text", expected="Dashboard"),
            ]
        }
    )
    results = await AssertionEvaluator().evaluate_all(
        page=FakePage(),
        plan=compiled,
        observation=snapshot,
        resolver=FakeResolver({"e1": FakeLocator()}),
    )

    assert len(results) == 2
    assert all(item.passed for item in results)


@pytest.mark.asyncio
async def test_role_specific_assertion_does_not_match_same_text_on_wrong_role():
    snapshot = observation(
        ElementInfo(
            element_id="e1",
            frame_id="f0",
            tag="a",
            role="link",
            name="Dashboard",
            text="Dashboard",
        )
    )
    result = await AssertionEvaluator().evaluate_one(
        page=FakePage(),
        assertion=AssertionSpec(assertion="visible", target="Dashboard heading"),
        observation=snapshot,
        resolver=FakeResolver({"e1": FakeLocator(visible=True)}),
    )

    assert result.passed is False
    assert "Could not identify" in result.message
