import json
import struct
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

FUNCTIONS = [  # name, section, vram, size: the decomp's link as the recompiler reads it
    ("first", "boot_code", 0x80200400, 8),
    ("second", "boot_code", 0x80200408, 4),
    ("ovl_fn", "ovl_a", 0x80400000, 4),
    ("ovl_fn2", "ovl_b", 0x80400000, 4),
    ("second", "ovl_b", 0x8040000C, 4),
]
SECTIONS = {"boot_code": 0x80200400, "ovl_a": 0x80400000, "ovl_b": 0x80400000}


def write_elf(path: Path, functions=FUNCTIONS, sections=SECTIONS) -> None:
    """A minimal big-endian ELF32 with one PROGBITS section per segment and a symbol table of sized functions."""
    shnames = b"\0" + b"".join(b"." + n.encode() + b"\0" for n in sections) + b".symtab\0.strtab\0.shstrtab\0"
    strtab, symtab = b"\0", bytes(16)
    index = {name: i + 1 for i, name in enumerate(sections)}
    for name, section, vram, size in functions:
        symtab += struct.pack(">IIIBBH", len(strtab), vram, size, 0x12, 0, index[section])
        strtab += name.encode() + b"\0"
    body = symtab + strtab + shnames
    shoff = 52 + len(body)
    headers = [bytes(40)]
    name_at = 1
    for name, addr in sections.items():
        headers.append(struct.pack(">10I", name_at, 1, 6, addr, 52, 0, 0, 0, 4, 0))
        name_at += len(name) + 2
    symtab_name, strtab_name, shstr_name = name_at, name_at + 8, name_at + 16
    count = len(sections)
    headers.append(struct.pack(">10I", symtab_name, 2, 0, 0, 52, len(symtab), count + 2, 1, 4, 16))
    headers.append(struct.pack(">10I", strtab_name, 3, 0, 0, 52 + len(symtab), len(strtab), 0, 0, 1, 0))
    headers.append(struct.pack(">10I", shstr_name, 3, 0, 0, 52 + len(symtab) + len(strtab), len(shnames), 0, 0, 1, 0))
    ident = b"\x7fELF\x01\x02\x01" + bytes(9)
    header = ident + struct.pack(">HHIIIIIHHHHHH", 2, 8, 1, 0, 0, shoff, 0, 52, 0, 0, 40, len(headers), count + 3)
    path.write_bytes(header + body + b"".join(headers))


