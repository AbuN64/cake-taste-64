"""Select the call closure a bounded probe needs from already-generated recomp C (read-only, never edited)."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from cake_taste import TasteError

_HEADER = re.compile(r"^RECOMP_FUNC void (\w+)\(uint8_t\* rdram, recomp_context\* ctx\) \{$", re.M)
_CALL = re.compile(r"\b([A-Za-z_]\w*)\(rdram, ctx\)")
_END = "\n;}\n"


def function_vrams(symbols: Path) -> dict[str, int]:
    """Name -> vram for every function in an N64Recomp symbols TOML."""
    try:
        data = tomllib.loads(symbols.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TasteError("repro.symbols", f"{symbols}: {exc}") from exc
    found: dict[str, int] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "functions" and isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and "name" in item and "vram" in item:
                            found[item["name"]] = int(item["vram"])
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def index_bodies(funcs_dir: Path) -> dict[str, str]:
    """Name -> full generated definition text, for every function in the generated C files."""
    bodies: dict[str, str] = {}
    for path in sorted(funcs_dir.glob("*.c")):
        text = path.read_text()
        for match in _HEADER.finditer(text):
            stop = text.find(_END, match.end())
            if stop < 0:
                raise TasteError("repro.generated", f"{path}: {match.group(1)} has no closing ';}}'")
            bodies[match.group(1)] = text[match.start() : stop + len(_END)]
    if not bodies:
        raise TasteError("repro.generated", f"no generated functions under {funcs_dir}")
    return bodies


@dataclass
class Closure:
    root: str
    bodies: dict[str, str] = field(default_factory=dict)
    stubs: dict[str, str] = field(default_factory=dict)  # name -> reason
    indirect: list[str] = field(default_factory=list)  # functions that call through LOOKUP_FUNC
    spilled: list[str] = field(default_factory=list)  # callees past the cap, cut as boundary stubs (spill mode)


def resolve_target(target: str, vrams: dict[str, int]) -> str:
    if target in vrams:
        return target
    try:
        address = int(target, 0)
    except ValueError:
        raise TasteError("repro.function", f"{target} is neither a known function name nor an address") from None
    names = [name for name, vram in vrams.items() if vram == address]
    if not names:
        raise TasteError("repro.function", f"no function starts at {address:#010x}")
    return sorted(names)[0]


def closure(
    root: str,
    bodies: dict[str, str],
    *,
    boundary: set[str] | frozenset[str] = frozenset(),
    extra: tuple[str, ...] = (),
    limit: int = 64,
    spill: bool = False,
) -> Closure:
    """Breadth-first direct-call closure; boundary names and bodiless callees become stubs. Past `limit` bodies the
    closure is an error, or with `spill` every callee not yet taken (nearest ones are taken first) becomes a boundary
    stub too: the probe records it as `spilled` and the caller compares the calls made to it."""
    if root in boundary or root not in bodies:
        raise TasteError("repro.closure", f"target {root} must have a generated body and cannot be a stub")
    result = Closure(root)
    queue = [root, *extra]
    seen: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        if name in boundary:
            result.stubs[name] = "boundary"
            continue
        body = bodies.get(name)
        if body is None:
            result.stubs[name] = "no generated body"
            continue
        if len(result.bodies) >= limit:
            if not spill:
                raise TasteError("repro.closure", f"closure of {root} exceeds {limit} functions; add boundary stubs")
            result.stubs[name] = "boundary"
            result.spilled.append(name)
            continue
        result.bodies[name] = body
        if "LOOKUP_FUNC(" in body:
            result.indirect.append(name)
        header_end = body.index("{")
        queue.extend(_CALL.findall(body[header_end:]))
    return result


def _hex(value: int) -> str:
    return f"0x{value & 0xFFFFFFFF:08X}u"


def render_unit(
    found: Closure,
    vrams: dict[str, int],
    include: str,
    trace_mode: bool,
    stub_v0: dict[str, int],
    headers: tuple[str, ...] | None = None,
    runtime_v0: bool = False,
) -> str:
    """One C unit: includes, prototypes, stubs, selected bodies and a vram table for get_function.

    `headers` replaces the bounded probe's own two includes (bounded.h, trace.h) for a probe with its own hooks.
    With `runtime_v0` a stub leaves v0 to its probe hook (which can answer differently per run); otherwise it
    returns the constant from `stub_v0`, default 0."""
    names = sorted(found.bodies) + sorted(found.stubs)
    own = [f'#include "{name}"' for name in headers] if headers is not None else [
        '#include "bounded.h"', '#include "trace.h"' if trace_mode else ""]
    lines = [include, *own, ""]
    lines += [f"void {name}(uint8_t* rdram, recomp_context* ctx);" for name in names]
    lines.append("")
    for name in sorted(found.stubs):
        value = stub_v0.get(name, 0)
        signature = f"void {name}(uint8_t* rdram, recomp_context* ctx)"
        result = "" if runtime_v0 else f" ctx->r2 = (gpr)(int64_t){value};"
        lines.append(f'{signature} {{ probe_stub("{name}");{result} }}')
    lines.append("")
    lines += [found.bodies[name] for name in sorted(found.bodies)]
    lines.append("const probe_func probe_funcs[] = {")
    for name in names:
        if name in vrams:
            lines.append(f'    {{{_hex(vrams[name])}, {name}, "{name}"}},')
    lines.append("    {0, 0, 0}")
    lines.append("};")
    return "\n".join(lines) + "\n"


def limit_depth(root: str, bodies: dict[str, str], depth: int, *, same_unit: str = "") -> set[str]:
    """Callees deeper than `depth` calls from the root: stub them. A name starting with `same_unit` is part of the
    root's own unit (its sibling functions) and costs no depth."""
    reached = {root: 0}
    queue = [root]
    deep: set[str] = set()
    while queue:
        name = queue.pop(0)
        body = bodies.get(name)
        if body is None:
            continue
        for callee in _CALL.findall(body[body.index("{"):]):
            step = 0 if same_unit and callee.startswith(same_unit) and name.startswith(same_unit) else 1
            level = reached[name] + step
            if level > depth:
                if callee in bodies:
                    deep.add(callee)
                continue
            if callee not in reached or level < reached[callee]:
                reached[callee] = level
                queue.append(callee)
    return deep - {name for name, level in reached.items() if level <= depth}
