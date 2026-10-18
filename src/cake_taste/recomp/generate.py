"""Write the symbols file, run N64Recomp (and RSPRecomp), and freeze every output with hashes."""

from __future__ import annotations

import json
import re
import resource
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import atomic_bytes, atomic_json, sha256_file

from .model import Section, boot_segment, build_sections, flat_image, symbols_toml
from .settings import Settings
from .splat import Layout, parse_layout
from .supplement import read_sections

_FAILED = re.compile(r"^(?:Error recompiling|Failed to analyze) (\S+?),?$")


def load_layout(settings: Settings, version: str) -> Layout:
    layouts = [parse_layout(p) for p in settings.layout_files(version)]
    segments = tuple(s for layout in layouts for s in layout.segments)
    supplement = settings.function_sections.get(version)
    extra = read_sections(supplement) if supplement else ()
    if {s.name for s in segments} & {s.name for s in extra}:
        raise TasteError("recomp.sections", "supplement section name duplicates a layout segment")
    return Layout(layouts[0].sha1, segments + extra)


def build(settings: Settings, version: str) -> tuple[Layout, list[Section], dict[str, int]]:
    layout = load_layout(settings, version)
    rom = settings.rom(version)
    size = rom.stat().st_size if rom.is_file() else None
    sections, counts = build_sections(layout, settings.asm_path(version), settings.symbols_path(version), size,
                                       settings.names_for(version), settings.sizes_for(version))
    return layout, sections, counts


def write_symbols(settings: Settings, version: str) -> dict[str, Any]:
    layout, sections, counts = build(settings, version)
    rom = settings.rom(version).read_bytes()
    flat = flat_image(layout, len(rom))
    path = settings.out(version) / "symbols.toml"
    atomic_bytes(path, symbols_toml(sections, flat.offsets).encode())
    atomic_bytes(settings.out(version) / "rom.flat", flat.render(rom))
    entry = settings.entrypoint if settings.entrypoint is not None else boot_segment(layout, sections).vram
    if settings.entrypoint is not None:
        # N64Recomp only accepts an entry function stored at rom offset 0x1000 of the image it reads.
        first = next(
            (
                s
                for s in sections
                if s.functions and s.functions[0].vram <= entry < s.functions[-1].vram + s.functions[-1].size
            ),
            None,
        )
        if first is None or flat.offsets[first.name] + entry - first.vram != 0x1000:
            raise TasteError("recomp.entrypoint", f"entrypoint 0x{entry:08X} is not at image offset 0x1000")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "sections": len(sections),
        "entrypoint": entry,
        "overlays": [s.name for s in sections if s.overlay],
        **counts,
    }


