import json
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path

from test_recomp import build

from cake_taste import TasteError
from cake_taste.recomp.generate import _reason, _rsp, load_layout, write_symbols
from cake_taste.recomp.settings import load
from cake_taste.recomp.supplement import read_sections


class SupplementTests(unittest.TestCase):
    def row(self):
        return {"name": "late_code", "rom": 0x5800, "vram": 0x80600000, "size": 16,
                "source": "fixture disassembly, four instructions",
                "functions": [{"name": "late_fn", "offset": 0, "size": 16}]}

    def test_explicit_section_reaches_symbols_and_analysis_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = build(root)
            path = root / "sections.json"
            path.write_text(json.dumps([self.row()]))
            rom = bytearray((root / "rom.z64").read_bytes())
            rom[0x5800:0x5810] = bytes(range(16))
            (root / "rom.z64").write_bytes(rom)
            settings = replace(load(profile), function_sections={"v1": path})
            result = write_symbols(settings, "v1")
            tables = tomllib.loads(Path(result["path"]).read_text())["section"]
            extra = next(s for s in tables if s["name"] == "late_code")
            self.assertEqual(extra["functions"], [{"name": "late_fn", "vram": 0x80600000, "size": 16}])
            flat = (root / "gen/v1/rom.flat").read_bytes()
            self.assertEqual(flat[extra["rom"]:extra["rom"] + 16], bytes(range(16)))
            self.assertEqual(result["functions"], 6)

    def test_missing_entry_can_be_added_inside_an_existing_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = build(root)
            path = root / "sections.json"
            row = {"name": "boot_code", "rom": 0x1000, "vram": 0x80200400, "size": 0x1000,
                   "source": "fixture instruction boundaries", "functions": [
                       {"name": "missing", "offset": 0x20, "size": 8}]}
            path.write_text(json.dumps([row]))
            settings = replace(load(profile), function_sections={"v1": path})
            result = write_symbols(settings, "v1")
            self.assertEqual(result["functions"], 6)
            tables = tomllib.loads(Path(result["path"]).read_text())["section"]
            boot = next(t for t in tables if t["name"] == "boot_code")
            self.assertEqual(boot["functions"][-1], {"name": "missing", "vram": 0x80200420, "size": 8})
            row["vram"] += 4
            path.write_text(json.dumps([row]))
            with self.assertRaises(TasteError):
                write_symbols(settings, "v1")

    def test_invalid_boundaries_and_missing_source_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sections.json"
            for mutate in (lambda r: r.update(source=""), lambda r: r.update(size=3),
                           lambda r: r["functions"][0].update(size=20),
                           lambda r: r["functions"].append(dict(r["functions"][0]))):
                row = self.row()
                mutate(row)
                path.write_text(json.dumps([row]))
                with self.assertRaises(TasteError):
                    read_sections(path)

    def test_function_extent_is_explicit_and_stays_inside_owning_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(load(build(root)), function_sizes={"v1": {0x80200400: 16}})
            result = write_symbols(settings, "v1")
            functions = tomllib.loads(Path(result["path"]).read_text())["section"][0]["functions"]
            self.assertEqual(functions[0]["size"], 16)
            self.assertEqual(functions[1]["vram"], 0x80200408)
            for override in ({0x80200400: 0x1004}, {0x80900000: 16}):
                with self.assertRaises(TasteError):
                    write_symbols(replace(settings, function_sizes={"v1": override}), "v1")

    def test_rsp_dispatch_targets_reach_recompiler_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(load(build(root)), rsp_indirect_targets=(0x1118, 0x12D0))
            _rsp(settings, "v1", load_layout(settings, "v1"))
            config = tomllib.loads((root / "gen/v1/rsp.toml").read_text())
            self.assertEqual(config["extra_indirect_branch_targets"], [0x1118, 0x12D0])

    def test_rsp_execution_is_independent_of_storage_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(load(build(root)), rsp_execution_vram=0x04001100)
            _rsp(settings, "v1", load_layout(settings, "v1"))
            config = tomllib.loads((root / "gen/v1/rsp.toml").read_text())
            self.assertEqual(config["text_address"], 0x04001100)
            with self.assertRaises(TasteError):
                _rsp(replace(settings, rsp_execution_vram=0xDFE30), "v1", load_layout(settings, "v1"))

    def test_refusal_keeps_the_concrete_cause(self):
        log = "No function found for jal target: 0x80600000\n"
        log += "Error in recompiling root, clearing output file\nError recompiling root\n"
        self.assertEqual(_reason(log, "root"), "No function found for jal target: 0x80600000")


if __name__ == "__main__":
    unittest.main()
