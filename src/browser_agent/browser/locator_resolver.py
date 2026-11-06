"""Resolve observation-scoped element IDs to strict Playwright locators."""

from __future__ import annotations

from playwright.async_api import Frame, Locator

from browser_agent.schemas import BrowserObservation


class StaleObservationError(RuntimeError):
    """Raised when an action refers to a different browser observation."""


class ObservedElementNotFoundError(RuntimeError):
    """Raised when an observed element no longer exists in the page."""


class AmbiguousObservedElementError(RuntimeError):
    """Raised if a temporary observation marker unexpectedly matches many nodes."""


class LocatorResolver:
    """Map an element ID from one observation to its exact DOM node.

    The observer temporarily tags each discovered element with two data attributes.
    The attributes are scoped to an observation ID and are removed on the next scan.
    This makes element IDs safe to use without fabricating CSS paths or relying on
    potentially duplicated accessible names.
    """

    def __init__(
        self,
        observation: BrowserObservation,
        frames: dict[str, Frame],
    ) -> None:
        self.observation = observation
        self.frames = dict(frames)
        self._elements = {element.element_id: element for element in observation.elements}

    def resolve(self, element_id: str, *, observation_id: str) -> Locator:
        if observation_id != self.observation.observation_id:
            raise StaleObservationError(
                "Action refers to a stale browser observation; inspect the page again "
                "and plan against the latest observation."
            )
        element = self._elements.get(element_id)
        if element is None:
            raise ObservedElementNotFoundError(
                f"Element {element_id!r} was not present in observation "
                f"{self.observation.observation_id!r}."
            )
        frame = self.frames.get(element.frame_id)
        if frame is None:
            raise ObservedElementNotFoundError(
                f"Frame {element.frame_id!r} for element {element_id!r} is no longer available."
            )
        selector = (
            f'[data-ba-observation="{self.observation.observation_id}"]'
            f'[data-ba-element="{element_id}"]'
        )
        return frame.locator(selector)

    async def resolve_unique(
        self,
        element_id: str,
        *,
        observation_id: str,
        timeout_ms: int = 1_000,
    ) -> Locator:
        locator = self.resolve(element_id, observation_id=observation_id)
        try:
            await locator.wait_for(state="attached", timeout=timeout_ms)
        except Exception as exc:
            raise ObservedElementNotFoundError(
                f"Observed element {element_id!r} is no longer attached to the page."
            ) from exc
        count = await locator.count()
        if count == 0:
            raise ObservedElementNotFoundError(
                f"Observed element {element_id!r} is no longer attached to the page."
            )
        if count > 1:
            raise AmbiguousObservedElementError(
                f"Observation marker for {element_id!r} matched {count} elements."
            )
        return locator
