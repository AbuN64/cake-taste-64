"""Render the explicit host CPU timing model from oracle facts."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

MODEL = "busiest-vi-window-full-budget"


def scale_from_measurement(calibration: dict, timing: dict) -> float:
    if calibration["model"] != MODEL:
        raise ValueError("unsupported CPU calibration model")
    for name in ("count_hz", "vi_hz"):
        value = timing[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"cpu.timing.{name} must be a positive integer")
    window = calibration["window"]
    for name in ("vi", "count_ticks", "entries", "native_ns"):
        value = window[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"cpu.calibration.window.{name} must be a positive integer")
    # An entry is a workload unit, not a hardware instruction/cycle. The two
    # entry-normalized terms make that distinction and the model assumption explicit.
    target_ticks_per_entry = timing["count_hz"] * window["vi"] / timing["vi_hz"] / window["entries"]
    native_ns_per_entry = window["native_ns"] / window["entries"]
    return target_ticks_per_entry * 1e9 / timing["count_hz"] / native_ns_per_entry


def render(facts: dict) -> str:
    cpu = facts["cpu"]
    if "native_time_scale" in cpu:
        raise ValueError("standalone cpu.native_time_scale is retired; supply calibration measurements")
    scale = scale_from_measurement(cpu["calibration"], cpu["timing"])
    if cpu["timing"]["vi_hz"] != 60:
        raise ValueError("the pinned VI runtime currently supports only 60 Hz")
    if (
        isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not math.isfinite(scale)
        or not 1 <= scale <= 1024
    ):
        raise ValueError("derived CPU scale must be a finite number in 1..1024")
    return f"#pragma once\n#define HOST_NATIVE_CPU_SCALE {float(scale)!r}\n"


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(render(json.loads(Path(sys.argv[1]).read_text())))
