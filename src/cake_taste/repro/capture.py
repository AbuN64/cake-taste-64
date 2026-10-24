"""Oracle capture: run the real ROM on the host build, stop at a function's entry, keep RDRAM and registers.

A function that reads state the game built at run time (allocated objects, filled pointers, a packed stream) cannot
be behaved from the ROM's static image. This runs the oracle host under gdb with a breakpoint on the function's
recompiled entry (first hit, or the Nth), dumps guest RDRAM and the entry registers, and freezes both under a key of
everything that changes them (ROM, function, hit, host binary and the inputs it was built from, the gdb script, the
run environment, the recorded stubs). The result is a `seed` a behaviour request adds to `seed.memory` plus one extra
case carrying the entry registers. The module knows the recompiler's `recomp_context` layout and the host's calling
shape (`rdram` in the first argument register, `ctx` in the second), nothing about any game.

Rules, one for every function and every project (the project's facts live in its profile's `repro.capture`):
- The host binary is used only when it is known to be built from the current inputs: the recompiled output's
  MANIFEST.json, the facts file and any `host_inputs`. A stamp beside the captures records the inputs a build used;
  a missing or different stamp runs `host_build` (an incremental build is cheap when nothing changed) or fails with
  `repro.capture.stale_host`. A binary is never trusted on its age.
- A capture counts as cached only once its capture.json exists and the files it names still hash as recorded;
  capture.json is written last. A partial entry is redone.
- A function the run does not reach within `timeout_seconds` is the named finding `repro.capture.unreached`, kept
  (one per reach key in work_root/misses: the recorded stubs do not change what is reached) so a sweep does not
  rerun the oracle for it; a longer timeout tries again. An oracle that exits
  early (a display it cannot open, a crash) is `repro.capture.oracle_exit` and is never cached.
- Captures run in parallel up to `jobs` at once over every process (slot locks next to the repro lock), never
  behind the one-at-a-time repro lock the probes use.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import struct
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
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
_TIMEOUT = 124  # what `timeout` exits with when it stopped the run
_KNOWN = {"facts", "host", "work_root", "gdb", "env", "timeout_seconds", "trace_seconds", "jobs", "host_inputs",
          "host_build", "display_command", "batch_seconds"}


def _config(profile: Any) -> dict[str, Any]:
    config = profile.section("repro").get("capture")
    if not isinstance(config, dict):
        raise TasteError("config.schema", "profile repro.capture is absent")
    missing = {"facts", "host", "work_root"} - config.keys()
    if missing:
        raise TasteError("config.schema", f"repro.capture: missing {sorted(missing)}")
    unknown = set(config) - _KNOWN
    if unknown:
        raise TasteError("config.schema", f"repro.capture: unknown keys {sorted(unknown)}")
    build = config.get("host_build")
    if build is not None and not (isinstance(build, dict) and isinstance(build.get("command"), list)):
        raise TasteError("config.schema", "repro.capture.host_build: needs a command list")
    return config


def gdb_config(
    symbol: str, hit: int, rdram_size: int, out: Path, stubs: list[str], trace_seconds: int = 30,
    condition: str | None = None,
) -> dict[str, Any]:
    """The config of the gdb script (gdb_capture.py): where to stop, what to dump, which callees to record."""
    return {"symbol": symbol, "hit": hit, "rdram_size": rdram_size, "out": str(out), "stubs": stubs,
            "max_writes": _MAX_WRITES, "max_calls": _MAX_CALLS,
            "trace_seconds": trace_seconds, "condition": condition}


def parse_context(raw: bytes) -> dict[str, int]:
    """Entry registers by the names a request's `registers` takes: r1..r31 and f0..f31 as raw bits."""
    if len(raw) < _CTX_BYTES:
        raise TasteError("repro.capture", f"register dump is {len(raw)} bytes, expected {_CTX_BYTES}")
    words = struct.unpack(f"<{_CTX_BYTES // 8}Q", raw[:_CTX_BYTES])
    return {**{f"r{i}": words[i] for i in range(1, 32)}, **{f"f{i}": words[32 + i] for i in range(32)}}


