"""Generic call-trace diff: first divergence per thread plus the oracle work list."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cake_taste import TasteError

END = "<end of trace>"
Trace = dict[str, list[tuple[int, str]]]


def read_trace(path: Path, ignore: Iterable[str] = ()) -> Trace:
    """Parse `frame thread name` lines into per-thread (frame, name) lists, dropping ignored names."""
    skip = frozenset(ignore)
    threads: Trace = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit():
            raise TasteError("trace.format", f"{path}:{number}: expected `frame thread name`")
        name = parts[2].strip()
        if name not in skip:
            threads.setdefault(parts[1], []).append((int(parts[0]), name))
    return threads


def diff_traces(expected: Trace, actual: Trace, work: int) -> dict[str, Any]:
    divergences: list[dict[str, Any]] = []
    for thread in sorted(expected.keys() | actual.keys()):
        want, got = expected.get(thread, []), actual.get(thread, [])
        for index in range(max(len(want), len(got))):
            left = want[index] if index < len(want) else None
            right = got[index] if index < len(got) else None
            if left is not None and right is not None and left[1] == right[1]:
                continue
            present = left or right
            assert present is not None
            divergences.append(
                {
                    "thread": thread,
                    "index": index,
                    "frame": present[0],
                    "expected": left[1] if left else END,
                    "actual": right[1] if right else END,
                    "work_list": [name for _, name in want[index : index + work]],
                }
            )
            break
    divergences.sort(key=lambda item: (item["frame"], item["thread"]))
    return {"identical": not divergences, "first": divergences[0] if divergences else None, "divergences": divergences}
