"""Bounded offline F3DEX2 command walk over a word-swapped RDRAM capture.

This reports the static command path, not rendered pixels. Conditional branches
and ucode changes fail explicitly because they require executing RSP state.
"""

import argparse
import collections
import json
import struct
from pathlib import Path


def trace(ram, start, size, limit=50000):
    segments = [0] * 16
    stack = []
    pc = start & 0x1FFFFFFF
    end = pc + size
    words = []
    commands = collections.Counter()
    raw = collections.Counter()
    light_offsets = set()

    def read(address, argument=False):
        if address & 7 or address < 0 or address + 8 > len(ram):
            raise ValueError(f"display list address outside aligned RDRAM: {address:#x}")
        if not stack and address + 8 > end:
            raise ValueError("root list exceeded task data_size before ENDDL")
        w0, w1 = struct.unpack_from("<II", ram, address)
        raw[w0 >> 24] += 1
        words.append({"address": f"{address:08x}", "w0": f"{w0:08x}", "w1": f"{w1:08x}", "argument": argument})
        return w0, w1

    for _ in range(limit):
        w0, w1 = read(pc)
        op = w0 >> 24
        commands[op] += 1
        pc += 8
        if op in (0x04, 0xDD):
            raise ValueError("conditional branch or ucode load requires live RSP state")
        if op == 0xDB and (w0 >> 16) & 255 == 6:
            offset = w0 & 65535
            if offset & 3 or offset >= 64:
                raise ValueError("invalid segment register")
            segments[offset // 4] = w1 & 0xFFFFFF
        if op == 0xDC and w0 & 255 == 10:
            light_offsets.add((w0 >> 5) & 0x7F8)
        if op in (0xE4, 0xE5):
            for expected in (0xE1, 0xF1):
                a0, _ = read(pc, True)
                if a0 >> 24 != expected:
                    raise ValueError("invalid F3DEX2 texture rectangle arguments")
                pc += 8
        if op == 0xDE:
            if ((w0 >> 16) & 255) == 0:
                if len(stack) >= 18:
                    raise ValueError("display list stack exceeded 18 entries")
                stack.append(pc)
            pc = (w1 & 0x1FFFFFFF) if w1 >= 0x80000000 else segments[(w1 >> 24) & 15] + (w1 & 0xFFFFFF)
        if op == 0xDF:
            if not stack:
                return {
                    "commands": {f"{k:02x}": v for k, v in sorted(commands.items())},
                    "all_words": {f"{k:02x}": v for k, v in sorted(raw.items())},
                    "light_offsets": sorted(light_offsets),
                    "words": words,
                }
            pc = stack.pop()
    raise ValueError("display list command limit exceeded")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rdram", type=Path)
    parser.add_argument("--start", type=lambda s: int(s, 0), required=True)
    parser.add_argument("--size", type=lambda s: int(s, 0), required=True)
    args = parser.parse_args()
    print(json.dumps(trace(args.rdram.read_bytes(), args.start, args.size), indent=2))
