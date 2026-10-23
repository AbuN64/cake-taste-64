"""Runs inside gdb (`gdb -batch -x gdb_capture_many.py HOST`): ONE oracle boot that captures many functions. For every
target (a function symbol and the callees to record) it does what gdb_capture.py does for one: at the function's entry
it dumps RDRAM and the entry registers, then records what each named callee returned and wrote until the function
returns. Targets are independent and may overlap (one entered while another is being traced). Not importable outside
gdb.
Config: JSON file named by TASTE_CAPTURE_CONFIG with targets (a list of {symbol, out, stubs}), hit, rdram_size,
seconds (how long an entry is waited for), trace_seconds (one target's trace limit), max_writes, max_calls.
The recompiler's calling shape is the only knowledge: `rdram` in the first argument register and `ctx` (a
`recomp_context`: r0..r31 as u64 at offset 0) in the second.
Per target it writes `rdram.host`, `ctx.bin`, `calls.json` and, last, an empty `done` in its out directory. Prints
CAPTURE-DONE <symbol> as each finishes, and CAPTURE-TIMEOUT when the run's limit cut it short.
Return and callee breakpoints are limited by gdb itself to the traced thread (a return also to its stack pointer), and
a callee's are off while a call to one is recorded: an address the whole game passes constantly costs the oracle
nothing in Python. CAPTURE-STOPS prints how many stops reached Python.
"""

import array
import contextlib
import json
import os
import re
import signal
import struct
import threading
import time
from pathlib import Path

import gdb

cfg = json.loads(Path(os.environ["TASTE_CAPTURE_CONFIG"]).read_text())
SIZE = cfg["rdram_size"]
BLOCK = 1 << 16
inferior = gdb.selected_inferior()
for command in ("set pagination off", "set confirm off", "handle SIGUSR1 nostop noprint pass"):
    gdb.execute(command)


def reg(name):
    return int(gdb.parse_and_eval("$" + name)) & 0xFFFFFFFFFFFFFFFF


def read(address, length):
    return bytes(inferior.read_memory(address, length))


def alive():
    return inferior.pid != 0


def address_of(symbol):
    """Entry address of a function symbol (the host has minimal symbols only: `info address` names it)."""
    try:
        found = re.search(r"is at (0x[0-9a-fA-F]+)", gdb.execute("info address " + symbol, to_string=True))
    except gdb.error:
        return None
    return int(found.group(1), 16) if found else None


def word_diff(before, after, limit):
    """Guest words (address, value) that differ; the host image holds each guest word as a little-endian u32."""
    if before == after:
        return [], False
    found = []
    for start in range(0, len(before), BLOCK):
        a, b = before[start : start + BLOCK], after[start : start + BLOCK]
        if a == b:
            continue
        old, new = array.array("I", a), array.array("I", b)
        for index, (x, y) in enumerate(zip(old, new, strict=True)):
            if x != y:
                found.append((0x80000000 | (start + index * 4), y))
                if len(found) > limit:
                    return [], True
    return found, False


class Target:
    def __init__(self, row):
        self.symbol, self.out, self.stubs = row["symbol"], Path(row["out"]), row["stubs"]
        self.entry = None
        self.entries = 0
        self.entered = False
        self.finished = False
        self.thread = None
        self.rdram = None
        self.deadline = None
        self.calls, self.unresolved, self.addresses, self.seen = [], [], {}, {}
        self.callee_marks = []  # one breakpoint per recorded callee, on this target's thread only
        self.leave = None  # the breakpoint at the return of the function
        self.back = None  # the breakpoint at the return of the callee being recorded
        self.inside = None
        self.complete = False


targets = [Target(row) for row in cfg["targets"]]
active = []  # entered and still tracing
pending = []  # (kind, target) events a breakpoint stop left for the main loop
stops = {"entry": 0, "event": 0}  # breakpoint stops that reached Python, to say what slowed the oracle
started = time.monotonic()
run_deadline = started + cfg["seconds"]  # no function is waited for past it...
hard_deadline = run_deadline + cfg["trace_seconds"] + 5  # ...but a trace under way may finish


class Mark(gdb.Breakpoint):
    """A breakpoint that stops the oracle for one event the main loop handles. Return and callee breakpoints are
    limited to the traced thread and a return to its stack pointer by gdb itself (thread, condition): a return
    address the whole game passes through constantly must not cost a Python call per pass."""

    def __init__(self, spec, kind, target, thread=None, sp=None):
        super().__init__(spec, internal=True)
        self.kind, self.target = kind, target
        if thread is not None:
            self.thread = thread
        if sp is not None:
            self.condition = f"$rsp == {sp}"

    def stop(self):
        try:
            target = self.target
            if self.kind == "entry":
                stops["entry"] += 1
                target.entries += 1
                if target.entered or target.entries != cfg["hit"]:
                    return False
                target.entered = True  # a recursive or other-thread entry is not an event of this call
            else:
                stops["event"] += 1
            pending.append((self.kind, target))
            return True
        except Exception:  # an error in a stop handler would only be silence: stop and let the main loop see it
            return True


