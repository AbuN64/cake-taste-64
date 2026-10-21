"""Build the section and function model, then write the N64Recomp symbols TOML."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cake_taste import TasteError

from .asmfuncs import Function, read_elf_functions, read_function_addresses, read_symbol_names
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
    layout: Layout,
    elf: Path,
    symbols_file: Path,
    rom_size: int | None = None,
    overrides: dict[int, str] | None = None,
    sizes: dict[int, int] | None = None,
    statics: dict[int, str] | None = None,
) -> tuple[list[Section], dict[str, int]]:
    names = {**read_symbol_names(symbols_file), **(overrides or {})}
    ordered = sorted(layout.segments, key=lambda s: s.start)
    spans = [(s, layout.extent(s, rom_size)) for s in ordered if s.vram is not None]
    grouped: dict[str, list[Function]] = {s.name: [] for s, _ in spans}
    used: dict[str, int] = {}
    renamed = 0
    unused_sizes = set(sizes or {})
    functions = [(f, None) for f in read_elf_functions(elf, {s.name: s.start for s in layout.segments})]
    for segment, size in spans:
        if segment.options.get("functions") and rom_size is not None and segment.start + size > rom_size:
            raise TasteError("recomp.sections", f"{segment.name} exceeds the ROM")
        for fn in segment.options.get("functions", []):
            functions.append((Function(fn["name"], segment.start + fn["offset"],
                                       (segment.vram or 0) + fn["offset"], fn["size"]), segment))
    for function, explicit_owner in functions:
        owner = explicit_owner or next((s for s, size in spans if s.start <= function.rom < s.start + size), None)
        if owner is None or owner.vram is None:
            raise TasteError("recomp.layout", f"{function.name} at rom 0x{function.rom:X} is in no segment")
        if owner.vram + function.rom - owner.start != function.vram:
            raise TasteError(
                "recomp.layout", f"{function.name}: vram 0x{function.vram:08X} disagrees with {owner.name}"
            )
        extent = (sizes or {}).get(function.vram, function.size)
        if function.rom + extent > owner.start + layout.extent(owner, rom_size):
            raise TasteError("recomp.sections", f"{function.name} size extends beyond {owner.name}")
        unused_sizes.discard(function.vram)
        name = names.get(function.vram, function.name)
        renamed += name != function.name
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = f"{name}_{function.vram:08X}"
        grouped[owner.name].append(Function(name, function.rom, function.vram, extent))
    for vram, name in sorted((statics or {}).items()):
        _add_static(layout, spans, grouped, symbols_file, vram, name)
    if unused_sizes:
        raise TasteError("recomp.sections", f"function_sizes addresses absent from functions: {sorted(unused_sizes)}")
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


def _add_static(layout, spans, grouped, symbols_file, vram, name) -> None:
    """Make code the disassembly left in a data split a function, sized by the decomp's own boundaries.

    The recompiler finds such a target only while analysing the code that jumps to it, so it can neither
    be ignored nor sized by the config. Its extent runs to the nearest boundary the decomp states after it:
    the next function, the next symbol typed as a function, the next split of the segment, or the segment end.
    """
    owners = [(s, size) for s, size in spans if s.vram is not None and s.vram <= vram < s.vram + size]
    owners = [(s, size) for s, size in owners if grouped[s.name]]
    if len(owners) != 1:
        raise TasteError("recomp.statics", f"{name}: 0x{vram:08X} is in {len(owners)} code sections, expected 1")
    owner, size = owners[0]
    for f in grouped[owner.name]:
        if f.vram <= vram < f.vram + f.size:
            raise TasteError("recomp.statics", f"{name}: 0x{vram:08X} is inside {f.name}, not between functions")
    after: list[tuple[int, str]] = [(f.vram, f"next function {f.name}") for f in grouped[owner.name] if f.vram > vram]
    after += [(a, f"next function symbol 0x{a:08X}") for a in read_function_addresses(symbols_file) if a > vram]
    after += [(owner.vram + start - owner.start, f"next split {path or kind}")
              for start, kind, path in owner.subsegments if owner.vram + start - owner.start > vram]
    after.append((owner.vram + size, f"end of {owner.name}"))
    end, why = min(after)
    grouped[owner.name].append(Function(name, owner.start + vram - owner.vram, vram, end - vram,
                                        f"extent to {why} at 0x{end:08X}"))


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
