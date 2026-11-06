"""Playwright browser lifecycle, actions, observation, and deterministic runners."""

from browser_agent.browser.locator_resolver import LocatorResolver
from browser_agent.browser.observer import BrowserObserver

__all__ = ["BrowserObserver", "LocatorResolver"]
