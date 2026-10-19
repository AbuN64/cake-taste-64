"""Oracle capture: run the real ROM on the host build, stop at a function's entry, keep RDRAM and registers.

A function that reads state the game built at run time (allocated objects, filled pointers) cannot be behaved
from the ROM's static image. This runs the oracle host under gdb with a breakpoint on the function's recompiled
entry (first hit, or the Nth), dumps guest RDRAM and the entry registers, and freezes both under a key of
(ROM sha1, function, hit, host binary). The result is a `seed` a behaviour request adds to `seed.memory` plus
one extra case carrying the entry registers. The module knows the recompiler's `recomp_context` layout and the
host's calling shape (`rdram` in the first argument register, `ctx` in the second), nothing about any game.
"""

from __future__ import annotations

import hashlib
import json
import struct
import time
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import atomic_bytes, atomic_json, sha256_file
from cake_taste.recomp.settings import load as load_recomp

from .bounded import host_order
from .closure import function_vrams, resolve_target
from .runner import run_command

_CTX_BYTES = 512  # recomp_context: r0..r31 then f0..f31, eight bytes each
_RAM_BASE = 0x80000000
_MAX_WRITES = 64  # words a recorded call may have written; a call that wrote more is kept without its writes
_MAX_CALLS = 4096


def _config(profile: Any) -> dict[str, Any]:
    config = profile.section("repro").get("capture")
    if not isinstance(config, dict):
        raise TasteError("config.schema", "profile repro.capture is absent")
    missing = {"facts", "host", "work_root"} - config.keys()
    if missing:
        raise TasteError("config.schema", f"repro.capture: missing {sorted(missing)}")
    return config


def gdb_config(
    symbol: str, hit: int, rdram_size: int, out: Path, stubs: list[str], trace_seconds: int = 30
) -> dict[str, Any]:
    """The config of the gdb script (gdb_capture.py): where to stop, what to dump, which callees to record."""
    return {"symbol": symbol, "hit": hit, "rdram_size": rdram_size, "out": str(out), "stubs": stubs,
            "max_writes": _MAX_WRITES, "max_calls": _MAX_CALLS,
            "trace_seconds": trace_seconds}


def parse_context(raw: bytes) -> dict[str, int]:
    """Entry registers by the names a request's `registers` takes: r1..r31 and f0..f31 as raw bits."""
    if len(raw) < _CTX_BYTES:
        raise TasteError("repro.capture", f"register dump is {len(raw)} bytes, expected {_CTX_BYTES}")
    words = struct.unpack(f"<{_CTX_BYTES // 8}Q", raw[:_CTX_BYTES])
    return {**{f"r{i}": words[i] for i in range(1, 32)}, **{f"f{i}": words[32 + i] for i in range(32)}}


