# Phase 4 — Autonomous observe–plan–act execution

## Implemented

Phase 4 connects the Phase 3 compiler and action planner to the Phase 2 observer and observed-action executor. The agent now accepts a natural-language test, compiles it, opens the supplied URL, inspects the live page, plans one constrained action, executes it, and repeats against a fresh observation.

### Agent loop

1. Compile the instruction into a selector-free `CompiledTestPlan`.
2. Open the URL in an isolated Playwright browser context.
3. Inspect the current page, including accessible controls, open Shadow DOM, and nested frames.
4. Ask `ActionPlanner` for one action for the first incomplete semantic step.
5. Validate the action against the current observation and plan.
6. Execute it through `ObservedActionExecutor`.
7. Record the result and screenshot, then re-observe before planning another action.
8. Stop when all action steps complete, the planner blocks, retry limits are exhausted, the action-attempt limit is reached, or the overall timeout expires.

### Run a natural-language test

Configure `.env` with the Phase 3 provider settings and declare test-account values as environment variables. Example:

```env
BA_LLM_PROVIDER=openai
BA_LLM_MODEL=your-supported-model
BA_LLM_API_KEY=your-api-key
BA_AGENT_MAX_RETRIES_PER_STEP=2
CUSTOMER_EMAIL=customer@example.com
CUSTOMER_PASSWORD=replace-with-test-password
```

Run:

```powershell
browser-agent run `
  --url "https://example.com/login" `
  --test "Log in as a customer and verify the dashboard is shown" `
  --data-ref CUSTOMER_EMAIL `
  --data-ref CUSTOMER_PASSWORD `
  --headed
```

Machine-readable report:

```powershell
browser-agent run `
  --url "https://example.com/login" `
  --test "Log in as a customer and verify the dashboard is shown" `
  --data-ref CUSTOMER_EMAIL `
  --data-ref CUSTOMER_PASSWORD `
  --json
```

### Controls

| Option | Default | Purpose |
|---|---:|---|
| `--max-steps` | `25` (or `BA_DEFAULT_MAX_STEPS`) | Maximum browser action attempts, including retries |
| `--timeout` | `120` seconds (or `BA_DEFAULT_TIMEOUT_SECONDS`) | End-to-end compile + browser execution timeout |
| `--max-retries` | `2` (or `BA_AGENT_MAX_RETRIES_PER_STEP`) | Retries after a failed action for the current semantic step |
| `--max-elements-per-frame` | `500` | Cap discovery for each frame |
| `--headed` | off | Show Chromium while it runs |
| `--json` | off | Print the full report as JSON |

Retry values are constrained to 0–5. The agent always re-observes the page before retrying. A failed action is never marked completed.

### Execution outcomes

- `ACTIONS_COMPLETED`: all compiled action steps executed successfully. This is **not** a test PASS.
- `BLOCKED`: the planner identified a CAPTCHA, MFA, missing data, ambiguity, or another human-intervention requirement.
- `FAILED`: an action exceeded its retry allowance.
- `STEP_LIMIT`: the maximum number of action attempts was reached before all steps completed.
- `TIMEOUT`: the end-to-end timeout elapsed.
- `ERROR`: compilation, planning, browser startup, or another unexpected error occurred.

Phase 4 intentionally does not evaluate `CompiledTestPlan.assertions`. The report sets `assertions_evaluated` to `false`; Phase 5 adds deterministic assertions and the final PASS/FAIL decision.

### Artifacts

Each run creates a unique directory under `BA_ARTIFACT_DIR` (default `artifacts/`):

```text
agent-.../
  compiled-plan.json
  observations/
    observation-001.json
    observation-002.json
  screenshots/
    step-001.png
    failure-002.png
  final.png
  events.json
  execution.json
```

The event history ties each planner decision to its observation, action attempt, execution result, and retry metadata. Fill/select literal values are redacted in step reports and event summaries. Environment values referenced with `value_ref` are resolved only inside the executor and are never sent to the LLM.

### Validation

The unit tests use a fake LLM/planner and browser fixtures so they do not incur API usage. They cover full execution, fresh observations between actions, retry success, retry exhaustion, action limits, blocked decisions, and saved artifacts. Browser observer and action execution integration tests remain part of the full test suite.

### Current limitations

- Assertions are not evaluated yet; `ACTIONS_COMPLETED` only means the action sequence ran.
- There is no automatic human hand-off UI. CAPTCHA/MFA or other blocked cases stop with a reason.
- The agent is limited to the supported action vocabulary: click, fill, select, check, uncheck, press, and wait.
- The LLM provider adapter currently supports OpenAI and OpenAI-compatible endpoints.
- A successful browser action is not proof that the application accepted the operation; Phase 5 assertions are required for that.
