"""Public `taste media` commands. The core CLI owns JSON stdout."""

import argparse
import json
from pathlib import Path

from cake_taste.files import atomic_bytes

from .publish import publish, render_html
from .store import audit, import_verified


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def run(profile, argv):
    settings = profile.section("media")
    for key in ("root", "timezone", "title"):
        if not settings.get(key):
            raise ValueError(f"media.{key} is required")
    root = profile.resolve_path(settings["root"])
    parser = Parser(prog="taste media", add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("audit", add_help=False)
    publishing = commands.add_parser("publish", add_help=False)
    publishing.add_argument("--source", action="append", required=True)
    publishing.add_argument("--title")
    publishing.add_argument("--description")
    publishing.add_argument("--date")
    publishing.add_argument("--revision")
    importing = commands.add_parser("import", add_help=False)
    importing.add_argument("--source", required=True)
    importing.add_argument("--target", required=True)
    commands.add_parser("render", add_help=False)
    args = parser.parse_args(argv)
    if args.command == "audit":
        return {"action": "audit", "root": str(root), **audit(root)}
    if args.command == "publish":
        result = publish(
            root,
            args.source,
            timezone_name=settings["timezone"],
            page_title=settings["title"],
            viewer_url=settings.get("viewer_url"),
            title=args.title,
            description=args.description,
            date_override=args.date,
            revision=args.revision,
        )
        return {"action": "publish", **result}
    if args.command == "import":
        source = profile.resolve_path(args.source)
        target = profile.resolve_path(args.target)
        return {"action": "import", **import_verified(source, target)}
    catalog = json.loads((Path(root) / "catalog.json").read_text())
    page = render_html(
        catalog["items"],
        settings["title"],
        settings.get("viewer_url", catalog.get("viewer_url", "")),
        settings["timezone"],
    )
    atomic_bytes(Path(root) / "index.html", page.encode())
    return {"action": "render", "root": str(root), "items": len(catalog["items"])}
