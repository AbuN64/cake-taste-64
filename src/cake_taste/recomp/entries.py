"""Entry points the disassembly hides inside a function: code pointers stored in data.
A decomp's function boundaries follow its own object layout, so a function the game starts through a
stored pointer can begin a word or two after the boundary (the word before it belongs to the previous
code or is scheduled ahead of the prologue). N64Recomp only finds entries that a `jal` targets, so the
host's function table lacks the address and the first indirect call to it ends the run ("Failed to
find function at 0x..."). Each such pointer becomes an alias function that runs from the pointed-at
word to the end of the function holding it. The original function stays whole.
"""
from __future__ import annotations

import struct

_PROLOGUE = 0x27BD  # addiu $sp, $sp, -N: how the compiler starts a function that keeps a frame
_KUSEG_ALIAS = 0x20000000  # words below this are KUSEG mappings of the KSEG0 code (0x00400000 for 0x80400000)
_DATA_KINDS = frozenset({"data", ".data"})


def code_pointers(layout, spans, grouped, rom: bytes) -> dict[int, tuple[int, str, int]]:
    """Targets of aligned data words that point at a function-prologue word inside a function.

    Returns vram -> (rom offset of the first pointer to it, name of the function holding it, end of that
    function). Only `data` subsegments are read: jump tables live in rodata, and a word whose neighbour
    points into the same function is a jump table entry wherever it sits.
    """
    code = [(s, size) for s, size in spans if grouped.get(s.name)]

    def holder(vram: int):
        owners = [(s, size) for s, size in code if s.vram <= vram < s.vram + size]
        if len(owners) != 1:
            return None
        section = owners[0][0]
        fn = next((f for f in grouped[section.name] if f.vram <= vram < f.vram + f.size), None)
        return (section, fn) if fn and fn.vram != vram else None

    def target(word: int) -> int:
        return word | 0x80000000 if word < _KUSEG_ALIAS else word

    found: dict[int, tuple[int, str, int]] = {}
    for segment, size in spans:
        bounds = [(start, kind) for start, kind, _ in segment.subsegments]
        ends = [start for start, _ in bounds[1:]] + ([segment.start + size] if bounds else [])
        for (start, kind), end in zip(bounds, ends, strict=True):
            if kind not in _DATA_KINDS:
                continue
            for at in range((start + 3) & ~3, min(end, len(rom)) - 3, 4):
                vram = target(struct.unpack_from(">I", rom, at)[0])
                if vram & 3 or vram in found:
                    continue
                hit = holder(vram)
                if hit is None:
                    continue
                section, fn = hit
                code_at = section.start + vram - section.vram
                if code_at + 4 > len(rom):
                    continue
                word = struct.unpack_from(">I", rom, code_at)[0]
                if word >> 16 != _PROLOGUE or not word & 0x8000:
                    continue
                neighbours = (target(struct.unpack_from(">I", rom, n)[0])
                              for n in (at - 4, at + 4) if start <= n < end - 3)
                if any(fn.vram <= v < fn.vram + fn.size for v in neighbours):
                    continue
                found[vram] = (at, fn.name, fn.vram + fn.size)
    return found
