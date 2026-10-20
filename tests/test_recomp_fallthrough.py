import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from cake_taste.recomp.fallthrough import link_fallthroughs


def function(name, instructions, statements=""):
    lines = [f"RECOMP_FUNC void {name}(uint8_t* rdram, recomp_context* ctx) {{"]
    lines += [f"    // 0x{address:08X}: {text}" for address, text in instructions]
    return "\n".join([*lines, statements, ";}\n"])


class FallthroughTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.funcs = self.path / "funcs"
        self.funcs.mkdir()
        self.symbols = self.path / "symbols.toml"

    def setup_sections(self, sections):
        self.symbols.write_text("\n".join(
            "[[section]]\nfunctions = [" + ",".join(
                f'{{name="{name}", vram={address}, size=4}}' for name, address in rows) + "]"
            for rows in sections))

    def test_static_prefix_executes_continuation_and_is_idempotent(self):
        self.setup_sections([[('body', 0x80001004)]])
        source = self.funcs / "a.c"
        source.write_text(function('static_0_80001000', [(0x80001000, 'lui $a0, 0x8015')],
                                   'ctx->base = 40;') +
                          function('body', [(0x80001004, 'sw $a0, 0($a1)'),
                                            (0x80001008, 'jr $ra'), (0x8000100C, 'nop')],
                                   '*rdram = (uint8_t)(ctx->base + 2); return;'))
        links = link_fallthroughs(self.funcs, self.symbols)
        self.assertEqual([(x['function'], x['next']) for x in links], [('static_0_80001000', 'body')])
        self.assertEqual(link_fallthroughs(self.funcs, self.symbols), [])
        wrapper = self.path / "fixture.c"
        wrapper.write_text('#include <stdint.h>\n#define RECOMP_FUNC\n'
                           'typedef struct { int base; } recomp_context;\n'
                           'void body(uint8_t*, recomp_context*);\n' + source.read_text() +
                           '\nint main(void) { uint8_t value=0; recomp_context ctx={0}; '
                           'static_0_80001000(&value,&ctx); return value != 42; }\n')
        exe = self.path / "fixture"
        subprocess.run([os.environ.get('CC', 'cc'), '-Wall', '-Werror', str(wrapper), '-o', str(exe)],
                       check=True, capture_output=True, timeout=10)
        subprocess.run([str(exe)], check=True, timeout=5)

    def test_terminal_transfer_and_delay_slot_do_not_fall_through(self):
        for op in ('jr $ra', 'j 0x80002000', 'b L_80001000', 'beq $a0, $a0, L_80001000',
                   'bgez $zero, L_80001000'):
            with self.subTest(op=op):
                self.setup_sections([[('first', 0x80001000), ('next', 0x80001008)]])
                source = self.funcs / "a.c"
                text = function('first', [(0x80001000, op), (0x80001004, 'nop')], 'return;') + \
                    function('next', [(0x80001008, 'break')], 'return;')
                source.write_text(text)
                self.assertEqual(link_fallthroughs(self.funcs, self.symbols), [])
                self.assertEqual(source.read_text(), text)

    def test_conditional_branch_has_a_fallthrough_edge(self):
        self.setup_sections([[('first', 0x80001000), ('next', 0x80001008)]])
        (self.funcs / 'a.c').write_text(function('first', [(0x80001000, 'bne $a0, $a1, L_80001000'),
                                                         (0x80001004, 'nop')]) +
                                      function('next', [(0x80001008, 'break')], 'return;'))
        self.assertEqual(link_fallthroughs(self.funcs, self.symbols)[0]['next'], 'next')

    def test_branch_into_final_delay_slot_preserves_its_fallthrough(self):
        self.setup_sections([[('first', 0x80001000), ('next', 0x80001010)]])
        (self.funcs / 'a.c').write_text(function('first', [(0x80001000, 'bne $a0, $a1, L_8000100C'),
                                                         (0x80001004, 'nop'),
                                                         (0x80001008, 'jr $ra'),
                                                         (0x8000100C, 'nop')]) +
                                      function('next', [(0x80001010, 'break')], 'return;'))
        self.assertEqual(link_fallthroughs(self.funcs, self.symbols)[0]['next'], 'next')

    def test_overlay_namespaces_and_nonadjacent_entries_are_separate(self):
        self.setup_sections([[('first', 0x80001000), ('later', 0x80001008)], [('other', 0x80001004)]])
        text = function('first', [(0x80001000, 'lui $a0, 1')]) + \
            function('later', [(0x80001008, 'break')], 'return;') + \
            function('other', [(0x80001004, 'break')], 'return;')
        (self.funcs / 'a.c').write_text(text)
        self.assertEqual(link_fallthroughs(self.funcs, self.symbols), [])

    def test_adjacent_entry_can_live_in_another_output_file(self):
        self.setup_sections([[('first', 0x80001000), ('next', 0x80001004)]])
        (self.funcs / 'a.c').write_text(function('first', [(0x80001000, 'lui $a0, 1')]))
        (self.funcs / 'b.c').write_text(function('next', [(0x80001004, 'break')], 'return;'))
        self.assertEqual(link_fallthroughs(self.funcs, self.symbols)[0]['next'], 'next')
