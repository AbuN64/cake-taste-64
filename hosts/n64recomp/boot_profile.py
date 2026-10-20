"""Render original-ROM loading facts independently of the recompiler's analysis image."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def render(facts: dict) -> str:
    def word(value: int | str) -> int:
        number = int(value, 0) if isinstance(value, str) else value
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 0xFFFFFFFF:
            raise ValueError("boot fact must be a 32-bit unsigned integer")
        return number

    entry = word(facts["header_entrypoint"])
    size = word(facts["boot"]["rdram_size"])
    if not 0x80000000 <= entry < 0xA0000000 or (entry & 0x1FFFFFFF) + 0x100000 > size:
        raise ValueError("runtime initial 1 MiB ROM load must fit in RDRAM")
    status = word(facts["boot"]["registers"]["status"])
    mode = word(facts["boot"]["vi_mode"])
    if not 0x80000000 <= mode < 0xA0000000 or (mode & 0x1FFFFFFF) + 0x50 > size:
        raise ValueError("initial OSViMode must fit in RDRAM")
    clock = word(facts["boot"]["os_clock_rate"])
    if not 0x80000000 <= clock < 0xA0000000 or clock % 8 or (clock & 0x1FFFFFFF) + 8 > size:
        raise ValueError("osClockRate must name an aligned 64-bit RDRAM global")
    count_hz = word(facts["cpu"]["timing"]["count_hz"])
    # The pinned runtime's osGetTime/osGetCount converts time at this rate.
    if count_hz != 46875000:
        raise ValueError("CPU count rate disagrees with the pinned runtime")
    sections = [(word(s["vram"]), word(s["rom"])) for s in facts["sections"]]
    if not sections or len({v for v, _ in sections}) != len(sections):
        raise ValueError("sections must have unique nonempty VRAM mappings")
    rows = ",".join(f"{{{v}u,{r}u}}" for v, r in sections)
    return ("#pragma once\n#include <stdint.h>\n"
            f"#define HOST_ROM_ENTRY_ADDR {entry}u\n#define HOST_INITIAL_STATUS {status}u\n"
            f"#define HOST_INITIAL_VI_MODE {mode}u\n"
            f"#define HOST_OS_CLOCK_RATE {clock}u\n#define HOST_COUNT_HZ {count_hz}u\n"
            f"static const uint32_t host_rom_sections[][2] = {{{rows}}};\n")


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(render(json.loads(Path(sys.argv[1]).read_text())))