def _key(rom_sha1: str, function: str, vram: int, hit: int, rdram_size: int, host_sha256: str,
         stubs: list[str]) -> str:
    # the calls recorded depend on which callees are stubs (a call inside a stub is not the probe's call), and the
    # oracle is not deterministic: every stub set is its own capture, memory and calls from one run
    text = json.dumps([rom_sha1, function, vram, hit, rdram_size, host_sha256, *([stubs] if stubs else [])])
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def capture_probe(
    profile: Any, *, function: str, version: str, hit: int = 1, stubs: tuple[str, ...] = ()
) -> dict[str, Any]:
    started = time.monotonic()
    names = sorted(set(stubs))
    if hit < 1:
        raise TasteError("repro.capture", "--hit counts from 1")
    config = _config(profile)
    settings = load_recomp(profile)
    facts = json.loads(profile.resolve_path(config["facts"]).read_text())
    boot = facts.get("boot", {})
    if facts.get("version") not in (None, version) or "rdram_size" not in boot:
        raise TasteError("repro.facts", f"facts {config['facts']} do not describe {version} with boot.rdram_size")
    rdram_size = int(boot["rdram_size"])
    vrams = function_vrams(settings.out(version) / "symbols.toml")
    target = resolve_target(function, vrams)
    rom = settings.rom(version)
    rom_sha1 = hashlib.sha1(rom.read_bytes()).hexdigest()
    host = profile.resolve_path(config["host"])
    if not host.is_file():
        raise TasteError("repro.capture", f"the oracle host build {host} does not exist")
    key = _key(rom_sha1, target, vrams[target], hit, rdram_size, sha256_file(host), names)
    root = profile.resolve_path(config["work_root"]) / "captures" / key
    done = root / "capture.json"
    if done.is_file():
        cached = json.loads(done.read_text())
        cached = {**cached, "cached": True, "wall_seconds": round(time.monotonic() - started, 3)}
        return {"capture": cached, "seed": seed_of(cached)}
    scratch = root / "run"
    scratch.mkdir(parents=True, exist_ok=True)
    for stale in ("rdram.host", "ctx.bin", "calls.json"):
        (scratch / stale).unlink(missing_ok=True)
    script = scratch / "gdb_capture.py"
    atomic_bytes(script, Path(__file__).with_name("gdb_capture.py").read_bytes())
    trace = int(config.get("trace_seconds", 30))  # how long the callee trace may run after the stop
    atomic_json(scratch / "config.json", gdb_config(target, hit, rdram_size, scratch, names, trace))
    seconds = int(config.get("timeout_seconds", 120))
    env = {str(k): str(v) for k, v in config.get("env", {}).items()}
    env["N64R_ROM"] = str(rom)
    env["TASTE_CAPTURE_CONFIG"] = str(scratch / "config.json")
    ran = run_command(
        profile,
        name=f"capture {target} hit {hit}",
        function=target,
        command=["timeout", "-s", "INT", str(seconds), str(config.get("gdb", "gdb")), "-batch", "-x", str(script),
                 str(host)],
        artifacts=[],
        env=env,
    )
    log = Path(ran["status"]["log_path"])
    text = log.read_text(errors="replace") if log.is_file() else ""
    if "CAPTURE-DONE" not in text or not (scratch / "rdram.host").is_file() or not (scratch / "ctx.bin").is_file():
        stops = text.split("CAPTURE-MISS", 1)[1].strip().splitlines()[:12] if "CAPTURE-MISS" in text else []
        where = "; the oracle was stopped in: " + " | ".join(s.strip() for s in stops) if stops else ""
        raise TasteError(
            "repro.capture",
            f"{target} entry hit {hit} was not reached within {seconds}s{where}; receipt: {ran['receipt']}",
        )
    ram = host_order((scratch / "rdram.host").read_bytes())
    if len(ram) != rdram_size:
        raise TasteError("repro.capture", f"dumped {len(ram)} bytes of RDRAM, expected {rdram_size}")
    registers = parse_context((scratch / "ctx.bin").read_bytes())
    traced = json.loads((scratch / "calls.json").read_text()) if (scratch / "calls.json").is_file() else {}
    if names:
        atomic_json(root / "calls.json", traced.get("calls", []), sort_keys=True)
    atomic_bytes(root / "rdram.bin", ram)
    (scratch / "rdram.host").unlink()
    record = {
        "schema": 1,
        "kind": "capture",
        "function": target,
        "vram": f"{vrams[target]:#010x}",
        "hit": hit,
        "version": version,
        "rom_sha1": rom_sha1,
        "host_sha256": sha256_file(host),
        "key": key,
        "rdram_path": str(root / "rdram.bin"),
        "rdram_sha256": hashlib.sha256(ram).hexdigest(),
        "rdram_bytes": len(ram),
        "registers": {name: f"{value:#x}" for name, value in registers.items()},
        "sp": f"{registers['r29'] & 0xFFFFFFFF:#010x}",
        "stubs": names,
        "calls": len(traced.get("calls", [])),
        "calls_complete": bool(traced.get("complete")),
        "calls_unresolved": traced.get("unresolved", []),
        "calls_dropped_writes": sum(bool(c.get("writes_dropped")) for c in traced.get("calls", [])),
        "calls_path": str(root / "calls.json") if names else None,
        "calls_sha256": sha256_file(root / "calls.json") if names else None,
        "receipt": ran["receipt"],
        "cached": False,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    atomic_json(done, record, sort_keys=True)
    return {"capture": record, "seed": seed_of(record)}


def seed_of(record: dict[str, Any]) -> dict[str, Any]:
    """What a behaviour request takes from a capture: the RDRAM image as one `seed.memory` item, the entry stack
    pointer, and the entry registers as one extra case."""
    case: dict[str, Any] = {"name": f"seed-{record['function']}-hit{record['hit']}", "registers": record["registers"]}
    if record.get("calls_path"):  # what each stubbed callee returned and wrote in the real run, in call order
        case["stub_calls"] = {"file": record["calls_path"], "sha256": record["calls_sha256"]}
    return {
        "memory": [{"addr": f"{_RAM_BASE:#x}", "file": record["rdram_path"], "sha256": record["rdram_sha256"]}],
        "sp": record["sp"],
        "case": case,
    }
