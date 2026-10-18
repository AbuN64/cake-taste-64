import struct
import tomllib
import unittest
from dataclasses import replace
from cake_taste import TasteError
from cake_taste.recomp.asmfuncs import Function
from cake_taste.recomp.indirect import hooks


class LocalIndirectTests(unittest.TestCase):
    def setUp(self):
        self.fn = Function("menu", 0, 0x80001000, 32)
        self.rom = struct.pack(">8I", 2 << 21 | 8, 0, 0, 0, 0, 0, 0, 0)
        self.fact = {"function": "0x80001000", "pc": "0x80001000",
                     "targets": ["0x80001008", "0x80001010"], "source": "fixture table"}

    def test_profile_targets_make_local_labels_and_fail_on_unknown_target(self):
        config = tomllib.loads("\n".join(hooks([self.fact], [self.fn], self.rom)))
        items = config["patches"]["hook"]
        self.assertEqual([h["before_vram"] for h in items], [0x80001000, 0x80001008, 0x80001010])
        self.assertIn("ctx->r2 & 0x1FFFFFFFu", items[0]["text"])
        self.assertIn("case 0x00001008u: goto local_80001000_80001008", items[0]["text"])
        self.assertIn("switch_error", items[0]["text"])
        self.assertEqual(items[1]["text"], "local_80001000_80001008:;")

    def test_rejects_unsourced_outside_duplicate_and_delay_targets(self):
        for changes in ({"source": ""}, {"targets": [0x80001020]},
                        {"targets": [0x80001008, 0x80001008]},
                        {"targets": [0x80001004]}, {"pc": 0x8000101c}):
            with self.subTest(changes=changes), self.assertRaises(TasteError):
                hooks([{**self.fact, **changes}], [self.fn], self.rom)
        with self.assertRaises(TasteError):
            hooks([self.fact, self.fact], [self.fn], self.rom)

    def test_never_skips_effectful_delay_or_converts_return(self):
        for instruction, delay in ((2 << 21 | 8, 0x24420001), (31 << 21 | 8, 0), (0, 0)):
            with self.subTest(instruction=instruction, delay=delay), self.assertRaises(TasteError):
                hooks([self.fact], [self.fn], struct.pack(">II", instruction, delay)+bytes(24))
