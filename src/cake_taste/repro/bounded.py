"""Bounded call probes: recompile only a question's closure, seed RDRAM, call one function, record the answer.

The request file says what to seed, which registers to set, which callees to stub, which regions to keep
and which values to check. Every game fact (addresses, sizes, expected values) lives in that request or
in the profile; this module knows only N64Recomp's output shape and the MIPS o32 calling convention.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from array import array
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import atomic_bytes, atomic_json, sha256_file
from cake_taste.recomp.generate import load_layout
from cake_taste.recomp.settings import load as load_recomp

from .closure import closure, function_vrams, index_bodies, render_unit, resolve_target
from .runner import run_command

_ABI = (
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra",
)
_CALLEE_SAVED = ("r16", "r17", "r18", "r19", "r20", "r21", "r22", "r23", "r29", "r30")
_CALLEE_SAVED_FP = ("f20", "f22", "f24", "f26", "f28", "f30")
_TYPES = {"u8": ">B", "s8": ">b", "u16": ">H", "s16": ">h", "u32": ">I", "s32": ">i", "f32": ">f", "f64": ">d"}


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise TasteError("repro.request", f"{what}: expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            pass
    raise TasteError("repro.request", f"{what}: expected an integer, got {value!r}")


class Memory:
    """Guest physical RDRAM in big-endian byte order, addressed by virtual address through the boot map."""

    def __init__(self, size: int, tlb: list[dict[str, int]]):
        self.data = bytearray(size)
        self.tlb = tlb

    def phys(self, vaddr: int, length: int = 1) -> int:
        vaddr &= 0xFFFFFFFF
        if 0x80000000 <= vaddr < 0xC0000000:
            pa = vaddr & 0x1FFFFFFF
        else:
            for item in self.tlb:
                if 0 <= vaddr - item["vaddr"] < item["size"]:
                    pa = item["paddr"] + vaddr - item["vaddr"]
                    break
            else:
                raise TasteError("repro.address", f"{vaddr:#010x} is not in KSEG0/KSEG1 or a profile TLB window")
        if pa + length > len(self.data):
            raise TasteError("repro.address", f"{vaddr:#010x}+{length:#x} is past the {len(self.data):#x}-byte RDRAM")
        return pa

    def write(self, vaddr: int, payload: bytes) -> None:
        pa = self.phys(vaddr, len(payload))
        self.data[pa : pa + len(payload)] = payload

    def read(self, vaddr: int, length: int) -> bytes:
        pa = self.phys(vaddr, length)
        return bytes(self.data[pa : pa + length])


def host_order(data: bytes) -> bytes:
    """Big-endian guest words <-> the word-swapped image N64Recomp's MEM_* accessors expect (an involution)."""
    words = array("I", data)
    if words.itemsize != 4:
        raise TasteError("repro.platform", "array('I') is not 32-bit here")
    words.byteswap()
    return words.tobytes()


def seed_layout(memory: Memory, layout: Any, rom: bytes) -> list[dict[str, int | str]]:
    """Copy every resident (non-overlay) KSEG0 segment's ROM bytes to its link address, as boot would."""
    loaded = []
    for segment in layout.segments:
        if segment.vram is None or segment.overlay or not 0x80000000 <= segment.vram < 0xA0000000:
            continue
        size = layout.extent(segment, len(rom))
        size = min(size, len(memory.data) - (segment.vram & 0x1FFFFFFF), len(rom) - segment.start)
        if size <= 0:
            continue
        memory.write(segment.vram, rom[segment.start : segment.start + size])
        loaded.append({"name": segment.name, "vram": segment.vram, "rom": segment.start, "size": size})
    return loaded


def _payload(item: dict[str, Any], index: int) -> bytes:
    kinds = [key for key in ("zero", "hex", *_TYPES) if key in item]
    if len(kinds) != 1:
        raise TasteError("repro.request", f"seed.memory[{index}]: give exactly one of zero, hex, {', '.join(_TYPES)}")
    kind = kinds[0]
    if kind == "zero":
        return bytes(_int(item["zero"], f"seed.memory[{index}].zero"))
    if kind == "hex":
        return bytes.fromhex(item["hex"])
    values = item[kind] if isinstance(item[kind], list) else [item[kind]]
    code = _TYPES[kind]
    if kind.startswith("f"):
        return b"".join(struct.pack(code, float(v)) for v in values)
    return b"".join(struct.pack(code, _int(v, f"seed.memory[{index}]")) for v in values)


