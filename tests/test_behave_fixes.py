import tempfile
import unittest
from pathlib import Path

from cake_taste import TasteError
from cake_taste.recomp import candidate
from cake_taste.repro import cases, closure


class HarnessInputTests(unittest.TestCase):
    def test_budget_and_lenient_lines_precede_the_seeds(self):
        text = cases.cases_text([{"name": "a"}], {}, 0x80001000, [(1, 2)], 0x80400000, 2000, None, 50000, True)
        lines = text.splitlines()
        self.assertEqual(lines[:4], ["timeout 2000", "budget 50000", "lenient 1", "seed 1 2"])

    def test_defaults_write_neither_line(self):
        text = cases.cases_text([{"name": "a"}], {}, 0x80001000, [(1, 2)], 0x80400000, 2000)
        self.assertNotIn("budget", text)
        self.assertNotIn("lenient", text)

    def test_stack_window_line_precedes_the_seeds(self):
        frame = (0x803FC000, 0x80400010)
        text = cases.cases_text([{"name": "a"}], {}, 0x80001000, [(1, 2)], 0x80400000, 2000, None, None, False, frame)
        self.assertEqual(text.splitlines()[:2], ["timeout 2000", "stack 803FC000 80400010"])
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


class GeneratedBodyExtentTests(unittest.TestCase):
    """index_bodies on shapes taken from real N64Recomp output."""

    HEADER = "RECOMP_FUNC void {}(uint8_t* rdram, recomp_context* ctx) {{\n"

    def index(self, text, log=None):
        with tempfile.TemporaryDirectory() as tmp:
            funcs = Path(tmp) / "funcs"
            funcs.mkdir()
            (funcs / "funcs_0.c").write_text(text)
            if log is not None:
                (Path(tmp) / "n64recomp.log").write_text(log)
            return closure.index_bodies(funcs)

    def test_each_body_stops_at_its_own_closing_line(self):
        first = self.HEADER.format("a") + "    ctx->r2 = 1;\n;}\n"
        second = self.HEADER.format("b") + "    a(rdram, ctx);\n;}\n"
        got = self.index('#include "recomp.h"\n' + first + "\n" + second)
        self.assertEqual(got, {"a": first, "b": second})

    def test_a_closing_line_inside_a_body_does_not_cut_it_short(self):
        body = self.HEADER.format("a") + "    if (x) {\n;}\n    ctx->r2 = 1;\n;}\n"
        self.assertEqual(self.index(body)["a"], body)

    def test_half_written_output_is_refused_with_the_generators_reason(self):
        text = self.HEADER.format("behave_f") + "    // 0x808009B0: addiu $sp, $sp, -0x60\n    if (x) {\n"
        log = "Function count: 4374\nNo function found for jal target: 0x802C5910\nError recompiling behave_f\n"
        with self.assertRaises(TasteError) as raised:
            self.index(text, log)
        message = str(raised.exception)
        self.assertIn("behave_f has no closing ';}'", message)
        self.assertIn("No function found for jal target: 0x802C5910", message)

    def test_a_half_written_last_body_is_refused_after_complete_ones(self):
        text = self.HEADER.format("a") + "\n;}\n\n" + self.HEADER.format("b") + "    x;\n"
        with self.assertRaises(TasteError):
            self.index(text)
