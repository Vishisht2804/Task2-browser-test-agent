"""Command-line entry point for request validation and deterministic execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pydantic import AnyHttpUrl, ValidationError

from browser_agent import __version__
from browser_agent.config import get_settings
from browser_agent.schemas import ManualTestPlan, TestSpec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="browser-agent",
        description=(
            "Run deterministic browser actions or validate a natural-language test request."
        ),
    )
    parser.add_argument("--version", action="version", version=f"browser-agent {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Accept and validate a natural-language browser test request.",
    )
    run_parser.add_argument("--url", required=True, help="Starting website URL (HTTP or HTTPS).")
    run_parser.add_argument("--test", required=True, help="Plain-English test instruction.")
    run_parser.add_argument(
        "--data-ref",
        action="append",
        default=[],
        metavar="ENV_VAR",
        help="Name of an environment variable containing test data; repeat as needed.",
    )
    run_parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum browser actions (default: BA_DEFAULT_MAX_STEPS or 25).",
    )
    run_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Total execution timeout in seconds (default: BA_DEFAULT_TIMEOUT_SECONDS or 120).",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the validated request as JSON.",
    )

    execute_parser = subparsers.add_parser(
        "execute",
        help="Execute a manually-authored JSON action plan in Chromium.",
    )
    execute_parser.add_argument("--url", required=True, help="Starting website URL.")
    execute_parser.add_argument(
        "--plan", required=True, type=Path, help="Path to a manual action-plan JSON file."
    )
    execute_parser.add_argument(
        "--headed", action="store_true", help="Show the browser window for this run."
    )
    execute_parser.add_argument(
        "--json", action="store_true", help="Print the complete execution report as JSON."
    )

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Open a page and discover its controls, text, frames, and open Shadow DOM.",
    )
    inspect_parser.add_argument("--url", required=True, help="Starting website URL.")
    inspect_parser.add_argument(
        "--headed", action="store_true", help="Show the browser window for this inspection."
    )
    inspect_parser.add_argument(
        "--json", action="store_true", help="Print the complete browser observation as JSON."
    )
    inspect_parser.add_argument(
        "--max-elements-per-frame",
        type=int,
        default=500,
        help="Maximum discovered elements per frame (default: 500).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.command == "execute":
        try:
            from browser_agent.browser.runner import run_manual_plan
        except ModuleNotFoundError as exc:
            if exc.name == "playwright":
                print(
                    "Playwright is not installed. Install browser support with "
                    '`python -m pip install -e ".[browser]"`.',
                    file=sys.stderr,
                )
                return 2
            raise

        try:
            validated_url = str(AnyHttpUrl(args.url))
        except ValidationError as exc:
            print(f"Invalid URL: {exc.errors(include_url=False)[0]['msg']}", file=sys.stderr)
            return 2

        try:
            plan_payload = json.loads(args.plan.read_text(encoding="utf-8"))
            plan = ManualTestPlan.model_validate(plan_payload)
        except FileNotFoundError:
            print(f"Action plan not found: {args.plan}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON in action plan: {exc}", file=sys.stderr)
            return 2
        except ValidationError as exc:
            print("Invalid action plan:", file=sys.stderr)
            for error in exc.errors(include_url=False):
                location = ".".join(str(part) for part in error["loc"])
                print(f"  {location}: {error['msg']}", file=sys.stderr)
            return 2

        report = run_manual_plan(
            url=validated_url,
            plan=plan,
            settings=settings,
            headless=False if args.headed else None,
        )
        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            print(f"Action sequence: {report.test_name}")
            print(f"Status:          {report.status.value}")
            print(f"URL:             {report.url}")
            succeeded = sum(step.success for step in report.steps)
            print(f"Steps:           {succeeded}/{len(report.steps)} succeeded")
            print(f"Duration:        {report.duration_seconds:.2f}s")
            print(
                f"Additional pages: {report.opened_page_count} "
                f"(popups: {report.popup_count})"
            )
            if report.failure_reason:
                print(f"Failure:         {report.failure_reason}")
            if report.artifact_paths:
                print("Artifacts:")
                for artifact in report.artifact_paths:
                    print(f"  - {artifact}")
            if report.status.value == "PASS":
                print(
                    "Note: all listed actions completed; Phase 1 does not evaluate "
                    "test assertions yet."
                )
        return 0 if report.status.value == "PASS" else 1

    if args.command == "inspect":
        try:
            from browser_agent.browser.observer_runner import run_inspection
        except ModuleNotFoundError as exc:
            if exc.name == "playwright":
                print(
                    "Playwright is not installed. Install browser support with "
                    '`python -m pip install -e ".[browser]"`.',
                    file=sys.stderr,
                )
                return 2
            raise

        try:
            validated_url = str(AnyHttpUrl(args.url))
        except ValidationError as exc:
            print(f"Invalid URL: {exc.errors(include_url=False)[0]['msg']}", file=sys.stderr)
            return 2
        if args.max_elements_per_frame < 1:
            print("--max-elements-per-frame must be at least 1", file=sys.stderr)
            return 2

        try:
            result = run_inspection(
                url=validated_url,
                settings=settings,
                headless=False if args.headed else None,
                max_elements_per_frame=args.max_elements_per_frame,
            )
        except Exception as exc:
            print(f"Inspection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        if args.json:
            print(result.observation.model_dump_json(indent=2))
        else:
            observation = result.observation
            main_frame_count = sum(frame.is_main_frame for frame in observation.frames)
            child_frame_count = len(observation.frames) - main_frame_count
            visible_count = sum(element.visible for element in observation.elements)
            print("Browser observation completed.")
            print(f"URL:             {observation.url}")
            print(f"Title:           {observation.title or '(no title)'}")
            print(f"Observation ID:  {observation.observation_id}")
            print(f"Frames:          {len(observation.frames)} ({child_frame_count} child frames)")
            print(
                f"Elements:        {len(observation.elements)} "
                f"({visible_count} visible)"
            )
            print(f"Visible text:    {len(observation.visible_text)} characters")
            if observation.warnings:
                print("Warnings:")
                for warning in observation.warnings:
                    print(f"  - {warning}")
            print(f"Observation:     {result.observation_path}")
            if result.screenshot_path:
                print(f"Screenshot:      {result.screenshot_path}")
        return 0

    if args.command != "run":
        parser.error("unknown command")

    try:
        spec = TestSpec(
            url=args.url,
            instruction=args.test,
            test_data_refs=args.data_ref,
            max_steps=(
                args.max_steps if args.max_steps is not None else settings.default_max_steps
            ),
            timeout_seconds=(
                args.timeout if args.timeout is not None else settings.default_timeout_seconds
            ),
        )
    except ValidationError as exc:
        print("Invalid test request:", file=sys.stderr)
        for error in exc.errors(include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            print(f"  {location}: {error['msg']}", file=sys.stderr)
        return 2

    if args.json:
        print(spec.model_dump_json(indent=2))
    else:
        print("Test request accepted and validated.")
        print(f"URL:          {spec.url}")
        print(f"Instruction:  {spec.instruction}")
        print(f"Test data:    {', '.join(spec.test_data_refs) or '(none)'}")
        print(f"Max steps:    {spec.max_steps}")
        print(f"Timeout:      {spec.timeout_seconds}s")
        print(
            "Status:       READY (use `execute` with a JSON action plan "
            "for Phase 1 browser execution)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