def registers(request: dict[str, Any], default_sp: int) -> dict[str, int]:
    """Request register names (ABI or rN/fN) -> 64-bit values the harness loads."""
    regs = {"r29": default_sp & 0xFFFFFFFF}
    for name, value in request.get("registers", {}).items():
        key = f"r{_ABI.index(name)}" if name in _ABI else name
        if key.startswith("f") and key[1:].isdigit():
            if isinstance(value, dict) and set(value) in ({"f32"}, {"f64"}):
                (kind, number), = value.items()
                bits = struct.unpack(">I" if kind == "f32" else ">Q", struct.pack(_TYPES[kind], float(number)))[0]
            else:
                bits = _int(value, f"registers.{name}")
            regs[key] = bits & 0xFFFFFFFFFFFFFFFF
        elif key.startswith("r") and key[1:].isdigit() and int(key[1:]) < 32:
            regs[key] = _int(value, f"registers.{name}")
        elif key == "status":
            regs[key] = _int(value, "registers.status")
        else:
            raise TasteError("repro.request", f"registers.{name}: unknown register")
    # A GPR holds the sign extension of its 32-bit value, as after any MIPS 32-bit load.
    for key, value in regs.items():
        if key.startswith("r"):
            regs[key] = (value & 0xFFFFFFFF) | (0xFFFFFFFF00000000 if value & 0x80000000 else 0)
    return regs


def parse_regs(text: str) -> dict[str, int]:
    return {name: int(value, 16) for name, value in (line.split() for line in text.splitlines() if line.strip())}


def changed_words(before: bytes, after: bytes) -> list[tuple[int, int, int]]:
    """(KSEG0 address, before, after) for every 32-bit word that differs."""
    old, new = array("I", before), array("I", after)
    if old.itemsize != 4:
        raise TasteError("repro.platform", "array('I') is not 32-bit here")
    old.byteswap()
    new.byteswap()
    return [(0x80000000 | i * 4, a, b) for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]


def evaluate(checks: list[dict[str, Any]], memory: Memory, regs: dict[str, int]) -> list[dict[str, Any]]:
    results = []
    for index, check in enumerate(checks):
        kind = check.get("type", "u32")
        if kind not in _TYPES:
            raise TasteError("repro.request", f"checks[{index}]: unknown type {kind}")
        code = _TYPES[kind]
        if "reg" in check:
            name = check["reg"]
            key = f"r{_ABI.index(name)}" if name in _ABI else name
            if key not in regs:
                raise TasteError("repro.request", f"checks[{index}]: unknown register {name}")
            raw = regs[key] & (0xFFFFFFFF if struct.calcsize(code) <= 4 else 0xFFFFFFFFFFFFFFFF)
            size = struct.calcsize(code)
            actual = struct.unpack(code, raw.to_bytes(8, "big")[8 - size :])[0]
            where = name
        else:
            address = _int(check["addr"], f"checks[{index}].addr")
            actual = struct.unpack(code, memory.read(address, struct.calcsize(code)))[0]
            where = f"{address:#010x}"
        if kind.startswith("f"):
            expect = struct.unpack(code, struct.pack(code, float(check["expect"])))[0]
            tolerance = float(check.get("tol", 0.0))
            ok = (math.isnan(actual) and math.isnan(expect)) or abs(actual - expect) <= tolerance
        else:
            expect = _int(check["expect"], f"checks[{index}].expect")
            ok = actual == expect
        results.append({"name": check.get("name", where), "at": where, "type": kind, "expect": expect,
                        "actual": actual, "ok": ok})
    return results


def _config(profile: Any) -> dict[str, Any]:
    config = profile.section("repro").get("bounded")
    if not isinstance(config, dict):
        raise TasteError("config.schema", "profile repro.bounded is absent")
    missing = {"facts", "host_dir", "runtime_include", "work_root"} - config.keys()
    if missing:
        raise TasteError("config.schema", f"repro.bounded: missing {sorted(missing)}")
    return config


