"""Immutable, content-addressed handoff packets from the oracle side to decomp."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import atomic_bytes, sha256_file

KINDS = ("name", "prototype", "type", "struct", "asset-format", "candidate-c")


def _canonical(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def packets_dir(profile: Any) -> Path:
    return profile.resolve_path(profile.section("handoff")["repo"]) / "packets"


def _evidence(profile: Any, ident: str) -> dict[str, Any]:
    runs = profile.resolve_path(profile.section("repro")["runs_root"])
    run = runs / ident
    receipt = run / "receipt.json"
    if not receipt.is_file():
        raise TasteError("handoff.evidence", f"evidence {ident} has no receipt under the repro runs root")
    probe_file = run / "probe.json"
    entry: dict[str, Any] = {"id": ident, "receipt_sha256": sha256_file(receipt)}
    if probe_file.is_file():
        probe = json.loads(probe_file.read_text())
        entry["probe"] = {
            key: probe[key] for key in ("kind", "function", "frames", "range", "input_sha256", "overlay", "env")
        }
        entry["outputs"] = probe["outputs"]
    else:
        entry["outputs"] = [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in json.loads(receipt.read_text()).get("artifacts", [])
        ]
    return entry


def build_packet(profile: Any, *, kind: str, subject: str, evidence: list[str], scope: str, gap: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise TasteError("handoff.kind", f"unknown kind {kind}; expected one of {', '.join(KINDS)}")
    if not subject.strip() or not evidence:
        raise TasteError("handoff.arguments", "subject and at least one evidence id are required")
    config = profile.section("handoff")
    rom = profile.resolve_path(config["rom"])
    tools = {}
    for name, path in sorted(config.get("tools", {}).items()):
        file = profile.resolve_path(path)
        if not file.is_file():
            raise TasteError("handoff.tool", f"tool {name} is missing: {file}")
        tools[name] = sha256_file(file)
    sha1 = hashlib.sha1()
    with rom.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha1.update(block)
    return {
        "schema": 1,
        "kind": kind,
        "subject": subject,
        "scope": scope,
        "gap": gap,
        "version": config["version"],
        "rom_sha1": sha1.hexdigest(),
        "tool_sha256": tools,
        "evidence": [_evidence(profile, ident) for ident in evidence],
    }


def write_packet(profile: Any, packet: dict[str, Any]) -> tuple[str, Path]:
    data = _canonical(packet)
    digest = hashlib.sha256(data).hexdigest()
    path = packets_dir(profile) / f"{digest}.json"
    if path.exists():
        if path.read_bytes() != data:
            raise TasteError("handoff.immutable", f"{path} exists with different bytes")
    else:
        atomic_bytes(path, data)
    return digest, path


def read_packet(profile: Any, digest: str) -> tuple[str, dict[str, Any]]:
    matches = sorted(packets_dir(profile).glob(f"{digest}*.json")) if digest else []
    if len(matches) != 1:
        raise TasteError("handoff.lookup", f"{len(matches)} packets match hash {digest!r}")
    return matches[0].stem, json.loads(matches[0].read_text())


def verify(profile: Any, path: Path) -> str:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != path.stem:
        return "hash-mismatch"
    for item in json.loads(data).get("evidence", []):
        for output in item.get("outputs", []):
            file = Path(output["path"])
            if not file.is_file():
                return "outputs-missing"
            if sha256_file(file) != output["sha256"]:
                return "outputs-changed"
    return "verified"
