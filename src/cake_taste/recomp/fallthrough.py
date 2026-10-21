"""Preserve MIPS fallthrough across adjacent generated function boundaries."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from cake_taste import TasteError
from cake_taste.files import atomic_bytes

_HEAD = re.compile(r"^RECOMP_FUNC void (\w+)\(", re.MULTILINE)
_INSN = re.compile(r"// 0x([0-9A-Fa-f]{8}): ([\w.]+)[ \t]*(.*)")
_STATIC = re.compile(r"static_(\d+)_[0-9A-Fa-f]{8}")
_MARKER = "/* MIPS fallthrough to adjacent entry */"


def _terminal(mnemonic: str, operands: str) -> bool:
    if mnemonic in {"jr", "j", "b", "eret", "break"}:
        return True
    args = [arg.strip() for arg in operands.split(",")]
    return (mnemonic in {"beq", "beql"} and len(args) == 3 and args[0] == args[1]) or (
        mnemonic in {"bgez", "bgezl", "blez", "blezl"} and args[0] == "$zero"
    )


def link_fallthroughs(funcs: Path, symbols: Path) -> list[dict[str, str | int]]:
    """Connect adjacent entries in the same overlay; a symbol boundary is not a return.

    A final unconditional transfer (including its delay slot) already transfers
    control in generated C. Otherwise append the next entry as a tail call. An
    unreachable appended call is harmless: earlier generated branches/returns
    still take precedence. Instruction comments retain the actual guest addresses,
    including recompiler-discovered static prefixes absent from symbols.toml.
    """
    sections = tomllib.loads(symbols.read_text())["section"]
    owners = {f["name"]: i for i, section in enumerate(sections) for f in section["functions"]}
    files = {path: path.read_text() for path in sorted(funcs.glob("*.c"))}
    bodies = []
    entries = {}
    for path, text in files.items():
        heads = list(_HEAD.finditer(text))
        for index, head in enumerate(heads):
            end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
            body = text[head.start():end]
            instructions = {int(address, 16): (op, args) for address, op, args in _INSN.findall(body)}
            if not instructions:
                continue
            name = head[1]
            static = _STATIC.fullmatch(name)
            owner = int(static[1]) if static else owners.get(name)
            if owner is None:
                continue
            start = min(instructions)
            key = (owner, start)
            if key in entries and entries[key] != name:
                raise TasteError("recomp.fallthrough", f"ambiguous entry 0x{start:08X} in section {owner}")
            entries[key] = name
            bodies.append((path, head.start(), end, name, owner, instructions))
    edits = {}
    links = []
    for path, start, end, name, owner, instructions in bodies:
        body = files[path][start:end]
        last = max(instructions)
        target = entries.get((owner, last + 4))
        if target is None or _MARKER in body:
            continue
        if _terminal(*instructions[last]):
            continue
        previous = instructions.get(last - 4)
        # A branch directly to a delay-slot address makes that instruction an
        # ordinary entry too; retain its possible fallthrough in that case.
        slot_targeted = any(re.search(rf"(?:L_|0x){last:08X}\b", args, re.IGNORECASE)
                            for _, args in instructions.values())
        if previous and _terminal(*previous) and not slot_targeted:
            continue
        # Before the generator's closing ";}" so every body keeps the one ending the closure reader splits on.
        closing = body.rfind(";}")
        if closing < 0:
            raise TasteError("recomp.fallthrough", f"missing function end for {name}")
        addition = f"\n    {_MARKER}\n    {target}(rdram, ctx);\n    return;\n"
        edits.setdefault(path, []).append((start + closing, addition))
        links.append({"function": name, "next": target, "address": last + 4, "section": owner})
    for path, insertions in edits.items():
        text = files[path]
        for offset, addition in sorted(insertions, reverse=True):
            text = text[:offset] + addition + text[offset:]
        atomic_bytes(path, text.encode())
    return links