def build(root: Path, tool_body: str = "", rsp_config: dict | None = None) -> object:
    decomp = root / "decomp"
    (decomp / "versions/v1").mkdir(parents=True)
    (decomp / "versions/v1/g.yaml").write_text(YAML)
    (decomp / "versions/v1/symbol_addrs.txt").write_text("second = 0x80200408; // type:func\nD_1 = 0x80200408;\n")
    write_elf(decomp / "versions/v1/link.elf")
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
                "elf": "versions/{version}/link.elf",
                "roms": {"v1": "rom.z64"},
                "gen_root": "gen",
                "tools": {"n64recomp": "n64recomp", "rsprecomp": "rsprecomp"},
                "trace_mode": True,
                "recomp_include": '#include "probe.h"',
                "stubs": ["osStub"],
                "rsp_audio": rsp_config or {"segment": "rsp_code", "function": "rsp_fn"},
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

    def test_elf_section_without_a_layout_segment_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            write_elf(Path(t) / "decomp/versions/v1/link.elf", sections={**SECTIONS, "stray": 0x80600000},
                      functions=[*FUNCTIONS, ("lost", "stray", 0x80600000, 4)])
            with self.assertRaises(TasteError) as caught:
                run(profile, ("symbols", "--version", "v1"))
            self.assertIn(".stray names no layout segment", str(caught.exception))

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

    def test_refused_static_is_declared_with_a_decomp_sourced_extent_then_ignored(self):
        body = (
            "#!/bin/sh\n"
            "if grep -q '\"static_0_8020040C\"' \"$1\"; then\n"
            "  grep -q static_0_8020040C symbols.toml || { echo 'Function static_0_8020040C is set as ignored"
            " in the config file but does not exist!' >&2; exit 1; }\n"
            "  echo 'Function count: 5'; echo x > funcs/f.c; exit 0\nfi\n"
            "echo 'Unhandled cop0 register in mtc0: 14' >&2\necho 'Error recompiling static_0_8020040C' >&2\nexit 1\n"
        )
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t), body)
            result = run(profile, ("generate", "--version", "v1"))
            out = Path(t) / "gen/v1"
            statics = (out / "statics.tsv").read_text().splitlines()
            symbols = tomllib.loads((out / "symbols.toml").read_text())
            refused = (out / "refused.tsv").read_text()
        self.assertEqual(result["refused"], 1)
        self.assertEqual(refused, "name\treason\nstatic_0_8020040C\tUnhandled cop0 register in mtc0: 14\n")
        fields = statics[1].split("\t")
        self.assertEqual(fields[:3], ["static_0_8020040C", "0x8020040C", "0x4"])
        self.assertIn("next split rodata/x at 0x80200410", fields[3])
        funcs = {f["name"]: f for f in symbols["section"][0]["functions"]}
        self.assertEqual(funcs["static_0_8020040C"]["size"], 4)

    def test_rsp_microcode_may_be_a_sourced_rom_span_outside_the_layout(self):
        span = {"function": "rsp_fn", "rom": 0x4000, "size": 0x20, "execution_vram": 0x1080, "source": "task fields",
                "extra_indirect_branch_targets": [0x1100]}
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t), rsp_config=span)
            result = run(profile, ("generate", "--version", "v1"))
            config = (Path(t) / "gen/v1/rsp.toml").read_text()
        self.assertEqual(result["rsp"]["segment"], "rom_4000")
        for line in ("text_offset = 0x4000", "text_size = 0x20", "text_address = 0x1080",
                     "extra_indirect_branch_targets = [4352]"):
            self.assertIn(line, config)

    def test_rsp_rom_span_needs_a_source_and_an_execution_address(self):
        for bad in ({"function": "f", "rom": 0x4000, "size": 0x20, "execution_vram": 0x1080},
                    {"function": "f", "rom": 0x4000, "size": 0x20, "source": "x"},
                    {"function": "f", "rom": 0x4000, "segment": "rsp_code", "size": 0x20}):
            with tempfile.TemporaryDirectory() as t, self.assertRaises(TasteError):
                run(build(Path(t), rsp_config=bad), ("symbols", "--version", "v1"))

    def test_generated_c_that_cannot_compile_refuses_its_function(self):
        body = (
            "#!/bin/sh\n"
            "echo 'Function count: 5'\n"
            "ok='RECOMP_FUNC void fine(uint8_t* rdram, recomp_context* ctx) {\n  goto L_1;\nL_1:\n  return;\n;}'\n"
            "zero='RECOMP_FUNC void zero_dest(uint8_t* rdram, recomp_context* ctx) {\n  0 = do_ldr(rdram);\n;}'\n"
            "away='RECOMP_FUNC void away(uint8_t* rdram, recomp_context* ctx) {\n  goto L_800F1010;\n;}'\n"
            "printf '%s\\n' \"$ok\" > funcs/f.c\n"
            "grep -q '\"zero_dest\"' \"$1\" || printf '%s\\n' \"$zero\" >> funcs/f.c\n"
            "grep -q '\"away\"' \"$1\" || printf '%s\\n' \"$away\" >> funcs/f.c\n"
        )
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t), body)
            result = run(profile, ("generate", "--version", "v1"))
            tsv = (Path(t) / "gen/v1/refused.tsv").read_text()
        self.assertEqual(result["refused"], 2)
        self.assertIn("zero_dest\tgenerated C assigns to the zero register", tsv)
        self.assertIn("away\tgenerated C branches to L_800F1010, a label outside the function", tsv)
        self.assertNotIn("fine\t", tsv)

    def test_calls_into_the_runtime_are_declared_for_the_generated_code(self):
        body = ("#!/bin/sh\necho 'Function count: 5'\n"
                "printf 'RECOMP_FUNC void a(uint8_t* rdram, recomp_context* ctx) {\\n"
                "  osYieldThread_recomp(rdram, ctx);\\n  osYieldThread_recomp(rdram, ctx);\\n"
                "  osStartThread_recomp(rdram, ctx);\\n;}\\n' > funcs/f.c\n")
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t), body)
            section = profile.section
            profile.section = lambda name: {**section(name), "symbol_names": {"v1": {"0x80200408": "osPiRawStartDma"}}}
            run(profile, ("generate", "--version", "v1"))
            header = (Path(t) / "gen/v1/funcs/recomp_decls.h").read_text()
            table = (Path(t) / "gen/v1/funcs/replaced_funcs.inl").read_text()
        self.assertEqual(header.count("osYieldThread_recomp"), 1)
        self.assertIn("void osStartThread_recomp(uint8_t* rdram, recomp_context* ctx);", header)
        self.assertIn("void osPiRawStartDma_recomp(uint8_t* rdram, recomp_context* ctx);", header)
        self.assertIn("{ 0x80200408u, osPiRawStartDma_recomp },", table)
        self.assertTrue(table.rstrip().endswith("{ 0u, nullptr }"))

    def test_backward_branches_get_a_service_point_forward_ones_do_not(self):
        body = ("#!/bin/sh\necho 'Function count: 5'\n"
                "printf 'RECOMP_FUNC void spin(uint8_t* rdram, recomp_context* ctx) {\\n"
                "L_1:\\n  if (x) {\\n    goto L_1;\\n  }\\n  if (y) {\\n    goto L_2;\\n  }\\nL_2:\\n  return;\\n;}\\n'"
                " > funcs/f.c\n")
        with tempfile.TemporaryDirectory() as t:
            result = run(build(Path(t), body), ("generate", "--version", "v1"))
            code = (Path(t) / "gen/v1/funcs/f.c").read_text()
            manifest = json.loads((Path(t) / "gen/v1/MANIFEST.json").read_text())
        self.assertIn("TRACE_LOOP() goto L_1;", code)
        self.assertNotIn("TRACE_LOOP() goto L_2;", code)
        self.assertEqual(manifest["loop_service_points"], 1)
        self.assertEqual(result["refused"], 0)

    def test_symbol_names_may_carry_their_source_and_must_not_be_empty(self):
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            section = profile.section
            profile.section = lambda name: {**section(name), "symbol_names": {"v1": {
                "0x80200400": {"name": "osFirst", "source": "same body as the donor's osFirst"},
                "0x80200408": "osSecond"}}}
            run(profile, ("symbols", "--version", "v1"))
            data = tomllib.loads((Path(t) / "gen/v1/symbols.toml").read_text())
            names = [f["name"] for f in data["section"][0]["functions"]]
            self.assertEqual(names, ["osFirst", "osSecond"])
            for bad in ({"0x80200400": {"name": "x"}}, {"0x80200400": ""},
                        {"0x80200400": {"name": "x", "source": ""}}):
                profile.section = lambda name, bad=bad: {**section(name), "symbol_names": {"v1": bad}}
                with self.assertRaises(TasteError):
                    run(profile, ("symbols", "--version", "v1"))

    def test_a_game_function_named_like_a_host_library_function_gets_another_c_name(self):
        """The host compiles the generated funcs.h beside libc and SDL headers: a game `frexp` made it fail to build."""
        from cake_taste.recomp.generate import config_toml
        from cake_taste.recomp.model import c_name
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t))
            section = profile.section
            profile.section = lambda name: {**section(name), "symbol_names": {"v1": {
                "0x80200400": "frexp", "0x80200408": "sqrt"}}}
            run(profile, ("symbols", "--version", "v1"))
            data = tomllib.loads((Path(t) / "gen/v1/symbols.toml").read_text())
            names = [f["name"] for f in data["section"][0]["functions"]]
            self.assertEqual(names, ["game_frexp", "sqrt"])  # sqrt: N64Recomp reimplements it
            from cake_taste.recomp.settings import load
            config = tomllib.loads(config_toml(load(profile), "v1", None, ["frexp", "osStub"], []))
            self.assertEqual(config["patches"]["ignored"], ["game_frexp", "osStub"])
        self.assertEqual(c_name("frexp"), "game_frexp")
        self.assertEqual((c_name("Frexp_Init"), c_name("game_frexp")), ("Frexp_Init", "game_frexp"))

    def test_static_inside_a_known_function_is_a_named_error(self):
        body = "#!/bin/sh\necho 'Error recompiling static_0_80200404' >&2\nexit 1\n"
        with tempfile.TemporaryDirectory() as t:
            profile = build(Path(t), body)
            with self.assertRaises(TasteError) as raised:
                run(profile, ("generate", "--version", "v1"))
        self.assertIn("inside first", str(raised.exception))

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