def _key(rom_sha1: str, function: str, vram: int, hit: int, rdram_size: int, host_sha256: str,
         stubs: list[str], inputs: str = "", script: str = "", env: dict[str, str] | None = None) -> str:
    # the calls recorded depend on which callees are stubs (a call inside a stub is not the probe's call), and the
    # oracle is not deterministic: every stub set is its own capture, memory and calls from one run. The inputs the
    # host was built from, the gdb script and the run environment (an input script, a display) change what is seen.
    text = json.dumps([rom_sha1, function, vram, hit, rdram_size, host_sha256, *([stubs] if stubs else []),
                       inputs, script, sorted((env or {}).items())])
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def _tree_digest(path: Path) -> str:
    """One digest of a file, or of a directory's files by relative path and content (caches and VCS left out)."""
    if path.is_file():
        return sha256_file(path)
    if not path.is_dir():
        return "absent"
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        parts = item.relative_to(path).parts
        if item.is_file() and not any(p in ("__pycache__", ".git") for p in parts):
            digest.update(f"{'/'.join(parts)}\0{sha256_file(item)}\n".encode())
    return digest.hexdigest()


def host_inputs(profile: Any, config: dict[str, Any], settings: Any, version: str) -> dict[str, str]:
    """Path -> digest of everything the host binary is built from: the recompiled output (its MANIFEST.json hashes
    every generated file), the facts file, and the profile's extra `host_inputs` (host sources, pins)."""
    manifest = settings.out(version) / "MANIFEST.json"
    if not manifest.is_file():
        raise TasteError("repro.capture.stale_host",
                         f"{manifest} is absent: the recompiled output has no manifest to check the host against")
    paths = [manifest, profile.resolve_path(config["facts"]),
             *(profile.resolve_path(p) for p in config.get("host_inputs", []))]
    return {str(p): _tree_digest(p) for p in paths}


def _stamp_path(profile: Any, config: dict[str, Any], host: Path) -> Path:
    name = hashlib.sha256(str(host).encode()).hexdigest()[:16]
    return profile.resolve_path(config["work_root"]) / "hosts" / f"{name}.json"


def _stale(stamp: Path, host: Path, inputs: dict[str, str]) -> str:
    """Why the host is not known to be built from `inputs` ('' when it is)."""
    if not host.is_file():
        return f"the oracle host build {host} does not exist"
    if not stamp.is_file():
        return f"no build of {host} was recorded (stamp {stamp})"
    try:
        known = json.loads(stamp.read_text())
    except (OSError, json.JSONDecodeError):
        return f"the build stamp {stamp} is unreadable"
    changed = sorted(p for p in {*inputs, *known.get("inputs", {})} if inputs.get(p) != known["inputs"].get(p))
    if changed:
        return f"its inputs changed since it was built: {', '.join(changed)}"
    if known.get("host_sha256") != sha256_file(host):
        return f"{host} was replaced since its recorded build"
    return ""


