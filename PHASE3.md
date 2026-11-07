# Phase 3 — LLM interpretation and action planning

**Status: Implemented**

Phase 3 adds AI interpretation without allowing the model to execute browser code or decide whether a test passed.

## Components

- `llm/client.py`: provider-neutral `LLMClient` protocol and an OpenAI-compatible asynchronous adapter. All responses are parsed and validated with Pydantic.
- `agent/compiler.py`: compiles plain English into `CompiledTestPlan`, with semantic targets, planned actions, data requirements, and assertions.
- `agent/planner.py`: maps the next semantic step to one element in the latest `BrowserObservation` and validates the response.
- `agent/planning_runner.py`: compiles a request, opens the website, observes the current page, proposes one action, and persists artifacts. It does not execute that action.
- `cli.py`: adds the `browser-agent plan` command.

## Data flow

```text
Plain-English test + URL + declared data-reference names
                         |
                         v
                  TestInterpreter
                         |
                         v
                 CompiledTestPlan
             /           |          \
         Steps       Data needs    Assertions
             \           |          /
                         v
                 BrowserObserver
                         |
                         v
                BrowserObservation
                         |
                         v
                   ActionPlanner
                         |
                         v
                AgentDecision (one action
                    or blocked/finish)
```

## Install and configure

```powershell
python -m pip install -e ".[dev,browser,llm]"
python -m playwright install chromium
```

Configure `.env`:

```dotenv
BA_LLM_PROVIDER=openai
BA_LLM_MODEL=your-provider-model-name
BA_LLM_API_KEY=your-api-key
BA_LLM_BASE_URL=
```

The current concrete adapter supports OpenAI and OpenAI-compatible Chat Completions endpoints. Other native provider adapters can be added behind the `LLMClient` protocol.

## Run

```powershell
browser-agent plan `
  --url "https://example.com/login" `
  --test "Log in as a customer and verify the dashboard is shown" `
  --data-ref CUSTOMER_EMAIL `
  --data-ref CUSTOMER_PASSWORD `
  --headed
```

`--json` prints the compiled plan, observation, first decision, and artifact paths as JSON.

## Security and correctness boundaries

- The model receives environment-variable names, not the values stored in the environment or `.env`.
- The compiler emits semantic targets, not selectors or executable code.
- Sensitive target descriptions must use data references; literal values for password/token/email/phone/account identifiers and related fields are rejected.
- Page text and element labels are treated as untrusted website data in the planner prompt.
- The planner can select only an element ID in the current observation and must copy the current observation ID.
- The planner must preserve the action type and value/data reference in the next semantic step.
- Unknown elements, stale observations, disabled/invisible targets, and incompatible element/action combinations are rejected locally.
- A missing required data reference produces `BLOCKED`; the model is not asked to guess a credential.
- `FINISH` is not PASS. The assertion engine must independently verify the expected result in Phase 5.
- The `plan` command only inspects the website and proposes one action. It does not execute browser actions; that is Phase 4.

## Artifacts

Each run writes beneath `BA_ARTIFACT_DIR`:

- `compiled-plan.json`
- `observation.json`
- `first-decision.json`
- `planning-summary.json`
- `page.png` when screenshot capture succeeds

## Tests

The Phase 3 tests use a fake LLM client and do not make real API calls. They cover compiler output validation, sensitive-value rejection, planner element mapping, stale/unknown element rejection, incompatible actions, missing data references, completion handling, provider response validation, and artifact persistence.
