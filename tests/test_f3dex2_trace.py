import runpy
import struct
import unittest
from pathlib import Path

TRACE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "hosts/n64recomp/tools/trace_f3dex2.py"))["trace"]


class DisplayListTests(unittest.TestCase):
    def test_nested_list_and_rectangle_arguments(self):
        ram = bytearray(128)
        for off, a, b in [
            (0, 0xDE000000, 0x80000040),
            (8, 0xE4000000, 0),
            (16, 0xE1000000, 0),
            (24, 0xF1000000, 0),
            (32, 0xDF000000, 0),
            (64, 0xDC08090A, 0),
            (72, 0xDF000000, 0),
        ]:
            struct.pack_into("<II", ram, off, a, b)
        result = TRACE(ram, 0x80000000, 40)
        self.assertEqual(result["commands"], {"dc": 1, "de": 1, "df": 2, "e4": 1})
        self.assertEqual(result["all_words"]["e1"], 1)
        self.assertEqual(result["light_offsets"], [72])

    def test_cycle_and_truncated_list_fail(self):
        ram = struct.pack("<II", 0xDE010000, 0x80000000)
        with self.assertRaisesRegex(ValueError, "limit"):
            TRACE(ram, 0, 8, limit=10)
        with self.assertRaisesRegex(ValueError, "RDRAM"):
            TRACE(bytes(8), 0, 16)

    def test_conditional_branch_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "live RSP"):
            TRACE(struct.pack("<II", 0x04000000, 0), 0, 8)
