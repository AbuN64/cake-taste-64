"""Function boundaries and names from a decomp's disassembly and symbol list."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cake_taste import TasteError

_ROW = re.compile(r"^\s*/\* ([0-9A-Fa-f]+) ([0-9A-Fa-f]{8}) [0-9A-Fa-f]{8} \*/")
_SYMBOL = re.compile(r"^\s*(\S+)\s*=\s*(0x[0-9A-Fa-f]+)\s*;(.*)$")
_SIZE = re.compile(r"^nonmatching\s+(\S+),\s*(0x[0-9A-Fa-f]+|\d+)")


@dataclass(frozen=True)
class Function:
    name: str
    rom: int
    vram: int
    size: int
    source: str = ""  # where a derived extent came from; empty for functions the disassembly delimits


def parse_asm(path: Path) -> list[Function]:
    found: list[Function] = []
    name = ""
    rows: list[tuple[int, int]] = []
    declared: dict[str, int] = {}
    for line in path.read_text().splitlines():
        if line.startswith("glabel "):
            name, rows = line.split()[1], []
        elif line.startswith("nonmatching "):
            match = _SIZE.match(line)
            if match:
                declared[match.group(1)] = int(match.group(2), 0)
        elif line.startswith("endlabel "):
            if not name or not rows:
                raise TasteError("recomp.asm", f"{path}: {line.strip()} closes an empty function")
            size = 4 * len(rows)
            if declared.get(name, size) != size:
                raise TasteError("recomp.asm", f"{path}: {name} has {size} bytes of rows but declares {declared[name]}")
            found.append(Function(name, rows[0][0], rows[0][1], size))
            name, rows = "", []
        elif name:
            match = _ROW.match(line)
            if match:
                rows.append((int(match.group(1), 16), int(match.group(2), 16)))
    return found


def read_functions(asm_dir: Path) -> list[Function]:
    if not asm_dir.is_dir():
        raise TasteError("recomp.asm", f"{asm_dir}: not a directory")
    result: list[Function] = []
    for path in sorted(asm_dir.glob("*.s")):
        result.extend(parse_asm(path))
    return result


def read_symbol_names(path: Path) -> dict[int, str]:
    """Map address to its function name, preferring entries typed as functions."""
    typed: dict[int, str] = {}
    other: dict[int, str] = {}
    for line in path.read_text().splitlines():
        match = _SYMBOL.match(line)
        if not match:
            continue
        address = int(match.group(2), 0)
        (typed if "type:func" in match.group(3) else other).setdefault(address, match.group(1))
    return {**other, **typed}


def read_function_addresses(path: Path) -> list[int]:
    """Addresses the symbol list types as functions, sorted: every one is a function boundary."""
    found = set()
    for line in path.read_text().splitlines():
        match = _SYMBOL.match(line)
        if match and "type:func" in match.group(3):
            found.add(int(match.group(2), 0))
    return sorted(found)
