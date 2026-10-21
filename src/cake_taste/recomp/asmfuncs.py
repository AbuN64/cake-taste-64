"""Function boundaries from a decomp's built ELF, and names from its symbol list."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from cake_taste import TasteError

_SYMBOL = re.compile(r"^\s*(\S+)\s*=\s*(0x[0-9A-Fa-f]+)\s*;(.*)$")


@dataclass(frozen=True)
class Function:
    name: str
    rom: int
    vram: int
    size: int
    source: str = ""  # where a derived extent came from; empty for functions the disassembly delimits


def read_elf_functions(elf: Path, segments: dict[str, int]) -> list[Function]:
    """Every sized FUNC symbol of a big-endian ELF32 link of the decomp. Each allocated section is named after its
    layout segment (a leading dot), so a function's ROM offset is the segment start plus its offset in the section.
    `segments` maps segment names to their ROM start."""
    try:
        data = elf.read_bytes()
    except OSError as exc:
        raise TasteError("recomp.elf", f"{elf}: {exc.strerror}") from exc
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 2:
        raise TasteError("recomp.elf", f"{elf}: not a big-endian ELF32 file")
    shoff, = struct.unpack_from(">I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from(">HHH", data, 0x2E)
    headers = [struct.unpack_from(">10I", data, shoff + i * shentsize) for i in range(shnum)]
    strings_at = headers[shstrndx][4]

    def text(base: int, at: int) -> str:
        return data[base + at : data.index(b"\0", base + at)].decode()

    names = [text(strings_at, h[0]) for h in headers]
    found: list[Function] = []
    for header in (h for h in headers if h[1] == 2):  # SHT_SYMTAB
        offset, size, link = header[4], header[5], header[6]
        strtab = headers[link][4]
        for at in range(offset, offset + size, 16):
            name_at, value, length, info, _, index = struct.unpack_from(">IIIBBH", data, at)
            if info & 0xF != 2 or not length or not 0 < index < shnum:  # STT_FUNC with a size in a real section
                continue
            segment = names[index].removeprefix(".")
            if segment not in segments:
                raise TasteError("recomp.elf", f"{elf}: section {names[index]} names no layout segment")
            vram = value
            found.append(Function(text(strtab, name_at), segments[segment] + vram - headers[index][3], vram, length))
    if not found:
        raise TasteError("recomp.elf", f"{elf}: no sized function symbols")
    return found


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
