"""The `recomp` profile section: validated here, by the component that owns it."""

from __future__ import annotations

from dataclasses import dataclass
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
    if rsp is not None and (not isinstance(rsp, dict) or set(rsp) != {"segment", "function"}):
        raise TasteError("config.schema", "recomp.rsp_audio: expected {segment, function}")
    per_file = raw.get("functions_per_output_file", 1)
    refusals = raw.get("max_refusals", 100)
    if type(per_file) is not int or per_file < 1 or type(refusals) is not int or refusals < 0:
        raise TasteError("config.schema", "recomp: functions_per_output_file and max_refusals must be integers")
    include = raw.get("recomp_include")
    if include is not None and not isinstance(include, str):
        raise TasteError("config.schema", "recomp.recomp_include: expected a string")
    names = raw.get("symbol_names", {})
    if not isinstance(names, dict) or not all(
        isinstance(v, dict) and all(isinstance(a, str) and isinstance(n, str) and n for a, n in v.items())
        for v in names.values()
    ):
        raise TasteError("config.schema", "recomp.symbol_names: expected version to {address: name} objects")
    try:
        symbol_names = {str(ver): {int(a, 0): n for a, n in m.items()} for ver, m in names.items()}
    except ValueError as error:
        raise TasteError("config.schema", f"recomp.symbol_names: bad address ({error})") from error
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
        rsp_segment=rsp["segment"] if rsp else None,
        rsp_function=rsp["function"] if rsp else None,
        functions_per_output_file=per_file,
        max_refusals=refusals,
        symbol_names=symbol_names,
    )
