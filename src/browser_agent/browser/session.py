"""Playwright browser lifecycle and page-level diagnostics."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from browser_agent.config import Settings


class BrowserStartupError(RuntimeError):
    """Raised when Playwright or the configured browser cannot be started."""


class BrowserSession:
    """Owns one Playwright process, browser, isolated context, and page."""

    def __init__(self, settings: Settings, *, headless: bool | None = None) -> None:
        self.settings = settings
        self.headless = settings.browser_headless if headless is None else headless
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.pages: list[Page] = []
        self.popups: list[Page] = []
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []

    async def __aenter__(self) -> Self:
        try:
            self.playwright = await async_playwright().start()
            launch_options: dict[str, object] = {"headless": self.headless}
            if self.settings.browser_executable_path:
                launch_options["executable_path"] = str(
                    self.settings.browser_executable_path
                )
            self.browser = await self.playwright.chromium.launch(**launch_options)
            self.context = await self.browser.new_context(
                viewport={"width": 1440, "height": 900},
                ignore_https_errors=False,
            )
            self.context.on("page", self._record_new_page)
            self.page = await self.context.new_page()
            self._track_page(self.page)
            return self
        except Exception as exc:
            await self.close()
            raise BrowserStartupError(
                "Could not start Chromium. Install the browser with "
                "`python -m playwright install chromium`, or configure "
                "BA_BROWSER_EXECUTABLE_PATH to an installed Chromium executable. "
                f"Original error: {exc}"
            ) from exc

    def _track_page(self, page: Page) -> None:
        if page not in self.pages:
            self.pages.append(page)
            page.set_default_timeout(self.settings.browser_action_timeout_ms)
            page.on("console", self._record_console_message)
            page.on("pageerror", self._record_page_error)
            page.on("popup", self._record_popup)

    def _record_new_page(self, page: Page) -> None:
        self._track_page(page)

    def _record_popup(self, page: Page) -> None:
        if page not in self.popups:
            self.popups.append(page)
        self._track_page(page)

    def _record_console_message(self, message: object) -> None:
        if getattr(message, "type", None) == "error":
            self.console_errors.append(str(getattr(message, "text", message)))

    def _record_page_error(self, error: Exception) -> None:
        self.page_errors.append(str(error))

    async def goto(self, url: str, *, timeout_ms: int | None = None) -> Page:
        if self.page is None:
            raise RuntimeError("Browser session has not been started")
        timeout = timeout_ms or self.settings.browser_navigation_timeout_ms
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        return self.page

    async def screenshot(self, path: str, *, full_page: bool = True) -> str:
        if self.page is None:
            raise RuntimeError("Browser session has not been started")
        await self.page.screenshot(path=path, full_page=full_page, animations="disabled")
        return path

    async def close(self) -> None:
        """Close resources in reverse creation order, even after partial startup."""
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self.playwright is not None:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        self.page = None
        self.pages.clear()
        self.popups.clear()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
