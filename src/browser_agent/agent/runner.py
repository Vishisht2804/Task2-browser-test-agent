"""Bounded observe-plan-act runner with deterministic final assertions."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import Page

from browser_agent.agent.compiler import TestInterpreter
from browser_agent.agent.planner import ActionPlanner
from browser_agent.assertions.evaluator import AssertionEvaluator
from browser_agent.browser.observer import BrowserObserver, _sanitize_url
from browser_agent.browser.observed_executor import ObservedActionExecutor
from browser_agent.browser.session import BrowserSession
from browser_agent.config import Settings
from browser_agent.llm.client import LLMClient, create_llm_client
from browser_agent.schemas import (
    ActionType,
    AgentAction,
    AgentDecisionType,
    AgentRunResult,
    AgentRunStatus,
    AssertionResult,
    BrowserObservation,
    CompiledTestPlan,
    StepResult,
    TestSpec,
    TestStatus,
)


def _safe_name(url: str) -> str:
    parts = urlsplit(url)
    source = f"{parts.hostname or 'page'}-{parts.path}"
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", source).strip("-").lower()[:40] or "page"


def _next_step_index(plan: CompiledTestPlan, completed: set[int]) -> int | None:
    return next((index for index in range(len(plan.steps)) if index not in completed), None)


def _active_page(session: BrowserSession, current: Page | None) -> Page:
    """Prefer the newest live tab, allowing a test to follow a popup it opened."""
    live_pages = [page for page in session.pages if not page.is_closed()]
    if not live_pages:
        raise RuntimeError("All browser pages were closed during test execution.")
    if current is not None and not current.is_closed() and live_pages[-1] is current:
        return current
    return live_pages[-1]


def _safe_action(action: AgentAction) -> AgentAction:
    """Avoid persisting literal fill/select values in execution artifacts."""
    if action.action in {ActionType.FILL, ActionType.SELECT} and action.value is not None:
        return action.model_copy(update={"value": "[REDACTED]"})
    return action


def _observation_payload(observation: BrowserObservation) -> dict[str, Any]:
    return observation.model_dump(mode="json")


def _safe_plan_payload(plan: CompiledTestPlan) -> dict[str, Any]:
    """Redact literal action inputs before persisting the compiled plan."""
    payload = plan.model_dump(mode="json")
    for step in payload.get("steps", []):
        if step.get("value") is not None:
            step["value"] = "[REDACTED]"
    for assertion in payload.get("assertions", []):
        expected = assertion.get("expected")
        target = str(assertion.get("target", "")).casefold()
        if isinstance(expected, str) and (
            any(
                term in target
                for term in ("password", "email", "phone", "mobile", "token", "secret")
            )
            or re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", expected, re.IGNORECASE)
        ):
            assertion["expected"] = "[REDACTED]"
    return payload


def _safe_assertion_result(result: AssertionResult) -> AssertionResult:
    """Keep personal or secret expected values out of the final report."""
    expected = result.assertion.expected
    target = result.assertion.target.casefold()
    is_sensitive = any(
        term in target for term in ("password", "email", "phone", "mobile", "token", "secret")
    ) or (
        isinstance(expected, str)
        and bool(re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", expected, re.IGNORECASE))
    )
    if not is_sensitive:
        return result
    safe_assertion = result.assertion.model_copy(update={"expected": "[REDACTED]"})
    return result.model_copy(update={"assertion": safe_assertion, "actual": "[REDACTED]"})


def _sanitize_error(message: str) -> str:
    """Redact common secret-bearing URL query values in browser error messages."""
    return re.sub(
        r"(?i)((?:token|key|secret|password|passwd|auth|session|code|credential)=)[^&\s'\"]+",
        r"\1[REDACTED]",
        message,
    )


async def _save_screenshot(page: Page, path: Path) -> Path | None:
    try:
        if page.is_closed():
            return None
        await page.screenshot(path=str(path), full_page=True, animations="disabled")
        return path
    except Exception:
        return None


async def create_agent_run(
    *,
    spec: TestSpec,
    settings: Settings,
    headless: bool | None = None,
    max_elements_per_frame: int = 500,
    max_retries_per_step: int | None = None,
    client: LLMClient | None = None,
) -> AgentRunResult:
    """Compile, inspect, and execute a test using a bounded agent loop.

    Every action is planned against a fresh browser observation. Failed actions are
    retried only within the configured per-step limit. A successful action advances
    the semantic plan; an error never causes an action to be silently treated as
    successful. Once all actions complete, deterministic Playwright assertions produce
    the separate final test_status PASS or FAIL.
    """
    if max_elements_per_frame < 1:
        raise ValueError("max_elements_per_frame must be at least 1")
    retry_limit = (
        settings.agent_max_retries_per_step
        if max_retries_per_step is None
        else max_retries_per_step
    )
    if retry_limit < 0 or retry_limit > 5:
        raise ValueError("max_retries_per_step must be between 0 and 5")

    selected_client = client or create_llm_client(settings)
    created_client = client is None
    started_at = datetime.now(timezone.utc)
    run_dir = (
        settings.artifact_dir
        / f"agent-{_safe_name(str(spec.url))}-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}"
        f"-{uuid4().hex[:6]}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    observations_dir = run_dir / "observations"
    screenshots_dir = run_dir / "screenshots"
    observations_dir.mkdir()
    screenshots_dir.mkdir()

    plan: CompiledTestPlan | None = None
    steps: list[StepResult] = []
    completed: set[int] = set()
    retries_by_step: dict[int, int] = {}
    event_history: list[dict[str, Any]] = []
    artifact_paths: list[Path] = []
    failure_reason: str | None = None
    status = AgentRunStatus.ERROR
    test_status: TestStatus | None = None
    assertion_results: list[AssertionResult] = []
    assertions_evaluated = False
    historical_observations: list[BrowserObservation] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    opened_page_count = 0
    popup_count = 0
    active_page: Page | None = None

    plan_path = run_dir / "compiled-plan.json"
    events_path = run_dir / "events.json"
    report_path = run_dir / "execution.json"

    try:
        async with asyncio.timeout(spec.timeout_seconds):
            plan = await TestInterpreter(selected_client).compile(spec)
            plan_path.write_text(
                json.dumps(_safe_plan_payload(plan), indent=2), encoding="utf-8"
            )
            artifact_paths.append(plan_path)

            async with BrowserSession(settings, headless=headless) as session:
                active_page = await session.goto(str(spec.url))
                observer = BrowserObserver(max_elements_per_frame=max_elements_per_frame)
                planner = ActionPlanner(
                    selected_client,
                    max_elements=settings.llm_max_observed_elements,
                    max_visible_text_chars=settings.llm_max_visible_text_chars,
                )
                last_action_feedback: str | None = None
                observation_number = 0

                while True:
                    next_index = _next_step_index(plan, completed)
                    if next_index is None:
                        status = AgentRunStatus.ACTIONS_COMPLETED
                        break
                    if len(steps) >= spec.max_steps:
                        status = AgentRunStatus.STEP_LIMIT
                        failure_reason = (
                            f"Reached the maximum of {spec.max_steps} action attempts before "
                            "all semantic steps were completed."
                        )
                        break

                    active_page = _active_page(session, active_page)
                    observation = await observer.inspect(active_page)
                    historical_observations.append(observation)
                    observation_number += 1
                    observation_path = (
                        observations_dir / f"observation-{observation_number:03d}.json"
                    )
                    observation_path.write_text(
                        json.dumps(_observation_payload(observation), indent=2),
                        encoding="utf-8",
                    )
                    artifact_paths.append(observation_path)

                    decision = await planner.plan_next(
                        spec=spec,
                        plan=plan,
                        observation=observation,
                        completed_step_indices=completed,
                        last_action_feedback=last_action_feedback,
                    )
                    decision_payload = decision.model_dump(mode="json")
                    if decision.action is not None:
                        decision_payload["action"] = _safe_action(
                            decision.action
                        ).model_dump(mode="json")
                    event: dict[str, Any] = {
                        "event": "planner_decision",
                        "observation_id": observation.observation_id,
                        "observation_path": str(observation_path),
                        "decision": decision_payload,
                        "completed_step_indices": sorted(completed),
                    }

                    if decision.decision == AgentDecisionType.BLOCKED:
                        status = AgentRunStatus.BLOCKED
                        failure_reason = decision.reason
                        event_history.append(event)
                        break
                    if decision.decision == AgentDecisionType.FINISH:
                        # ActionPlanner only permits finish when every step is complete.
                        status = AgentRunStatus.ACTIONS_COMPLETED
                        event_history.append(event)
                        break

                    action = decision.action
                    if action is None:  # Defensive; the Pydantic contract forbids this.
                        raise RuntimeError("Planner returned an act decision without an action.")
                    action_executor = ObservedActionExecutor(observer.locator_resolver(), settings)
                    attempt_started = datetime.now(timezone.utc)
                    step_screenshot: Path | None = None
                    try:
                        action_message = await action_executor.execute(action)
                        success = True
                        event["execution"] = {
                            "success": True,
                            "message": action_message,
                        }
                        completed.add(next_index)
                        retries_by_step.pop(next_index, None)
                        last_action_feedback = (
                            f"Step {next_index + 1} completed successfully. {action_message}"
                        )
                    except Exception as exc:
                        success = False
                        retry_count = retries_by_step.get(next_index, 0) + 1
                        retries_by_step[next_index] = retry_count
                        error_message = _sanitize_error(
                            f"{type(exc).__name__}: {str(exc)[:700]}"
                        )
                        event["execution"] = {
                            "success": False,
                            "error": error_message,
                            "retry_number": retry_count,
                            "retry_limit": retry_limit,
                        }
                        last_action_feedback = (
                            f"The previous attempt for step {next_index + 1} failed: "
                            f"{error_message}. Re-inspect the page and choose a safe retry "
                            "for the same semantic step, or return blocked if intervention "
                            "is required. Do not claim the step completed."
                        )

                    active_page = _active_page(session, active_page)
                    if success:
                        step_screenshot = await _save_screenshot(
                            active_page,
                            screenshots_dir / f"step-{len(steps) + 1:03d}.png",
                        )
                    else:
                        step_screenshot = await _save_screenshot(
                            active_page,
                            screenshots_dir / f"failure-{len(steps) + 1:03d}.png",
                        )
                    if step_screenshot is not None:
                        artifact_paths.append(step_screenshot)

                    finished_at = datetime.now(timezone.utc)
                    steps.append(
                        StepResult(
                            step_number=len(steps) + 1,
                            action=_safe_action(action),
                            success=success,
                            message=(
                                action_message
                                if success
                                else event["execution"]["error"]
                            ),
                            started_at=attempt_started,
                            finished_at=finished_at,
                            screenshot_path=(
                                str(step_screenshot) if step_screenshot is not None else None
                            ),
                        )
                    )
                    event["step_number"] = len(steps)
                    event["semantic_step_index"] = next_index
                    event["action"] = _safe_action(action).model_dump(mode="json")
                    event_history.append(event)

                    if not success and retries_by_step[next_index] > retry_limit:
                        status = AgentRunStatus.FAILED
                        failure_reason = (
                            f"Step {next_index + 1} failed after {retry_limit} retries. "
                            f"Last error: {event['execution']['error']}"
                        )
                        break

                # Evaluate deterministic assertions only after every planned action has
                # completed. A completed action sequence is not itself a passing test.
                if status == AgentRunStatus.ACTIONS_COMPLETED and plan is not None:
                    active_page = _active_page(session, active_page)
                    final_observation = await observer.inspect(active_page)
                    final_observation_path = run_dir / "assertion-observation.json"
                    final_observation_path.write_text(
                        json.dumps(_observation_payload(final_observation), indent=2),
                        encoding="utf-8",
                    )
                    artifact_paths.append(final_observation_path)

                    raw_assertion_results = await AssertionEvaluator().evaluate_all(
                        page=active_page,
                        plan=plan,
                        observation=final_observation,
                        resolver=observer.locator_resolver(),
                        historical_observations=historical_observations,
                    )
                    assertion_results = [
                        _safe_assertion_result(item) for item in raw_assertion_results
                    ]
                    assertions_evaluated = True
                    test_status = (
                        TestStatus.PASS
                        if assertion_results and all(item.passed for item in assertion_results)
                        else TestStatus.FAIL
                    )
                    if test_status == TestStatus.FAIL:
                        failed_targets = [
                            item.assertion.target for item in assertion_results if not item.passed
                        ]
                        failure_reason = "Failed assertions: " + "; ".join(failed_targets)
                    assertion_path = run_dir / "assertions.json"
                    assertion_path.write_text(
                        json.dumps(
                            [item.model_dump(mode="json") for item in assertion_results],
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    artifact_paths.append(assertion_path)
                    event_history.append(
                        {
                            "event": "assertions_evaluated",
                            "observation_id": final_observation.observation_id,
                            "test_status": test_status.value,
                            "passed": sum(item.passed for item in assertion_results),
                            "failed": sum(not item.passed for item in assertion_results),
                        }
                    )

                # Capture diagnostics before BrowserSession clears its page references.
                console_errors = list(session.console_errors)
                page_errors = list(session.page_errors)
                opened_page_count = max(0, len(session.pages) - 1)
                popup_count = len(session.popups)
                if active_page is not None:
                    final_screenshot = await _save_screenshot(
                        active_page, run_dir / "final.png"
                    )
                    if final_screenshot is not None:
                        artifact_paths.append(final_screenshot)

    except TimeoutError:
        status = AgentRunStatus.TIMEOUT
        test_status = None
        assertion_results = []
        assertions_evaluated = False
        failure_reason = f"The test exceeded its {spec.timeout_seconds}-second time limit."
    except Exception as exc:
        status = AgentRunStatus.ERROR
        test_status = None
        assertion_results = []
        assertions_evaluated = False
        failure_reason = _sanitize_error(
            f"{type(exc).__name__}: {str(exc)[:1_000]}"
        )

    if plan is not None:
        artifact_paths.insert(0, plan_path)
    events_path.write_text(json.dumps(event_history, indent=2), encoding="utf-8")
    artifact_paths.append(events_path)
    artifact_paths = list(dict.fromkeys(path for path in artifact_paths if path.exists()))

    result = AgentRunResult(
        test_name=plan.name if plan is not None else "Natural-language browser test",
        status=status,
        test_status=test_status,
        url=_sanitize_url(str(spec.url)),
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        steps=steps,
        completed_step_indices=sorted(completed),
        assertions=assertion_results,
        failure_reason=failure_reason,
        console_errors=console_errors,
        page_errors=page_errors,
        opened_page_count=opened_page_count,
        popup_count=popup_count,
        assertions_evaluated=assertions_evaluated,
        artifact_paths=[str(path) for path in artifact_paths] + [str(report_path)],
    )
    report_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    if created_client:
        close = getattr(selected_client, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
    return result


def run_agent(
    *,
    spec: TestSpec,
    settings: Settings,
    headless: bool | None = None,
    max_elements_per_frame: int = 500,
    max_retries_per_step: int | None = None,
) -> AgentRunResult:
    """Synchronous CLI adapter for :func:`create_agent_run`."""
    return asyncio.run(
        create_agent_run(
            spec=spec,
            settings=settings,
            headless=headless,
            max_elements_per_frame=max_elements_per_frame,
            max_retries_per_step=max_retries_per_step,
        )
    )
