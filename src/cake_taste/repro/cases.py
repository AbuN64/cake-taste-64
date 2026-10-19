"""Behaviour probe: call one function of a bounded closure on many input cases and record what it does.

One request names the cases (registers, stack words, memory patches); every case starts from the same RDRAM
image and yields an ordered event record: each call and stub hit with its argument registers, each store with
its address, width and value, the set of words read (those outside the seeded ranges flagged), the returned
registers and the callee-saved registers left changed. With a `candidate` object (see recomp.candidate) the
named function is replaced by the object's recompiled body, the callees staying the original ones, so the
same request run twice compares two bodies under one execution model. This module knows N64Recomp's output
shape and the MIPS o32 convention; every game fact comes from the request or the profile.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import atomic_bytes, atomic_json, sha256_file
from cake_taste.recomp.candidate import PREFIX, recompile
from cake_taste.recomp.candidate import parse as parse_candidate
from cake_taste.recomp.generate import load_layout
from cake_taste.recomp.settings import load as load_recomp

from .bounded import (
    Memory,
    _config,
    _int,
    _payload,
    host_order,
    host_profile_header,
    registers,
    seed_layout,
)
from .closure import closure, function_vrams, index_bodies, limit_depth, render_unit, resolve_target
from .runner import run_command

_RAM_END = 0x80000000
_CALLS = ("call", "stub")


def parse_events(text: str) -> list[dict[str, Any]]:
    """The harness's events.txt as one dict per case (see hosts/n64recomp/behave/harness.c for the grammar)."""
    cases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]
        if kind == "case":
            current = {
                "name": line[5:],
                "events": [],
                "reads": [],
                "unseeded": [],
                "net": [],
                "status": None,
                "detail": "",
            }
            cases.append(current)
            continue
        if current is None:
            raise TasteError("repro.events", f"events line {number}: {kind} before any case")
        try:
            if kind in _CALLS:
                current["events"].append(
                    {
                        "k": kind,
                        "name": parts[1],
                        "args": [int(x, 16) for x in parts[2:10]],
                        "f": [int(x, 16) for x in parts[10:12]],
                    }
                )
            elif kind == "st":
                current["events"].append(
                    {"k": "st", "addr": int(parts[1], 16), "w": int(parts[2]), "value": int(parts[3], 16)}
                )
            elif kind in ("rd", "uns"):
                current["unseeded" if kind == "uns" else "reads"].append([int(parts[1], 16), int(parts[2])])
            elif kind == "ret":
                current["ret"] = {
                    "v0": int(parts[1], 16),
                    "v1": int(parts[2], 16),
                    "f0": int(parts[3], 16),
                    "f2": int(parts[4], 16),
                }
            elif kind == "saved":
                current["saved"] = parts[1:]
            elif kind == "net":
                current["net"].append([int(parts[1], 16), int(parts[2], 16)])
            elif kind == "overflow":
                current["overflow"] = True
            elif kind == "end":
                current["status"] = parts[1]
                current["detail"] = " ".join(parts[2:])
            else:
                raise TasteError("repro.events", f"events line {number}: unknown event {kind}")
        except (IndexError, ValueError) as exc:
            raise TasteError("repro.events", f"events line {number}: {line!r}") from exc
    unfinished = [c["name"] for c in cases if c["status"] is None]
    if unfinished:
        raise TasteError("repro.events", f"harness died inside case {unfinished[0]}")
    return cases


def _hex(data: bytes) -> str:
    return data.hex().upper()


def cases_text(
    cases: list[dict[str, Any]],
    regs_base: dict[str, int],
    entry: int,
    common_seed: list[tuple[int, int]],
    sp: int,
    timeout_ms: int,
) -> str:
    """The harness cases file: shared seeded ranges, then each case's registers, stack words and memory patches."""
    lines = [f"timeout {timeout_ms}"] + [f"seed {lo:X} {hi:X}" for lo, hi in common_seed]
    for index, item in enumerate(cases):
        name = str(item.get("name", f"case{index}"))
        regs = registers({"registers": item.get("registers", {})}, sp, {})
        merged = {**regs_base, **regs}
        lines += [f"case {name}", f"entry {entry:X}"]
        lines += [f"reg {key} {value:X}" for key, value in sorted(merged.items())]
        for number, word in enumerate(item.get("stack", [])):
            address = sp + 16 + 4 * number
            raw = (_int(word, f"cases[{index}].stack[{number}]") & 0xFFFFFFFF).to_bytes(4, "big")
            lines += [f"mem {address:X} {_hex(raw)}", f"seed {address:X} {address + 4:X}"]
        for number, patch in enumerate(item.get("memory", [])):
            address = _int(patch["addr"], f"cases[{index}].memory[{number}].addr")
            payload = _payload(patch, number)
            lines.append(f"mem {address:X} {_hex(payload)}")
            lines.append(f"seed {address:X} {address + len(payload):X}")
        lines.append("go")
    return "\n".join(lines) + "\n"


