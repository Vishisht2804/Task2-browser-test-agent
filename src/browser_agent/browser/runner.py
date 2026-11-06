"""Run a deterministic action plan and persist execution evidence."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from browser_agent.browser.executor import BrowserExecutor
from browser_agent.browser.session import BrowserSession, BrowserStartupError
from browser_agent.config import Settings
from browser_agent.schemas import (
    ManualExecutionReport,
    ManualStepResult,
    ManualTestPlan,
    TestStatus,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()[:48] or "browser-test"


async def execute_manual_plan(
    *,
    url: str,
    plan: ManualTestPlan,
    settings: Settings,
    headless: bool | None = None,
) -> ManualExecutionReport:
    """Execute the plan sequentially; PASS means actions completed, not assertions passed."""
    started_at = datetime.now(timezone.utc)
    run_dir = settings.artifact_dir / f"{_safe_name(plan.name)}-{_timestamp()}-{uuid4().hex[:6]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    steps: list[ManualStepResult] = []
    artifact_paths: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    opened_page_count = 0
    popup_count = 0
    failure_reason: str | None = None
    status = TestStatus.PASS
    final_url = url

    try:
        async with asyncio.timeout(settings.default_timeout_seconds):
            async with BrowserSession(settings, headless=headless) as session:
                try:
                    page = await session.goto(url)
                    executor = BrowserExecutor(page, settings)
                    for number, action in enumerate(plan.actions, start=1):
                        action_started = datetime.now(timezone.utc)
                        try:
                            message = await executor.execute(action)
                            steps.append(
                                ManualStepResult(
                                    step_number=number,
                                    action=action.action,
                                    locator=executor.describe_locator(action.locator),
                                    success=True,
                                    message=message,
                                    started_at=action_started,
                                    finished_at=datetime.now(timezone.utc),
                                )
                            )
                        except Exception as exc:
                            failure_reason = f"Step {number} ({action.action.value}) failed: {exc}"
                            status = TestStatus.FAIL
                            failure_screenshot = run_dir / "failure.png"
                            try:
                                await session.screenshot(str(failure_screenshot))
                                artifact_paths.append(str(failure_screenshot))
                            except Exception:
                                pass
                            steps.append(
                                ManualStepResult(
                                    step_number=number,
                                    action=action.action,
                                    locator=executor.describe_locator(action.locator),
                                    success=False,
                                    message=f"{type(exc).__name__}: {exc}",
                                    started_at=action_started,
                                    finished_at=datetime.now(timezone.utc),
                                    screenshot_path=(
                                        str(failure_screenshot)
                                        if failure_screenshot.exists()
                                        else None
                                    ),
                                )
                            )
                            break

                    final_url = page.url
                    if status == TestStatus.PASS:
                        final_screenshot = run_dir / "final.png"
                        await session.screenshot(str(final_screenshot))
                        artifact_paths.append(str(final_screenshot))
                except Exception as exc:
                    status = TestStatus.ERROR
                    failure_reason = (
                        "Browser navigation or session error: "
                        f"{type(exc).__name__}: {exc}"
                    )
                finally:
                    console_errors = session.console_errors.copy()
                    page_errors = session.page_errors.copy()
                    opened_page_count = max(0, len(session.pages) - 1)
                    popup_count = len(session.popups)
    except TimeoutError:
        status = TestStatus.ERROR
        failure_reason = (
            f"Overall execution exceeded the configured "
            f"{settings.default_timeout_seconds}-second timeout"
        )
    except BrowserStartupError as exc:
        status = TestStatus.ERROR
        failure_reason = str(exc)
    except Exception as exc:
        status = TestStatus.ERROR
        failure_reason = f"Unexpected browser runner error: {type(exc).__name__}: {exc}"

    finished_at = datetime.now(timezone.utc)
    report_path = run_dir / "execution.json"
    report = ManualExecutionReport(
        test_name=plan.name,
        status=status,
        url=final_url,
        started_at=started_at,
        finished_at=finished_at,
        steps=steps,
        console_errors=console_errors,
        page_errors=page_errors,
        opened_page_count=opened_page_count,
        popup_count=popup_count,
        failure_reason=failure_reason,
        artifact_paths=[*artifact_paths, str(report_path)],
    )
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return report


def run_manual_plan(
    *,
    url: str,
    plan: ManualTestPlan,
    settings: Settings,
    headless: bool | None = None,
) -> ManualExecutionReport:
    """Synchronous adapter used by the CLI and simple scripts."""
    return asyncio.run(
        execute_manual_plan(url=url, plan=plan, settings=settings, headless=headless)
    )
