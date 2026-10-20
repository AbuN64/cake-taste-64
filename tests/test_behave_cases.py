import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste import TasteError
from cake_taste.recomp import candidate as cand
from cake_taste.repro import bounded, cases
from cake_taste.repro.closure import closure, function_vrams, limit_depth, render_unit

HEAD = "RECOMP_FUNC void {}(uint8_t* rdram, recomp_context* ctx) {{\n"


def body(name, *calls):
    return HEAD.format(name) + "".join(f"    {c}(rdram, ctx);\n" for c in calls) + "    return;\n;}\n"


SYMBOLS = """[[section]]
name = "a"
functions = [
    { name = "root", vram = 0x80001000, size = 0x10 },
    { name = "leaf", vram = 0x80001010, size = 0x10 },
    { name = "deep", vram = 0x80001020, size = 0x10 },
]
"""

EVENTS = """case c0
call root 80A00000 1 0 0 0 0 0 0 3F800000 0
call leaf 5 0 0 0 0 0 0 0 0 0
st 80A00004 4 5
rd 80A00008 4
uns 80100000 4
ret 7 0 0 0
saved r17
net 80A00004 5
end ok
case c1
call root 0 1 0 0 0 0 0 0 0 0
end unmapped 00000000 4 last 80A00018
"""


class EventTests(unittest.TestCase):
    def test_events_parse_into_one_record_per_case(self):
        got = cases.parse_events(EVENTS)
        self.assertEqual([c["status"] for c in got], ["ok", "unmapped"])
        first = got[0]
        self.assertEqual(
            first["events"][0],
            {"k": "call", "name": "root", "args": [0x80A00000, 1, 0, 0, 0, 0, 0, 0], "f": [0x3F800000, 0]},
        )
        self.assertEqual(first["events"][2], {"k": "st", "addr": 0x80A00004, "w": 4, "value": 5})
        self.assertEqual(
            (first["reads"], first["unseeded"], first["net"]), ([[0x80A00008, 4]], [[0x80100000, 4]], [[0x80A00004, 5]])
        )
        self.assertEqual((first["ret"]["v0"], first["saved"]), (7, ["r17"]))
        self.assertEqual(got[1]["detail"], "00000000 4 last 80A00018")

    def test_a_harness_that_died_inside_a_case_is_refused(self):
        with self.assertRaises(TasteError):
            cases.parse_events("case c0\ncall root 0 0 0 0 0 0 0 0 0 0\n")
        with self.assertRaises(TasteError):
            cases.parse_events("call root 0 0 0 0 0 0 0 0 0 0\n")
        with self.assertRaises(TasteError):
            cases.parse_events("case c\nbogus 1\nend ok\n")

    def test_the_cases_file_carries_registers_stack_words_memory_and_seeded_ranges(self):
        rows = [
            {
                "name": "c0",
                "registers": {"a0": 0x80A00000, "f12": 0x3F800000},
                "stack": [1, -1],
                "memory": [{"addr": 0x80A00000, "hex": "00000001"}],
            }
        ]
        text = cases.cases_text(
            rows, {"r29": 0x80400000, "status": 0x24000000}, 0x80001000, [(0x80000000, 0x80000100)], 0x80400000, 1500
        )
        lines = text.splitlines()
        self.assertEqual(lines[:3], ["timeout 1500", "seed 80000000 80000100", "case c0"])
        self.assertIn("entry 80001000", lines)
        self.assertIn("reg r4 FFFFFFFF80A00000", lines)
        self.assertIn("reg f12 3F800000", lines)
        self.assertIn("mem 80400010 00000001", lines)
        self.assertIn("mem 80400014 FFFFFFFF", lines)
        self.assertIn("mem 80A00000 00000001", lines)
        self.assertEqual(lines[-1], "go")

    def test_each_case_names_what_its_stubs_return(self):
        rows = [{"name": "c0", "stub_v0": {"get": 0x80A01000}}, {"name": "c1"}, {"name": "c2", "stub_v0": {"get": 0}}]
        text = cases.cases_text(rows, {}, 0x80001000, [], 0x80400000, 1500, {"get": 7, "other": 9})
        by_case = [chunk.splitlines() for chunk in text.split("case ")[1:]]
        self.assertEqual([[x for x in c if x.startswith("stubv")] for c in by_case], [
            ["stubv get 80A01000", "stubv other 9"],
            ["stubv get 7", "stubv other 9"],
            ["stubv get 0", "stubv other 9"],
        ])


