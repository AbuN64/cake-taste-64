"""Public logs commands: append, tail and validate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .events import EventReader, append_event, events_path, validate_lines


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste logs")
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("append")
    add.add_argument("--actor", required=True)
    add.add_argument("--status", default="info")
    add.add_argument("--message", required=True)
    add.add_argument("--evidence")
    add.add_argument("--evidence-sha256", help="sha256 of the evidence bytes; writes the path/sha256 object form")
    add.add_argument("--kind")
    add.add_argument("--payload", type=json.loads)
    add.add_argument("--subject", type=json.loads, help="JSON object: repo, ref, version, function, address")
    add.add_argument("--lane", type=json.loads, help="JSON object: name, kind, model, track")
    tail = commands.add_parser("tail")
    tail.add_argument("--count", type=int, default=20)
    check = commands.add_parser("validate")
    check.add_argument("--path", type=Path, help="JSONL feed; default is the profile events_path")
    args = parser.parse_args(argv)
    if args.action == "append":
        evidence = args.evidence
        if args.evidence_sha256:
            if not evidence:
                raise ValueError("--evidence-sha256 needs --evidence")
            evidence = {"path": evidence, "sha256": args.evidence_sha256}
        return append_event(
            profile,
            args.actor,
            args.status,
            args.message,
            evidence,
            kind=args.kind,
            payload=args.payload,
            subject=args.subject,
            lane=args.lane,
        )
    if args.action == "validate":
        path = profile.resolve_path(str(args.path)) if args.path else events_path(profile)
        with path.open("rb") as stream:
            report = validate_lines(stream)
        if report["findings"]:
            raise ValueError(f"{path}: {len(report['findings'])} findings; first: {report['findings'][:5]}")
        return {"path": str(path), **report}
    path = events_path(profile)
    reader = EventReader(path)
    records = reader.read_new()
    return {"path": str(path), "events": records[-max(0, args.count) :], "total": reader.count}
