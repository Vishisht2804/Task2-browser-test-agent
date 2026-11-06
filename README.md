# Browser Agent

A generic browser-testing tool that turns plain-English test cases into browser actions. The project is being built in phases. **Phase 0 is the foundation only**: it defines the project structure, shared data contracts, configuration, and a CLI that validates a test request. Browser execution and LLM adapters are intentionally not implemented yet.

## Requirements

- Python 3.12 or newer
- pip

## Setup

From the project root:

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

Install the package and development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` when you want to customize settings. `.env` is ignored by Git. Test-data values should be stored in `.env` or another secret store; pass only their variable names to the CLI.

## Validate a test request

```bash
python -m browser_agent run \
  --url "https://example.com/login" \
  --test "Log in as a customer and verify the dashboard is shown" \
  --data-ref CUSTOMER_EMAIL \
  --data-ref CUSTOMER_PASSWORD
```

The Phase 0 CLI validates and prints the request. It does **not** open a browser yet.

Print the validated request as JSON:

```bash
python -m browser_agent run \
  --url "https://example.com/login" \
  --test "Log in and verify the dashboard appears" \
  --json
```

The installed console command is also available:

```bash
browser-agent run --url "https://example.com" --test "Verify the homepage loads"
```

## Configuration

Settings use the `BA_` environment-variable prefix.

| Variable | Default | Purpose |
|---|---|---|
| `BA_ENVIRONMENT` | `development` | Runtime environment |
| `BA_LOG_LEVEL` | `INFO` | Logging level |
| `BA_DEFAULT_MAX_STEPS` | `25` | Default action limit |
| `BA_DEFAULT_TIMEOUT_SECONDS` | `120` | Default total test timeout |
| `BA_BROWSER_HEADLESS` | `true` | Browser mode for later phases |
| `BA_BROWSER_ACTION_TIMEOUT_MS` | `10000` | Per-action timeout for later phases |
| `BA_ARTIFACT_DIR` | `artifacts` | Report and screenshot directory |
| `BA_LLM_PROVIDER` | `openai` | Provider selection for Phase 3 |
| `BA_LLM_MODEL` | unset | Provider model name |
| `BA_LLM_API_KEY` | unset | Provider API key |
| `BA_LLM_BASE_URL` | unset | Optional compatible API endpoint |

The LLM interface is defined as a Python protocol in `browser_agent.llm.client`. Provider-specific implementations will be added later and injected through this interface. Browser code will not import a vendor SDK.

## Data contracts

`browser_agent.schemas` defines the shared Pydantic contracts:

- `TestSpec`: user request, URL, test-data references, and execution limits.
- `BrowserObservation`: page metadata, frame inventory, and discovered elements.
- `AgentAction` / `AgentDecision`: constrained browser actions and planner decisions.
- `AssertionSpec`: machine-readable expected conditions.
- `ExecutionResult`: final test status, steps, assertion outcomes, and artifact paths.

Element identifiers are scoped to one observation. A later observer implementation must re-discover elements after page state changes rather than trust stale IDs.

## Run tests

```bash
python -m pytest
```

## Current scope and next phase

Phase 0 provides validated contracts and the command-line entry point. Phase 1 will implement the Playwright browser session and deterministic action executor. The optional `browser` dependency group installs Playwright when that phase begins:

```bash
python -m pip install -e ".[dev,browser]"
python -m playwright install chromium
```
