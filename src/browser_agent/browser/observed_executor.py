"""Execute constrained actions against IDs returned by BrowserObserver."""

from __future__ import annotations

from browser_agent.browser.executor import resolve_test_data
from browser_agent.browser.locator_resolver import LocatorResolver
from browser_agent.config import Settings
from browser_agent.schemas import ActionType, AgentAction


class ObservedActionExecutor:
    """Playwright action executor for observation-scoped element identifiers."""

    def __init__(self, resolver: LocatorResolver, settings: Settings) -> None:
        self.resolver = resolver
        self.settings = settings

    async def execute(self, action: AgentAction) -> str:
        timeout = action.timeout_ms or self.settings.browser_action_timeout_ms
        locator = self.resolver.resolve(
            action.element_id,
            observation_id=action.observation_id,
        )

        if action.action == ActionType.WAIT:
            count = await locator.count()
            if action.wait_state in {"hidden", "detached"} and count == 0:
                return f"Wait condition {action.wait_state!r} already satisfied"
            if count == 0:
                await locator.first.wait_for(state=action.wait_state, timeout=timeout)
                return f"Element {action.element_id} reached state {action.wait_state!r}"
            if count > 1:
                raise RuntimeError(
                    f"Observed element {action.element_id!r} matched {count} nodes"
                )
            await locator.wait_for(state=action.wait_state, timeout=timeout)
            return f"Element {action.element_id} reached state {action.wait_state!r}"

        selected = await self.resolver.resolve_unique(
            action.element_id,
            observation_id=action.observation_id,
            timeout_ms=timeout,
        )
        if action.action == ActionType.CLICK:
            await selected.click(timeout=timeout)
            return f"Clicked observed element {action.element_id}"
        if action.action == ActionType.FILL:
            value = (
                action.value
                if action.value is not None
                else resolve_test_data(action.value_ref or "")
            )
            await selected.fill(value, timeout=timeout)
            return f"Filled observed element {action.element_id}"
        if action.action == ActionType.SELECT:
            value = (
                action.value
                if action.value is not None
                else resolve_test_data(action.value_ref or "")
            )
            await selected.select_option(value, timeout=timeout)
            return f"Selected an option in observed element {action.element_id}"
        if action.action == ActionType.CHECK:
            await selected.check(timeout=timeout)
            return f"Checked observed element {action.element_id}"
        if action.action == ActionType.UNCHECK:
            await selected.uncheck(timeout=timeout)
            return f"Unchecked observed element {action.element_id}"
        if action.action == ActionType.PRESS:
            await selected.press(action.value or "", timeout=timeout)
            return f"Pressed {action.value!r} on observed element {action.element_id}"

        raise ValueError(f"Unsupported observed action: {action.action}")
