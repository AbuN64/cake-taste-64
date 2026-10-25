"""Sourced local computed branches that the upstream analyser cannot discover."""
from __future__ import annotations

import json
import struct
import sys
from array import array

from cake_taste import TasteError

from .model import c_name


def _number(value):
    return int(value, 0) if isinstance(value, str) else value


def _label(pc, target):
    return f"local_{pc:08X}_{target:08X}"

def _selector(rom: bytes, offset: int, start: int, pc: int, reg: int) -> str:
    """The C value the jr dispatches on. When the word before the jr is  with another base register,
    switch on that memory word: upstream may have recognised the table and emitted the lw as an address computation
    (its switch then reads an addend saved once, wrong for a table pointer walked by a loop), so the register would
    hold the table slot, not the target. Otherwise switch on the register."""
    if pc - 4 >= start:
        (previous,) = struct.unpack_from(">I", rom, offset - 4)
        base, rt, imm = (previous >> 21) & 31, (previous >> 16) & 31, previous & 0xFFFF
        if previous >> 26 == 0x23 and rt == reg and base != reg:
            return f"MEM_W({imm - 0x10000 if imm & 0x8000 else imm}, ctx->r{base})"
    return f"ctx->r{reg}"
def hooks(facts, functions, rom: bytes) -> list[str]:
    """Use upstream hooks; never skip a side-effecting branch delay instruction."""
    result = []
    seen = set()
    try:
        for fact in facts:
            start, pc = (_number(fact[k]) for k in ("function", "pc"))
            targets = [_number(t) for t in fact["targets"]]
            fn = next(f for f in functions if f.vram == start)
            end = start + fn.size
            if (not fact.get("source") or not targets or len(set(targets)) != len(targets)
                    or (start, pc) in seen or pc < start or pc + 8 > end
                    or any(type(t) is not int or t & 3 or not start <= t < end for t in [pc, *targets])
                    or pc + 4 in targets):
                raise ValueError("need sourced, unique aligned local targets and a complete delay slot")
            offset = fn.rom + pc - start
            instruction, delay = struct.unpack_from(">II", rom, offset)
            reg = (instruction >> 21) & 31
            if instruction != (reg << 21) | 8 or reg in (0, 31) or delay:
                raise ValueError("only jr through a general register with a nop delay slot is qualified")
            seen.add((start, pc))
            text = [f"switch ((uint32_t){_selector(rom, offset, start, pc, reg)} & 0x1FFFFFFFu) {{"]
            text += [f"case 0x{t & 0x1fffffff:08X}u: goto {_label(pc, t)};" for t in targets]
            text += [f'default: switch_error("{fn.name}", 0x{pc:08X}u, 0);', "}"]
            entries = [(pc, "\n".join(text)), *((t, _label(pc, t) + ":;") for t in targets)]
            for address, code in entries:
                result += ["[[patches.hook]]", f"func = {json.dumps(c_name(fn.name))}",
                           f"before_vram = 0x{address:08X}", f"text = {json.dumps(code)}", ""]
    except (KeyError, TypeError, ValueError, StopIteration, struct.error) as exc:
        raise TasteError("recomp.indirect", f"invalid local_indirect_jumps: {exc}") from exc
    return result


_MASK = 0x1FFFFFFF


def walked(sections, rom: bytes) -> list[dict]:
    """Computed jumps through a jump table a loop walks by pointer, with the table's targets: `jr rX` (not $ra, nop
    delay slot) whose rX comes from `lw rX, 0(rB)` at most three instructions before, where the function steps rB by
    `addiu rB, rB, 4`. Upstream either misses such a jump (an indirect tail call) or dispatches on an addend saved once
    before the loop, so every later iteration takes the first case (PORT-FIX-O1 found four by hand). The table is the
    one run (two or more words) in the function's section whose every word is an aligned address inside the function
    (masked to 29 bits: tables may hold low aliases); a site with no such run, or several, is left out. Facts in the
    `local_indirect_jumps` form, `source` naming the detection."""
    found = []
    image = array("I", rom[: len(rom) & ~3])
    if sys.byteorder == "little":
        image.byteswap()
    for section in sections:
        for fn in section.functions:
            start, end = fn.vram & _MASK, (fn.vram + fn.size) & _MASK
            code = [int.from_bytes(rom[fn.rom + i : fn.rom + i + 4], "big") for i in range(0, fn.size, 4)]
            steps = {(w >> 21) & 31 for w in code
                     if w >> 26 == 0x09 and (w >> 21) & 31 == (w >> 16) & 31 and w & 0xFFFF == 4}
            for i, w in enumerate(code):
                reg = (w >> 21) & 31
                if w != (reg << 21) | 8 or reg in (0, 31) or i + 1 >= len(code) or code[i + 1]:
                    continue
                loads = [c for c in code[max(0, i - 3) : i]
                         if c >> 26 == 0x23 and (c >> 16) & 31 == reg and c & 0xFFFF == 0 and (c >> 21) & 31 in steps]
                if not loads:
                    continue
                runs, run = [], []
                hits = [at for at, value in enumerate(image) if start <= value & _MASK < end and not value & 3]
                for at in [*hits, -2]:
                    if run and at == run[-1][0] + 1:
                        run.append((at, image[at]))
                        continue
                    if len(run) >= 2:
                        runs.append(run)
                    run = [(at, image[at])] if at >= 0 else []
                if len(runs) != 1:
                    continue
                table = runs[0]
                pc = fn.vram + 4 * i
                targets = list(dict.fromkeys((fn.vram & ~_MASK) | (v & _MASK) for _, v in table))
                found.append({
                    "function": f"0x{fn.vram:08X}", "pc": f"0x{pc:08X}", "targets": [f"0x{t:08X}" for t in targets],
                    "source": f"detected: jr at 0x{pc:08X} reads a table walked by 4; table at ROM "
                              f"0x{4 * table[0][0]:X} ({len(table)} words)",
                })
    return found
