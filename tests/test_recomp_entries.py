import tempfile
import tomllib
import unittest
from pathlib import Path

from test_recomp import build

from cake_taste.recomp.generate import write_symbols
from cake_taste.recomp.settings import load

PROLOGUE = (0x27BDFFE0).to_bytes(4, "big")  # addiu $sp, $sp, -0x20


def patched(root: Path, edits: dict[int, int]) -> object:
    profile = build(root)
    rom = bytearray((root / "rom.z64").read_bytes())
    for at, word in edits.items():
        rom[at:at + 4] = word.to_bytes(4, "big")
    (root / "rom.z64").write_bytes(rom)
    return load(profile)


def entries(settings) -> list[dict]:
    path = write_symbols(settings, "v1")["path"]
    boot = next(s for s in tomllib.loads(Path(path).read_text())["section"] if s["name"] == "boot_code")
    return [f for f in boot["functions"] if f["name"].startswith("entry_")]


class CodePointerEntryTests(unittest.TestCase):
    """boot_code: `first` is 0x80200400..0x80200408, then `second`; the .data subsegment starts at rom 0x1010."""

    def test_stored_pointer_into_a_function_prologue_becomes_an_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = patched(Path(tmp), {0x1004: 0x27BDFFE0, 0x1010: 0x80200404})
            self.assertEqual(entries(settings), [{"name": "entry_80200404", "vram": 0x80200404, "size": 4}])

    def test_kuseg_alias_pointer_names_the_same_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = patched(Path(tmp), {0x1004: 0x27BDFFE0, 0x1010: 0x00200404})
            self.assertEqual([f["vram"] for f in entries(settings)], [0x80200404])

    def test_word_that_does_not_start_a_frame_is_no_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = patched(Path(tmp), {0x1004: 0x24020001, 0x1010: 0x80200404})
            self.assertEqual(entries(settings), [])

    def test_pointer_to_an_existing_function_start_is_no_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = patched(Path(tmp), {0x1010: 0x80200408})
            self.assertEqual(entries(settings), [])

    def test_jump_table_neighbours_inside_one_function_are_no_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = patched(Path(tmp), {0x1004: 0x27BDFFE0, 0x1010: 0x80200404, 0x1014: 0x80200400})
            self.assertEqual(entries(settings), [])


if __name__ == "__main__":
    unittest.main()
