"""The `recomp` profile section: validated here, by the component that owns it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cake_taste import TasteError

_REQUIRED = {"decomp_root", "layout_glob", "symbols_file", "asm_dir", "roms", "gen_root", "tools"}
_OPTIONAL = {
    "entrypoint",
    "trace_mode",
    "recomp_include",
    "stubs",
    "ignored",
    "rsp_audio",
    "functions_per_output_file",
    "max_refusals",
    "symbol_names",
    "function_sections",
    "function_sizes",
    "local_indirect_jumps",
}


@dataclass(frozen=True)
class Settings:
    decomp_root: Path
    layout_glob: str
    symbols_file: str
    asm_dir: str
    roms: dict[str, Path]
    gen_root: Path
    n64recomp: Path
    rsprecomp: Path
    entrypoint: int | None
    trace_mode: bool
    recomp_include: str | None
    stubs: tuple[str, ...]
    ignored: tuple[str, ...]
    rsp_segment: str | None
    rsp_function: str | None
    functions_per_output_file: int
    max_refusals: int
    symbol_names: dict[str, dict[int, str]]

    function_sections: dict[str, Path] = field(default_factory=dict)

    function_sizes: dict[str, dict[int, int]] = field(default_factory=dict)

    local_indirect_jumps: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    rsp_indirect_targets: tuple[int, ...] = ()
    rsp_execution_vram: int | None = None
    rsp_rom: int | None = None  # microcode text given as a sourced ROM span instead of a layout segment
    rsp_size: int | None = None

    def sizes_for(self, version: str) -> dict[int, int]:
        return self.function_sizes.get(version, {})

    def names_for(self, version: str) -> dict[int, str]:
        return self.symbol_names.get(version, {})

    def layout_files(self, version: str) -> list[Path]:
        found = sorted(self.decomp_root.glob(self.layout_glob.format(version=version)))
        if not found:
            raise TasteError("recomp.layout", f"no segment file matches {self.layout_glob.format(version=version)}")
        return found

    def symbols_path(self, version: str) -> Path:
        return self.decomp_root / self.symbols_file.format(version=version)

    def asm_path(self, version: str) -> Path:
        return self.decomp_root / self.asm_dir.format(version=version)

    def rom(self, version: str) -> Path:
        if version not in self.roms:
            raise TasteError("recomp.version", f"recomp.roms has no entry for {version}")
        return self.roms[version]

    def out(self, version: str) -> Path:
        return self.gen_root / version


def _strings(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise TasteError("config.schema", f"recomp.{key}: expected a list of nonempty strings")
    return tuple(value)


def load(profile: Any) -> Settings:
    raw = profile.section("recomp")
    missing, extra = _REQUIRED - raw.keys(), raw.keys() - _REQUIRED - _OPTIONAL
    if missing or extra:
        raise TasteError("config.schema", f"recomp: missing {sorted(missing)} unknown {sorted(extra)}")
    for key in ("layout_glob", "symbols_file", "asm_dir"):
        if not isinstance(raw[key], str) or "{version}" not in raw[key]:
            raise TasteError("config.schema", f"recomp.{key}: expected a string containing {{version}}")
    roms, tools = raw["roms"], raw["tools"]
    if not isinstance(roms, dict) or not roms:
        raise TasteError("config.schema", "recomp.roms: expected a version to path object")
    if not isinstance(tools, dict) or set(tools) != {"n64recomp", "rsprecomp"}:
        raise TasteError("config.schema", "recomp.tools: expected exactly n64recomp and rsprecomp")
    entry = raw.get("entrypoint")
    if entry is not None and (type(entry) is not int or entry < 0 or entry & 3):
        raise TasteError("config.schema", "recomp.entrypoint: expected a word-aligned integer")
    rsp = raw.get("rsp_audio")
    if rsp is not None and (not isinstance(rsp, dict) or "function" not in rsp or ("segment" in rsp) == ("rom" in rsp)
                            or rsp.keys() - {"segment", "function", "extra_indirect_branch_targets",
                                             "source", "execution_vram", "rom", "size"}):
        raise TasteError("config.schema", "recomp.rsp_audio: expected function, a segment or a rom span, and targets")
    if rsp is not None and "rom" in rsp:
        rom, size = rsp["rom"], rsp.get("size")
        if (type(rom) is not int or type(size) is not int or rom < 0 or rom & 3 or size <= 0 or size & 3
                or not rsp.get("source") or rsp.get("execution_vram") is None):
            raise TasteError("config.schema", "recomp.rsp_audio rom span needs aligned rom and size, source and "
                             "execution_vram")
    targets = rsp.get("extra_indirect_branch_targets", []) if rsp else []
    if (not isinstance(targets, list)
            or any(type(t) is not int or not 0x1000 <= t < 0x2000 or t & 3 for t in targets)
            or len(targets) != len(set(targets)) or (targets and not rsp.get("source"))):
        raise TasteError("config.schema", "recomp.rsp_audio targets must be unique aligned IMEM addresses with source")
    execution = rsp.get("execution_vram") if rsp else None
    if execution is not None and (type(execution) is not int or execution & 3
            or execution & ~0xFFF not in (0x1000, 0x04001000) or not rsp.get("source")):
        raise TasteError("config.schema", "recomp.rsp_audio execution_vram needs a sourced aligned IMEM address")
    per_file = raw.get("functions_per_output_file", 1)
    refusals = raw.get("max_refusals", 100)
    if type(per_file) is not int or per_file < 1 or type(refusals) is not int or refusals < 0:
        raise TasteError("config.schema", "recomp: functions_per_output_file and max_refusals must be integers")
    include = raw.get("recomp_include")
    if include is not None and not isinstance(include, str):
        raise TasteError("config.schema", "recomp.recomp_include: expected a string")
    names = raw.get("symbol_names", {})
    if not isinstance(names, dict) or not all(isinstance(v, dict) for v in names.values()):
        raise TasteError("config.schema", "recomp.symbol_names: expected version to {address: name} objects")
    symbol_names: dict[str, dict[int, str]] = {}
    for ver, mapping in names.items():
        symbol_names[str(ver)] = {}
        for address, item in mapping.items():
            # A name is a string, or {"name", "source"} when the evidence for it travels with it.
            name = item.get("name") if isinstance(item, dict) else item
            source_ok = not isinstance(item, dict) or (isinstance(item.get("source"), str) and item["source"])
            if not isinstance(address, str) or not isinstance(name, str) or not name or not source_ok:
                raise TasteError("config.schema", "recomp.symbol_names: expected address to name or {name, source}")
            try:
                symbol_names[str(ver)][int(address, 0)] = name
            except ValueError as error:
                raise TasteError("config.schema", f"recomp.symbol_names: bad address ({error})") from error
    sections = raw.get("function_sections", {})
    if not isinstance(sections, dict) or not all(isinstance(v, str) and v for v in sections.values()):
        raise TasteError("config.schema", "recomp.function_sections: expected version to path objects")
    sizes = raw.get("function_sizes", {})
    function_sizes = {}
    try:
        for version, entries in sizes.items():
            function_sizes[version] = {}
            for address, fact in entries.items():
                addr, size = int(address, 0), fact["size"]
                if (addr < 0 or addr > 0xFFFFFFFF or addr & 3 or type(size) is not int
                        or size <= 0 or size & 3 or not fact.get("source")):
                    raise ValueError("sizes need aligned addresses, positive word extents, and a source")
                function_sizes[version][addr] = size
    except (AttributeError, TypeError, KeyError, ValueError) as exc:
        raise TasteError("config.schema", f"recomp.function_sizes: {exc}") from exc
    return Settings(
        decomp_root=profile.resolve_path(raw["decomp_root"]),
        layout_glob=raw["layout_glob"],
        symbols_file=raw["symbols_file"],
        asm_dir=raw["asm_dir"],
        roms={str(k): profile.resolve_path(v) for k, v in roms.items()},
        gen_root=profile.resolve_path(raw["gen_root"]),
        n64recomp=profile.resolve_path(tools["n64recomp"]),
        rsprecomp=profile.resolve_path(tools["rsprecomp"]),
        entrypoint=entry,
        trace_mode=bool(raw.get("trace_mode", False)),
        recomp_include=include,
        stubs=_strings(raw.get("stubs", []), "stubs"),
        ignored=_strings(raw.get("ignored", []), "ignored"),
        rsp_segment=rsp.get("segment") if rsp else None,
        rsp_function=rsp["function"] if rsp else None,
        functions_per_output_file=per_file,
        max_refusals=refusals,
        symbol_names=symbol_names,
        function_sections={str(v): profile.resolve_path(p) for v, p in sections.items()},
        function_sizes=function_sizes,
        local_indirect_jumps=raw.get("local_indirect_jumps", {}),
        rsp_indirect_targets=tuple(targets),
        rsp_execution_vram=execution,
        rsp_rom=rsp.get("rom") if rsp else None,
        rsp_size=rsp.get("size") if rsp else None,
    )
