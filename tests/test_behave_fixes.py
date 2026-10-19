import unittest

from cake_taste.recomp import candidate
from cake_taste.repro import cases


class HarnessInputTests(unittest.TestCase):
    def test_budget_and_lenient_lines_precede_the_seeds(self):
        text = cases.cases_text([{"name": "a"}], {}, 0x80001000, [(1, 2)], 0x80400000, 2000, None, 50000, True)
        lines = text.splitlines()
        self.assertEqual(lines[:4], ["timeout 2000", "budget 50000", "lenient 1", "seed 1 2"])

    def test_defaults_write_neither_line(self):
        text = cases.cases_text([{"name": "a"}], {}, 0x80001000, [(1, 2)], 0x80400000, 2000)
        self.assertNotIn("budget", text)
        self.assertNotIn("lenient", text)

    def test_the_events_line_is_the_written_count(self):
        got = cases.parse_events("case c\nevents 123\nend ok\n")
        self.assertEqual(got[0]["events_written"], 123)


class StubSectionTests(unittest.TestCase):
    def test_each_target_gets_a_return_only_section(self):
        lines, filler = candidate._stub_sections([0x80000400, 0x80000488], 0x1000)
        text = "\n".join(lines)
        self.assertIn("vram = 0x80000400", text)
        self.assertIn("rom = 0x1008", text)
        self.assertIn('name = "func_80000488"', text)
        self.assertEqual(filler, bytes.fromhex("03E0000800000000") * 2)

    def test_the_refusal_text_names_the_target(self):
        found = candidate._NO_TARGET.findall("x\nNo function found for jal target: 0x80000400\n")
        self.assertEqual(found, ["80000400"])


if __name__ == "__main__":
    unittest.main()
