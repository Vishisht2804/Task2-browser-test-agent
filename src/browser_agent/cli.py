"""Command-line entry point. Phase 0 validates requests; execution comes later."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from pydantic import ValidationError

from browser_agent import __version__
from browser_agent.config import get_settings
from browser_agent.schemas import TestSpec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="browser-agent",
        description="Validate a natural-language browser test request.",
    )
    parser.add_argument("--version", action="version", version=f"browser-agent {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Accept and validate a browser test request (execution is added in later phases).",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "run":
        parser.error("unknown command")

    settings = get_settings()
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
        print("Status:       READY (browser execution is not implemented in Phase 0)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
