# Browser Agent

A generic browser-testing tool built in phases. The current implementation includes the project foundation (Phase 0), deterministic Playwright execution (Phase 1), browser observation with open Shadow DOM and nested iframe discovery (Phase 2), and LLM-based test interpretation plus constrained first-action planning (Phase 3). The autonomous observe–plan–act loop is Phase 4.

## Requirements

- Python 3.12 or newer
- pip
- Chromium (installed with Playwright below, or supplied through `BA_BROWSER_EXECUTABLE_PATH`)

## Setup

From the project root:

```bash
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.venv\\Scripts\\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Install development and browser dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev,browser,llm]"
python -m playwright install chromium
```

On Linux servers, Playwright may also need operating-system browser libraries. Use `python -m playwright install --with-deps chromium` on a supported Linux environment when required.

Copy `.env.example` to `.env` and set `BA_LLM_MODEL` and `BA_LLM_API_KEY` before using Phase 3. `.env` is ignored by Git. Put test-account credentials in `.env` or a separate secret store, then declare only their variable names with `--data-ref`. The LLM receives the variable names, never the values. Do not commit credentials or put passwords, tokens, or personal account data in the natural-language instruction.

## Phase 1: execute a deterministic action plan

Phase 1 deliberately does not use an LLM. It proves that browser lifecycle, locators, actions, timeouts, and artifacts work before AI is introduced.

Run the sample plan against a page that has labels named `Email`, `Password`, and a `Login` button:

```powershell
$env:CUSTOMER_EMAIL = "customer@example.com"
$env:CUSTOMER_PASSWORD = "replace-with-test-account-password"

browser-agent execute `
  --url "https://your-test-site.example/login" `
  --plan "examples-login-plan.json"
```

Or use a local `.env` file with the same variables. The executor resolves `value_ref` values at runtime and never writes the actual value into the execution log.

Show the browser window:

```powershell
browser-agent execute --headed --url "https://your-test-site.example/login" --plan "examples-login-plan.json"
```

Print the full execution report as JSON:

```powershell
browser-agent execute --json --url "https://your-test-site.example/login" --plan "examples-login-plan.json"
```

### Manual action-plan format

A plan is a JSON object containing a name and an ordered list of actions:

```json
{
  "name": "Example customer login",
  "actions": [
    {
      "action": "fill",
      "locator": { "strategy": "label", "value": "Email" },
      "value_ref": "CUSTOMER_EMAIL"
    },
    {
      "action": "fill",
      "locator": { "strategy": "label", "value": "Password" },
      "value_ref": "CUSTOMER_PASSWORD"
    },
    {
      "action": "click",
      "locator": {
        "strategy": "role",
        "role": "button",
        "value": "Login"
      }
    }
  ]
}
```

Supported locator strategies:

| Strategy | Example |
|---|---|
| `role` | `{"strategy":"role","role":"button","value":"Login"}` |
| `label` | `{"strategy":"label","value":"Email"}` |
| `placeholder` | `{"strategy":"placeholder","value":"Search"}` |
| `text` | `{"strategy":"text","value":"Continue"}` |
| `test_id` | `{"strategy":"test_id","value":"submit-button"}` |
| `css` | `{"strategy":"css","value":"form#login button[type=submit]"}` |

If a locator matches more than one element, execution fails with an ambiguity error instead of silently choosing the first match. To intentionally select one match, include a zero-based `index` in the locator. Prefer a more specific semantic locator over an index where possible.

Supported actions:

- `click`
- `fill`
- `select`
- `check`
- `uncheck`
- `press`
- `wait` (`wait_state` can be `visible`, `hidden`, `attached`, or `detached`)

`fill` and `select` require exactly one of `value` or `value_ref`. `press` uses `value` for the key name, such as `Enter`. Other actions do not accept a value.

Example wait action:

