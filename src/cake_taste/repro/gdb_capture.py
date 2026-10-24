"""Runs inside gdb (`gdb -batch -x gdb_capture.py HOST`): stop the oracle at a function's entry, dump what a
behaviour probe needs, and record what the named callees did on the way out. Not importable outside gdb.

Config: JSON file named by TASTE_CAPTURE_CONFIG with symbol, hit, rdram_size, out (directory), stubs (callee
symbols), max_writes (words kept per call), max_calls. The recompiler's calling shape is the only knowledge:
`rdram` in the first argument register and `ctx` (a `recomp_context`: r0..r31 as u64 at offset 0) in the second.
Writes `rdram.host`, `ctx.bin`, `calls.json`; prints CAPTURE-STOP / CAPTURE-DONE, or CAPTURE-MISS and the stacks.
"""

import array
import json
import os
import re
import signal
import struct
import threading
from pathlib import Path

import gdb

cfg = json.loads(Path(os.environ["TASTE_CAPTURE_CONFIG"]).read_text())
OUT = cfg["out"]
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


def word_diff(before, after, base, limit):
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


class Mark(gdb.Breakpoint):
    pass


class Entry(Mark):
    def __init__(self):
        super().__init__("*" + cfg["symbol"], internal=True)
        self.matches = 0
        self.error = None
        if not cfg.get("condition"):
            self.ignore_count = cfg["hit"] - 1

    def stop(self):
        if not cfg.get("condition"):
            self.matches = cfg["hit"]
            return True
        try:
            matches = bool(gdb.parse_and_eval(cfg["condition"]))
        except gdb.error as exc:
            self.error = str(exc)
            return True
        if matches:
            self.matches += 1
        return self.matches >= cfg["hit"]


def main():
    entry = Entry()
    gdb.execute("run")
    if entry.error:
        raise RuntimeError("capture condition: " + entry.error)
    reached = alive() and entry.matches >= cfg["hit"]
    if not reached:
        print("CAPTURE-MISS")
        gdb.execute("thread apply all bt 6")
        gdb.execute("kill") if alive() else None
        gdb.execute("quit")

    print("CAPTURE-STOP")
    entry.delete()  # a recursive or other-thread entry is not an event of this call
    rdram, ctx, rsp = reg("rdi"), reg("rsi"), reg("rsp")
    thread = gdb.selected_thread().num
    Path(OUT, "rdram.host").write_bytes(read(rdram, SIZE))
    Path(OUT, "ctx.bin").write_bytes(read(ctx, 512))
    calls, unresolved, complete = [], [], False
    if cfg["stubs"]:
        done = Mark(f"*{struct.unpack('<Q', read(rsp, 8))[0]}", internal=True)
        done.thread = thread
        done.condition = f"$rsp == {rsp + 8}"
        leave, floor = struct.unpack("<Q", read(rsp, 8))[0], rsp + 8
        stubs = {}
        for name in cfg["stubs"]:
            where = address_of(name)
            if where is None:
                unresolved.append(name)
            elif where not in stubs:
                stubs[where] = name
                Mark(f"*{where}", internal=True).thread = thread
        seen, inside = {}, None
        # a function that does not return (a thread's body) is cut at the budget: SIGINT to gdb itself, which gdb
        # delivers as KeyboardInterrupt out of the running `continue` (what the outer `timeout` does too)
        budget = threading.Timer(cfg["trace_seconds"], os.kill, (os.getpid(), signal.SIGINT))
        budget.daemon = True
        budget.start()
        while len(calls) < cfg["max_calls"]:
            try:
                gdb.execute("continue")
            except KeyboardInterrupt:
                break
            if not alive():
                break
            pc, sp = reg("rip"), reg("rsp")
            if pc == leave and sp == floor:
                complete = True
                break
            if inside is None and pc in stubs:
                name, here = stubs[pc], reg("rsi")
                args = struct.unpack("<4Q", read(here + 32, 32))
                back = struct.unpack("<Q", read(sp, 8))[0]
                inside = {"name": name, "ctx": here, "back": back, "floor": sp + 8, "args": args,
                          "before": read(rdram, SIZE), "stop": Mark(f"*{back}", internal=True)}
                inside["stop"].thread = thread
                inside["stop"].condition = f"$rsp == {sp + 8}"
            elif inside is not None and pc == inside["back"] and sp == inside["floor"]:
                v0, v1 = struct.unpack("<2Q", read(inside["ctx"] + 16, 16))
                writes, dropped = word_diff(inside["before"], read(rdram, SIZE), rdram, cfg["max_writes"])
                seen[inside["name"]] = seen.get(inside["name"], 0) + 1
                args = [a & 0xFFFFFFFF for a in inside["args"]]
                calls.append({"name": inside["name"], "n": seen[inside["name"]], "args": args,
                              "v0": v0 & 0xFFFFFFFF, "v1": v1 & 0xFFFFFFFF,
                              "writes": [[a, v] for a, v in writes], "writes_dropped": dropped})
                inside["stop"].delete()
                inside = None
            elif pc not in stubs and not (inside is not None and pc == inside["back"]) and pc != leave:
                break  # interrupted (timeout): keep what was recorded
    budget.cancel() if cfg["stubs"] else None
    Path(OUT, "calls.json").write_text(
        json.dumps({"calls": calls, "complete": complete, "unresolved": unresolved, "stubs": cfg["stubs"]})
    )
    print("CAPTURE-DONE")
    gdb.execute("kill")
    gdb.execute("quit")


try:
    main()
except Exception:  # a script error must say so, not look like a function the oracle never reached
    import traceback

    print("CAPTURE-ERROR")
    traceback.print_exc()
    if alive():
        gdb.execute("kill")
    gdb.execute("quit")
