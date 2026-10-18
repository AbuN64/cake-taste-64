"""Build the section and function model, then write the N64Recomp symbols TOML."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cake_taste import TasteError

from .asmfuncs import Function, read_functions, read_symbol_names
from .splat import Layout, Segment


@dataclass(frozen=True)
class Section:
    name: str
    rom: int
    vram: int
    size: int
    overlay: bool
    functions: tuple[Function, ...]


def build_sections(
    layout: Layout, asm_dir: Path, symbols_file: Path, rom_size: int | None = None
) -> tuple[list[Section], dict[str, int]]:
    names = read_symbol_names(symbols_file)
    ordered = sorted(layout.segments, key=lambda s: s.start)
    spans = [(s, layout.extent(s, rom_size)) for s in ordered if s.vram is not None]
    grouped: dict[str, list[Function]] = {s.name: [] for s, _ in spans}
    used: dict[str, int] = {}
    renamed = 0
    for function in read_functions(asm_dir):
        owner = next((s for s, size in spans if s.start <= function.rom < s.start + size), None)
        if owner is None or owner.vram is None:
            raise TasteError("recomp.layout", f"{function.name} at rom 0x{function.rom:X} is in no segment")
        if owner.vram + function.rom - owner.start != function.vram:
            raise TasteError(
                "recomp.layout", f"{function.name}: vram 0x{function.vram:08X} disagrees with {owner.name}"
            )
        name = names.get(function.vram, function.name)
        renamed += name != function.name
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = f"{name}_{function.vram:08X}"
        grouped[owner.name].append(Function(name, function.rom, function.vram, function.size))
    sections = [
        Section(s.name, s.start, s.vram or 0, size, s.overlay, tuple(sorted(grouped[s.name], key=lambda f: f.vram)))
        for s, size in spans
        if grouped[s.name]
    ]
    return sections, {
        "functions": sum(len(s.functions) for s in sections),
        "renamed_from_symbols": renamed,
        "duplicate_names": sum(1 for v in used.values() if v > 1),
    }


_FLAT_KINDS = frozenset({"code", "resource"})


@dataclass(frozen=True)
class FlatImage:
    """Segments laid out so rom offset = vram - base.

    N64Recomp reads jump tables at `vram + func.rom - func.vram`, which only works when code and its
    rodata share one rom-to-vram delta. Splitting layouts break that, so recompilation reads this image.
    """

    size: int
    offsets: dict[str, int]
    parts: tuple[tuple[int, int, int], ...]  # (image offset, real rom start, size)

    def render(self, rom: bytes) -> bytes:
        image = bytearray(self.size)
        for offset, start, size in self.parts:
            image[offset : offset + size] = rom[start : start + size]
        return bytes(image)


def flat_image(layout: Layout, rom_size: int) -> FlatImage:
    mapped = sorted(
        (s for s in layout.segments if s.vram is not None and s.kind in _FLAT_KINDS and not s.overlay),
        key=lambda s: s.vram or 0,
    )
    if not mapped:
        raise TasteError("recomp.layout", "no code or resource segment has a vram")
    base = mapped[0].vram or 0
    offsets: dict[str, int] = {}
    parts: list[tuple[int, int, int]] = []
    end = 0
    for segment in mapped:
        size = layout.extent(segment, rom_size)
        offset = (segment.vram or 0) - base
        if offset < end:
            raise TasteError("recomp.layout", f"segment {segment.name} overlaps the previous one in vram")
        offsets[segment.name] = offset
        parts.append((offset, segment.start, size))
        end = offset + size
    for segment in sorted((s for s in layout.segments if s.overlay and s.vram is not None), key=lambda s: s.start):
        size = layout.extent(segment, rom_size)
        end = (end + 15) & ~15
        offsets[segment.name] = end
        parts.append((end, segment.start, size))
        end += size
    return FlatImage(end, offsets, tuple(parts))


def symbols_toml(sections: list[Section], offsets: dict[str, int]) -> str:
    out = ["# N64Recomp symbols file written by `taste recomp symbols`", ""]
    for section in sections:
        out += [
            "[[section]]",
            f"name = {json.dumps(section.name)}",
            f"rom = 0x{offsets[section.name]:X}",
            f"vram = 0x{section.vram:08X}",
            f"size = 0x{section.size:X}",
            "functions = [",
        ]
        for f in section.functions:
            out.append(f"    {{ name = {json.dumps(f.name)}, vram = 0x{f.vram:08X}, size = 0x{f.size:X} }},")
        out += ["]", ""]
    return "\n".join(out)


def boot_segment(layout: Layout, sections: list[Section]) -> Segment:
    """The first segment, in rom order, that holds functions: where the program starts."""
    first = min(sections, key=lambda s: s.rom)
    return next(s for s in layout.segments if s.name == first.name)