```json
{
  "action": "wait",
  "locator": { "strategy": "text", "value": "Signed in" },
  "wait_state": "visible",
  "timeout_ms": 10000
}
```

### What PASS means in Phase 1

A `PASS` result means every action in the manual plan completed. It does **not** yet prove the overall test objective, because deterministic assertions are implemented in a later phase. Phase 5 will add explicit expected-condition evaluation.

Each run creates a unique directory beneath `artifacts/` containing a final screenshot (or failure screenshot) and `execution.json`. The report also records browser console errors, uncaught page errors, and counts of additional tabs/popups created during the run. Secret values referenced through `value_ref` are not included in the report.

Phase 1's developer-authored action plans target the main document. Phase 2 adds the separate observation-scoped runtime for frame-aware and Shadow-DOM-aware actions.

## Phase 2: inspect an unfamiliar page

Phase 2 adds automatic inspection. It discovers interactive and semantically relevant elements without requiring predefined selectors, scans the main document and nested frames, and recursively traverses **open** Shadow DOM roots.

Run an inspection:

```powershell
browser-agent inspect --url "https://example.com"
```

Show the browser while inspecting:

```powershell
browser-agent inspect --headed --url "https://example.com"
```

Print the complete structured observation as JSON:

```powershell
browser-agent inspect --json --url "https://example.com"
```

Limit the number of discovered elements in each frame:

```powershell
browser-agent inspect --url "https://example.com" --max-elements-per-frame 250
```

Each inspection creates an artifact directory beneath `BA_ARTIFACT_DIR` (default `artifacts/`) with `observation.json` and `page.png`. The observation contains:

- Page URL and title (sensitive URL query parameters are redacted).
- A frame inventory with parent IDs and nesting depth.
- Buttons, links, inputs, selects, textareas, ARIA-role elements, headings, forms, landmarks, dialogs, and focusable/custom interactive elements.
- Accessible-name and label heuristics, placeholder, input type, visibility, enabled state, checked state, test ID, and selected safe attributes.
- A bounded sample of visible page text.
- Warnings for inaccessible frames or per-frame element limits.

Password fields and form-control values are not copied into observations. Hidden input fields are excluded. The observer uses temporary `data-ba-*` attributes to bind an element ID to its exact DOM node; the next inspection removes the previous markers. Element IDs are valid only for the associated `observation_id` and should not be reused after the page changes.

### Using observed element IDs in Python

```python
from browser_agent.browser.session import BrowserSession
from browser_agent.browser.observer import BrowserObserver
from browser_agent.browser.observed_executor import ObservedActionExecutor
from browser_agent.config import get_settings
from browser_agent.schemas import AgentAction

async def inspect_and_fill(url: str) -> None:
    settings = get_settings()
    async with BrowserSession(settings) as session:
        page = await session.goto(url)
        observer = BrowserObserver()
        observation = await observer.inspect(page)
        email = next(item for item in observation.elements if item.label == "Email")

        executor = ObservedActionExecutor(observer.locator_resolver(), settings)
        await executor.execute(
            AgentAction(
                action="fill",
                element_id=email.element_id,
                observation_id=observation.observation_id,
                value_ref="CUSTOMER_EMAIL",
            )
        )
```

The resolver rejects an action whose `observation_id` does not match the snapshot. It also fails if the marked element is no longer attached or the marker unexpectedly matches multiple elements.

### Phase 2 limitations

- Closed Shadow DOM roots are not accessible to Playwright and cannot be discovered.
- Accessible-name computation is a practical DOM/ARIA heuristic, not a complete implementation of the browser accessibility tree.
- The element inventory is capped per frame (500 by default); a warning identifies truncation.
- Manual Phase 1 JSON plans still use their original main-document locator syntax. Observation-scoped, frame-aware actions are available through `ObservedActionExecutor` for the next agent phase.

## Phase 3: interpret and plan a natural-language test

Phase 3 introduces the LLM but still does **not** execute browser actions. It has two separate responsibilities:

