from pathlib import Path

import pytest

from browser_agent.browser.locator_resolver import (
    ObservedElementNotFoundError,
    StaleObservationError,
)
from browser_agent.browser.observed_executor import ObservedActionExecutor
from browser_agent.browser.observer import BrowserObserver
from browser_agent.browser.session import BrowserSession, BrowserStartupError
from browser_agent.config import Settings
from browser_agent.schemas import ActionType, AgentAction


def _settings(tmp_path: Path) -> Settings:
    executable = Path("/usr/bin/chromium")
    return Settings(
        _env_file=None,
        artifact_dir=tmp_path,
        browser_executable_path=executable if executable.exists() else None,
        browser_action_timeout_ms=2_000,
        browser_navigation_timeout_ms=5_000,
    )


async def _start_session(settings: Settings) -> BrowserSession:
    session = BrowserSession(settings)
    try:
        await session.__aenter__()
    except BrowserStartupError as exc:
        pytest.skip(f"Chromium is not installed in this environment: {exc}")
    return session


@pytest.mark.asyncio
async def test_observer_discovers_semantic_controls_and_does_not_capture_values(
    tmp_path: Path,
) -> None:
    session = await _start_session(_settings(tmp_path))
    try:
        assert session.page is not None
        await session.page.set_content(
            """<!doctype html><html><head><title>Account Login</title></head><body>
              <main>
                <h1>Customer Login</h1>
                <form>
                  <label for="email">Email address</label>
                  <input id="email" name="email" type="email" autocomplete="email"
                         value="private@example.com" required>
                  <label for="password">Password</label>
                  <input id="password" name="password" type="password"
                         value="do-not-leak-this">
                  <button type="submit">Sign in</button>
                  <button type="button" disabled>Unavailable</button>
                  <input type="hidden" value="hidden-secret">
                </form>
              </main>
            </body></html>"""
        )

        observer = BrowserObserver()
        observation = await observer.inspect(session.page)

        assert observation.title == "Account Login"
        assert observation.url == "about:blank"
        assert len(observation.frames) == 1
        assert observation.frames[0].is_main_frame is True
        assert any(
            element.role == "heading" and element.name == "Customer Login"
            for element in observation.elements
        )

        email = next(element for element in observation.elements if element.id_attribute == "email")
        password = next(
            element for element in observation.elements if element.id_attribute == "password"
        )
        disabled = next(
            element for element in observation.elements if element.name == "Unavailable"
        )

        assert email.role == "textbox"
        assert email.label == "Email address"
        assert email.input_type == "email"
        assert email.required is True
        assert email.autocomplete == "email"
        assert password.input_type == "password"
        assert not any(element.input_type == "hidden" for element in observation.elements)
        assert "private@example.com" not in observation.model_dump_json()
        assert "do-not-leak-this" not in observation.model_dump_json()
        assert "hidden-secret" not in observation.model_dump_json()
        assert disabled.enabled is False
        assert "Customer Login" in observation.visible_text
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_observer_discovers_nested_open_shadow_dom_and_resolves_elements(
    tmp_path: Path,
) -> None:
    session = await _start_session(_settings(tmp_path))
    try:
        assert session.page is not None
        await session.page.set_content(
            """<!doctype html><html><body>
              <div id="outer-host"></div>
              <script>
                const outer = document.querySelector('#outer-host').attachShadow({mode: 'open'});
                outer.innerHTML = `
                  <label for="shadow-email">Shadow email</label>
                  <input id="shadow-email" aria-label="Shadow email input">
                  <div id="inner-host"></div>`;
                const inner = outer.querySelector('#inner-host').attachShadow({mode: 'open'});
                inner.innerHTML = '<label for="deep">Deep field</label><input id="deep">';
              </script>
            </body></html>"""
        )

        observer = BrowserObserver()
        observation = await observer.inspect(session.page)
        email = next(
            element for element in observation.elements
            if element.aria_label == "Shadow email input"
        )
        deep = next(element for element in observation.elements if element.id_attribute == "deep")
        resolver = observer.locator_resolver()

        email_locator = await resolver.resolve_unique(
            email.element_id,
            observation_id=observation.observation_id,
        )
        await email_locator.fill("shadow@example.com")
        assert await email_locator.input_value() == "shadow@example.com"

        deep_locator = await resolver.resolve_unique(
            deep.element_id,
            observation_id=observation.observation_id,
        )
        await deep_locator.fill("nested shadow works")
        assert await deep_locator.input_value() == "nested shadow works"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_observer_discovers_controls_in_nested_iframes(tmp_path: Path) -> None:
    session = await _start_session(_settings(tmp_path))
    try:
        assert session.page is not None
        await session.page.set_content('<iframe id="outer" name="outer"></iframe>')
        outer = session.page.frame(name="outer")
        assert outer is not None
        await outer.set_content(
            '<label for="outer-field">Outer frame field</label>'
            '<input id="outer-field">'
            '<iframe id="inner" name="inner"></iframe>'
        )
        await session.page.wait_for_timeout(100)
        inner = session.page.frame(name="inner")
        assert inner is not None
        await inner.set_content(
            '<label for="deep-field">Nested frame field</label>'
            '<input id="deep-field">'
            '<button type="button">Nested submit</button>'
        )

        observer = BrowserObserver()
        observation = await observer.inspect(session.page)
        assert len(observation.frames) == 3

        outer_field = next(
            element for element in observation.elements if element.id_attribute == "outer-field"
        )
        deep_field = next(
            element for element in observation.elements if element.id_attribute == "deep-field"
        )
        nested_button = next(
            element for element in observation.elements if element.name == "Nested submit"
        )

        outer_frame = next(
            frame for frame in observation.frames if frame.frame_id == outer_field.frame_id
        )
        inner_frame = next(
            frame for frame in observation.frames if frame.frame_id == deep_field.frame_id
        )
        assert outer_frame.depth == 1
        assert inner_frame.depth == 2
        assert inner_frame.parent_frame_id == outer_frame.frame_id

        resolver = observer.locator_resolver()
        action_executor = ObservedActionExecutor(resolver, _settings(tmp_path))
        await action_executor.execute(
            AgentAction(
                action=ActionType.FILL,
                element_id=deep_field.element_id,
                observation_id=observation.observation_id,
                value="from parent test",
            )
        )
        await action_executor.execute(
            AgentAction(
                action=ActionType.CLICK,
                element_id=nested_button.element_id,
                observation_id=observation.observation_id,
            )
        )
        assert await inner.locator("#deep-field").input_value() == "from parent test"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_locator_resolver_rejects_stale_observation_ids(tmp_path: Path) -> None:
    session = await _start_session(_settings(tmp_path))
    try:
        assert session.page is not None
        await session.page.set_content('<button>Save</button>')
        observer = BrowserObserver()
        first = await observer.inspect(session.page)
        old_resolver = observer.locator_resolver()
        element = next(element for element in first.elements if element.name == "Save")

        second = await observer.inspect(session.page)
        new_resolver = observer.locator_resolver()
        assert first.observation_id != second.observation_id
        with pytest.raises(StaleObservationError, match="stale browser observation"):
            new_resolver.resolve(element.element_id, observation_id=first.observation_id)
        with pytest.raises(ObservedElementNotFoundError):
            await old_resolver.resolve_unique(
                element.element_id,
                observation_id=first.observation_id,
                timeout_ms=200,
            )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_observer_caps_elements_and_emits_warning(tmp_path: Path) -> None:
    session = await _start_session(_settings(tmp_path))
    try:
        assert session.page is not None
        await session.page.set_content(
            "<button>One</button><button>Two</button><button>Three</button>"
        )
        observation = await BrowserObserver(max_elements_per_frame=2).inspect(session.page)
        assert len(observation.elements) == 2
        assert any("capped at 2" in warning for warning in observation.warnings)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_inspection_runner_writes_observation_and_screenshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from browser_agent.browser.observer_runner import inspect_url

    settings = _settings(tmp_path / "artifacts")

    async def set_test_content(
        session: BrowserSession, _url: str, *, timeout_ms: int | None = None
    ):
        assert session.page is not None
        await session.page.set_content(
            '<main><h1>Inspection fixture</h1><button>Continue</button></main>'
        )
        return session.page

    monkeypatch.setattr(BrowserSession, "goto", set_test_content)
    result = await inspect_url(
        url="https://fixture.example/",
        settings=settings,
        max_elements_per_frame=50,
    )

    assert result.observation.title == ""
    assert any(element.name == "Continue" for element in result.observation.elements)
    assert result.observation_path.is_file()
    assert result.screenshot_path is not None
    assert result.screenshot_path.is_file()
    saved = result.observation_path.read_text(encoding="utf-8")
    assert result.observation.observation_id in saved