class ClosureTests(unittest.TestCase):
    def test_depth_limits_how_far_original_callees_run(self):
        bodies = {"root": body("root", "leaf"), "leaf": body("leaf", "deep"), "deep": body("deep")}
        self.assertEqual(limit_depth("root", bodies, 0), {"leaf"})
        self.assertEqual(limit_depth("root", bodies, 1), {"deep"})
        self.assertEqual(limit_depth("root", bodies, 2), set())

    def test_sibling_functions_of_the_candidate_cost_no_depth(self):
        bodies = {
            "behave_a": body("behave_a", "behave_b"),
            "behave_b": body("behave_b", "leaf"),
            "leaf": body("leaf", "deep"),
            "deep": body("deep"),
        }
        self.assertEqual(limit_depth("behave_a", bodies, 1, same_unit="behave_"), {"deep"})

    def test_past_the_cap_the_nearest_callees_stay_and_the_rest_are_boundary_stubs(self):
        bodies = {n: body(n) for n in ("a", "b", "c", "d")}
        bodies["root"] = body("root", "a", "b", "c")
        bodies["a"] = body("a", "d")
        with self.assertRaises(TasteError):
            closure("root", bodies, limit=3)
        found = closure("root", bodies, limit=3, spill=True)
        self.assertEqual(sorted(found.bodies), ["a", "b", "root"])
        self.assertEqual(found.stubs, {"c": "boundary", "d": "boundary"})
        self.assertEqual(sorted(found.spilled), ["c", "d"])
        self.assertEqual(closure("root", bodies, limit=9, spill=True).spilled, [])

    def test_a_stub_leaves_v0_to_its_hook_when_the_probe_answers_per_run(self):
        found = closure("root", {"root": body("root", "leaf")})
        fixed = render_unit(found, {"root": 0x80001000}, '#include "recomp.h"', True, {"leaf": 5})
        self.assertIn("ctx->r2 = (gpr)(int64_t)5;", fixed)
        runtime = render_unit(found, {"root": 0x80001000}, '#include "recomp.h"', True, {"leaf": 5}, runtime_v0=True)
        self.assertIn('probe_stub("leaf");', runtime)
        self.assertNotIn("ctx->r2", runtime)

    def test_a_probe_unit_can_use_its_own_headers(self):
        found = closure("root", {"root": body("root")})
        unit = render_unit(found, {"root": 0x80001000}, '#include "recomp.h"', True, {}, headers=("behave.h",))
        self.assertIn('#include "behave.h"', unit)
        self.assertNotIn("bounded.h", unit)


