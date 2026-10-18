"""Render the explicit host CPU timing model from oracle facts."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def render(facts: dict) -> str:
    scale = facts["cpu"]["native_time_scale"]
    if (
        isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not math.isfinite(scale)
        or not 1 <= scale <= 1024
    ):
        raise ValueError("cpu.native_time_scale must be a finite number in 1..1024")
    return f"#pragma once\n#define HOST_NATIVE_CPU_SCALE {float(scale)!r}\n"


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(render(json.loads(Path(sys.argv[1]).read_text())))
