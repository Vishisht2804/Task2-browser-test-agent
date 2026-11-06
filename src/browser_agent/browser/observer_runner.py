"""CLI helper that saves a browser observation and screenshot."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from browser_agent.browser.observer import BrowserObserver
from browser_agent.browser.session import BrowserSession
from browser_agent.config import Settings
from browser_agent.schemas import BrowserObservation


@dataclass(frozen=True)
class InspectionArtifacts:
    observation: BrowserObservation
    observation_path: Path
    screenshot_path: Path | None


def _safe_name(value: str) -> str:
    parts = urlsplit(value)
    safe_source = f"{parts.hostname or 'page'}-{parts.path}"
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", safe_source).strip("-").lower()[:40] or "page"


async def inspect_url(
    *,
    url: str,
    settings: Settings,
    headless: bool | None = None,
    max_elements_per_frame: int = 500,
) -> InspectionArtifacts:
    """Navigate to a URL, inspect it, and persist JSON plus a screenshot."""
    run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = settings.artifact_dir / f"inspection-{_safe_name(url)}-{run_name}-{uuid4().hex[:6]}"
    run_dir.mkdir(parents=True, exist_ok=False)

    async with asyncio.timeout(settings.default_timeout_seconds):
        async with BrowserSession(settings, headless=headless) as session:
            page = await session.goto(url)
            observer = BrowserObserver(max_elements_per_frame=max_elements_per_frame)
            observation = await observer.inspect(page)
            observation_path = run_dir / "observation.json"
            observation_path.write_text(
                json.dumps(observation.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )
            screenshot_path: Path | None = run_dir / "page.png"
            try:
                await session.screenshot(str(screenshot_path))
            except Exception:
                screenshot_path = None

    return InspectionArtifacts(
        observation=observation,
        observation_path=observation_path,
        screenshot_path=screenshot_path,
    )


def run_inspection(
    *,
    url: str,
    settings: Settings,
    headless: bool | None = None,
    max_elements_per_frame: int = 500,
) -> InspectionArtifacts:
    """Synchronous adapter for the CLI."""
    return asyncio.run(
        inspect_url(
            url=url,
            settings=settings,
            headless=headless,
            max_elements_per_frame=max_elements_per_frame,
        )
    )