1. `TestInterpreter` converts the user's instruction into a selector-free `CompiledTestPlan` containing a goal, semantic action steps, data requirements, and deterministic assertions.
2. `ActionPlanner` receives the current Phase 2 browser observation and selects one observed element for the next semantic step. Its response is validated against the current observation and plan before it is accepted.

The model cannot return arbitrary Python, JavaScript, CSS selectors, or XPath. It can only return a `AgentDecision` containing one of the constrained `AgentAction` operations, or a `blocked` decision. The planner rejects unknown element IDs, stale observation IDs, incompatible element/action pairs, changed values, and undeclared data references. A planner `finish` decision does not mean PASS; assertions must still be evaluated by the deterministic assertion engine in Phase 5.

### Configure the LLM

Install the optional provider SDK if it was not installed during setup:

```powershell
python -m pip install -e ".[llm]"
```

Set these values in `.env`:

```dotenv
BA_LLM_PROVIDER=openai
BA_LLM_MODEL=your-provider-model-name
BA_LLM_API_KEY=replace-with-your-api-key
# Optional; leave unset for OpenAI's standard API endpoint.
BA_LLM_BASE_URL=
BA_LLM_TIMEOUT_SECONDS=45
```

The current concrete adapter supports OpenAI and endpoints exposing an OpenAI-compatible Chat Completions API. The internal `LLMClient` protocol is provider-neutral, but native Anthropic/Gemini adapters have not been implemented. A compatible local service can be configured through `BA_LLM_BASE_URL` and the corresponding model/key values.

### Plan a test

For a test that does not need account credentials:

```powershell
browser-agent plan `
  --url "https://example.com" `
  --test "Search for a wireless keyboard and verify that results are displayed"
```

For a login flow, declare the names of the required data references:

```powershell
browser-agent plan `
  --url "https://example.com/login" `
  --test "Log in as a customer and verify the dashboard is shown" `
  --data-ref CUSTOMER_EMAIL `
  --data-ref CUSTOMER_PASSWORD `
  --headed
```

Put the actual values in the local `.env` file:

```dotenv
CUSTOMER_EMAIL=customer@example.com
CUSTOMER_PASSWORD=your-test-account-password
```

The Phase 3 compiler may identify data references needed by the test. If the current command did not declare a required reference, the planner returns `BLOCKED` rather than inventing a value or proceeding with a credential field. Add the required `--data-ref` argument and rerun. The values themselves are not placed in the LLM prompt or the generated plan.

Print structured output:

```powershell
browser-agent plan `
  --url "https://example.com/login" `
  --test "Log in as a customer and verify the dashboard is shown" `
  --data-ref CUSTOMER_EMAIL `
  --data-ref CUSTOMER_PASSWORD `
  --json
```

Each planning run writes `compiled-plan.json`, `observation.json`, `first-decision.json`, `planning-summary.json`, and (when screenshot capture succeeds) `page.png` beneath `BA_ARTIFACT_DIR`. Only the proposed first action is produced. Phase 4 will connect repeated observations and decisions to the action executor.

### Phase 3 security and limitations

- The LLM sees data-reference names, not values loaded from `.env`.
- Passwords, tokens, email addresses, phone numbers, usernames, and similar sensitive targets must use environment references rather than literal plan values. The compiler rejects literal values for these sensitive target descriptions.
- Website text is explicitly treated as untrusted input in the planner prompt. The planner is restricted to the current semantic step and current observation.
- LLM output is parsed and validated against Pydantic schemas before it can be used.
- The concrete model adapter currently uses JSON mode plus local schema validation for compatibility with OpenAI-compatible endpoints; it does not assume every provider supports the same strict JSON Schema API.
- No action is executed by `plan`. The browser is opened only for observation and screenshot capture.

## Phase 0: validate a plain-English request

The `run` command accepts and validates a natural-language test request without calling the LLM or opening a browser. Use the Phase 3 `plan` command for LLM interpretation and first-action planning.

