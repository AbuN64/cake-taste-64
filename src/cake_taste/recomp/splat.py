"""Read the subset of a splat segment file that symbol generation needs (no YAML dependency)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cake_taste import TasteError

_ROW = re.compile(r"^\s*- \[(.*)\]\s*(?:#.*)?$")
_KEY = re.compile(r"^(\s*)(?:- )?([A-Za-z_][\w.]*):\s*(.*?)\s*$")


@dataclass(frozen=True)
class Segment:
    name: str
    kind: str
    start: int
    vram: int | None
    size: int | None
    options: dict[str, Any] = field(default_factory=dict)
    subsegments: tuple[tuple[int, str, str], ...] = ()

    @property
    def overlay(self) -> bool:
        return self.kind == "overlay" or bool(self.options.get("overlay")) or "exclusive_ram_id" in self.options


@dataclass(frozen=True)
class Layout:
    sha1: str
    segments: tuple[Segment, ...]

    def extent(self, segment: Segment, rom_size: int | None = None) -> int:
        """Bytes a segment owns: its `size` key, else up to the next segment start."""
        if segment.size is not None:
            return segment.size
        later = sorted(s.start for s in self.segments if s.start > segment.start)
        if later:
            return later[0] - segment.start
        if rom_size is not None:
            return rom_size - segment.start
        raise TasteError("recomp.layout", f"segment {segment.name} is last and has no size")


def _scalar(text: str) -> Any:
    text = text.split(" #")[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text, 0)
    except ValueError:
        return text


def _row(text: str) -> list[Any]:
    return [_scalar(part) for part in text.split(",")]


def parse_layout(path: Path) -> Layout:
    sha1 = ""
    segments: list[Segment] = []
    current: dict[str, Any] | None = None
    in_segments = False
    in_sub = False
    subs: list[tuple[int, str, str]] = []

    def close() -> None:
        nonlocal current, subs
        if current is None:
            return
        opts = dict(current)
        name = str(opts.pop("name", f"segment_{len(segments)}"))
        kind = str(opts.pop("type", ""))
        start = opts.pop("start", None)
        if not isinstance(start, int):
            raise TasteError("recomp.layout", f"{path}: segment {name} has no integer start")
        vram = opts.pop("vram", None)
        size = opts.pop("size", None)
        segments.append(
            Segment(
                name,
                kind,
                start,
                vram if isinstance(vram, int) else None,
                size if isinstance(size, int) else None,
                opts,
                tuple(subs),
            )
        )
        current, subs = None, []

    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not in_segments:
            match = _KEY.match(line)
            if match and not match.group(1):
                if match.group(2) == "sha1":
                    sha1 = str(_scalar(match.group(3)))
                in_segments = match.group(2) == "segments" and not match.group(3)
            continue
        if not line.startswith(" ") and not line.startswith("-"):
            close()
            in_segments = False
            continue
        row = _ROW.match(line)
        if row and in_sub and current is not None:
            values = _row(row.group(1))
            if len(values) >= 2 and isinstance(values[0], int):
                subs.append((values[0], str(values[1]), str(values[2]) if len(values) > 2 else ""))
            continue
        if row and not in_sub:
            close()
            values = _row(row.group(1))
            if len(values) >= 2 and isinstance(values[0], int):
                current = {"start": values[0], "type": values[1], "name": values[2] if len(values) > 2 else ""}
            continue
        match = _KEY.match(line)
        if not match:
            continue
        indent, key, value = len(match.group(1)), match.group(2), match.group(3)
        if line.lstrip().startswith("- "):
            close()
            current, in_sub = {}, False
        if current is None:
            continue
        if key == "subsegments":
            in_sub = True
        elif value:
            in_sub = False if indent <= 4 else in_sub
            current[key] = _scalar(value)
    close()
    if not segments:
        raise TasteError("recomp.layout", f"{path}: no segments found")
    return Layout(sha1, tuple(segments))
