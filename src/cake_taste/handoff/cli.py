"""`taste handoff new|show|list`: write and inspect immutable oracle-to-decomp packets."""

from __future__ import annotations

import argparse
from typing import Any

from cake_taste.logs.events import append_event

from .packets import KINDS, build_packet, packets_dir, read_packet, verify, write_packet


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste handoff")
    commands = parser.add_subparsers(dest="action", required=True)
    new = commands.add_parser("new")
    new.add_argument("--kind", required=True, choices=KINDS)
    new.add_argument("--subject", required=True)
    new.add_argument("--evidence", required=True, help="comma-separated repro run ids")
    new.add_argument("--scope", required=True)
    new.add_argument("--gap", required=True)
    show = commands.add_parser("show")
    show.add_argument("hash")
    commands.add_parser("list")
    args = parser.parse_args(argv)
    if args.action == "new":
        ids = [item for item in args.evidence.split(",") if item]
        packet = build_packet(
            profile, kind=args.kind, subject=args.subject, evidence=ids, scope=args.scope, gap=args.gap
        )
        digest, path = write_packet(profile, packet)
        append_event(profile, "handoff", "handoff", f"HANDOFF {args.kind} {args.subject} -> decomp", str(path))
        return {"hash": digest, "path": str(path)}
    if args.action == "show":
        digest, packet = read_packet(profile, args.hash)
        return {"hash": digest, "packet": packet}
    rows = []
    for path in sorted(packets_dir(profile).glob("*.json")):
        _, packet = read_packet(profile, path.stem)
        rows.append(
            {"hash": path.stem, "kind": packet["kind"], "subject": packet["subject"], "state": verify(profile, path)}
        )
    return {"packets": rows}
