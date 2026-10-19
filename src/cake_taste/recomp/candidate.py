"""Recompile the functions of one linked object next to the original ROM's symbols.

The object is a flat image (code, then read-only and writable data) already linked at its own address, plus its
function table. It becomes one extra section of the original symbols file, appended to a copy of the flattened
ROM; every original function is ignored, so N64Recomp emits C only for the object's functions while their calls
still resolve to the original names. The generated C is read, never edited.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import atomic_bytes, sha256_bytes, sha256_file

from .settings import Settings

PREFIX = "behave_"
_MISSING = re.compile(r"Function (\S+) is set as ignored in the config file but does not exist")
_FAILED = re.compile(r"^(?:Error recompiling|Failed to analyze) (\S+?),?$", re.M)


@dataclass(frozen=True)
class Function:
    name: str
    offset: int
    size: int


@dataclass(frozen=True)
class Candidate:
    image: bytes
    vram: int
    functions: tuple[Function, ...]

    @property
    def sha256(self) -> str:
        parts = "".join(f"{f.name}:{f.offset}:{f.size};" for f in self.functions)
        return sha256_bytes(self.image + f"{self.vram}:{parts}".encode())


def parse(spec: dict[str, Any], base: Path) -> Candidate:
    """The `candidate` request object: image path, link address and function table, validated."""
    try:
        path = Path(spec["image"])
        vram = int(str(spec["vram"]), 0)
        rows = spec["functions"]
    except (KeyError, TypeError, ValueError) as exc:
        raise TasteError("repro.candidate", f"candidate needs image, vram and functions: {exc}") from exc
    path = path if path.is_absolute() else base / path
    try:
        image = path.read_bytes()
    except OSError as exc:
        raise TasteError("repro.candidate", f"candidate image: {exc}") from exc
    found = []
    for row in rows:
        try:
            item = Function(str(row["name"]), int(str(row["offset"]), 0), int(str(row["size"]), 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise TasteError("repro.candidate", f"candidate function row {row!r}: {exc}") from exc
        if (
            item.offset < 0
            or item.offset & 3
            or item.size <= 0
            or item.size & 3
            or item.offset + item.size > len(image)
        ):
            raise TasteError("repro.candidate", f"{item.name}: offset and size must be word-aligned inside the image")
        found.append(item)
    if not found or vram & 3 or not 0x80000000 <= vram < 0xA0000000:
        raise TasteError("repro.candidate", "candidate needs functions and a word-aligned KSEG0 address")
    return Candidate(image, vram, tuple(found))


def _toml(settings: Settings, version: str, symbols: Path, rom: Path, funcs: Path, ignored: list[str]) -> str:
    lines = [
        "[input]",
        f'symbols_file_path = "{symbols}"',
        f'rom_file_path = "{rom}"',
        f'output_func_path = "{funcs}"',
        'recomp_include = "#include \\"recomp.h\\""',
        "trace_mode = true",
        "",
        "[patches]",
        "stubs = [" + ", ".join(f'"{n}"' for n in settings.stubs) + "]",
        "ignored = [" + ", ".join(f'"{n}"' for n in ignored) + "]",
        "",
    ]
    return "\n".join(lines)


def recompile(
    settings: Settings, version: str, candidate: Candidate, work: Path, originals: dict[str, int]
) -> dict[str, str]:
    """Generated C for each candidate function, keyed by its `behave_` name; cached by every input's digest."""
    gen = settings.out(version)
    symbols_text = (gen / "symbols.toml").read_text()
    flat = (gen / "rom.flat").read_bytes()
    clash = [f.name for f in candidate.functions if PREFIX + f.name in originals]
    if clash:
        raise TasteError("repro.candidate", f"original symbols already use the name {PREFIX}{clash[0]}")
    for name, vram in originals.items():
        if candidate.vram <= vram < candidate.vram + len(candidate.image):
            raise TasteError("repro.candidate", f"candidate image covers the original {name} at {vram:#010x}")
    key = sha256_bytes(
        (
            sha256_bytes(symbols_text.encode())
            + sha256_bytes(flat)
            + candidate.sha256
            + sha256_file(settings.n64recomp)
        ).encode()
    )[:20]
    out = work / f"candidate-{key}"
    funcs = out / "funcs"
    cached = sorted(funcs.glob("*.c")) if funcs.is_dir() else []
    if not cached:
        out.mkdir(parents=True, exist_ok=True)
        offset = (len(flat) + 15) & ~15
        rom = flat + bytes(offset - len(flat)) + candidate.image
        section = [
            "",
            "[[section]]",
            'name = "behave_candidate"',
            f"rom = 0x{offset:X}",
            f"vram = 0x{candidate.vram:08X}",
            f"size = 0x{len(candidate.image):X}",
            "functions = [",
            *[
                f'    {{ name = "{PREFIX}{f.name}", vram = 0x{candidate.vram + f.offset:08X}, size = 0x{f.size:X} }},'
                for f in candidate.functions
            ],
            "]",
            "",
        ]
        atomic_bytes(out / "symbols.toml", (symbols_text + "\n".join(section)).encode())
        atomic_bytes(out / "rom.flat", rom)
        alias: dict[str, str] = {}
        while True:
            ignored = [alias.get(name, name) for name in originals]
            atomic_bytes(
                out / "recomp.toml",
                _toml(settings, version, out / "symbols.toml", out / "rom.flat", funcs, ignored).encode(),
            )
            funcs.mkdir(exist_ok=True)
            done = subprocess.run(
                [str(settings.n64recomp), str(out / "recomp.toml")],
                cwd=out,
                capture_output=True,
                text=True,
                check=False,
            )
            log = done.stdout + done.stderr
            missing = _MISSING.search(log)
            if missing and missing.group(1) not in alias:
                alias[missing.group(1)] = missing.group(1) + "_recomp"  # N64Recomp renames its built-in libultra names
                continue
            break
        atomic_bytes(out / "n64recomp.log", log.encode())
        if done.returncode != 0:
            failed = _FAILED.findall(log)
            lines = [ln for ln in log.splitlines() if ln.strip() and not ln.startswith("[Info]")]
            reason = " | ".join(lines[-3:]) or f"exit {done.returncode}"
            raise TasteError(
                "repro.candidate", f"N64Recomp refused the candidate ({', '.join(failed) or 'no name'}): {reason}"
            )
    from cake_taste.repro.closure import index_bodies

    bodies = {n: b for n, b in index_bodies(funcs).items() if n.startswith(PREFIX)}
    missing_names = [f.name for f in candidate.functions if PREFIX + f.name not in bodies]
    if missing_names:
        raise TasteError("repro.candidate", f"N64Recomp produced no C for {', '.join(missing_names)}")
    return bodies