def _array(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def config_toml(settings: Settings, version: str, entry: int | None, ignored: list[str], overlays: list[str]) -> str:
    out = settings.out(version)
    lines = ["[input]"]
    if entry is not None:
        lines.append(f"entrypoint = 0x{entry:08X}")
    lines += [
        f"symbols_file_path = {json.dumps(str(out / 'symbols.toml'))}",
        f"rom_file_path = {json.dumps(str(out / 'rom.flat'))}",
        f"output_func_path = {json.dumps(str(out / 'funcs'))}",
    ]
    if settings.recomp_include is not None:
        lines.append(f"recomp_include = {json.dumps(settings.recomp_include)}")
    if settings.trace_mode:
        lines.append("trace_mode = true")
    if settings.functions_per_output_file > 1:
        lines.append(f"functions_per_output_file = {settings.functions_per_output_file}")
    if overlays:
        lines.append(f"relocatable_sections_path = {json.dumps(str(out / 'relocatable_sections.txt'))}")
    lines += ["", "[patches]", f"stubs = {_array(list(settings.stubs))}", f"ignored = {_array(ignored)}", ""]
    return "\n".join(lines)


def _run(command: list[str], cwd: Path) -> tuple[int, str, int]:
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return done.returncode, done.stdout + done.stderr, max(after, before)


def _locate_crash(settings: Settings, version: str, entry: int | None, ignored: list[str], overlays: list[str],
                  alias: dict[str, str]) -> str:
    """Name the function that kills N64Recomp: ignore all others in halves until one is left."""
    out = settings.out(version)
    _, sections, _ = build(settings, version)
    names = [f.name for s in sections for f in s.functions if f.name not in ignored]
    probe = out / "probe.toml"

    def dies(candidates: list[str]) -> bool:
        keep = set(candidates)
        while True:
            skip = [*ignored, *(alias.get(n, n) for n in names if n not in keep)]
            atomic_bytes(probe, config_toml(settings, version, entry, skip, overlays).encode())
            code, log, _ = _run([str(settings.n64recomp), str(probe)], out)
            missing = re.search(r"Function (\S+) is set as ignored in the config file but does not exist", log)
            if missing is None or missing.group(1) in alias:
                return code < 0
            # built-in "renamed" functions are looked up as <name>_recomp by the ignore list
            alias[missing.group(1)] = missing.group(1) + "_recomp"

    pool = names
    if not dies(pool):
        raise TasteError("recomp.n64recomp", "crash does not reproduce in the locating probe")
    while len(pool) > 1:
        half = pool[: len(pool) // 2]
        pool = half if dies(half) else pool[len(pool) // 2 :]
    return pool[0]


def _function_at(settings: Settings, version: str, vram: int) -> str:
    _, sections, _ = build(settings, version)
    return next(f.name for s in sections for f in s.functions if f.vram == vram)


def _reason(output: str, name: str) -> str:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for index, line in enumerate(lines):
        if _FAILED.match(line) and _FAILED.match(line).group(1) == name:  # type: ignore[union-attr]
            before = [ln for ln in lines[:index] if not ln.startswith(("[Info]", "[Warn]", "Error in recompiling"))]
            return before[-1] if before else line
    return "no reason printed"


def _files(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def generate(settings: Settings, version: str, refused_copy: Path | None = None) -> dict[str, Any]:
    symbols = write_symbols(settings, version)
    out = settings.out(version)
    layout = load_layout(settings, version)
    overlays = symbols["overlays"]
    if overlays:
        atomic_bytes(out / "relocatable_sections.txt", ("\n".join(overlays) + "\n").encode())
    refused: list[tuple[str, str]] = []
    entry: int | None = settings.entrypoint
    entry_name = _function_at(settings, version, entry) if entry is not None else ""
    alias: dict[str, str] = {}
    peak = 0
    log = ""
    while True:
        ignored = [*settings.ignored, *(alias.get(name, name) for name, _ in refused)]
        atomic_bytes(out / "recomp.toml", config_toml(settings, version, entry, ignored, overlays).encode())
        if (out / "funcs").exists():
            shutil.rmtree(out / "funcs")  # stale output of an earlier attempt; the next run rewrites everything
        (out / "funcs").mkdir(parents=True, exist_ok=True)
        code, log, rss = _run([str(settings.n64recomp), str(out / "recomp.toml")], out)
        peak = max(peak, rss)
        if code == 0:
            break
        missing = re.search(r"Function (\S+) is set as ignored in the config file but does not exist", log)
        if missing and missing.group(1) not in alias:
            # N64Recomp renames its built-in "renamed" functions to <name>_recomp before checking the ignore list.
            alias[missing.group(1)] = missing.group(1) + "_recomp"
            continue
        failed = next((m.group(1) for ln in reversed(log.splitlines()) if (m := _FAILED.match(ln.strip()))), None)
        if failed is None and code < 0 and len(refused) < settings.max_refusals:
            failed = _locate_crash(settings, version, entry, ignored, overlays, alias)
            log += f"\nN64Recomp died with signal {-code} while analysing {failed}\nError recompiling {failed}\n"
        if failed is None or len(refused) >= settings.max_refusals:
            raise TasteError("recomp.n64recomp", f"exit {code}: {' | '.join(log.strip().splitlines()[-3:])}")
        reason = _reason(log, failed)
        if failed == "recomp_entrypoint":
            # N64Recomp renames the entry function; ignoring it while it is the entry crashes the tool, so
            # the entrypoint is dropped (the host must supply one) and the function is ignored by its real name.
            entry, failed = None, entry_name
            reason = f"entrypoint function; entrypoint dropped from config: {reason}"
        refused.append((failed, reason))
        atomic_bytes(out / "refused.tsv", ("name\treason\n" + "".join(f"{n}\t{r}\n" for n, r in refused)).encode())
    (out / "probe.toml").unlink(missing_ok=True)  # scratch config of the crash locator
    count = re.search(r"Function count: (\d+)", log)
    tsv = "name\treason\n" + "".join(f"{n}\t{r}\n" for n, r in refused)
    atomic_bytes(out / "refused.tsv", tsv.encode())
    if refused_copy is not None:
        atomic_bytes(refused_copy, tsv.encode())
    rsp = _rsp(settings, version, layout) if settings.rsp_segment else None
    if rsp:
        peak = max(peak, rsp.pop("peak_rss_kb"))
    manifest = {
        "schema_version": 1,
        "version": version,
        "tools": {"n64recomp": sha256_file(settings.n64recomp), "rsprecomp": sha256_file(settings.rsprecomp)},
        "inputs": {
            "layout": [sha256_file(p) for p in settings.layout_files(version)],
            "symbol_addrs": sha256_file(settings.symbols_path(version)),
            "rom": sha256_file(settings.rom(version)),
            "rom_flat": sha256_file(out / "rom.flat"),
        },
        "symbols": {k: symbols[k] for k in ("sha256", "sections", "functions")},
        "entrypoint_in_config": entry,
        "function_count_reported": int(count.group(1)) if count else None,
        "refused": len(refused),
        "rsp": rsp,
        "files": {k: v for k, v in _files(out).items() if k != "MANIFEST.json"},
    }
    atomic_json(out / "MANIFEST.json", manifest, sort_keys=True)
    return {
        "manifest": str(out / "MANIFEST.json"),
        "functions_in_symbols": symbols["functions"],
        "function_count_reported": manifest["function_count_reported"],
        "refused": len(refused),
        "output_files": len(manifest["files"]),
        "peak_rss_kb": peak,
        "rsp": rsp,
    }


def _rsp(settings: Settings, version: str, layout: Layout) -> dict[str, Any]:
    segment = next((s for s in layout.segments if s.name == settings.rsp_segment), None)
    if segment is None or segment.vram is None:
        raise TasteError("recomp.rsp", f"segment {settings.rsp_segment} not found")
    size = layout.extent(segment, settings.rom(version).stat().st_size)
    execution = segment.options.get("execution_vram", segment.vram)
    out = settings.out(version)
    target = out / "rsp" / f"{settings.rsp_function}.cpp"
    target.parent.mkdir(parents=True, exist_ok=True)
    config = "\n".join(
        [
            f"text_offset = 0x{segment.start:X}",
            f"text_size = 0x{size:X}",
            f"text_address = 0x{execution:X}",
            f"rom_file_path = {json.dumps(str(settings.rom(version)))}",
            f"output_file_path = {json.dumps(str(target))}",
            f"output_function_name = {json.dumps(settings.rsp_function)}",
            f"extra_indirect_branch_targets = {list(settings.rsp_indirect_targets)}",
            "",
        ]
    )
    atomic_bytes(out / "rsp.toml", config.encode())
    code, log, rss = _run([str(settings.rsprecomp), str(out / "rsp.toml")], out)
    if code != 0 or not target.is_file():
        raise TasteError("recomp.rsprecomp", f"exit {code}: {' | '.join(log.strip().splitlines()[-3:])}")
    return {
        "segment": segment.name,
        "output": str(target.relative_to(out)),
        "sha256": sha256_file(target),
        "peak_rss_kb": rss,
    }


def oracle_facts(settings: Settings, version: str) -> dict[str, Any]:
    layout, sections, counts = build(settings, version)
    boot = boot_segment(layout, sections)
    rom = settings.rom(version)
    header = rom.read_bytes()[:16] if rom.is_file() else b""
    return {
        "schema_version": 1,
        "version": version,
        "rom_sha1": layout.sha1,
        "entrypoint": settings.entrypoint if settings.entrypoint is not None else boot.vram,
        "boot_segment": boot.name,
        "header_entrypoint": int.from_bytes(header[8:12], "big") if len(header) >= 12 else None,
        "overlays": [{"name": s.name, "rom": s.rom, "vram": s.vram, "size": s.size} for s in sections if s.overlay],
        "sections": [
            {"name": s.name, "rom": s.rom, "vram": s.vram, "size": s.size, "functions": len(s.functions)}
            for s in sections
        ],
        **counts,
    }