def host_profile_header(boot: dict[str, Any]) -> str:
    tlb = "".join(f"{{{t['vaddr']}u,{t['paddr']}u,{t['size']}u}}," for t in boot.get("tlb", []))
    return (
        "#pragma once\n#include <stdint.h>\n"
        f"#define HOST_RDRAM_SIZE {boot['rdram_size']}u\n#define HOST_TLB_COUNT {len(boot.get('tlb', []))}\n"
        f"static const uint32_t host_tlb[][3] = {{{tlb}{{0,0,0}}}};\n"
    )


def bounded_probe(profile: Any, *, function: str | None, script: Path) -> dict[str, Any]:
    config = _config(profile)
    try:
        request = json.loads(script.read_text())
    except (OSError, ValueError) as exc:
        raise TasteError("repro.request", f"{script}: {exc}") from exc
    version = request.get("version")
    target_text = function or request.get("function")
    if not version or not target_text:
        raise TasteError("repro.request", "the request needs version and function (or pass --function)")
    settings = load_recomp(profile)
    facts = json.loads(profile.resolve_path(config["facts"]).read_text())
    boot = facts.get("boot", {})
    if facts.get("version") not in (None, version) or "rdram_size" not in boot:
        raise TasteError("repro.facts", f"facts {config['facts']} do not describe {version} with boot.rdram_size")
    gen = settings.out(version)

    vrams = function_vrams(gen / "symbols.toml")
    target = resolve_target(str(target_text), vrams)
    extra = tuple(resolve_target(str(name), vrams) for name in request.get("extra_functions", []))
    boundary = {resolve_target(str(name), vrams) for name in request.get("boundary", [])}
    stub_v0 = {resolve_target(str(k), vrams): _int(v, f"stub_v0.{k}") for k, v in request.get("stub_v0", {}).items()}
    found = closure(target, index_bodies(gen / "funcs"), boundary=boundary, extra=extra,
                    limit=int(request.get("max_functions", config.get("max_functions", 64))))
    unit = render_unit(found, vrams, settings.recomp_include or '#include "recomp.h"', settings.trace_mode, stub_v0)

    memory = Memory(int(boot["rdram_size"]), boot.get("tlb", []))
    seed = request.get("seed", {})
    loaded = seed_layout(memory, load_layout(settings, version), settings.rom(version).read_bytes()) \
        if seed.get("layout", True) else []
    for index, item in enumerate(seed.get("memory", [])):
        memory.write(_int(item["addr"], f"seed.memory[{index}].addr"), _payload(item, index))
    before = bytes(memory.data)
    regs_in = registers(request, _int(request.get("sp", boot.get("sp", 0x80400000)), "sp"))
    regs_in["entry"] = vrams[target]
    regs_text = "".join(f"{k} 0x{v:x}\n" for k, v in sorted(regs_in.items()))

    digest = hashlib.sha256(unit.encode() + regs_text.encode() + hashlib.sha256(before).digest()).hexdigest()[:16]
    work = profile.resolve_path(config["work_root"]) / f"{target}-{digest}"
    work.mkdir(parents=True, exist_ok=True)
    atomic_bytes(work / "closure.c", unit.encode())
    atomic_bytes(work / "host_profile.h", host_profile_header(boot).encode())
    atomic_bytes(work / "rdram.bin", host_order(before))
    atomic_bytes(work / "regs.txt", regs_text.encode())

    host = profile.resolve_path(config["host_dir"])
    binary = work / "harness"
    build = [
        str(config.get("cc", "cc")), *config.get("cflags", ["-O1", "-w", "-std=gnu17"]),
        "-I", str(host / "bounded"), "-I", str(host / "probe"), "-I", str(work),
        "-I", str(profile.resolve_path(config["runtime_include"])),
        str(work / "closure.c"), str(host / "bounded" / "harness.c"), str(host / "addrmap.c"),
        "-o", str(binary), "-lm",
    ]
    built = run_command(profile, name=f"bounded {target} build", function=target, command=build,
                        artifacts=[work / "closure.c"])
    if built["status"]["phase"] != "passed":
        raise TasteError("repro.failed", f"bounded build failed; receipt: {built['receipt']}")
    ran = run_command(profile, name=f"bounded {target} run", function=target,
                      command=[str(binary), str(work / "rdram.bin"), str(work / "regs.txt"), "."],
                      artifacts=[Path("trace.txt"), Path("regs.txt")])
    run_dir = Path(ran["status"]["run_dir"])
    trace = (run_dir / "trace.txt").read_text().splitlines() if (run_dir / "trace.txt").is_file() else []
    if ran["status"]["phase"] != "passed":
        fatal = next((line for line in trace if line.startswith("fatal ")), "no fatal line")
        raise TasteError("repro.failed", f"bounded run failed ({fatal}); receipt: {ran['receipt']}")

    after_memory = Memory(len(before), memory.tlb)
    after_memory.data[:] = host_order((run_dir / "rdram.bin").read_bytes())
    regs_out = parse_regs((run_dir / "regs.txt").read_text())
    sp = regs_in["r29"] & 0xFFFFFFFF
    window = _int(request.get("stack_window", 0x4000), "stack_window")
    writes = changed_words(before, bytes(after_memory.data))
    stack = [w for w in writes if sp - window <= w[0] < sp + 0x10]
    rows = "".join(json.dumps({"addr": f"{a:#010x}", "before": f"{b:#010x}", "after": f"{c:#010x}"}) + "\n"
                   for a, b, c in writes if not sp - window <= a < sp + 0x10)
    probe_dir = run_dir / "probe"
    atomic_bytes(probe_dir / "writes.jsonl", rows.encode())
    write_log = probe_dir / "writes.jsonl"
    outputs = [{"name": "writes", "path": str(write_log), "sha256": sha256_file(write_log)}]
    for item in request.get("outputs", []):
        address, size = _int(item["addr"], "outputs.addr"), _int(item["size"], "outputs.size")
        path = probe_dir / f"{item['name']}.bin"
        atomic_bytes(path, after_memory.read(address, size))
        outputs.append({"name": item["name"], "addr": f"{address:#010x}", "size": size, "path": str(path),
                        "sha256": sha256_file(path)})
    checks = evaluate(request.get("checks", []), after_memory, regs_out)
    saved = [r for r in (*_CALLEE_SAVED, *_CALLEE_SAVED_FP) if regs_out.get(r, 0) != regs_in.get(r, 0)]
    calls = [line.split()[1] for line in trace if line.startswith("call ")]
    record = {
        "schema": 1,
        "kind": "call",
        "function": target,
        "vram": f"{vrams[target]:#010x}",
        "frames": None,
        "range": None,
        "input_sha256": sha256_file(script),
        "overlay": unit_summary(found),
        "env": {},
        "build_receipt": built["receipt"],
        "closure": sorted(found.bodies),
        "stubs": found.stubs,
        "indirect": found.indirect,
        "seeded_segments": len(loaded),
        "rdram_before_sha256": hashlib.sha256(before).hexdigest(),
        "rdram_after_sha256": hashlib.sha256(after_memory.data).hexdigest(),
        "calls": len(calls),
        "functions_executed": sorted(set(calls)),
        "stubs_hit": sorted({line.split()[1] for line in trace if line.startswith("stub ")}),
        "changed_words": len(writes) - len(stack),
        "stack_words": len(stack),
        "callee_saved_changed": saved,
        "returned": {"v0": regs_out["r2"] & 0xFFFFFFFF, "v1": regs_out["r3"] & 0xFFFFFFFF, "f0": regs_out["f0"]},
        "checks": checks,
        "checked": len(checks),
        "differences": sum(not c["ok"] for c in checks),
        "outputs": outputs + [{"path": item["path"], "sha256": item["sha256"]} for item in ran["status"]["artifacts"]],
    }
    atomic_json(run_dir / "probe.json", record, sort_keys=True)
    return {"probe": record, "run_id": run_dir.name, "receipt": ran["receipt"]}


def unit_summary(found: Any) -> str:
    """Human-readable closure description stored where full-oracle probes store their overlay text."""
    stubs = ", ".join(f"{name} ({why})" for name, why in sorted(found.stubs.items())) or "none"
    return f"bounded closure of {found.root}: {len(found.bodies)} recompiled functions; stubs: {stubs}\n"
