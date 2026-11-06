from pathlib import Path

import pytest

from browser_agent.browser.executor import AmbiguousLocatorError, BrowserExecutor
from browser_agent.browser.session import BrowserSession, BrowserStartupError
from browser_agent.config import Settings
from browser_agent.schemas import ManualAction, ManualLocator


async def _skip_if_chromium_unavailable(settings: Settings) -> None:
    try:
        async with BrowserSession(settings):
            pass
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")


@pytest.mark.asyncio
async def test_browser_session_can_navigate_to_blank_page(tmp_path: Path) -> None:
    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path,
        browser_executable_path=executable if executable.exists() else None,
    )
    try:
        async with BrowserSession(settings) as session:
            page = await session.goto("about:blank")
            assert page.url == "about:blank"
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")


@pytest.mark.asyncio
async def test_browser_session_tracks_popups_and_new_pages(tmp_path: Path) -> None:
    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path,
        browser_executable_path=executable if executable.exists() else None,
    )
    try:
        async with BrowserSession(settings) as session:
            assert session.page is not None
            await session.page.set_content(
                "<button id='open' onclick=\"window.open('about:blank')\">Open</button>"
            )
            async with session.page.expect_popup() as popup_event:
                await session.page.get_by_role("button", name="Open").click()
            popup = await popup_event.value
            assert popup in session.pages
            assert popup in session.popups
            assert len(session.pages) == 2
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")


@pytest.mark.asyncio
async def test_browser_session_and_deterministic_actions(tmp_path: Path) -> None:
    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path,
        browser_executable_path=executable if executable.exists() else None,
        browser_action_timeout_ms=2_000,
        browser_navigation_timeout_ms=5_000,
    )

    try:
        session = BrowserSession(settings)
        await session.__aenter__()
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")

    try:
        assert session.page is not None
        await session.page.set_content(
            """
            <!doctype html>
            <html><body>
              <form id="account">
                <label for="email">Email</label>
                <input id="email" type="email">
                <label for="password">Password</label>
                <input id="password" type="password">
                <label for="country">Country</label>
                <select id="country">
                  <option value="">Choose</option>
                  <option value="in">India</option>
                </select>
                <label><input id="updates" type="checkbox"> Receive updates</label>
                <button type="button" id="submit">Submit</button>
              </form>
              <p id="result" hidden></p>
              <script>
                document.querySelector('#submit').addEventListener('click', () => {
                  const result = document.querySelector('#result');
                  result.hidden = false;
                  result.textContent = 'Submitted';
                });
              </script>
            </body></html>
            """
        )
        executor = BrowserExecutor(session.page, settings)
        actions = [
            ManualAction(
                action="fill",
                locator=ManualLocator(strategy="label", value="Email"),
                value="test@example.com",
            ),
            ManualAction(
                action="fill",
                locator=ManualLocator(strategy="label", value="Password"),
                value="not-a-real-secret",
            ),
            ManualAction(
                action="select",
                locator=ManualLocator(strategy="label", value="Country"),
                value="in",
            ),
            ManualAction(
                action="check",
                locator=ManualLocator(strategy="label", value="Receive updates"),
            ),
            ManualAction(
                action="click",
                locator=ManualLocator(
                    strategy="role", role="button", value="Submit"
                ),
            ),
            ManualAction(
                action="wait",
                locator=ManualLocator(strategy="text", value="Submitted"),
                wait_state="visible",
            ),
        ]

        for action in actions:
            await executor.execute(action)

        assert await session.page.get_by_label("Email").input_value() == "test@example.com"
        assert await session.page.get_by_label("Country").input_value() == "in"
        assert await session.page.get_by_label("Receive updates").is_checked()
        assert await session.page.get_by_text("Submitted").is_visible()

        screenshot = tmp_path / "phase1-integration.png"
        await session.screenshot(str(screenshot))
        assert screenshot.is_file()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_executor_rejects_ambiguous_locator(tmp_path: Path) -> None:
    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path,
        browser_executable_path=executable if executable.exists() else None,
    )
    session = BrowserSession(settings)
    try:
        await session.__aenter__()
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")

    try:
        assert session.page is not None
        await session.page.set_content(
            '<button>Save</button><button>Save</button>'
        )
        executor = BrowserExecutor(session.page, settings)
        action = ManualAction(
            action="click",
            locator=ManualLocator(strategy="role", role="button", value="Save"),
        )
        with pytest.raises(AmbiguousLocatorError, match="matched 2 elements"):
            await executor.execute(action)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_executor_waits_for_a_dynamically_inserted_control(tmp_path: Path) -> None:
    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path,
        browser_executable_path=executable if executable.exists() else None,
        browser_action_timeout_ms=2_000,
    )
    session = BrowserSession(settings)
    try:
        await session.__aenter__()
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")

    try:
        assert session.page is not None
        await session.page.set_content(
            """<html><body><div id='mount'></div>
            <script>
              setTimeout(() => {
                const input = document.createElement('input');
                input.setAttribute('aria-label', 'Delayed search');
                document.querySelector('#mount').appendChild(input);
              }, 150);
            </script></body></html>"""
        )
        executor = BrowserExecutor(session.page, settings)
        await executor.execute(
            ManualAction(
                action="fill",
                locator=ManualLocator(strategy="label", value="Delayed search"),
                value="appeared",
            )
        )
        assert await session.page.get_by_label("Delayed search").input_value() == "appeared"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_runner_executes_plan_and_saves_report_and_screenshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from browser_agent.browser.runner import execute_manual_plan
    from browser_agent.schemas import ManualTestPlan

    url = "https://example.test/"
    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path / "artifacts",
        browser_executable_path=executable if executable.exists() else None,
        default_timeout_seconds=20,
    )
    plan = ManualTestPlan(
        name="Local search action sequence",
        actions=[
            {
                "action": "fill",
                "locator": {"strategy": "label", "value": "Search"},
                "value": "browser agent",
            },
            {
                "action": "click",
                "locator": {"strategy": "role", "role": "button", "value": "Go"},
            },
            {
                "action": "wait",
                "locator": {"strategy": "text", "value": "Results shown"},
                "wait_state": "visible",
            },
        ],
    )

    async def set_test_content(
        session: BrowserSession, _url: str, *, timeout_ms: int | None = None
    ):
        assert session.page is not None
        await session.page.set_content(
            """<!doctype html><html><body>
              <label for='query'>Search</label>
              <input id='query'>
              <button type='button' id='go'>Go</button>
              <p id='result' hidden>Results shown</p>
              <script>
                document.querySelector('#go').addEventListener('click', () => {
                  document.querySelector('#result').hidden = false;
                });
              </script>
            </body></html>"""
        )
        return session.page

    await _skip_if_chromium_unavailable(settings)
    monkeypatch.setattr(BrowserSession, "goto", set_test_content)
    report = await execute_manual_plan(url=url, plan=plan, settings=settings)

    assert report.status.value == "PASS"
    assert len(report.steps) == 3
    assert all(step.success for step in report.steps)
    assert any(Path(path).name == "final.png" for path in report.artifact_paths)
    report_path = next(path for path in report.artifact_paths if path.endswith("execution.json"))
    assert Path(report_path).is_file()


