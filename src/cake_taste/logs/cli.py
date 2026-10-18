"""Public logs commands: append, tail, and live console."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .console import run_console
from .events import EventReader, append_event, events_path
from .header import Header


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste logs")
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("append")
    add.add_argument("--actor", required=True)
    add.add_argument("--status", default="info")
    add.add_argument("--message", required=True)
    add.add_argument("--evidence")
    add.add_argument("--kind")
    add.add_argument("--payload", type=json.loads)
    tail = commands.add_parser("tail")
    tail.add_argument("--count", type=int, default=20)
    commands.add_parser("watch-refs")
    console = commands.add_parser("console")
    console.add_argument("--title")
    args = parser.parse_args(argv)
    if args.action == "append":
        return append_event(
            profile, args.actor, args.status, args.message, args.evidence, kind=args.kind, payload=args.payload
        )
    path = events_path(profile)
    if args.action == "tail":
        reader = EventReader(path)
        records = reader.read_new()
        return {"path": str(path), "events": records[-max(0, args.count) :], "total": reader.count}
    section = profile.section("logs")
    if args.action == "watch-refs":
        from .gitrefs import RefSource

        RefSource(profile, section["git_refs"]).serve()
        return {"closed": True}
    run_console(
        path,
        title=args.title or str(section.get("title", "ORCHESTRATOR")),
        max_lines=int(section.get("max_lines", 4000)),
        header=Header(list(section.get("header_kinds", [])), list(section.get("header_counters", []))),
    )
    return {"closed": True, "path": str(path)}
