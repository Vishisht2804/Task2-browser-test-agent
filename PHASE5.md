# Phase 5 — Deterministic assertions and final PASS/FAIL

## Goal

Phase 5 evaluates the expected conditions produced by the natural-language test compiler. The LLM plans browser actions, but it does not decide whether the test passed. Playwright and the final browser observation determine the outcome.

## Implementation

### Assertion evaluator

`src/browser_agent/assertions/evaluator.py` implements `AssertionEvaluator`. It evaluates each `AssertionSpec` against the final page after the agent has completed all planned actions.

Supported assertion types:

| Type | Evaluation |
|---|---|
| `visible` | Resolve a unique semantic target from the final observation and ask Playwright whether it is visible. If the target is a non-semantic text container, try one exact text locator. |
| `hidden` | Verify the resolved element is not visible. If it was removed from the final DOM, require evidence that the target existed in an earlier observation. A target that was never found does not pass. |
| `text_contains` | Search rendered `body.inner_text()` across the main page and attached frames, case-insensitively. |
| `url_contains` | Check the current browser URL for the expected fragment, case-insensitively. |
| `checked` | Check the observed checkbox/radio state using the observer metadata or Playwright. |
| `enabled` | Check the current enabled state using Playwright. |
| `value_equals` | Compare the current input/select/content-editable value exactly. Sensitive values are redacted in reports. |

Element-based assertions require a clear target. Matching is deterministic and ambiguity fails closed; it does not ask the LLM to reinterpret an assertion after execution. The compiler prompt now requests specific labels and roles such as `Dashboard heading` or `Remember me checkbox`.

### Final outcome model

`AgentRunResult` now distinguishes two statuses:

- `status`: action-loop outcome, such as `ACTIONS_COMPLETED`, `BLOCKED`, `FAILED`, `STEP_LIMIT`, `TIMEOUT`, or `ERROR`.
- `test_status`: assertion-derived `PASS` or `FAIL`. It remains `null` unless the action loop completed and all assertions were evaluated.

This distinction matters: the agent can successfully execute its actions while the final assertion fails. That outcome is `status=ACTIONS_COMPLETED` and `test_status=FAIL`.

### CLI

The existing `run` command now reports the final test result:

```powershell
browser-agent run `
  --url "https://example.com/login" `
  --test "Log in as a customer and verify the Dashboard heading is visible" `
  --data-ref CUSTOMER_EMAIL `
  --data-ref CUSTOMER_PASSWORD `
  --headed
```

A successful assertion set exits with code `0`. An assertion failure or incomplete execution exits non-zero. Use `--json` for the full `AgentRunResult` object.

### Artifacts

Completed runs include:

```text
agent-.../
  compiled-plan.json
  observations/
    observation-001.json
    observation-002.json
  assertion-observation.json
  assertions.json
  screenshots/
    step-001.png
  final.png
  events.json
  execution.json
```

`assertion-observation.json` records the fresh final DOM state used for evaluation. `assertions.json` records each result, expected condition, actual state, and pass/fail message. The final `execution.json` contains both statuses.

Literal fill/select action values are redacted from the persisted compiled plan and action history. Sensitive assertion values and sensitive field values are redacted from the final assertion report.

## Validation

The Phase 5 test suite covers:

- All seven assertion types.
- Visible and hidden elements.
- Hidden elements removed after being observed.
- No false pass when a hidden target was never seen.
- Ambiguous semantic targets.
- Case-insensitive rendered text and URL checks.
- Checked, enabled, and exact-value checks.
- Full Chromium execution where actions complete and text assertion passes.
- Full Chromium execution where actions complete but an assertion fails.
- Assertion result serialization and artifact generation.

The LLM is mocked for the automated test suite; tests do not make model API calls or spend API credits.

## Limitations

- Assertion targets must identify a unique element or exact visible text. Ambiguous targets fail instead of guessing.
- `text_contains` searches rendered body text and attached frames; it does not inspect canvas pixels.
- Closed Shadow DOM remains inaccessible to Playwright's normal locators.
- `value_equals` is exact and case-sensitive; text and URL substring assertions are case-insensitive.
- If the action loop is blocked or fails, assertions are not evaluated and `test_status` is null.
