"""Deterministic execution of developer-authored Playwright action plans."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
from playwright.async_api import Locator, Page

from browser_agent.config import Settings
from browser_agent.schemas import ActionType, LocatorStrategy, ManualAction, ManualLocator


class LocatorNotFoundError(RuntimeError):
    """Raised when a locator does not match any element."""


class AmbiguousLocatorError(RuntimeError):
    """Raised when a locator matches multiple elements without an explicit index."""


class TestDataError(RuntimeError):
    """Raised when an action references a missing test-data environment variable."""


def resolve_test_data(reference: str, *, env_file: Path | None = None) -> str:
    """Resolve a secret from the process environment or local .env without logging it."""
    value = os.environ.get(reference)
    if value is None:
        selected_env_file = env_file or Path(".env")
        if selected_env_file.is_file():
            value = dotenv_values(selected_env_file).get(reference)
    if value is None:
        raise TestDataError(
            f"Required test-data variable {reference!r} was not found in the environment or .env"
        )
    return value


class BrowserExecutor:
    """Maps a constrained manual action to a Playwright operation."""

    def __init__(self, page: Page, settings: Settings) -> None:
        self.page = page
        self.settings = settings

    def build_locator(self, spec: ManualLocator) -> Locator:
        value = spec.value
        if spec.strategy == LocatorStrategy.ROLE:
            locator = self.page.get_by_role(
                spec.role,
                name=value,
                exact=spec.exact,
            )
        elif spec.strategy == LocatorStrategy.LABEL:
            locator = self.page.get_by_label(value, exact=spec.exact)
        elif spec.strategy == LocatorStrategy.PLACEHOLDER:
            locator = self.page.get_by_placeholder(value, exact=spec.exact)
        elif spec.strategy == LocatorStrategy.TEXT:
            locator = self.page.get_by_text(value, exact=spec.exact)
        elif spec.strategy == LocatorStrategy.TEST_ID:
            locator = self.page.get_by_test_id(value)
        elif spec.strategy == LocatorStrategy.CSS:
            locator = self.page.locator(value)
        else:  # pragma: no cover - Pydantic rejects unknown strategies
            raise ValueError(f"Unsupported locator strategy: {spec.strategy}")

        return locator

    async def _resolve_unique(self, spec: ManualLocator, *, timeout_ms: int) -> Locator:
        locator = self.build_locator(spec)
        if spec.index is not None:
            selected = locator.nth(spec.index)
            await selected.wait_for(state="visible", timeout=timeout_ms)
            return selected

        # Waiting on the first match supports late-rendered controls without letting
        # Playwright's strict-mode behavior silently choose among duplicates.
        await locator.first.wait_for(state="visible", timeout=timeout_ms)
        count = await locator.count()
        if count == 0:
            raise LocatorNotFoundError(
                f"No element matched {self.describe_locator(spec)}"
            )
        if count > 1:
            raise AmbiguousLocatorError(
                f"{self.describe_locator(spec)} matched {count} elements; "
                "provide an explicit index or a more specific locator"
            )
        return locator

    @staticmethod
    def describe_locator(spec: ManualLocator) -> str:
        if spec.strategy == LocatorStrategy.ROLE:
            return f"role={spec.role!r}, name={spec.value!r}"
        return f"{spec.strategy.value}={spec.value!r}"

    async def execute(self, action: ManualAction) -> str:
        timeout = action.timeout_ms or self.settings.browser_action_timeout_ms
        locator = self.build_locator(action.locator)

        # Waiting for a hidden or detached target must also work when it is absent.
        if action.action == ActionType.WAIT:
            count = await locator.count()
            if action.locator.index is not None:
                selected = locator.nth(action.locator.index)
                if action.locator.index >= count and action.wait_state in {"hidden", "detached"}:
                    return (
                        f"Wait condition already satisfied for "
                        f"{self.describe_locator(action.locator)}"
                    )
                await selected.wait_for(state=action.wait_state, timeout=timeout)
            elif action.wait_state in {"hidden", "detached"}:
                if count > 1:
                    raise AmbiguousLocatorError(
                        f"{self.describe_locator(action.locator)} matched {count} elements; "
                        "provide an explicit index or a more specific locator"
                    )
                if count == 1:
                    await locator.wait_for(state=action.wait_state, timeout=timeout)
            else:
                await locator.first.wait_for(state=action.wait_state, timeout=timeout)
                count = await locator.count()
                if count > 1:
                    raise AmbiguousLocatorError(
                        f"{self.describe_locator(action.locator)} matched {count} elements; "
                        "provide an explicit index or a more specific locator"
                    )
            return (
                f"Waited for {self.describe_locator(action.locator)} "
                f"to become {action.wait_state}"
            )

        locator = await self._resolve_unique(action.locator, timeout_ms=timeout)

        if action.action == ActionType.CLICK:
            await locator.click(timeout=timeout)
            return f"Clicked {self.describe_locator(action.locator)}"
        if action.action == ActionType.FILL:
            value = self._action_value(action)
            await locator.fill(value, timeout=timeout)
            return f"Filled {self.describe_locator(action.locator)}"
        if action.action == ActionType.SELECT:
            value = self._action_value(action)
            await locator.select_option(value=value, timeout=timeout)
            return f"Selected an option in {self.describe_locator(action.locator)}"
        if action.action == ActionType.CHECK:
            await locator.check(timeout=timeout)
            return f"Checked {self.describe_locator(action.locator)}"
        if action.action == ActionType.UNCHECK:
            await locator.uncheck(timeout=timeout)
            return f"Unchecked {self.describe_locator(action.locator)}"
        if action.action == ActionType.PRESS:
            await locator.press(action.value, timeout=timeout)
            return f"Pressed {action.value!r} on {self.describe_locator(action.locator)}"

        raise ValueError(f"Unsupported action: {action.action}")

    @staticmethod
    def _action_value(action: ManualAction) -> str:
        if action.value is not None:
            return action.value
        if action.value_ref is not None:
            return resolve_test_data(action.value_ref)
        raise ValueError(f"{action.action.value} action has no value")