def drop(mark):
    if mark is not None and mark.is_valid():
        mark.delete()


def finish(target):
    if target.finished:
        return
    target.finished = True
    if target in active:
        active.remove(target)
    for mark in (target.leave, target.back, *target.callee_marks):
        drop(mark)
    Path(target.out, "calls.json").write_text(
        json.dumps({"calls": target.calls, "complete": target.complete, "unresolved": target.unresolved,
                    "stubs": target.stubs})
    )
    Path(target.out, "done").write_text("")
    print(f"CAPTURE-DONE {target.symbol} t={time.monotonic() - started:.1f}s calls={len(target.calls)} "
          f"complete={target.complete}")


def handle(kind, target):
    if kind == "entry":
        target.entry.enabled = False  # a hot function must not keep stopping the oracle after its capture
        rdram, ctx, rsp = reg("rdi"), reg("rsi"), reg("rsp")
        target.rdram, target.thread = rdram, gdb.selected_thread().num
        Path(target.out, "rdram.host").write_bytes(read(rdram, SIZE))
        Path(target.out, "ctx.bin").write_bytes(read(ctx, 512))
        if not target.stubs:
            finish(target)
            return
        leave = struct.unpack("<Q", read(rsp, 8))[0]
        for name in target.stubs:
            where = address_of(name)
            if where is None:
                target.unresolved.append(name)
            elif where not in target.addresses:
                target.addresses[where] = name
                target.callee_marks.append(Mark(f"*{where}", "callee", target, target.thread))
        target.deadline = time.monotonic() + cfg["trace_seconds"]
        target.leave = Mark(f"*{leave}", "leave", target, target.thread, rsp + 8)
        active.append(target)
    elif kind == "leave":
        target.complete = True
        finish(target)
    elif kind == "callee":
        pc, sp, here = reg("rip"), reg("rsp"), reg("rsi")
        back = struct.unpack("<Q", read(sp, 8))[0]
        target.inside = {"name": target.addresses[pc], "ctx": here, "args": struct.unpack("<4Q", read(here + 32, 32)),
                         "before": read(target.rdram, SIZE)}
        for mark in target.callee_marks:  # a call inside a stub is not the probe's call
            mark.enabled = False
        target.back = Mark(f"*{back}", "back", target, target.thread, sp + 8)
    elif kind == "back":
        inside = target.inside
        v0, v1 = struct.unpack("<2Q", read(inside["ctx"] + 16, 16))
        writes, dropped = word_diff(inside["before"], read(target.rdram, SIZE), cfg["max_writes"])
        name = inside["name"]
        target.seen[name] = target.seen.get(name, 0) + 1
        target.calls.append({"name": name, "n": target.seen[name], "args": [a & 0xFFFFFFFF for a in inside["args"]],
                             "v0": v0 & 0xFFFFFFFF, "v1": v1 & 0xFFFFFFFF,
                             "writes": [[a, v] for a, v in writes], "writes_dropped": dropped})
        drop(target.back)
        target.back = target.inside = None
        if len(target.calls) >= cfg["max_calls"]:
            finish(target)
            return
        for mark in target.callee_marks:
            mark.enabled = True


def arm():
    """Wake the oracle (SIGINT to gdb itself, which interrupts `continue`) when the next trace limit or the run's
    limit comes: a function that does not return (a thread's body) is cut at its budget."""
    times = [hard_deadline if active else run_deadline, *(t.deadline for t in active)]
    timer = threading.Timer(max(0.05, min(times) - time.monotonic()), os.kill, (os.getpid(), signal.SIGINT))
    timer.daemon = True
    timer.start()
    return timer


def main():
    for target in targets:
        target.entry = Mark("*" + target.symbol, "entry", target)
    timer = arm()
    with contextlib.suppress(KeyboardInterrupt):
        gdb.execute("run")
    while alive():
        timer.cancel()
        now = time.monotonic()
        while pending:
            handle(*pending.pop(0))
        for target in [t for t in active if t.deadline <= now]:
            finish(target)  # cut at the budget: what was recorded stays, `complete` stays false
        if all(t.finished for t in targets) or now >= (hard_deadline if active else run_deadline):
            break
        timer = arm()
        with contextlib.suppress(KeyboardInterrupt):
            gdb.execute("continue")
    timer.cancel()
    print("CAPTURE-STOPS " + json.dumps(stops))
    if any(not t.finished for t in targets) and alive() and time.monotonic() >= run_deadline:
        print("CAPTURE-TIMEOUT")
    for target in active[:]:
        finish(target)
    if alive():
        gdb.execute("kill")
    gdb.execute("quit")


try:
    main()
except Exception:  # a script error must say so, not look like functions the oracle never reached
    import traceback

    print("CAPTURE-ERROR")
    traceback.print_exc()
    if alive():
        gdb.execute("kill")
    gdb.execute("quit")
