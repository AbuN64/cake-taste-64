"""Render a verified asset ledger as one portable internal dashboard."""

import argparse
import hashlib
import html
import json
from collections import Counter
from datetime import UTC, datetime
from importlib.resources import files

from cake_taste import TasteError
from cake_taste.media.store import atomic_bytes


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise TasteError("catalog.arguments", message)


def document(ledger_bytes, summary_bytes, groups):
    """Verify identities and the published census before displaying any counts."""
    summary = json.loads(summary_bytes)
    digest = hashlib.sha256(ledger_bytes).hexdigest()
    if digest != summary["distinct_assets_jsonl_sha256"]:
        raise ValueError("Asset ledger SHA-256 differs from its summary")
    rows = [json.loads(line) for line in ledger_bytes.splitlines() if line.strip()]
    ids = [row["asset_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate asset identity in distinct ledger")
    if set(groups) != {"confirmed", "observed_pending_validation"}:
        raise ValueError("asset_status_groups must define confirmed and observed_pending_validation")
    lookup = {}
    for group, statuses in groups.items():
        if not isinstance(statuses, list) or not statuses:
            raise ValueError("Each asset status group must contain explicit status strings")
        for status in statuses:
            if not isinstance(status, str) or not status or status in lookup:
                raise ValueError("Asset verification statuses must be nonempty and disjoint")
            lookup[status] = group
    for row in rows:
        if row["verification_status"] not in lookup:
            raise ValueError(f"Unclassified verification status: {row['verification_status']}")
        for key in ("asset_id", "type", "format", "revision", "rom_sha256"):
            if not isinstance(row[key], str) or not row[key]:
                raise ValueError(f"Missing asset field: {key}")
        if row["rom_sha256"] != summary["rom_sha256"] or row["revision"] != summary["revision"]:
            raise ValueError("Asset target identity differs from summary")
        for location in row["locations"]:
            start, end = location["range"]
            if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                raise ValueError("Invalid asset location range")
            if location["space"] == "rom" and end > summary["rom_size"]:
                raise ValueError("Asset range exceeds ROM size")
    for group in groups:
        subset = [row for row in rows if lookup[row["verification_status"]] == group]
        expected = summary[group]
        counts = {
            "distinct_assets": len(subset),
            "counts_by_type": dict(Counter(row["type"] for row in subset)),
            "counts_by_format": dict(Counter(row["format"] for row in subset)),
            "named": sum(bool(row.get("name")) for row in subset),
            "unnamed": sum(not row.get("name") for row in subset),
        }
        if any(expected[key] != value for key, value in counts.items()):
            raise ValueError(f"Asset summary census differs from ledger: {group}")
    if len(rows) != summary["identified_total_including_observed"]:
        raise ValueError("Asset summary total differs from ledger")
    return {"rows": rows, "summary": summary, "groups": lookup, "ledger_sha256": digest}


def render(data, title):
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    template = files("cake_taste.catalog").joinpath("page.html").read_text()
    return template.replace("__TITLE__", html.escape(title)).replace("__DATA__", payload).encode()


def run(profile, argv):
    parser = Parser(prog="taste catalog", add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("render", add_help=False)
    command.add_argument("--output", required=True)
    command.add_argument("--title", required=True)
    args = parser.parse_args(argv)
    target = profile.section("target")
    ledger = profile.resolve_path(target["asset_ledger"])
    summary = profile.resolve_path(target["asset_summary"])
    output = profile.resolve_path(args.output)
    if output in (ledger, summary) or output.is_relative_to(profile.project_root):
        raise ValueError("Dashboard output must be outside source and ledger inputs")
    data = document(ledger.read_bytes(), summary.read_bytes(), target["asset_status_groups"])
    data["generated_at"] = datetime.now(UTC).isoformat()
    data["title"] = args.title
    data["source_paths"] = {"ledger": str(ledger), "summary": str(summary)}
    if "asset_observations" in target:
        observations = profile.resolve_path(target["asset_observations"])
        if output == observations:
            raise ValueError("Dashboard output must not replace raw observations")
        with observations.open("rb") as stream:
            data["raw_observation_count"] = sum(bool(line.strip()) for line in stream)
    content = render(data, args.title)
    atomic_bytes(output, content)
    return {
        "output": str(output),
        "sha256": hashlib.sha256(content).hexdigest(),
        "ledger_sha256": data["ledger_sha256"],
        "rows": len(data["rows"]),
        "confirmed": data["summary"]["confirmed"]["distinct_assets"],
        "pending": data["summary"]["observed_pending_validation"]["distinct_assets"],
    }
