"""Run Phase 3 interpretation and one-step planning without executing actions."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from browser_agent.agent.compiler import TestInterpreter
from browser_agent.agent.planner import ActionPlanner
from browser_agent.browser.observer import BrowserObserver
from browser_agent.browser.session import BrowserSession
from browser_agent.config import Settings
from browser_agent.llm.client import LLMClient, create_llm_client
from browser_agent.schemas import AgentDecision, BrowserObservation, CompiledTestPlan, TestSpec


@dataclass(frozen=True)
class PlanningArtifacts:
    """Saved Phase 3 outputs and the first proposed browser action."""

    plan: CompiledTestPlan
    observation: BrowserObservation
    decision: AgentDecision
    artifact_dir: Path
    artifact_paths: list[Path]


def _safe_name(url: str) -> str:
    parts = urlsplit(url)
    source = f"{parts.hostname or 'page'}-{parts.path}"
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", source).strip("-").lower()[:40] or "page"


async def create_planning_artifacts(
    *,
    spec: TestSpec,
    settings: Settings,
    headless: bool | None = None,
    max_elements_per_frame: int = 500,
    client: LLMClient | None = None,
) -> PlanningArtifacts:
    """Compile the instruction, inspect the live page, and plan one action.

    No browser action is executed in Phase 3. The result is a proposal for Phase 4.
    """
    selected_client = client or create_llm_client(settings)
    created_client = client is None
    run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = (
        settings.artifact_dir
        / f"planning-{_safe_name(str(spec.url))}-{run_name}-{uuid4().hex[:6]}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)

    try:
        async with asyncio.timeout(spec.timeout_seconds):
            plan = await TestInterpreter(selected_client).compile(spec)
            async with BrowserSession(settings, headless=headless) as session:
                page = await session.goto(str(spec.url))
                observer = BrowserObserver(max_elements_per_frame=max_elements_per_frame)
                observation = await observer.inspect(page)
                screenshot_path = run_dir / "page.png"
                try:
                    await session.screenshot(str(screenshot_path))
                except Exception:
                    screenshot_path = None

            decision = await ActionPlanner(
                selected_client,
                max_elements=settings.llm_max_observed_elements,
                max_visible_text_chars=settings.llm_max_visible_text_chars,
            ).plan_next(
                spec=spec,
                plan=plan,
                observation=observation,
            )

        plan_path = run_dir / "compiled-plan.json"
        observation_path = run_dir / "observation.json"
        decision_path = run_dir / "first-decision.json"
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        observation_path.write_text(
            json.dumps(observation.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        decision_path.write_text(decision.model_dump_json(indent=2), encoding="utf-8")
        artifact_paths = [plan_path, observation_path, decision_path]
        if screenshot_path is not None:
            artifact_paths.append(screenshot_path)
        summary_path = run_dir / "planning-summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "test_spec": {
                        "url": observation.url,
                        "instruction": spec.instruction,
                        "test_data_refs": spec.test_data_refs,
                        "max_steps": spec.max_steps,
                        "timeout_seconds": spec.timeout_seconds,
                    },
                    "decision": decision.model_dump(mode="json"),
                    "artifacts": [str(path) for path in artifact_paths],
                    "note": "Phase 3 plans one action; it does not execute browser actions.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        artifact_paths.append(summary_path)
        return PlanningArtifacts(
            plan=plan,
            observation=observation,
            decision=decision,
            artifact_dir=run_dir,
            artifact_paths=artifact_paths,
        )
    finally:
        if created_client:
            close = getattr(selected_client, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    # Cleanup failure must not hide the actual planning result/error.
                    pass


def run_planning(
    *,
    spec: TestSpec,
    settings: Settings,
    headless: bool | None = None,
    max_elements_per_frame: int = 500,
) -> PlanningArtifacts:
    """Synchronous adapter for the CLI."""
    return asyncio.run(
        create_planning_artifacts(
            spec=spec,
            settings=settings,
            headless=headless,
            max_elements_per_frame=max_elements_per_frame,
        )
    )
