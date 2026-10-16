"""Public isolated reproduction commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cake_taste import TasteError

from .runner import run_command


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste repro")
    commands = parser.add_subparsers(dest="action", required=True)
    task = commands.add_parser("run")
    task.add_argument("--name", required=True)
    task.add_argument("--function", default="")
    task.add_argument("--cwd", type=Path)
    task.add_argument("--artifact", action="append", type=Path, default=[])
    task.add_argument("--expect-exit-code", type=int, default=0)
    task.add_argument("command", nargs=argparse.REMAINDER)
    commands.add_parser("status")
    args = parser.parse_args(argv)
    if args.action == "status":
        config = profile.section("repro")
        path = profile.resolve_path(config["status_path"])
        return {"path": str(path), "status": json.loads(path.read_text()) if path.is_file() else None}
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    result = run_command(
        profile,
        name=args.name,
        function=args.function,
        command=command,
        cwd=args.cwd,
        artifacts=args.artifact,
        expected_exit_code=args.expect_exit_code,
    )
    status = result["status"]
    if status["phase"] != "passed":
        reason = status["error"] or (
            f"child exit {status['exit_code']} did not match expected {status['expected_exit_code']}"
        )
        raise TasteError("repro.failed", f"{reason}; receipt: {result['receipt']}")
    return result
