# Phase 1 — Deterministic Playwright Browser Engine

## Scope implemented

- Async Playwright Chromium session lifecycle.
- Isolated browser context and page.
- Configurable headless mode, action timeout, navigation timeout, and executable path.
- Navigation using `domcontentloaded`.
- Console-error and uncaught-page-error collection.
- Tracking of additional pages and popups opened during execution.
- Screenshot capture.
- Developer-authored JSON action plans.
- Semantic locators: role, label, placeholder, text, test ID, and CSS fallback.
- Strict locator uniqueness checks; ambiguous matches fail unless a zero-based index is explicit.
- Actions: click, fill, select, check, uncheck, press, and wait.
- Test data lookup from process environment or `.env` using `value_ref`.
- Execution JSON and final/failure screenshots.
- CLI `execute` command with optional headed mode.
- Unit and browser integration tests.

## Run

```bash
python -m pip install -e ".[dev,browser]"
python -m playwright install chromium
python -m pytest
```

Run a plan:

```bash
browser-agent execute --url "https://your-test-site.example/login" --plan "examples-login-plan.json"
```

## Completion criteria

- Browser starts and closes cleanly.
- A manual action plan can navigate to a page and execute supported interactions.
- Semantic locators work.
- Ambiguous locators are rejected rather than silently choosing one.
- Referenced test data is loaded without being written into the execution report.
- Successful and failed action sequences produce evidence.

## Explicit non-goals

- No LLM calls.
- No natural-language-to-action conversion.
- No automatic DOM observation or element discovery.
- No automatic selector generation.
- No assertion-based test outcome yet. `PASS` means only that all listed actions ran successfully; Phase 5 will determine whether expected outcomes were actually met.