```powershell
browser-agent run `
  --url "https://example.com/login" `
  --test "Log in as a customer and verify the dashboard is shown" `
  --data-ref CUSTOMER_EMAIL `
  --data-ref CUSTOMER_PASSWORD
```

Print the validated request as JSON:

```powershell
browser-agent run --url "https://example.com" --test "Verify the homepage loads" --json
```

## Configuration

Settings use the `BA_` environment-variable prefix.

| Variable | Default | Purpose |
|---|---|---|
| `BA_ENVIRONMENT` | `development` | Runtime environment |
| `BA_LOG_LEVEL` | `INFO` | Runtime log level |
| `BA_DEFAULT_MAX_STEPS` | `25` | Default agent action limit |
| `BA_DEFAULT_TIMEOUT_SECONDS` | `120` | Default total test timeout for later agent execution |
| `BA_BROWSER_HEADLESS` | `true` | Run Chromium headlessly by default |
| `BA_BROWSER_ACTION_TIMEOUT_MS` | `10000` | Default timeout for browser actions |
| `BA_BROWSER_NAVIGATION_TIMEOUT_MS` | `30000` | Navigation timeout |
| `BA_BROWSER_EXECUTABLE_PATH` | unset | Optional path to a local Chromium/Chrome executable |
| `BA_ARTIFACT_DIR` | `artifacts` | Screenshot and execution-report directory |
| `BA_LLM_PROVIDER` | `openai` | Provider selection for Phase 3 |
| `BA_LLM_MODEL` | unset | Model name supported by the selected provider; required for Phase 3 |
| `BA_LLM_API_KEY` | unset | Provider API key; required for Phase 3 |
| `BA_LLM_BASE_URL` | unset | Optional OpenAI-compatible API endpoint |
| `BA_LLM_TIMEOUT_SECONDS` | `45` | Timeout for one LLM request |
| `BA_LLM_MAX_OBSERVED_ELEMENTS` | `250` | Maximum observed elements sent to the planner |
| `BA_LLM_MAX_VISIBLE_TEXT_CHARS` | `8000` | Maximum visible page-text characters sent to the planner |

## Data contracts

`browser_agent.schemas` defines the shared Pydantic contracts:

- `TestSpec`: user request, URL, test-data references, and execution limits.
- `BrowserObservation`: page metadata, frame inventory, visible text, and discovered elements.
- `AgentAction` / `AgentDecision`: constrained browser actions and planner decisions.
- `CompiledTestPlan`, `PlannedStep`, and `DataRequirement`: LLM interpretation output before DOM mapping.
- `ManualTestPlan` / `ManualAction`: deterministic, developer-authored action plans for Phase 1.
- `AssertionSpec`: machine-readable expected conditions for Phase 5.
- `ManualExecutionReport` / `ExecutionResult`: action and test execution outcomes.

## Run tests

```bash
python -m pytest
```

The integration tests use a locally installed Chromium. They are skipped when Chromium is unavailable; install it with `python -m playwright install chromium`.

## Development roadmap

- **Phase 0 — Foundation:** project structure, configuration, CLI request validation, and shared schemas.
- **Phase 1 — Browser engine:** Chromium lifecycle, deterministic actions, semantic locators, screenshots, and execution logs.
- **Phase 2 — Observer:** DOM and accessibility-oriented inspection, open Shadow DOM, nested iframes, and observation-scoped locator resolution. Implemented.
- **Phase 3 — LLM integration:** plain-English test interpretation, structured semantic plans, constrained first-action planning, and model-response validation. Implemented.
- **Phase 4 — Agent loop:** observe–plan–act execution and bounded error recovery.
- **Phase 5 — Assertions and reporting:** deterministic expected-condition evaluation and reliable PASS/FAIL.
- **Phase 6 — Validation:** complex DOM fixtures, PHPTravels, and an unrelated website.