@pytest.mark.asyncio
async def test_runner_records_failed_action_and_failure_screenshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from browser_agent.browser.runner import execute_manual_plan
    from browser_agent.schemas import ManualTestPlan

    executable = Path("/usr/bin/chromium")
    settings = Settings(
        _env_file=None,
        artifact_dir=tmp_path / "artifacts",
        browser_executable_path=executable if executable.exists() else None,
        default_timeout_seconds=20,
    )
    plan = ManualTestPlan(
        name="Missing button failure",
        actions=[
            {
                "action": "click",
                "locator": {
                    "strategy": "role",
                    "role": "button",
                    "value": "Button that does not exist",
                },
                "timeout_ms": 300,
            }
        ],
    )

    async def set_test_content(
        session: BrowserSession, _url: str, *, timeout_ms: int | None = None
    ):
        assert session.page is not None
        await session.page.set_content("<html><body><p>Empty page</p></body></html>")
        return session.page

    await _skip_if_chromium_unavailable(settings)
    monkeypatch.setattr(BrowserSession, "goto", set_test_content)
    report = await execute_manual_plan(
        url="https://example.test/",
        plan=plan,
        settings=settings,
    )

    assert report.status.value == "FAIL"
    assert len(report.steps) == 1
    assert report.steps[0].success is False
    assert report.failure_reason is not None
    assert any(Path(path).name == "failure.png" for path in report.artifact_paths)
