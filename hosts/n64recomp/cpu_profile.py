"""Render the logical guest clock (RUNTIME.md "Virtual guest time") from oracle facts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def render(facts: dict) -> str:
    return f"#pragma once\n#define HOST_GUEST_NS_PER_ENTRY {guest_ns_per_entry(facts['cpu'])!r}\n"


def guest_ns_per_entry(cpu: dict) -> float:
    """The logical clock's fixed charge (RUNTIME.md "Virtual guest time"): guest nanoseconds per charged entry, the
    VI time of the recorded calibration window divided by the entries it ran."""
    timing, window = cpu["timing"], cpu["calibration"]["window"]
    for key, value in (("timing.vi_hz", timing["vi_hz"]), ("calibration.window.vi", window["vi"]),
                       ("calibration.window.entries", window["entries"])):
        if type(value) is not int or value <= 0:
            raise ValueError(f"cpu.{key} must be a positive integer")
    if timing["vi_hz"] != 60:
        raise ValueError("the pinned VI runtime currently supports only 60 Hz")
    return float(window["vi"] * 1e9 / timing["vi_hz"] / window["entries"])


SYMBOL = re.compile(r'\{ name = "([A-Za-z_][A-Za-z0-9_]*)", vram = 0x([0-9A-Fa-f]+),')


def render_native(facts: dict, symbols: str) -> str:
    """Which recompiled functions charge guest work under the entries clock (RUNTIME.md "Virtual guest time"):
    every function of symbols.toml whose vram lies in no cpu.native_code range [lo, hi), the code the port runs
    natively (the SDK range and the functions it binds to native code)."""
    ranges = facts["cpu"]["native_code"]
    if not isinstance(ranges, list) or not ranges or not all(
            isinstance(r, list) and len(r) == 2 and all(type(v) is int for v in r) and r[0] < r[1] for r in ranges):
        raise ValueError("cpu.native_code must be a non-empty list of [lo, hi) vram ranges")
    functions = {}
    for name, vram in SYMBOL.findall(symbols):
        if functions.setdefault(name, int(vram, 16)) != int(vram, 16):
            raise ValueError(f"symbols.toml names {name} at two addresses")
    if not functions:
        raise ValueError("symbols.toml lists no functions")
    rows = [f'    {{"{name}", {int(not any(lo <= vram < hi for lo, hi in ranges))}}},'
            for name, vram in sorted(functions.items(), key=lambda item: item[0].encode())]
    return ("#pragma once\n// Sorted by name (strcmp); charged = an entry is guest work.\n"
            "static const struct { const char *name; unsigned char charged; } host_functions[] = {\n"
            + "\n".join(rows) + "\n};\n")


if __name__ == "__main__":
    facts = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(render(facts))
    Path(sys.argv[4]).write_text(render_native(facts, Path(sys.argv[3]).read_text()))