def ensure_host(profile: Any, config: dict[str, Any], settings: Any, version: str) -> dict[str, Any]:
    """The host binary's digest and its inputs' digest, once it is known to be built from the current inputs. A stale
    host is rebuilt with `host_build` (one build at a time) or refused as `repro.capture.stale_host`."""
    host = profile.resolve_path(config["host"])
    stamp = _stamp_path(profile, config, host)
    inputs = host_inputs(profile, config, settings, version)
    why = _stale(stamp, host, inputs)
    built = False
    if why:
        build = config.get("host_build")
        if not build:
            raise TasteError("repro.capture.stale_host", f"{why}; configure repro.capture.host_build to rebuild it")
        stamp.parent.mkdir(parents=True, exist_ok=True)
        with (stamp.parent / "build.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            why = _stale(stamp, host, host_inputs(profile, config, settings, version))  # another may have built it
            if why:
                log = stamp.with_suffix(".log")
                seconds = int(build.get("timeout_seconds", 1800))
                with log.open("w") as out:
                    try:
                        code = subprocess.run([str(c) for c in build["command"]], stdout=out, stderr=subprocess.STDOUT,
                                              timeout=seconds, check=False).returncode
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        code = -1
                        out.write(f"\n{exc}\n")
                if code != 0:
                    raise TasteError("repro.capture.host_build",
                                     f"the host was stale ({why}) and its build failed (exit {code}); log: {log}")
                after = host_inputs(profile, config, settings, version)
                if after != inputs or not host.is_file():
                    raise TasteError("repro.capture.stale_host",
                                     f"the inputs of {host} changed while it was built; run the capture again")
                atomic_json(stamp, {"host": str(host), "host_sha256": sha256_file(host), "inputs": after,
                                    "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "why": why}, sort_keys=True)
                built = True
            inputs = host_inputs(profile, config, settings, version)
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return {"host": host, "host_sha256": sha256_file(host), "inputs_digest": digest, "built": built}


def _display(profile: Any, config: dict[str, Any], env: dict[str, str]) -> None:
    """A Wayland display the oracle can open: the socket named by the run environment, started with
    `display_command` when it is not there (one start over every process). A capture never opens windows on a
    display it was not given. Without one the host runs headless (SDL_VIDEODRIVER=offscreen: no window, RT64 on a
    Vulkan headless surface), which needs no display at all. X11 is never used."""
    if env.get("DISPLAY"):
        raise TasteError("repro.capture.display", "repro.capture.env names an X11 DISPLAY; the oracle runs headless "
                         "(SDL_VIDEODRIVER=offscreen) or on Wayland, never X11")
    if "WAYLAND_DISPLAY" in env and "XDG_RUNTIME_DIR" in env:
        socket = Path(env["XDG_RUNTIME_DIR"]) / env["WAYLAND_DISPLAY"]
    else:
        return
    if socket.exists():
        return
    command = config.get("display_command")
    if not command:
        raise TasteError("repro.capture.display", f"the capture display {socket} is not running and the profile "
                         "names no repro.capture.display_command to start it")
    root = profile.resolve_path(config["work_root"])
    root.mkdir(parents=True, exist_ok=True)
    with (root / "display.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not socket.exists():
            if "XDG_RUNTIME_DIR" in env:
                Path(env["XDG_RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True, mode=0o700)
            with (root / "display.log").open("a") as log:
                subprocess.Popen([str(c) for c in command], stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,
                                 env={**os.environ, **{k: env[k] for k in ("XDG_RUNTIME_DIR",) if k in env}})
            deadline = time.monotonic() + 15
            while not socket.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
    if not socket.exists():
        raise TasteError("repro.capture.display", f"display_command did not bring up {socket} within 15s; "
                         f"log: {root / 'display.log'}")


def _cached(done: Path) -> dict[str, Any] | None:
    """A finished capture: capture.json (written last) and every file it names still as recorded."""
    try:
        record = json.loads(done.read_text())
        ram = Path(record["rdram_path"])
        if ram.stat().st_size != record["rdram_bytes"] or sha256_file(ram) != record["rdram_sha256"]:
            return None
        if record.get("calls_path") and sha256_file(record["calls_path"]) != record["calls_sha256"]:
            return None
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return record


class _Setup:
    """What every capture of one version shares: the profile's capture settings, the symbol table, the ROM, the
    host binary (known to be built from the current inputs) and the gdb script's text."""

    def __init__(self, profile: Any, version: str, env: dict[str, str] | None = None,
                 seconds: int | None = None) -> None:
        self.profile = profile
        self.version = version
        self.config = _config(profile)
        self.settings = load_recomp(profile)
        facts = json.loads(profile.resolve_path(self.config["facts"]).read_text())
        boot = facts.get("boot", {})
        if facts.get("version") not in (None, version) or "rdram_size" not in boot:
            raise TasteError(
                "repro.facts", f"facts {self.config['facts']} do not describe {version} with boot.rdram_size"
            )
        self.rdram_size = int(boot["rdram_size"])
        self.vrams = function_vrams(self.settings.out(version) / "symbols.toml")
        self.rom = self.settings.rom(version)
        self.rom_sha1 = hashlib.sha1(self.rom.read_bytes()).hexdigest()
        self.built = ensure_host(profile, self.config, self.settings, version)
        self.seconds = max(int(self.config.get("timeout_seconds", 120)), int(seconds or 0))
        self.work = profile.resolve_path(self.config["work_root"])
        # a run of its own (a movie that reaches the function) overrides the profile's fixed input; the env is in
        # every key, so each movie is its own capture
        self.env = {**{str(k): str(v) for k, v in self.config.get("env", {}).items()},
                    **{str(k): str(v) for k, v in (env or {}).items()}}

    def target(self, function: str) -> str:
        return resolve_target(function, self.vrams)

    def keys(self, target: str, hit: int, names: list[str], script_source: bytes,
             condition: str | None = None) -> tuple[str, str]:
        """The capture's key and the key of whether the run reaches the entry (the recorded stubs do not change it)."""
        digest = hashlib.sha256(script_source + (b"\0" + condition.encode() if condition else b"")).hexdigest()
        args = (self.rom_sha1, target, self.vrams[target], hit, self.rdram_size, self.built["host_sha256"])
        return (_key(*args, names, self.built["inputs_digest"], digest, self.env),
                _key(*args, [], self.built["inputs_digest"], digest, self.env))

    def cached(self, key: str, reach: str, started: float) -> dict[str, Any] | None:
        """The finished capture of `key`, or None; raises the cached miss when a run this long never reached it."""
        done = self.work / "captures" / key / "capture.json"
        cached = _cached(done) if done.is_file() else None
        if cached:
            return {**cached, "cached": True, "wall_seconds": round(time.monotonic() - started, 3)}
        miss = self.work / "misses" / f"{reach}.json"
        if miss.is_file():
            known = json.loads(miss.read_text())
            if known.get("timeout_seconds", 0) >= self.seconds:  # a longer timeout tries again
                raise TasteError("repro.capture.unreached", known["message"] + " (cached miss)")
        return None

    def commit(self, *, target: str, hit: int, names: list[str], key: str, reach: str, scratch: Path,
               receipt: str, started: float, condition: str | None = None) -> dict[str, Any]:
        """Freeze one finished gdb dump (scratch holds rdram.host, ctx.bin and calls.json) as the capture of `key`."""
        root = self.work / "captures" / key
        ram = host_order((scratch / "rdram.host").read_bytes())
        if len(ram) != self.rdram_size:
            raise TasteError("repro.capture", f"dumped {len(ram)} bytes of RDRAM, expected {self.rdram_size}")
        registers = parse_context((scratch / "ctx.bin").read_bytes())
        traced = json.loads((scratch / "calls.json").read_text()) if (scratch / "calls.json").is_file() else {}
        if names:
            atomic_json(root / "calls.json", traced.get("calls", []), sort_keys=True)
        atomic_bytes(root / "rdram.bin", ram)
        (scratch / "rdram.host").unlink()
        (self.work / "misses" / f"{reach}.json").unlink(missing_ok=True)
        record = {
            "schema": 1,
            "kind": "capture",
            "function": target,
            "vram": f"{self.vrams[target]:#010x}",
            "hit": hit,
            "condition": condition,
            "version": self.version,
            "rom_sha1": self.rom_sha1,
            "host_sha256": self.built["host_sha256"],
            "host_inputs": self.built["inputs_digest"],
            "host_built": self.built["built"],
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
            "receipt": receipt,
            "cached": False,
            "wall_seconds": round(time.monotonic() - started, 3),
        }
        atomic_json(root / "capture.json", record, sort_keys=True)  # the completeness mark: written last
        return record


def capture_probe(
    profile: Any, *, function: str, version: str, hit: int = 1, stubs: tuple[str, ...] = (),
    env: dict[str, str] | None = None, seconds: int | None = None, condition: str | None = None,
) -> dict[str, Any]:
    """`env` and `seconds` are this capture's own run (the movie that reaches the function and how long it needs):
    they replace the profile's fixed input and raise its time limit, and they are part of the capture's key."""
    started = time.monotonic()
    names = sorted(set(stubs))
    if hit < 1:
        raise TasteError("repro.capture", "--hit counts from 1")
    setup = _Setup(profile, version, env, seconds)
    config, env = setup.config, setup.env
    target = setup.target(function)
    host, seconds = setup.built["host"], setup.seconds
    script_source = Path(__file__).with_name("gdb_capture.py").read_bytes()
    key, reach = setup.keys(target, hit, names, script_source, condition)
    root = setup.work / "captures" / key
    done, miss = root / "capture.json", setup.work / "misses" / f"{reach}.json"
    cached = setup.cached(key, reach, started)
    if cached:
        return {"capture": cached, "seed": seed_of(cached)}
    done.unlink(missing_ok=True)  # a partial or corrupted entry is redone
    _display(profile, config, env)
    scratch = root / "run"
    scratch.mkdir(parents=True, exist_ok=True)
    for stale in ("rdram.host", "ctx.bin", "calls.json"):
        (scratch / stale).unlink(missing_ok=True)
    script = scratch / "gdb_capture.py"
    atomic_bytes(script, script_source)
    trace = int(config.get("trace_seconds", 30))  # how long the callee trace may run after the stop
    atomic_json(scratch / "config.json", gdb_config(target, hit, setup.rdram_size, scratch, names, trace, condition))
    env["N64R_ROM"] = str(setup.rom)
    env["TASTE_CAPTURE_CONFIG"] = str(scratch / "config.json")
    ran = run_command(
        profile,
        name=f"capture {target} hit {hit}",
        function=target,
        command=["timeout", "-s", "INT", str(seconds), str(config.get("gdb", "gdb")), "-batch", "-x", str(script),
                 str(host)],
        artifacts=[],
        env=env,
        slots=max(1, int(config.get("jobs", 2))),
    )
    log = Path(ran["status"]["log_path"])
    text = log.read_text(errors="replace") if log.is_file() else ""
    if "CAPTURE-ERROR" in text:
        raise TasteError("repro.capture", f"the gdb capture script failed; receipt: {ran['receipt']}")
    if "CAPTURE-DONE" not in text or not (scratch / "rdram.host").is_file() or not (scratch / "ctx.bin").is_file():
        stops = text.split("CAPTURE-MISS", 1)[1].strip().splitlines()[:12] if "CAPTURE-MISS" in text else []
        where = "; the oracle was stopped in: " + " | ".join(s.strip() for s in stops) if stops else ""
        if ran["status"].get("exit_code") != _TIMEOUT:
            tail = " | ".join(line.strip() for line in text.strip().splitlines()[-4:])
            raise TasteError("repro.capture.oracle_exit",
                             f"the oracle ended (exit {ran['status'].get('exit_code')}) before {target} entry hit "
                             f"{hit}: {tail}; receipt: {ran['receipt']}")
        message = (f"{target} entry hit {hit} was not reached within {seconds}s of the oracle run{where}; reaching it "
                   "needs a scripted input in repro.capture.env or a longer repro.capture.timeout_seconds")
        atomic_json(miss, {"message": message, "timeout_seconds": seconds, "receipt": ran["receipt"]})
        raise TasteError("repro.capture.unreached", f"{message}; receipt: {ran['receipt']}")
    record = setup.commit(target=target, hit=hit, names=names, key=key, reach=reach, scratch=scratch,
                          receipt=ran["receipt"], started=started, condition=condition)
    return {"capture": record, "seed": seed_of(record)}


def gdb_batch_config(targets: list[dict[str, Any]], hit: int, rdram_size: int, seconds: int,
                     trace_seconds: int = 30) -> dict[str, Any]:
    """The config of the batch gdb script (gdb_capture_many.py): one entry per function (symbol, out directory,
    callees to record), the run's own time limit and the per-function trace limit."""
    return {"targets": targets, "hit": hit, "rdram_size": rdram_size, "seconds": seconds,
            "max_writes": _MAX_WRITES, "max_calls": _MAX_CALLS, "trace_seconds": trace_seconds}


def capture_batch(
    profile: Any, *, plan: list[dict[str, Any]], version: str, hit: int = 1
) -> dict[str, Any]:
    """Rows that name their own run (`env`, `seconds`: a movie that reaches the function) are captured one oracle
    boot per distinct run, the others share the profile's run; the rows come back in plan order."""
    started = time.monotonic()
    groups: dict[str, list[int]] = {}
    for index, item in enumerate(plan):
        own = json.dumps([item.get("env") or {}, item.get("seconds") or 0], sort_keys=True)
        groups.setdefault(own, []).append(index)
    ordered: list[dict[str, Any]] = [{}] * len(plan)
    counts: dict[str, int] = {}
    runs, receipt = 0, ""
    for name, indexes in groups.items():
        env, seconds = json.loads(name)
        got = _capture_group(profile, plan=[plan[i] for i in indexes], version=version, hit=hit, env=env,
                             seconds=seconds or None)
        for i, row in zip(indexes, got["captures"], strict=True):
            ordered[i] = row
        runs += got["oracle_runs"]
        receipt = got["receipt"] or receipt
    for row in ordered:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"captures": ordered, "counts": counts, "oracle_runs": runs, "receipt": receipt,
            "wall_seconds": round(time.monotonic() - started, 3)}


def _capture_group(
    profile: Any, *, plan: list[dict[str, Any]], version: str, hit: int = 1, env: dict[str, str] | None = None,
    seconds: int | None = None,
) -> dict[str, Any]:
    """Capture many functions in ONE oracle boot. `plan` rows are {function, stubs}; each ends with exactly the
    capture (same key, same files) `capture_probe` would have made for that function and stub set, so a later
    single capture is a cache hit, or with its named finding (`repro.capture.unreached` for one the run never
    entered, kept like a single capture's miss). Functions already captured or known unreachable are not asked
    again; when nothing is left the oracle does not run. An entry is waited for `repro.capture.batch_seconds`
    (default 150, never less than a single capture's limit): a function the oracle has not entered by then is
    unreached, as for a single capture. Every function a single capture reaches in its 120 s the batch reaches
    in the same game time (measured: 21 of 21 within 58 s)."""
    started = time.monotonic()
    if hit < 1:
        raise TasteError("repro.capture", "--hit counts from 1")
    setup = _Setup(profile, version, env, seconds)
    config, env = setup.config, setup.env
    seconds = max(int(config.get("batch_seconds", max(150, setup.seconds))), setup.seconds)
    setup.seconds = seconds
    script_source = Path(__file__).with_name("gdb_capture_many.py").read_bytes()
    # the single capture's script keys the entries, so one cache serves both
    single = Path(__file__).with_name("gdb_capture.py").read_bytes()
    rows: dict[str, dict[str, Any]] = {}
    todo: dict[str, dict[str, Any]] = {}
    for item in plan:
        label = str(item["function"])
        names = sorted(set(item.get("stubs", [])))
        try:
            target = setup.target(label)
            key, reach = setup.keys(target, hit, names, single)
            cached = setup.cached(key, reach, started)
        except TasteError as exc:
            rows[label] = {"function": label, "status": exc.key, "message": exc.detail}
            continue
        if cached:
            rows[label] = {"function": target, "status": "captured", "key": key, "cached": True,
                           "wall_seconds": cached["wall_seconds"]}
        else:
            todo.setdefault(f"{target}/{key}", {"labels": [], "target": target, "names": names, "key": key,
                                                "reach": reach})["labels"].append(label)
    receipt = ""
    if todo:
        _display(profile, config, env)
        batch_root = setup.work / "batches" / hashlib.sha256("\n".join(sorted(todo)).encode()).hexdigest()[:16]
        batch_root.mkdir(parents=True, exist_ok=True)
        targets = []
        for item in todo.values():
            scratch = setup.work / "captures" / item["key"] / "run"
            scratch.mkdir(parents=True, exist_ok=True)
            for stale in ("rdram.host", "ctx.bin", "calls.json", "done"):
                (scratch / stale).unlink(missing_ok=True)
            (setup.work / "captures" / item["key"] / "capture.json").unlink(missing_ok=True)
            item["scratch"] = scratch
            targets.append({"symbol": item["target"], "out": str(scratch), "stubs": item["names"]})
        script = batch_root / "gdb_capture_many.py"
        atomic_bytes(script, script_source)
        atomic_json(batch_root / "config.json", gdb_batch_config(
            targets, hit, setup.rdram_size, seconds, int(config.get("trace_seconds", 30))))
        env["N64R_ROM"] = str(setup.rom)
        env["TASTE_CAPTURE_CONFIG"] = str(batch_root / "config.json")
        ran = run_command(
            profile,
            name=f"capture batch of {len(targets)} hit {hit}",
            function="",
            command=["timeout", "-s", "INT", str(seconds + int(config.get("trace_seconds", 30)) + 30),
                     str(config.get("gdb", "gdb")), "-batch", "-x", str(script), str(setup.built["host"])],
            artifacts=[],
            env=env,
            slots=max(1, int(config.get("jobs", 2))),
        )
        receipt = ran["receipt"]
        log = Path(ran["status"]["log_path"])
        text = log.read_text(errors="replace") if log.is_file() else ""
        if "CAPTURE-ERROR" in text:
            raise TasteError("repro.capture", f"the gdb batch capture script failed; receipt: {receipt}")
        timed_out = ran["status"].get("exit_code") == _TIMEOUT or "CAPTURE-TIMEOUT" in text
        for item in todo.values():
            scratch = item["scratch"]
            row: dict[str, Any]
            if (scratch / "done").is_file() and (scratch / "rdram.host").is_file() and (scratch / "ctx.bin").is_file():
                record = setup.commit(target=item["target"], hit=hit, names=item["names"], key=item["key"],
                                      reach=item["reach"], scratch=scratch, receipt=receipt, started=started)
                row = {"function": item["target"], "status": "captured", "key": item["key"],
                       "cached": False, "calls": record["calls"], "calls_complete": record["calls_complete"]}
            elif timed_out:
                message = (f"{item['target']} entry hit {hit} was not reached within {seconds}s of the oracle run "
                           "(a batch capture); reaching it needs a scripted input in repro.capture.env or a longer "
                           "repro.capture.batch_seconds")
                atomic_json(setup.work / "misses" / f"{item['reach']}.json",
                            {"message": message, "timeout_seconds": seconds, "receipt": receipt})
                row = {"function": item["target"], "status": "repro.capture.unreached", "message": message}
            else:
                row = {"function": item["target"], "status": "repro.capture.oracle_exit",
                       "message": f"the oracle ended (exit {ran['status'].get('exit_code')}) before "
                       f"{item['target']} entry hit {hit}; receipt: {receipt}"}
            for label in item["labels"]:
                rows[label] = row
    ordered = [rows[str(item["function"])] for item in plan]
    counts: dict[str, int] = {}
    for row in ordered:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"captures": ordered, "counts": counts, "oracle_runs": int(bool(todo)), "receipt": receipt,
            "wall_seconds": round(time.monotonic() - started, 3)}


def capture_many(
    profile: Any, *, functions: list[str], version: str, hit: int = 1, stubs: tuple[str, ...] = (),
    jobs: int | None = None, env: dict[str, str] | None = None, seconds: int | None = None,
    condition: str | None = None,
) -> dict[str, Any]:
    """Capture several functions, `jobs` oracle runs at a time (default: the profile's `jobs`; the slot locks bound
    the total over every process). Each function ends captured or with its named finding."""
    width = max(1, jobs or int(_config(profile).get("jobs", 2)))
    started = time.monotonic()

    def one(function: str) -> dict[str, Any]:
        try:
            got = capture_probe(profile, function=function, version=version, hit=hit, stubs=stubs, env=env,
                                seconds=seconds, condition=condition)
        except TasteError as exc:
            return {"function": function, "status": exc.key, "message": exc.detail}
        shown = got["capture"]
        return {"function": function, "status": "captured", "key": shown["key"], "cached": shown["cached"],
                "wall_seconds": shown["wall_seconds"]}

    with ThreadPoolExecutor(max_workers=width) as pool:
        rows = list(pool.map(one, functions))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"captures": rows, "counts": counts, "jobs": width, "wall_seconds": round(time.monotonic() - started, 3)}


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
