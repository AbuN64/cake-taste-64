"""Append-only factual events and incremental reading for a project profile."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUSES = frozenset({"info", "started", "passed", "failed", "blocked", "decision", "handoff"})


def events_path(profile: Any) -> Path:
    config = profile.section("logs")
    return profile.resolve_path(config["events_path"])


def make_event(
    actor: str,
    status: str,
    message: str,
    evidence: str | None = None,
    *,
    timestamp: datetime | None = None,
    kind: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor, status, message = actor.strip(), status.strip(), message.strip()
    if not actor or not message:
        raise ValueError("actor and message must be nonempty")
    if status not in STATUSES:
        raise ValueError("unknown event status: " + status)
    if any(ord(char) < 32 for char in actor + status):
        raise ValueError("actor/status contain control characters")
    when = timestamp or datetime.now(UTC)
    extra: dict[str, Any] = {}
    if kind:
        extra["kind"] = kind.strip()
    if payload:
        extra["payload"] = payload
    return {
        **extra,
        "schema": 1,
        "time": when.astimezone(UTC).isoformat(),
        "actor": actor,
        "status": status,
        "message": message,
        "evidence": evidence or None,
    }


def append_event(
    profile: Any,
    actor: str,
    status: str,
    message: str,
    evidence: str | None = None,
    *,
    kind: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = make_event(actor, status, message, evidence, kind=kind, payload=payload)
    path = events_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream, fcntl.LOCK_UN)
    return {"event": record, "path": str(path)}


class EventReader:
    """Read appended JSONL once; callers wake it on filesystem events."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.partial = b""
        self.identity: tuple[int, int] | None = None
        self.count = 0

    def read_new(self) -> list[dict[str, Any]]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return []
        identity = (stat.st_dev, stat.st_ino)
        if identity != self.identity or stat.st_size < self.offset:
            self.identity, self.offset, self.partial, self.count = identity, 0, b"", 0
        if stat.st_size == self.offset:
            return []
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            data = stream.read()
            self.offset = stream.tell()
        pieces = (self.partial + data).split(bytes([10]))
        self.partial = pieces.pop()
        records: list[dict[str, Any]] = []
        for raw in pieces:
            if not raw:
                continue
            value = json.loads(raw)
            if value.get("schema") != 1:
                raise ValueError("unsupported event schema")
            records.append(value)
            self.count += 1
        return records


def format_event(value: dict[str, Any]) -> list[str]:
    stamp = datetime.fromisoformat(str(value["time"])).astimezone().strftime("%H:%M:%S")
    actor, status = str(value["actor"]), str(value["status"]).upper()
    message = str(value["message"]).replace("\n", " ")
    lines = [f"{stamp} [{actor}/{status}] {message}"]
    if value.get("evidence"):
        lines.append("    evidence: " + str(value["evidence"]))
    return lines
