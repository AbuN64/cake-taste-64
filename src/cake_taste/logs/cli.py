"""Public logs commands: append, tail, and live console."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .console import run_console
from .events import MISSION_KINDS, EventReader, append_event, events_path


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
    commands.add_parser("status-line")
    console = commands.add_parser("console")
    console.add_argument("--title")
    console.add_argument("--kinds", help="comma-separated event kinds; mission: " + ",".join(MISSION_KINDS))
    console.add_argument("--host-log", type=Path, help="host metrics in the title; raw output in a collapsed section")
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
    if args.action == "status-line":
        from .statusline import follow

        follow(path)
        return {"closed": True}
    if args.action == "watch-refs":
        from .gitrefs import RefSource

        RefSource(profile, section["git_refs"]).serve()
        return {"closed": True}
    kinds = {kind.strip() for kind in args.kinds.split(",") if kind.strip()} if args.kinds is not None else None
    run_console(
        path,
        title=args.title or str(section.get("title", "ORCHESTRATOR")),
        max_lines=int(section.get("max_lines", 4000)),
        kinds=kinds,
        sources=dict(section.get("console_sources", {})),
        host_log=profile.resolve_path(str(args.host_log)) if args.host_log else None,
    )
    return {"closed": True, "path": str(path)}
