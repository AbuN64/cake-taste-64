import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste import TasteError
from cake_taste.recomp.splat import Layout, Segment
from cake_taste.repro import bounded
from cake_taste.repro.closure import closure, function_vrams, index_bodies, render_unit, resolve_target

HEAD = "RECOMP_FUNC void {}(uint8_t* rdram, recomp_context* ctx) {{\n"


def body(name, *calls, indirect=False):
    lines = [HEAD.format(name)] + [f"    {c}(rdram, ctx);\n" for c in calls]
    if indirect:
        lines.append("    LOOKUP_FUNC(ctx->r25)(rdram, ctx);\n")
    return "".join(lines) + "    return;\n;}\n"


SYMBOLS = """[[section]]
name = "a"
functions = [
    { name = "root", vram = 0x80001000, size = 0x10 },
    { name = "leaf", vram = 0x80001010, size = 0x10 },
    { name = "far", vram = 0x80001020, size = 0x10 },
]
"""


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "funcs").mkdir()
        (self.root / "funcs" / "funcs_0.c").write_text(
            '#include "recomp.h"\n' + body("root", "leaf", "osThing_recomp", "far", indirect=True) + body("leaf")
        )
        (self.root / "funcs" / "funcs_1.c").write_text(body("far", "leaf"))
        (self.root / "symbols.toml").write_text(SYMBOLS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_closure_selects_callees_and_stubs_bodiless_and_boundary(self):
        bodies = index_bodies(self.root / "funcs")
        assert set(bodies) == {"root", "leaf", "far"}
        found = closure("root", bodies)
        assert set(found.bodies) == {"root", "leaf", "far"}
        assert found.stubs == {"osThing_recomp": "no generated body"} and found.indirect == ["root"]
        bounded_found = closure("root", bodies, boundary={"far"})
        assert set(bounded_found.bodies) == {"root", "leaf"} and bounded_found.stubs["far"] == "boundary"
        with self.assertRaises(TasteError):
            closure("root", bodies, limit=1)

    def test_unit_has_stub_returns_and_vram_table(self):
        vrams = function_vrams(self.root / "symbols.toml")
        found = closure("root", index_bodies(self.root / "funcs"), boundary={"far"})
        unit = render_unit(found, vrams, '#include "recomp.h"', True, {"far": -1})
        assert '#include "bounded.h"' in unit and '#include "trace.h"' in unit
        assert 'probe_stub("far"); ctx->r2 = (gpr)(int64_t)-1;' in unit
        assert '{0x80001000u, root, "root"}' in unit and "osThing_recomp, " not in unit.split("probe_funcs")[1]

    def test_resolve_target_by_name_or_address(self):
        vrams = function_vrams(self.root / "symbols.toml")
        assert resolve_target("leaf", vrams) == "leaf" and resolve_target("0x80001020", vrams) == "far"
        for bad in ("nope", "0x80009999"):
            with self.assertRaises(TasteError):
                resolve_target(bad, vrams)


class MemoryTests(unittest.TestCase):
    def test_kseg_tlb_and_bounds(self):
        memory = bounded.Memory(0x1000, [{"vaddr": 0x200000, "paddr": 0x100, "size": 0x100}])
        assert memory.phys(0x80000010) == 0x10 and memory.phys(0xA0000020) == 0x20 and memory.phys(0x200004) == 0x104
        for bad in (0x300000, 0x80000FFE):
            with self.assertRaises(TasteError):
                memory.phys(bad, 4)

    def test_host_order_is_a_word_swap_involution(self):
        assert bounded.host_order(b"\x01\x02\x03\x04") == b"\x04\x03\x02\x01"
        data = bytes(range(16))
        assert bounded.host_order(bounded.host_order(data)) == data

    def test_seed_layout_copies_resident_kseg0_segments_only(self):
        rom = bytes(range(64))
        layout = Layout("x", (
            Segment("header", "bin", 0, 0, None),
            Segment("code", "code", 16, 0x80000100, None),
            Segment("ovl", "code", 32, 0x80000100, None, {"overlay": True}),
            Segment("data", "data", 48, 0x80000200, 16),
        ))
        memory = bounded.Memory(0x400, [])
        loaded = bounded.seed_layout(memory, layout, rom)
        assert [item["name"] for item in loaded] == ["code", "data"]
        assert memory.read(0x80000100, 16) == rom[16:32] and memory.read(0x80000200, 16) == rom[48:64]

    def test_registers_aliases_sign_extension_and_floats(self):
        regs = bounded.registers({"registers": {"a0": "0x80600000", "f12": {"f32": 1.0}, "status": 1}}, 0x803FFFC0)
        assert regs["r4"] == 0xFFFFFFFF80600000 and regs["r29"] == 0xFFFFFFFF803FFFC0
        assert regs["f12"] == 0x3F800000 and regs["status"] == 1
        with self.assertRaises(TasteError):
            bounded.registers({"registers": {"q9": 1}}, 0)

    def test_changed_words_and_checks(self):
        before = bytes(8)
        after = b"\x00\x00\x00\x00" + struct.pack(">f", 0.1)
        memory = bounded.Memory(8, [])
        memory.data[:] = after
        assert bounded.changed_words(before, after) == [
            (0x80000004, 0, struct.unpack(">I", after[4:])[0])
        ]
        results = bounded.evaluate(
            [{"addr": "0x80000004", "type": "f32", "expect": 0.1}, {"reg": "v0", "type": "s32", "expect": -1},
             {"addr": "0x80000000", "type": "u16", "expect": 1}],
            memory, {"r2": 0xFFFFFFFFFFFFFFFF},
        )
        assert [r["ok"] for r in results] == [True, True, False]


class BoundedProbeTests(unittest.TestCase):
    def test_public_flow_with_a_fake_harness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gen = root / "gen" / "v1"
            (gen / "funcs").mkdir(parents=True)
            (gen / "funcs" / "funcs_0.c").write_text(body("root", "leaf") + body("leaf"))
            (gen / "symbols.toml").write_text(SYMBOLS)
            facts = {"version": "v1", "boot": {"rdram_size": 0x1000, "sp": 0x80000800}}
            (root / "facts.json").write_text(json.dumps(facts))
            request = root / "request.json"
            request.write_text(json.dumps({
                "version": "v1", "function": "0x80001000", "stack_window": 256, "registers": {"a0": "0x80000100"},
                "seed": {"layout": False, "memory": [{"addr": "0x80000100", "u32": [7]}]},
                "outputs": [{"name": "obj", "addr": "0x80000100", "size": 8}],
                "checks": [{"addr": "0x80000104", "type": "s32", "expect": 9}],
            }))
            settings = type("S", (), {"out": lambda self, v: gen, "recomp_include": None, "trace_mode": True})()
            profile = type("P", (), {
                "section": lambda self, name: {"bounded": {"facts": "facts.json", "host_dir": "host",
                                                           "runtime_include": "inc", "work_root": "work"}},
                "resolve_path": lambda self, value: root / value,
            })()
            calls = []

            def fake_run(profile, *, name, function, command, artifacts=(), **_):
                calls.append(command)
                run_dir = root / "runs" / str(len(calls))
                run_dir.mkdir(parents=True)
                if command[0].endswith("harness"):
                    image = bytearray(bounded.host_order(Path(command[1]).read_bytes()))
                    image[0x104:0x108] = struct.pack(">i", 9)
                    (run_dir / "rdram.bin").write_bytes(bounded.host_order(bytes(image)))
                    kept = [line for line in Path(command[2]).read_text().splitlines() if not line.startswith("entry")]
                    (run_dir / "regs.txt").write_text("\n".join(kept) + "\nr2 0x0\nr3 0x0\nf0 0x0\n")
                    (run_dir / "trace.txt").write_text("call root\ncall leaf\nreturn\n")
                return {"receipt": str(run_dir / "receipt.json"),
                        "status": {"phase": "passed", "run_dir": str(run_dir), "artifacts": []}}

            with patch.object(bounded, "load_recomp", return_value=settings), \
                 patch.object(bounded, "run_command", side_effect=fake_run):
                result = bounded.bounded_probe(profile, function=None, script=request)
            probe = result["probe"]
            assert calls[0][0] == "cc" and any(part.endswith("closure.c") for part in calls[0])
            assert probe["closure"] == ["leaf", "root"] and probe["calls"] == 2
            assert probe["checked"] == 1 and probe["differences"] == 0 and probe["changed_words"] == 1
            assert (Path(result["receipt"]).parent / "probe.json").is_file()
            assert Path(probe["outputs"][1]["path"]).read_bytes() == struct.pack(">ii", 7, 9)


if __name__ == "__main__":
    unittest.main()
