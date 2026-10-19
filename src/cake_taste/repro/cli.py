"""Public isolated reproduction commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cake_taste import TasteError

from .bounded import bounded_probe
from .capture import capture_probe
from .cases import cases_probe
from .narration import MATCH_STATES, narrated_probe, probe_story
from .probe import KINDS, probe
from .runner import run_command
from .scenario import scenario


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
    scene = commands.add_parser("scenario")
    scene.add_argument("name")
    grab = commands.add_parser("capture", help="run the oracle to a function's entry and freeze RDRAM and registers")
    grab.add_argument("--function", required=True, help="name or address")
    grab.add_argument("--version", required=True)
    grab.add_argument("--stubs", default="", help="comma-separated callees to record, in call order")
    grab.add_argument("--hit", type=int, default=1, help="which entry to stop at (default: the first)")
    shot = commands.add_parser("probe")
    shot.add_argument("--kind", required=True, choices=(*KINDS, "call", "cases"),
                      help="call = bounded closure probe; cases = many cases with an event record")
    shot.add_argument("--function", help="name or address; a call request may name it instead")
    shot.add_argument("--range", dest="span")
    shot.add_argument("--frames", type=int)
    shot.add_argument("--input", required=True, type=Path)
    shot.add_argument("--question")
    shot.add_argument("--owner-address")
    shot.add_argument("--decomp-path")
    shot.add_argument("--match-state", choices=MATCH_STATES)
    shot.add_argument("--decomp-finding")
    args = parser.parse_args(argv)
    if args.action == "scenario":
        return scenario(profile, args.name)
    if args.action == "capture":
        return capture_probe(
            profile, function=args.function, version=args.version, hit=args.hit,
            stubs=tuple(n for n in args.stubs.split(",") if n),
        )
    if args.action == "status":
        config = profile.section("repro")
        path = profile.resolve_path(config["status_path"])
        return {"path": str(path), "status": json.loads(path.read_text()) if path.is_file() else None}
    if args.action == "probe":
        story = probe_story(args.input, args.function, {
            "question": args.question, "owner_address": args.owner_address,
            "decomp_path": args.decomp_path, "match_state": args.match_state,
            "decomp_finding": args.decomp_finding,
        }, profile)
        if args.kind == "call":
            return narrated_probe(profile, story, lambda: bounded_probe(
                profile, function=args.function, script=args.input))
        if args.kind == "cases":

            def run_cases() -> dict[str, Any]:
                return cases_probe(profile, function=args.function, script=args.input)

            # a request that is one of many (learning rounds) says so and stays off the narration feed
            return narrated_probe(profile, story, run_cases) if story.get("narrate", True) else run_cases()
        if not args.function:
            raise TasteError("repro.function", f"probe {args.kind} needs --function")
        return narrated_probe(profile, story, lambda: probe(
            profile, kind=args.kind, function=args.function, script=args.input, frames=args.frames, span=args.span))
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
