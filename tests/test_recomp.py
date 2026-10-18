import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from cake_taste import TasteError
from cake_taste.recomp.cli import run
from cake_taste.recomp.settings import load
from cake_taste.recomp.splat import parse_layout

YAML = """name: "Game"
sha1: 0123456789abcdef0123456789abcdef01234567
options:
  basename: Game
segments:
  - name: "retained_0"
    type: bin
    start: 0x0
    vram: 0x0
  - name: boot_code
    type: code
    start: 0x1000
    vram: 0x80200400
    subsegments:
      - [0x1000, c, "first"]
      - [0x1010, .data, "rodata/x"]
  - name: ovl_a
    type: code
    start: 0x2000
    vram: 0x80400000
    exclusive_ram_id: slot
    subsegments:
      - [0x2000, asm, "ovl_fn"]
  - name: ovl_b
    type: code
    start: 0x3000
    vram: 0x80400000
    exclusive_ram_id: slot
    subsegments:
      - [0x3000, asm, "ovl_fn2"]
  - name: rsp_code
    type: resource
    start: 0x4000
    vram: 0x80500000
    execution_vram: 0x4001080
    subsegments:
      - [0x4000, .data, "r.s"]
  - name: tail
    type: bin
    start: 0x5000
  - [0x6000]
"""

ASM = """.include "macro.inc"
nonmatching first, 0x8
glabel first
    /* 1000 80200400 24020001 */  addiu $v0, $zero, 0x1
    /* 1004 80200404 03E00008 */  jr $ra
endlabel first
nonmatching second, 0x4
glabel second
    /* 1008 80200408 03E00008 */  jr $ra
endlabel second
"""


def row(rom, vram):
    return f"    /* {rom:X} {vram:08X} 03E00008 */  jr $ra\n"


def build(root: Path, tool_body: str = "") -> object:
    decomp = root / "decomp"
    (decomp / "versions/v1").mkdir(parents=True)
    (decomp / "asm/v1").mkdir(parents=True)
    (decomp / "versions/v1/g.yaml").write_text(YAML)
    (decomp / "versions/v1/symbol_addrs.txt").write_text("second = 0x80200408; // type:func\nD_1 = 0x80200408;\n")
    (decomp / "asm/v1/a.s").write_text(ASM)
    (decomp / "asm/v1/b.s").write_text(
        "glabel ovl_fn\n" + row(0x2000, 0x80400000) + "endlabel ovl_fn\n"
        "glabel ovl_fn2\n" + row(0x3000, 0x80400000) + "endlabel ovl_fn2\n"
        "glabel second\n" + row(0x300C, 0x8040000C) + "endlabel second\n"
    )
    rom = root / "rom.z64"
    rom.write_bytes(bytes(8) + (0x80200400).to_bytes(4, "big") + bytes(0x6000))
    tool = root / "n64recomp"
    tool.write_text(tool_body or "#!/bin/sh\necho 'Function count: 5'\necho x > funcs/f.c\n")
    tool.chmod(0o755)
    rsp = root / "rsprecomp"
    rsp.write_text('#!/bin/sh\necho code > "$(sed -n \'s/^output_file_path = "\\(.*\\)"/\\1/p\' "$1")"\n')
    rsp.chmod(0o755)

    class Profile:
        def section(self, name):
            return {
                "decomp_root": "decomp",
                "layout_glob": "versions/{version}/*.yaml",
                "symbols_file": "versions/{version}/symbol_addrs.txt",
                "asm_dir": "asm/{version}",
                "roms": {"v1": "rom.z64"},
                "gen_root": "gen",
                "tools": {"n64recomp": "n64recomp", "rsprecomp": "rsprecomp"},
                "trace_mode": True,
                "recomp_include": '#include "probe.h"',
                "stubs": ["osStub"],
                "rsp_audio": {"segment": "rsp_code", "function": "rsp_fn"},
            }

        def resolve_path(self, value):
            return root / value

    return Profile()