class CandidateTests(unittest.TestCase):
    def spec(self, root, **over):
        (root / "c.bin").write_bytes(bytes(64))
        spec = {"image": "c.bin", "vram": "0x80800000", "functions": [{"name": "f", "offset": 0, "size": 32}], **over}
        return cand.parse(spec, root)

    def test_a_valid_object_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            got = self.spec(Path(directory))
            self.assertEqual((got.vram, [f.name for f in got.functions], len(got.image)), (0x80800000, ["f"], 64))
            self.assertEqual(len(got.sha256), 64)

    def test_invalid_specs_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for over in (
                {"vram": "0x1000"},
                {"functions": []},
                {"functions": [{"name": "f", "offset": 60, "size": 32}]},
                {"functions": [{"name": "f", "offset": 1, "size": 32}]},
            ):
                with self.assertRaises(TasteError):
                    self.spec(root, **over)
            with self.assertRaises(TasteError):
                cand.parse(
                    {"image": "missing.bin", "vram": 0x80800000, "functions": [{"name": "f", "offset": 0, "size": 4}]},
                    root,
                )

    def test_recompile_appends_a_section_ignores_every_original_and_returns_only_prefixed_bodies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gen = root / "gen" / "v1"
            gen.mkdir(parents=True)
            (gen / "symbols.toml").write_text(SYMBOLS)
            (gen / "rom.flat").write_bytes(bytes(100))
            tool = root / "N64Recomp"
            tool.write_text(
                '#!/bin/sh\ngrep -o \'output_func_path = "[^"]*\' "$1" | cut -d\'"\' -f2 > outdir\n'
                f"out=$(cat outdir)\necho run >> {root}/calls\n"
                f"printf '%s' '{body('behave_f', 'leaf')}' > \"$out/funcs_0.c\"\n"
            )
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
            settings = type("S", (), {"out": lambda self, v: gen, "n64recomp": tool, "stubs": ()})()
            candidate = self.spec_in(root)
            bodies = cand.recompile(settings, "v1", candidate, root / "work", function_vrams(gen / "symbols.toml"))
            self.assertEqual(list(bodies), ["behave_f"])
            work = next((root / "work").glob("candidate-*"))
            symbols = (work / "symbols.toml").read_text()
            self.assertIn('{ name = "behave_f", vram = 0x80800000, size = 0x20 }', symbols)
            self.assertIn("rom = 0x70", symbols)  # appended after the 100-byte image, aligned to 16
            config = (work / "recomp.toml").read_text()
            self.assertIn('"root", "leaf", "deep"', config)
            self.assertEqual((work / "rom.flat").stat().st_size, 0x70 + 64)
            # a second call with the same inputs reuses the generated C
            self.assertEqual(
                list(cand.recompile(settings, "v1", candidate, root / "work", function_vrams(gen / "symbols.toml"))),
                ["behave_f"],
            )
            self.assertEqual((root / "calls").read_text().split(), ["run"])

    def test_a_refused_run_is_never_reused_as_cached_c(self):
        # N64Recomp refuses a function and leaves its file half written (no closing ';}'): the next call must run
        # N64Recomp again, not index the leftover
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gen = root / "gen" / "v1"
            gen.mkdir(parents=True)
            (gen / "symbols.toml").write_text(SYMBOLS)
            (gen / "rom.flat").write_bytes(bytes(100))
            tool = root / "N64Recomp"
            half = HEAD.format("behave_f") + "    // cut\n"
            tool.write_text(
                '#!/bin/sh\nout=$(grep -o \'output_func_path = "[^"]*\' "$1" | cut -d\'"\' -f2)\n'
                f"echo run >> {root}/calls\n"
                f"if [ -f {root}/fixed ]; then printf '%s' '{body('behave_f')}' > \"$out/funcs_0.c\"; exit 0; fi\n"
                f"printf '%s' '{half}' > \"$out/funcs_0.c\"\necho 'Error recompiling behave_f'\nexit 1\n"
            )
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
            settings = type("S", (), {"out": lambda self, v: gen, "n64recomp": tool, "stubs": ()})()
            candidate = self.spec_in(root)
            vrams = function_vrams(gen / "symbols.toml")
            with self.assertRaises(TasteError) as caught:
                cand.recompile(settings, "v1", candidate, root / "work", vrams)
            self.assertIn("N64Recomp refused the candidate (behave_f)", caught.exception.detail)
            work = next((root / "work").glob("candidate-*"))
            self.assertEqual(list((work / "funcs").glob("*.c")), [])  # the half-written file is gone
            # an entry an older run left half written (no `complete` mark) is redone too
            (work / "funcs" / "funcs_0.c").write_text(half)
            (root / "fixed").write_text("")
            self.assertEqual(list(cand.recompile(settings, "v1", candidate, root / "work", vrams)), ["behave_f"])
            self.assertEqual(list(cand.recompile(settings, "v1", candidate, root / "work", vrams)), ["behave_f"])
            self.assertEqual((root / "calls").read_text().split(), ["run", "run"])

    def spec_in(self, root):
        return self.spec(root)

    def test_an_image_over_an_original_function_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gen = root / "gen"
            gen.mkdir()
            (gen / "symbols.toml").write_text(SYMBOLS)
            (gen / "rom.flat").write_bytes(bytes(16))
            settings = type("S", (), {"out": lambda self, v: gen, "n64recomp": root / "x", "stubs": ()})()
            candidate = cand.Candidate(bytes(64), 0x80001000, (cand.Function("f", 0, 32),))
            with self.assertRaises(TasteError):
                cand.recompile(settings, "v1", candidate, root / "work", {"root": 0x80001000})


