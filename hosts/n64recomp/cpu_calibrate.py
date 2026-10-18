"""Derive a frame-budget model from immutable, aggregate VI measurements.

This assumes full CPU utilization in the busiest sampled window. It does not
infer R4300 cycle costs, caches, or original-console utilization from host time.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import sys
from pathlib import Path

from cpu_profile import MODEL, scale_from_measurement

PATTERN = re.compile(
    r"cpu_cadence vi=(\d+) count_ticks=(\d+) entries=(\d+) native_ns=(\d+)"
)



def calibrate(log: bytes, timing: dict, source_run: str) -> dict:
    rows = [dict(zip(("vi", "count_ticks", "entries", "native_ns"), map(int, match), strict=True))
            for match in PATTERN.findall(log.decode())]
    windows = []
    for first, last in itertools.pairwise(rows):
        delta = {key: last[key] - first[key] for key in first}
        if delta["vi"] != 60 or min(delta.values()) <= 0:
            continue
        windows.append({"first_vi": first["vi"], "last_vi": last["vi"], **delta})
    if not windows:
        raise ValueError("no complete, positive 60-VI measurement windows")
    window = max(windows, key=lambda row: row["native_ns"] / row["vi"])
    result = {
        "model": MODEL,
        "assumption": ("The busiest measured VI window receives the full guest CPU budget; "
                       "host overhead and original idle time are not measured R4300 work."),
        "source_run": source_run,
        "host_log_sha256": hashlib.sha256(log).hexdigest(),
        "window_count": len(windows),
        "window": window,
    }
    result["derived_native_time_scale"] = scale_from_measurement(result, timing)
    result["observed_count_ticks_per_vi"] = window["count_ticks"] / window["vi"]
    result["nominal_count_ticks_per_vi"] = timing["count_hz"] / timing["vi_hz"]
    return result


if __name__ == "__main__":
    facts = json.loads(Path(sys.argv[2]).read_text())
    print(json.dumps(calibrate(Path(sys.argv[1]).read_bytes(), facts["cpu"]["timing"], sys.argv[3]), indent=2))
