"""Explicit, sourced function sections for code missing from a decomp's disassembly."""
from __future__ import annotations

import json
import re
from pathlib import Path

from cake_taste import TasteError

from .splat import Segment


def read_sections(path: Path) -> tuple[Segment, ...]:
    try:
        records = json.loads(path.read_text())
        result = []
        names = set()
        for row in records:
            name, rom, vram, size = (row[k] for k in ("name", "rom", "vram", "size"))
            if not row.get("source") or not re.fullmatch(r"[A-Za-z_]\w*", name) or name in names:
                raise ValueError("sections need unique names and a source")
            if any(type(n) is not int or n < 0 or n & 3 for n in (rom, vram, size)) or not size:
                raise ValueError("section addresses and sizes must be nonnegative aligned integers")
            functions = row["functions"]
            end = 0
            if not functions:
                raise ValueError("section must contain functions")
            for fn in sorted(functions, key=lambda f: f["offset"]):
                offset, length = fn["offset"], fn["size"]
                if (not re.fullmatch(r"[A-Za-z_]\w*", fn["name"])
                        or any(type(n) is not int or n < 0 or n & 3 for n in (offset, length))
                        or not length or offset < end or offset + length > size):
                    raise ValueError("function boundaries must be aligned, disjoint, and inside their section")
                end = offset + length
            names.add(name)
            result.append(Segment(name, "code", rom, vram, size,
                                  {"functions": functions, "source": row["source"]}))
        return tuple(result)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TasteError("recomp.sections", f"{path}: {exc}") from exc
