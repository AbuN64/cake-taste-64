"""`taste recomp symbols|generate|facts --version V`."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cake_taste.files import atomic_json

from . import generate as gen
from .settings import load


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste recomp")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("symbols", "generate", "facts"):
        command = commands.add_parser(name)
        command.add_argument("--version", required=True)
        if name == "generate":
            command.add_argument("--refused", type=Path, help="also copy refused.tsv here")
        if name == "facts":
            command.add_argument("--output", type=Path, help="write the facts JSON here")
    args = parser.parse_args(argv)
    settings = load(profile)
    if args.action == "symbols":
        return gen.write_symbols(settings, args.version)
    if args.action == "generate":
        return gen.generate(settings, args.version, args.refused)
    facts = gen.oracle_facts(settings, args.version)
    if args.output is not None:
        atomic_json(args.output, facts, sort_keys=True)
    return facts
