"""The one public `taste` entry point; owned domains expose run(profile, argv)."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import traceback
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.config import Profile, load_profile
from cake_taste.dependencies import invoke, verify_dependencies

DOMAINS = ("build", "media", "panes", "stream", "logs", "repro")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise TasteError("cli.arguments", message)


def _profile(path: Path | None) -> Profile:
    if path is None:
        raise TasteError("config.missing", "pass --profile PATH before the command")
    return load_profile(path)


def _domain(name: str, profile: Profile, argv: Sequence[str]) -> dict[str, Any]:
    path = f"cake_taste.{name}.cli"
    try:
        module = importlib.import_module(path)
    except ModuleNotFoundError as exc:
        if exc.name in (path, f"cake_taste.{name}"):
            raise TasteError("component.unavailable", f"{name} is not installed in this Cake Taste build") from exc
        raise
    result = module.run(profile, tuple(argv))
    if not isinstance(result, dict):
        raise TasteError("component.output", f"{name}.cli.run must return a JSON object")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _Parser(prog="taste", description="Bounded experiments, direct tool evidence, and native-output services")
    parser.add_argument("--profile", type=Path, help="explicit project JSON profile")
    parser.add_argument("command", choices=("profile", "dependencies", "unbake", "recipe", *DOMAINS))
    parser.add_argument("args", nargs=argparse.REMAINDER)
    started = time.monotonic()
    invocation = uuid.uuid4().hex
    result: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = []
    code = 0
    command = "parse"
    try:
        parsed = parser.parse_args(args)
        command = parsed.command
        if command == "profile":
            if parsed.args:
                raise TasteError("cli.arguments", "profile takes no extra arguments")
            result = _profile(parsed.profile).describe()
        elif command == "dependencies":
            if parsed.args:
                raise TasteError("cli.arguments", "dependencies takes no extra arguments")
            result = verify_dependencies()
        elif command in ("unbake", "recipe"):
            stdin = sys.stdin.buffer.read() if "-" in parsed.args else b""
            called = invoke(command, _profile(parsed.profile), parsed.args, stdin=stdin)
            if called.stderr:
                sys.stderr.write(called.stderr)
            result = called.describe()
            code = 0 if called.ok else 1
            if code:
                findings = called.document.get("findings", []) or [
                    {
                        "key": "dependency.refused",
                        "blocking": True,
                        "message": f"{command} returned exit {called.returncode}, ok={called.document['ok']}",
                    }
                ]
        else:
            result = _domain(command, _profile(parsed.profile), parsed.args)
    except TasteError as exc:
        code, findings = 1, [exc.finding()]
    except (ValueError, OSError) as exc:
        code, findings = 1, [{"key": "command.failed", "message": str(exc), "blocking": True}]
    except SystemExit as exc:
        # argparse help is the sole plain-text stdout path.
        if exc.code == 0:
            return 0
        code, findings = 2, [{"key": "cli.arguments", "message": f"{command} rejected its arguments", "blocking": True}]
    except Exception as exc:
        sys.stderr.write(traceback.format_exc())
        code, findings = 3, [{"key": "internal.error", "message": repr(exc), "blocking": True}]
    envelope = {
        "ok": code == 0,
        "result": result,
        "findings": findings,
        "stages": [{"name": command, "wall_seconds": round(time.monotonic() - started, 6)}],
        "invocation": invocation,
    }
    sys.stdout.write(json.dumps(envelope, sort_keys=True, default=str) + "\n")
    return code
