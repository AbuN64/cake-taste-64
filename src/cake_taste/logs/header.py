"""Sticky console header: latest event per configured kind plus payload counters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .wrap import wrap_line


class Header:
    def __init__(self, kinds: list[str], counters: list[str]):
        self.kinds = list(kinds)
        self.counters = list(counters)
        self.latest: dict[str, dict[str, Any]] = {}
        self.values: dict[str, Any] = {}

    def update(self, record: dict[str, Any]) -> None:
        if record.get("kind") in self.kinds:
            self.latest[record["kind"]] = record
        payload = record.get("payload")
        if isinstance(payload, dict):
            for name in self.counters:
                if name in payload:
                    self.values[name] = payload[name]

    def lines(self, width: int) -> list[str]:
        """One truncated line per kind, then one counters line."""
        result = []
        for kind in self.kinds:
            record = self.latest.get(kind)
            if record is None:
                text = f"{kind}: -"
            else:
                stamp = datetime.fromisoformat(str(record["time"])).astimezone().strftime("%H:%M:%S")
                message = str(record["message"]).replace(chr(10), " ")
                text = f"{kind}: {stamp} {message}"
            result.append(wrap_line(text, max(1, width))[0])
        counts = "  ".join(f"{name}={self.values.get(name, chr(45))}" for name in self.counters)
        result.append(wrap_line(counts, max(1, width))[0] if counts else "")
        return result