class FlowTests(unittest.TestCase):
    def test_public_flow_with_a_fake_harness_and_the_original_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gen = root / "gen" / "v1"
            (gen / "funcs").mkdir(parents=True)
            (gen / "funcs" / "funcs_0.c").write_text(body("root", "leaf") + body("leaf"))
            (gen / "symbols.toml").write_text(SYMBOLS)
            (root / "rom").write_bytes(bytes(16))
            (root / "facts.json").write_text(
                json.dumps({"version": "v1", "boot": {"rdram_size": 0x1000, "sp": 0x800007F0}})
            )
            (root / "host" / "behave").mkdir(parents=True)
            for name in ("harness.c", "behave.h"):
                (root / "host" / "behave" / name).write_text("x")
            request = root / "request.json"
            request.write_text(
                json.dumps(
                    {
                        "version": "v1",
                        "function": "0x80001000",
                        "memory_end": 0x80002000,
                        "cases": [{"name": "c0", "registers": {"a0": 5}}, {"name": "c1", "registers": {"a0": 0}}],
                    }
                )
            )
            settings = type(
                "S",
                (),
                {
                    "out": lambda self, v: gen,
                    "recomp_include": None,
                    "trace_mode": True,
                    "rom": lambda self, v: root / "rom",
                },
            )()
            profile = type(
                "P",
                (),
                {
                    "section": lambda self, name: {
                        "bounded": {
                            "facts": "facts.json",
                            "host_dir": "host",
                            "runtime_include": "inc",
                            "work_root": "work",
                        }
                    },
                    "resolve_path": lambda self, value: root / value,
                },
            )()
            commands = []

            def fake_run(profile, *, name, function, command, artifacts=(), **_):
                commands.append(command)
                run_dir = root / "runs" / str(len(commands))
                run_dir.mkdir(parents=True)
                (run_dir / "receipt.json").write_text("{}")
                if len(commands) == 2:
                    (run_dir / "events.txt").write_text(EVENTS)
                return {
                    "receipt": str(run_dir / "receipt.json"),
                    "status": {"phase": "passed", "run_dir": str(run_dir), "artifacts": [], "lock_wait_seconds": 1.5},
                }

            def probe():
                with (
                    patch.object(cases, "load_recomp", return_value=settings),
                    patch.object(cases, "load_layout", return_value=type("L", (), {"segments": ()})()),
                    patch.object(cases, "run_command", fake_run),
                ):
                    return cases.cases_probe(profile, function=None, script=request)

            result = probe()
            record = result["probe"]
            self.assertEqual(
                (record["kind"], record["cases"], record["statuses"], record["cached"]),
                ("cases", 2, {"ok": 1, "unmapped": 1}, False),
            )
            self.assertEqual((record["sp"], record["stack_window"]), (0x800007F0, 0x4000))
            self.assertEqual(record["lock_wait_seconds"], 3.0)  # queued 1.5 s for the build and 1.5 s for the run
            frozen = json.loads(Path(record["cases_path"]).read_text())
            self.assertEqual(frozen[0]["inputs"]["registers"], {"a0": 5})
            self.assertEqual(len(commands), 2)  # build, run
            self.assertIn("behave", " ".join(commands[0]))
            self.assertIn(
                '#include "behave.h"', (next((root / "work").glob("behave-root-*")) / "closure.c").read_text()
            )
            again = probe()  # the same original request: nothing is built or run
            self.assertTrue(again["probe"]["cached"])
            self.assertEqual(len(commands), 2)
            self.assertEqual(json.loads(Path(again["probe"]["cases_path"]).read_text()), frozen)


class NarrationTests(unittest.TestCase):
    def run_probe(self, narrate):
        from cake_taste.repro import cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "r.json"
            request.write_text(json.dumps({"story": {"narrate": narrate}}))
            profile = type(
                "P",
                (),
                {
                    "section": lambda self, name: {"events_path": "events.jsonl"},
                    "resolve_path": lambda self, value: root / value,
                },
            )()
            done = {
                "probe": {"kind": "cases", "cases": 3, "statuses": {"ok": 2, "unmapped": 1}},
                "run_id": "r",
                "receipt": str(root / "receipt.json"),
            }
            with patch.object(cli, "cases_probe", return_value=done):
                result = cli.run(profile, ("probe", "--kind", "cases", "--function", "f", "--input", str(request)))
            feed = root / "events.jsonl"
            return result, feed.read_text() if feed.exists() else ""

    def test_a_probe_that_says_it_is_one_of_many_stays_off_the_event_feed(self):
        result, feed = self.run_probe(False)
        self.assertEqual(result["probe"]["cases"], 3)
        self.assertEqual(feed, "")

    def test_a_narrated_probe_reports_its_case_counts(self):
        _, feed = self.run_probe(True)
        self.assertIn("3 cases: 2 ok, 1 unmapped", feed)


class ReuseTests(unittest.TestCase):
    def test_the_bounded_probe_helpers_it_builds_on_are_still_there(self):
        for name in (
            "Memory",
            "host_order",
            "registers",
            "seed_layout",
            "_payload",
            "_int",
            "_config",
            "host_profile_header",
        ):
            self.assertTrue(hasattr(bounded, name), name)


if __name__ == "__main__":
    unittest.main()