def cases_probe(profile: Any, *, function: str | None, script: Path) -> dict[str, Any]:
    started = time.monotonic()
    config = _config(profile)
    try:
        request = json.loads(script.read_text())
    except (OSError, ValueError) as exc:
        raise TasteError("repro.request", f"{script}: {exc}") from exc
    version, target_text = request.get("version"), function or request.get("function")
    if not version or not target_text:
        raise TasteError("repro.request", "the request needs version and function (or pass --function)")
    cases = request.get("cases")
    if not isinstance(cases, list) or not cases:
        raise TasteError("repro.request", "the request needs a nonempty cases list")
    settings = load_recomp(profile)
    facts = json.loads(profile.resolve_path(config["facts"]).read_text())
    boot = facts.get("boot", {})
    if facts.get("version") not in (None, version) or "rdram_size" not in boot:
        raise TasteError("repro.facts", f"facts {config['facts']} do not describe {version} with boot.rdram_size")
    gen = settings.out(version)
    vrams = function_vrams(gen / "symbols.toml")
    target = resolve_target(str(target_text), vrams)
    bodies = index_bodies(gen / "funcs")
    root, entry, candidate_sha = target, vrams[target], None
    work_root = profile.resolve_path(config["work_root"])
    candidate = None
    if request.get("candidate"):
        candidate = parse_candidate(request["candidate"], script.parent)
        made = recompile(settings, version, candidate, work_root, vrams)
        bodies = {**bodies, **made}
        vrams = {**vrams, **{PREFIX + f.name: candidate.vram + f.offset for f in candidate.functions}}
        named = str(request["candidate"].get("entry", target))
        root = PREFIX + named
        if root not in made:
            raise TasteError("repro.candidate", f"candidate defines no function {named}")
        entry, candidate_sha = vrams[root], candidate.sha256
    stub_v0 = {resolve_target(str(k), vrams): _int(v, f"stub_v0.{k}") for k, v in request.get("stub_v0", {}).items()}
    boundary = {resolve_target(str(name), vrams) for name in request.get("boundary", [])}
    depth = int(request.get("depth", 1))
    deep = limit_depth(root, bodies, depth, same_unit=PREFIX if candidate else "")
    found = closure(
        root,
        bodies,
        boundary=boundary | stub_v0.keys() | deep,
        limit=int(request.get("max_functions", config.get("max_functions", 256))),
    )
    include = '#include "recomp.h"\n#include "behave.h"'
    unit = render_unit(found, vrams, include, True, stub_v0, headers=())

    memory_end = _int(request.get("memory_end", _RAM_END + int(boot["rdram_size"])), "memory_end")
    memory = Memory(max(int(boot["rdram_size"]), memory_end - _RAM_END), boot.get("tlb", []))
    seed = request.get("seed", {})
    loaded = (
        seed_layout(memory, load_layout(settings, version), settings.rom(version).read_bytes())
        if seed.get("layout", True)
        else []
    )
    seeded = []
    for item in loaded:
        seeded.append((int(item["vram"]), int(item["vram"]) + int(item["size"])))
        if int(item["vram"]) >= _RAM_END:
            seeded.append((int(item["vram"]) & 0x7FFFFFFF, (int(item["vram"]) & 0x7FFFFFFF) + int(item["size"])))
    if candidate:
        memory.write(candidate.vram, candidate.image)
        seeded.append((candidate.vram, candidate.vram + len(candidate.image)))
    for index, item in enumerate(seed.get("memory", [])):
        address, payload = _int(item["addr"], f"seed.memory[{index}].addr"), _payload(item, index)
        memory.write(address, payload)
        seeded.append((address, address + len(payload)))
    sp = _int(request.get("sp", boot.get("sp", 0x80400000)), "sp")
    window = _int(request.get("stack_window", 0x4000), "stack_window")
    seeded.append((sp - window, sp + 0x400))
    regs_base = registers({}, sp, boot.get("registers", {}))
    before = bytes(memory.data)
    timeout_ms = int(float(request.get("timeout_seconds", 2)) * 1000)
    cases_file = cases_text(cases, regs_base, entry, seeded, sp, timeout_ms)

    header = host_profile_header(boot)
    host = profile.resolve_path(config["host_dir"])
    digest = hashlib.sha256(
        unit.encode()
        + header.encode()
        + b"".join(sha256_file(host / "behave" / name).encode() for name in ("harness.c", "behave.h"))
    ).hexdigest()[:16]
    work = work_root / f"behave-{root}-{digest}"
    work.mkdir(parents=True, exist_ok=True)
    atomic_bytes(work / "closure.c", unit.encode())
    atomic_bytes(work / "host_profile.h", header.encode())
    atomic_bytes(work / "rdram.bin", host_order(before))
    atomic_bytes(work / "cases.txt", cases_file.encode())
    cache = None
    if candidate is None:  # an original's events depend only on the ROM, its symbols, the closure and the cases
        key = hashlib.sha256(
            json.dumps(
                [
                    digest,
                    hashlib.sha256(cases_file.encode()).hexdigest(),
                    sha256_file(settings.rom(version)),
                    sha256_file(gen / "symbols.toml"),
                    memory_end,
                    depth,
                ]
            ).encode()
        ).hexdigest()[:24]
        cache = work_root / "results" / key
        if (cache / "probe.json").is_file():
            cached = json.loads((cache / "probe.json").read_text())
            return {
                "probe": {**cached, "cached": True, "wall_seconds": round(time.monotonic() - started, 3)},
                "run_id": key,
                "receipt": str(cache / "receipt.json"),
            }
    binary = work / "harness"
    build_receipt = None
    if not binary.is_file():
        build = [
            str(config.get("cc", "cc")),
            *config.get("cflags", ["-O1", "-w", "-std=gnu17"]),
            "-I",
            str(host / "behave"),
            "-I",
            str(host / "probe"),
            "-I",
            str(work),
            "-I",
            str(profile.resolve_path(config["runtime_include"])),
            str(work / "closure.c"),
            str(host / "behave" / "harness.c"),
            str(host / "addrmap.c"),
            "-o",
            str(binary),
            "-lm",
        ]
        built = run_command(
            profile, name=f"behave {root} build", function=root, command=build, artifacts=[work / "closure.c"]
        )
        build_receipt = built["receipt"]
        if built["status"]["phase"] != "passed":
            raise TasteError("repro.failed", f"behave build failed; receipt: {build_receipt}")
    ran = run_command(
        profile,
        name=f"behave {root} run",
        function=root,
        command=[str(binary), str(work / "rdram.bin"), str(work / "cases.txt"), "."],
        artifacts=[Path("events.txt")],
    )
    run_dir = Path(ran["status"]["run_dir"])
    if ran["status"]["phase"] != "passed":
        raise TasteError("repro.failed", f"behave run failed; receipt: {ran['receipt']}")
    results = parse_events((run_dir / "events.txt").read_text())
    for result, item in zip(results, cases, strict=True):
        result["inputs"] = {k: item[k] for k in ("registers", "stack", "memory") if k in item}
    probe_dir = run_dir / "probe"
    cases_path = probe_dir / "cases.json"
    atomic_json(cases_path, results, sort_keys=True)
    statuses: dict[str, int] = {}
    for result in results:
        statuses[result["status"]] = statuses.get(result["status"], 0) + 1
    record = {
        "schema": 1,
        "kind": "cases",
        "function": target,
        "root": root,
        "vram": f"{entry:#010x}",
        "candidate_sha256": candidate_sha,
        "input_sha256": sha256_file(script),
        "closure": sorted(found.bodies),
        "stubs": found.stubs,
        "indirect": found.indirect,
        "depth": depth,
        "seeded_ranges": len(seeded),
        "cases": len(results),
        "statuses": statuses,
        "cases_path": str(cases_path),
        "cases_sha256": sha256_file(cases_path),
        "build_receipt": build_receipt,
        "wall_seconds": round(time.monotonic() - started, 3),
        "sp": sp,
        "stack_window": window,
        "cached": False,
        "checked": None,
        "differences": None,
    }
    atomic_json(run_dir / "probe.json", record, sort_keys=True)
    if cache is not None:
        record = {**record, "cases_path": str(cache / "cases.json")}
        atomic_json(cache / "cases.json", results, sort_keys=True)
        atomic_json(cache / "probe.json", record, sort_keys=True)
        atomic_bytes(cache / "receipt.json", Path(ran["receipt"]).read_bytes())
    return {"probe": record, "run_id": run_dir.name, "receipt": ran["receipt"]}
