"""Commands for configured process, console, artifact, and layout roles."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from . import artifact, layout
from .supervisor import run_supervisor


def _role(profile: Any, role_id: str) -> dict[str, Any]:
    roles = profile.section("panes")["roles"]
    if role_id not in roles:
        raise ValueError("unknown pane role: " + role_id)
    return roles[role_id]


def _launch(profile: Any, config: dict[str, Any]) -> None:
    release = (
        profile.resolve_path(config["release_current"]).resolve(strict=True) if config.get("release_current") else None
    )
    mapping = {
        "release": str(release) if release else "",
        "runtime_root": str(profile.runtime_root),
        "output_root": str(profile.output_root),
    }
    command = [str(value).format_map(mapping) for value in config["command"]]
    env = os.environ.copy()
    env.update({str(key): str(value).format_map(mapping) for key, value in config.get("env", {}).items()})
    os.execvpe(command[0], command, env)


def run(profile: Any, argv: tuple[str, ...]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="taste panes")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("launch", "supervise", "viewer", "status"):
        item = commands.add_parser(name)
        item.add_argument("--role", required=True)
    show = commands.add_parser("show-artifact")
    show.add_argument("--role", required=True)
    show.add_argument("--source", required=True, type=Path)
    show.add_argument("--name", required=True)
    commands.add_parser("layout")
    args = parser.parse_args(argv)
    if args.action == "layout":
        return layout.run_layout(profile)
    config = _role(profile, args.role)
    if args.action == "launch":
        _launch(profile, config)
        raise RuntimeError("exec returned")
    if args.action == "supervise":
        if config.get("kind") != "supervised":
            raise ValueError("role is not supervised")
        run_supervisor(profile, args.role, config)
        return {"closed": True, "role": args.role}
    if args.action == "show-artifact":
        if config.get("kind") != "artifact":
            raise ValueError("role is not an artifact viewer")
        return artifact.show(profile, config, args.source, args.name)
    if args.action == "viewer":
        if config.get("kind") != "artifact":
            raise ValueError("role is not an artifact viewer")
        selected = artifact.current(profile, config)
        mapping = {
            "image": selected["display_copy"],
            "name": selected["name"],
            "runtime_root": str(profile.runtime_root),
            "output_root": str(profile.output_root),
        }
        command = [str(item).format_map(mapping) for item in config["command"]]
        env = os.environ.copy()
        env.update({str(key): str(value).format_map(mapping) for key, value in config.get("env", {}).items()})
        os.execvpe(command[0], command, env)
        raise RuntimeError("exec returned")
    path = profile.resolve_path(config["status_path"])
    return {
        "role": args.role,
        "status_path": str(path),
        "status": json.loads(path.read_text()) if path.is_file() else None,
    }
