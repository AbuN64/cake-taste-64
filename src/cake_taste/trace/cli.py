"""`taste trace diff EXPECTED ACTUAL`: align two call traces and name the first divergence."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .diff import diff_traces, read_trace


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste trace")
    commands = parser.add_subparsers(dest="action", required=True)
    diff = commands.add_parser("diff")
    diff.add_argument("expected", type=Path, help="oracle trace")
    diff.add_argument("actual", type=Path, help="port trace")
    diff.add_argument("--next", type=int, default=None, help="work-list length (default: profile trace.work_list)")
    args = parser.parse_args(argv)
    config = profile.section("trace")
    platform = config.get("platform", [])
    count = args.next if args.next is not None else int(config.get("work_list", 20))
    result = diff_traces(read_trace(args.expected, platform), read_trace(args.actual, platform), count)
    return {"dropped_platform_names": len(platform), **result}
