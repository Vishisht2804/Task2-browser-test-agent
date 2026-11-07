"""Evaluate compiled test assertions against the final, live browser state.

Assertions are deliberately deterministic: this module does not ask the LLM whether
an expected condition passed. Semantic targets are matched against the latest browser
observation and then queried through the observation-scoped Playwright resolver.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from playwright.async_api import Locator, Page

from browser_agent.browser.locator_resolver import LocatorResolver
from browser_agent.browser.observer import _sanitize_url
from browser_agent.schemas import (
    AssertionResult,
    AssertionSpec,
    AssertionType,
    BrowserObservation,
    CompiledTestPlan,
    ElementInfo,
)

_GENERIC_TARGET_WORDS = {
    "a", "an", "the", "is", "are", "shown", "displayed", "visible", "hidden",
    "element", "control", "field", "input", "textbox", "button", "link", "heading",
    "title", "form", "page", "section", "area", "item", "checkbox", "radio",
    "dropdown", "select", "combobox", "menu", "dialog", "modal", "text", "label",
    "current", "expected", "target", "customer", "user", "account",
}
_ROLE_DESCRIPTORS = {
    "button": "button",
    "link": "link",
    "heading": "heading",
    "title": "heading",
    "textbox": "textbox",
    "searchbox": "searchbox",
    "input": "textbox",
    "field": None,
    "checkbox": "checkbox",
    "radio": "radio",
    "dropdown": "combobox",
    "select": "combobox",
    "combobox": "combobox",
    "form": "form",
    "dialog": "dialog",
    "modal": "dialog",
    "navigation": "navigation",
    "main": "main",
}


class AssertionTargetError(ValueError):
    """Raised when a semantic assertion target cannot be resolved unambiguously."""


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _target_role_descriptor(target: str) -> str | None:
    for token in re.findall(r"[a-z0-9]+", _normalize(target)):
        role = _ROLE_DESCRIPTORS.get(token)
        if role is not None:
            return role
    return None


def _element_match_score(target: str, element: ElementInfo) -> int:
    """Score one observed element against a concise natural-language target."""
    normalized_target = _normalize(target)
    role_descriptor = _target_role_descriptor(target)
    target_tokens = re.findall(r"[a-z0-9]+", normalized_target)
    remaining_tokens = [token for token in target_tokens if token not in _ROLE_DESCRIPTORS]
    if role_descriptor is not None and element.role != role_descriptor:
        return 0

    meaningful_target = {
        token for token in remaining_tokens if token not in _GENERIC_TARGET_WORDS
    }
    if not meaningful_target:
        meaningful_target = set(remaining_tokens)
    if not meaningful_target:
        return 0

    fields = [
        element.name,
        element.label,
        element.placeholder,
        element.text,
        element.id_attribute,
        element.name_attribute,
        element.aria_label,
    ]
    normalized_fields = [_normalize(item) for item in fields if item]
    if not normalized_fields:
        return 0

    role_bonus = 8 if role_descriptor else 0

    if any(value == normalized_target for value in normalized_fields):
        return 100 + role_bonus

    # Ignore descriptor words when checking whether the target phrase appears.
    core_target = " ".join(remaining_tokens)
    if core_target and any(core_target in value for value in normalized_fields):
        return 92 + role_bonus

    field_tokens = [set(re.findall(r"[a-z0-9]+", value)) for value in normalized_fields]
    best_coverage = max(
        (len(meaningful_target & tokens) / len(meaningful_target) for tokens in field_tokens),
        default=0.0,
    )
    if best_coverage == 1.0:
        return 84 + role_bonus
    if best_coverage >= 0.8 and len(meaningful_target) >= 2:
        return 70 + role_bonus
    if best_coverage >= 0.6 and len(meaningful_target) >= 3:
        return 55 + role_bonus
    return 0 if best_coverage == 0 else max(0, int(best_coverage * 40))


def _best_element_match(target: str, observation: BrowserObservation) -> ElementInfo | None:
    scored = [
        (score, element)
        for element in observation.elements
        if (score := _element_match_score(target, element)) > 0
    ]
    if not scored:
        return None
    highest = max(score for score, _ in scored)
    best = [element for score, element in scored if score == highest]
    if len(best) != 1:
        ids = ", ".join(item.element_id for item in best[:5])
        raise AssertionTargetError(
            f"Assertion target {target!r} is ambiguous; matching elements: {ids}. "
            "Use a more specific target such as 'Dashboard heading'."
        )
    # Refuse weak partial matches. A semantic assertion must not pass based on a
    # coincidental shared word in an unrelated control.
    if highest < 55:
        return None
    return best[0]


class AssertionEvaluator:
    """Evaluate every assertion using Playwright state and the final DOM snapshot."""

    async def evaluate_all(
        self,
        *,
        page: Page,
        plan: CompiledTestPlan,
        observation: BrowserObservation,
        resolver: LocatorResolver,
        historical_observations: Sequence[BrowserObservation] = (),
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for assertion in plan.assertions:
            results.append(
                await self.evaluate_one(
                    page=page,
                    assertion=assertion,
                    observation=observation,
                    resolver=resolver,
                    historical_observations=historical_observations,
                )
            )
        return results

    async def evaluate_one(
        self,
        *,
        page: Page,
        assertion: AssertionSpec,
        observation: BrowserObservation,
        resolver: LocatorResolver,
        historical_observations: Sequence[BrowserObservation] = (),
    ) -> AssertionResult:
        try:
            passed, actual, message = await self._evaluate(
                page=page,
                assertion=assertion,
                observation=observation,
                resolver=resolver,
                historical_observations=historical_observations,
            )
            return AssertionResult(
                assertion=assertion,
                passed=passed,
                actual=actual,
                message=message,
            )
        except Exception as exc:  # Fail closed: an evaluation error is never a pass.
            return AssertionResult(
                assertion=assertion,
                passed=False,
                actual=None,
                message=f"Could not evaluate assertion: {type(exc).__name__}: {str(exc)[:400]}",
            )

    async def _evaluate(
        self,
        *,
        page: Page,
        assertion: AssertionSpec,
        observation: BrowserObservation,
        resolver: LocatorResolver,
        historical_observations: Sequence[BrowserObservation],
    ) -> tuple[bool, str | bool | None, str]:
        kind = assertion.assertion
        target = assertion.target

        if kind == AssertionType.URL_CONTAINS:
            current_url = page.url
            passed = str(assertion.expected).casefold() in current_url.casefold()
            return (
                passed,
                _sanitize_url(current_url),
                "Current URL contains the expected fragment."
                if passed
                else "Current URL does not contain the expected fragment.",
            )

        if kind == AssertionType.TEXT_CONTAINS:
            visible_text = await self._visible_page_text(page)
            expected = re.sub(r"\s+", " ", str(assertion.expected)).strip().casefold()
            rendered_text = re.sub(r"\s+", " ", visible_text).strip().casefold()
            passed = expected in rendered_text
            return (
                passed,
                passed,
                "Expected text was found in rendered page text."
                if passed
                else "Expected text was not found in rendered page text.",
            )

        element = _best_element_match(target, observation)
        if (
            element is None
            and kind in {AssertionType.VISIBLE, AssertionType.HIDDEN}
            and _target_role_descriptor(target) is None
        ):
            text_locator = await self._unique_text_locator(page, target)
            if text_locator is not None:
                actual = await text_locator.is_visible()
                passed = actual if kind == AssertionType.VISIBLE else not actual
                expected_label = "visible" if kind == AssertionType.VISIBLE else "hidden"
                return (
                    passed,
                    actual,
                    f"Matching page text is {expected_label}."
                    if passed
                    else f"Matching page text is not {expected_label}.",
                )

        if element is None:
            if kind == AssertionType.HIDDEN and self._was_previously_observed(
                target, historical_observations
            ):
                return (
                    True,
                    False,
                    "Target was previously observed and is now absent from the page.",
                )
            raise AssertionTargetError(
                f"Could not identify an observed element for assertion target {target!r}."
            )

        locator = await resolver.resolve_unique(
            element.element_id,
            observation_id=observation.observation_id,
        )

        if kind == AssertionType.VISIBLE:
            actual = await locator.is_visible()
            return (
                actual,
                actual,
                "Target element is visible." if actual else "Target element is not visible.",
            )

        if kind == AssertionType.HIDDEN:
            actual = await locator.is_visible()
            hidden = not actual
            return (
                hidden,
                hidden,
                "Target element is hidden." if hidden else "Target element is still visible.",
            )

        if kind == AssertionType.ENABLED:
            actual = await locator.is_enabled()
            expected = bool(assertion.expected)
            passed = actual is expected
            return (
                passed,
                actual,
                f"Target enabled state is {actual}; expected {expected}.",
            )

        if kind == AssertionType.CHECKED:
            if element.checked is not None:
                actual = element.checked
            else:
                actual = await locator.is_checked()
            expected = bool(assertion.expected)
            passed = actual is expected
            return (
                passed,
                actual,
                f"Target checked state is {actual}; expected {expected}.",
            )

        if kind == AssertionType.VALUE_EQUALS:
            try:
                actual_value = await locator.input_value()
            except Exception:
                actual_value = await locator.inner_text()
            expected_value = str(assertion.expected)
            passed = actual_value == expected_value
            # Do not copy a potentially personal or secret field value into reports.
            return (
                passed,
                "[REDACTED]" if _looks_sensitive(target, actual_value) else actual_value,
                "Target value matches the expected value."
                if passed
                else "Target value does not match the expected value.",
            )

        raise ValueError(f"Unsupported assertion type: {kind}")

    @staticmethod
    async def _unique_text_locator(page: Page, target: str) -> Locator | None:
        """Fallback for visible text in non-semantic containers such as div/span/p."""
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", _normalize(target))
            if token not in _GENERIC_TARGET_WORDS and token not in _ROLE_DESCRIPTORS
        ]
        phrase = " ".join(tokens)
        if not phrase:
            return None
        matches: list[Locator] = []
        for frame in page.frames:
            try:
                candidate = frame.get_by_text(phrase, exact=True)
                count = await candidate.count()
                for index in range(min(count, 10)):
                    matches.append(candidate.nth(index))
            except Exception:
                continue
        if len(matches) > 1:
            raise AssertionTargetError(
                f"Assertion target {target!r} matches multiple text elements; "
                "make the target more specific."
            )
        return matches[0] if matches else None

    @staticmethod
    async def _visible_page_text(page: Page) -> str:
        parts: list[str] = []
        for frame in page.frames:
            try:
                body = frame.locator("body")
                if await body.count():
                    parts.append(await body.inner_text(timeout=2_000))
            except Exception:
                continue
        return "\n".join(parts)

    @staticmethod
    def _was_previously_observed(
        target: str,
        historical_observations: Sequence[BrowserObservation],
    ) -> bool:
        for snapshot in reversed(historical_observations):
            try:
                match = _best_element_match(target, snapshot)
            except AssertionTargetError:
                continue
            if match is not None:
                return True
        return False


def _looks_sensitive(target: str, value: str) -> bool:
    lowered = target.casefold()
    if any(
        term in lowered
        for term in ("password", "passcode", "token", "secret", "email", "phone", "mobile", "ssn")
    ):
        return True
    return bool(
        re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", value, re.IGNORECASE)
    )
