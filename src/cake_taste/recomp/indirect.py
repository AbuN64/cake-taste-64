"""Sourced local computed branches that the upstream analyser cannot discover."""
from __future__ import annotations

import json
import struct

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