class RecompTests(unittest.TestCase):
    def test_layout_parser_reads_segments_and_subsegments(self):
        with tempfile.TemporaryDirectory() as t:
            build(Path(t))
            layout = parse_layout(Path(t) / "decomp/versions/v1/g.yaml")
        self.assertEqual(layout.sha1, "0123456789abcdef0123456789abcdef01234567")
        names = [s.name for s in layout.segments]
        self.assertEqual(names[:3], ["retained_0", "boot_code", "ovl_a"])
        boot = layout.segments[1]
        self.assertEqual(boot.subsegments[0], (0x1000, "c", "first"))
        self.assertTrue(layout.segments[2].overlay and not boot.overlay)
        self.assertEqual(layout.extent(boot), 0x1000)

    def test_symbols_split_overlays_with_shared_vram_and_word_sizes(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            result = run(profile, ("symbols", "--version", "v1"))
            data = tomllib.loads(Path(result["path"]).read_text())
        sections = {s["name"]: s for s in data["section"]}
        self.assertEqual(list(sections), ["boot_code", "ovl_a", "ovl_b"])
        self.assertEqual(sections["ovl_a"]["vram"], sections["ovl_b"]["vram"])
        self.assertNotEqual(sections["ovl_a"]["rom"], sections["ovl_b"]["rom"])
        funcs = sections["boot_code"]["functions"]
        self.assertEqual([f["name"] for f in funcs], ["first", "second"])
        self.assertTrue(all(f["size"] % 4 == 0 for s in sections.values() for f in s["functions"]))
        self.assertEqual(result["entrypoint"], 0x80200400)
        self.assertEqual(result["overlays"], ["ovl_a", "ovl_b"])
        self.assertEqual(result["duplicate_names"], 1)
        self.assertIn("second_8040000C", [f["name"] for f in sections["ovl_b"]["functions"]])

    def test_size_disagreeing_with_declaration_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            asm = Path(t) / "decomp/asm/v1/a.s"
            asm.write_text(asm.read_text().replace("first, 0x8", "first, 0xC"))
            with self.assertRaises(TasteError):
                run(profile, ("symbols", "--version", "v1"))

    def test_generate_writes_config_runs_tools_and_freezes_hashes(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            result = run(profile, ("generate", "--version", "v1"))
            out = Path(t) / "gen/v1"
            config = tomllib.loads((out / "recomp.toml").read_text())
            manifest = json.loads((out / "MANIFEST.json").read_text())
            self.assertTrue((out / "relocatable_sections.txt").is_file())
        self.assertNotIn("entrypoint", config["input"])
        self.assertTrue(config["input"]["trace_mode"])
        self.assertEqual(config["input"]["recomp_include"], '#include "probe.h"')
        self.assertEqual(config["patches"]["stubs"], ["osStub"])
        self.assertEqual(result["function_count_reported"], 5)
        self.assertIn("funcs/f.c", manifest["files"])
        self.assertIn("rsp/rsp_fn.cpp", manifest["files"])
        self.assertEqual(manifest["rsp"]["segment"], "rsp_code")

    def test_refused_function_is_ignored_and_recorded_with_reason(self):
        body = (
            "#!/bin/sh\n"
            "if grep -q '\"bad\"' \"$1\"; then echo 'Function count: 5'; echo x > funcs/f.c; exit 0; fi\n"
            "echo 'Unhandled instruction: cache' >&2\necho 'Error recompiling bad' >&2\nexit 1\n"
        )
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t), body)
            result = run(profile, ("generate", "--version", "v1", "--refused", str(Path(t) / "r.tsv")))
            tsv = (Path(t) / "r.tsv").read_text()
        self.assertEqual(result["refused"], 1)
        self.assertEqual(tsv, "name\treason\nbad\tUnhandled instruction: cache\n")

    def test_crashing_function_is_located_by_bisection_and_refused(self):
        body = (
            "#!/bin/sh\n"
            "if grep -q 'ignored = .*\"first\"' \"$1\"; then echo 'Function count: 5'; echo x > funcs/f.c; exit 0; fi\n"
            "kill -SEGV $$\n"
        )
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t), body)
            result = run(profile, ("generate", "--version", "v1"))
            tsv = (Path(t) / "gen/v1/refused.tsv").read_text()
            self.assertFalse((Path(t) / "gen/v1/probe.toml").exists())
        self.assertEqual(result["refused"], 1)
        self.assertIn("first\tN64Recomp died with signal 11 while analysing first", tsv)

    def test_unattributable_failure_is_a_named_error(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t), "#!/bin/sh\necho boom >&2\nexit 2\n")
            with self.assertRaises(TasteError) as raised:
                run(profile, ("generate", "--version", "v1"))
        self.assertEqual(raised.exception.key, "recomp.n64recomp")

    def test_flat_image_gives_code_and_data_one_rom_to_vram_delta(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            rom = Path(t) / "rom.z64"
            data = bytearray(rom.read_bytes())
            data[0x1010:0x1014] = b"\xde\xad\xbe\xef"
            data[0x2000:0x2004] = b"\x01\x02\x03\x04"
            rom.write_bytes(bytes(data))
            run(profile, ("symbols", "--version", "v1"))
            image = (Path(t) / "gen/v1/rom.flat").read_bytes()
            symbols = tomllib.loads((Path(t) / "gen/v1/symbols.toml").read_text())
        boot = symbols["section"][0]
        self.assertEqual(boot["rom"], 0)
        self.assertEqual(image[0x10:0x14], b"\xde\xad\xbe\xef")
        overlay = symbols["section"][1]
        self.assertEqual(image[overlay["rom"] : overlay["rom"] + 4], b"\x01\x02\x03\x04")

    def test_explicit_entrypoint_must_sit_at_image_offset_0x1000(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            section = profile.section
            profile.section = lambda name: {**section(name), "entrypoint": 0x80200400}
            with self.assertRaises(TasteError) as raised:
                run(profile, ("symbols", "--version", "v1"))
        self.assertEqual(raised.exception.key, "recomp.entrypoint")

    def test_facts_carry_sha1_entrypoint_and_overlay_table(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            out = Path(t) / "oracle.json"
            facts = run(profile, ("facts", "--version", "v1", "--output", str(out)))
            self.assertEqual(json.loads(out.read_text()), facts)
        self.assertEqual(facts["entrypoint"], 0x80200400)
        self.assertEqual(facts["header_entrypoint"], 0x80200400)
        self.assertEqual([o["name"] for o in facts["overlays"]], ["ovl_a", "ovl_b"])
        self.assertEqual(facts["rom_sha1"], "0123456789abcdef0123456789abcdef01234567")

    def test_profile_section_rejects_unknown_and_missing_keys(self):
        class Bad:
            def section(self, name):
                return {"decomp_root": "x", "nonsense": 1}

            def resolve_path(self, value):
                return Path(value)

        with self.assertRaises(TasteError):
            load(Bad())


if __name__ == "__main__":
    unittest.main()
