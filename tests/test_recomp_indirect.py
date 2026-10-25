import struct
import tomllib
import unittest

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
    def test_switches_on_the_table_word_when_a_lw_from_another_base_feeds_the_jr(self):
        fn = Function("walk", 0, 0x80001000, 32)
        lw = 0x23 << 26 | 17 << 21 | 2 << 16  # lw v0, 0(s1): upstream may emit it as the slot address
        rom = struct.pack(">8I", lw, 2 << 21 | 8, 0, 0, 0, 0, 0, 0)
        fact = {**self.fact, "pc": "0x80001004", "targets": ["0x80001010", "0x80001018"]}
        text = tomllib.loads("\n".join(hooks([fact], [fn], rom)))["patches"]["hook"][0]["text"]
        self.assertIn("switch ((uint32_t)MEM_W(0, ctx->r17) & 0x1FFFFFFFu)", text)
        same = struct.pack(">8I", 0x23 << 26 | 2 << 21 | 2 << 16, 2 << 21 | 8, 0, 0, 0, 0, 0, 0)
        text = tomllib.loads("\n".join(hooks([fact], [fn], same)))["patches"]["hook"][0]["text"]
        self.assertIn("switch ((uint32_t)ctx->r2 & 0x1FFFFFFFu)", text)
