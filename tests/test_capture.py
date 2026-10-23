import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste import TasteError
from cake_taste.repro import bounded, capture

SYMBOLS = """[[section]]
name = "a"
functions = [
    { name = "root", vram = 0x80001000, size = 0x10 },
]
"""
RDRAM = 0x1000


def context(**regs):
    words = [0] * 64
    for name, value in regs.items():
        words[int(name[1:]) + (32 if name[0] == "f" else 0)] = value
    return struct.pack("<64Q", *words)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = root = Path(directory.name)
        gen = root / "gen" / "v1"
        gen.mkdir(parents=True)
        (gen / "symbols.toml").write_text(SYMBOLS)
        (gen / "MANIFEST.json").write_text('{"files": {"funcs/funcs_0.c": "aa"}}')
        (root / "rom.z64").write_bytes(b"rom")
        (root / "host").write_bytes(b"host")
        (root / "facts.json").write_text(json.dumps({"version": "v1", "boot": {"rdram_size": RDRAM}}))
        self.settings = type("S", (), {"out": lambda s, v: gen, "rom": lambda s, v: root / "rom.z64"})()
        self.config = {"facts": "facts.json", "host": "host", "work_root": "work",
                       "env": {"WAYLAND_DISPLAY": "wayland-1"},
                       "host_build": {"command": ["sh", "-c", f"echo built >> {root}/builds"]}}
        self.profile = type("P", (), {
            "section": lambda s, name: {"capture": self.config},
            "resolve_path": lambda s, value: root / value,
        })()
        self.runs = []
        self.reached = True
        self.exit_code = 124  # what `timeout` exits with when it stopped a run that never reached the entry

    def fake_run(self, profile, *, name, function, command, artifacts=(), env=None, **_):
        self.runs.append((command, env))
        run_dir = self.root / "runs" / str(len(self.runs))
        run_dir.mkdir(parents=True)
        out = Path(command[command.index("-x") + 1]).parent
        if self.reached:
            image = bytearray(RDRAM)
            image[0x100:0x104] = b"\x12\x34\x56\x78"
            (out / "rdram.host").write_bytes(bounded.host_order(bytes(image)))
            (out / "ctx.bin").write_bytes(context(r4=0x80000100, r29=0x80000800, f12=0x3F800000))
            config = json.loads((out / "config.json").read_text())
            if config["stubs"]:
                calls = [{"name": config["stubs"][0], "n": 1, "args": [1, 2, 3, 4], "v0": 0x80105000, "v1": 0,
                          "writes": [[0x80105010, 7]], "writes_dropped": False}]
                (out / "calls.json").write_text(json.dumps({"calls": calls, "complete": True, "unresolved": ["gone"],
                                                            "stubs": config["stubs"]}))
            text = "CAPTURE-STOP\nCAPTURE-DONE\n"
        else:
            text = "CAPTURE-MISS\nThread 1\n#0  poll () from libc\n#1  main ()\n"
        (run_dir / "output.log").write_text(text)
        return {"receipt": str(run_dir / "receipt.json"),
                "status": {"phase": "passed", "run_dir": str(run_dir), "log_path": str(run_dir / "output.log"),
                           "exit_code": 0 if self.reached else self.exit_code}}

    def builds(self):
        path = self.root / "builds"
        return len(path.read_text().splitlines()) if path.is_file() else 0

    def grab(self, **kw):
        with patch.object(capture, "load_recomp", return_value=self.settings), \
             patch.object(capture, "run_command", side_effect=self.fake_run):
            return capture.capture_probe(self.profile, function="0x80001000", version="v1", **kw)

    def test_the_gdb_config_names_where_to_stop_and_what_to_record(self):
        config = capture.gdb_config("root", 3, 0x800000, Path("/o"), ["leaf"])
        self.assertEqual((config["symbol"], config["hit"], config["rdram_size"], config["stubs"]),
                         ("root", 3, 0x800000, ["leaf"]))
        self.assertGreater(config["max_writes"], 0)
        script = Path(capture.__file__).with_name("gdb_capture.py").read_text()
        self.assertIn('"*" + cfg["symbol"]', script)  # the exact entry, not a prologue skip that clobbers rdi/rsi
        self.assertIn("ignore_count", script)

    def test_the_context_dump_names_the_entry_registers(self):
        regs = capture.parse_context(context(r4=0x80000100, f12=0x3F800000))
        self.assertEqual((regs["r4"], regs["f12"], "r0" in regs), (0x80000100, 0x3F800000, False))
        with self.assertRaises(TasteError):
            capture.parse_context(b"short")

    def test_a_capture_freezes_big_endian_rdram_and_registers_and_is_cached(self):
        got = self.grab(hit=2)
        record, seed = got["capture"], got["seed"]
        self.assertEqual((record["function"], record["hit"], record["cached"]), ("root", 2, False))
        self.assertEqual(record["rom_sha1"], hashlib.sha1(b"rom").hexdigest())
        ram = Path(record["rdram_path"]).read_bytes()
        self.assertEqual(ram[0x100:0x104], b"\x12\x34\x56\x78")  # guest byte order, not the host's word swap
        self.assertEqual(seed["case"]["registers"]["r4"], "0x80000100")
        self.assertEqual(seed["sp"], "0x80000800")
        gdb = self.root / "work" / "captures" / record["key"] / "run" / "config.json"
        self.assertEqual(json.loads(gdb.read_text())["hit"], 2)
        command, env = self.runs[0]
        self.assertEqual(env["N64R_ROM"], str(self.root / "rom.z64"))
        self.assertEqual(command[:3], ["timeout", "-s", "INT"])
        again = self.grab(hit=2)
        self.assertEqual((again["capture"]["cached"], len(self.runs)), (True, 1))  # the second asks the oracle nothing
        self.grab(hit=1)
        self.assertEqual(len(self.runs), 2)  # another hit is another capture

    def test_recorded_callee_calls_are_part_of_the_capture_and_travel_with_the_seed(self):
        plain = self.grab()
        traced = self.grab(stubs=("leaf", "alloc"))
        self.assertNotEqual(plain["capture"]["key"], traced["capture"]["key"])  # each stub set is its own capture
        record = traced["capture"]
        self.assertEqual((record["calls"], record["calls_complete"], record["calls_unresolved"]), (1, True, ["gone"]))
        self.assertEqual(record["stubs"], ["alloc", "leaf"])
        spec = traced["seed"]["case"]["stub_calls"]
        self.assertEqual(json.loads(Path(spec["file"]).read_text())[0]["v0"], 0x80105000)
        self.assertNotIn("stub_calls", plain["seed"]["case"])
        self.assertEqual(self.grab(stubs=("alloc", "leaf"))["capture"]["cached"], True)

    def test_stub_calls_become_scall_lines_for_the_entry_case_only(self):
        from cake_taste.repro import cases

        path = self.root / "calls.json"
        path.write_text(json.dumps([{"name": "leaf", "v0": 0x80105000, "v1": 1, "writes": [[0x80105010, 7]]},
                                    {"name": "leaf", "v0": 0, "v1": 0, "writes": []}]))
        spec = {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        text = cases.cases_text([{"name": "gen"}, {"name": "entry", "stub_calls": spec}], {}, 0x80001000, [],
                                0x80400000, 1000)
        before, after = text.split("case entry")
        self.assertNotIn("scall", before)
        self.assertIn("scall leaf 80105000 1 80105010:7\nscall leaf 0 0\ngo", after)
        with self.assertRaises(TasteError):
            cases.stub_calls({"stub_calls": {"file": str(path), "sha256": "0" * 64}}, 0)

    def test_a_function_never_reached_says_where_the_oracle_stopped(self):
        self.reached = False
        with self.assertRaises(TasteError) as raised:
            self.grab()
        self.assertIn("was not reached", raised.exception.detail)
        self.assertIn("poll () from libc", raised.exception.detail)

    def test_a_never_reached_entry_is_named_and_kept_until_a_longer_timeout(self):
        self.reached = False
        for _ in range(2):
            with self.assertRaises(TasteError) as raised:
                self.grab()
            self.assertEqual(raised.exception.key, "repro.capture.unreached")
        self.assertEqual(len(self.runs), 1)  # the miss is kept: a sweep does not rerun the oracle for it
        with self.assertRaises(TasteError):
            self.grab(stubs=("leaf",))
        self.assertEqual(len(self.runs), 1)  # nor for another stub set: what the run reaches does not depend on it
        self.assertIn("scripted input", raised.exception.detail)
        self.config["timeout_seconds"] = 300
        with self.assertRaises(TasteError):
            self.grab()
        self.assertEqual(len(self.runs), 2)  # a longer run tries again

    def test_an_oracle_that_ends_early_is_its_own_finding_and_never_cached(self):
        self.reached, self.exit_code = False, 1
        for _ in range(2):
            with self.assertRaises(TasteError) as raised:
                self.grab()
            self.assertEqual(raised.exception.key, "repro.capture.oracle_exit")
        self.assertEqual(len(self.runs), 2)

    def test_a_host_with_no_recorded_build_is_built_once_and_a_changed_input_rebuilds_it(self):
        self.grab()
        self.assertEqual(self.builds(), 1)
        self.grab()
        self.assertEqual((self.builds(), len(self.runs)), (1, 1))
        (self.root / "gen" / "v1" / "MANIFEST.json").write_text('{"files": {"funcs/funcs_0.c": "bb"}}')
        got = self.grab()
        self.assertEqual((self.builds(), len(self.runs)), (2, 2))  # new inputs: a new build and a new capture key
        self.assertTrue(got["capture"]["host_built"])

    def test_a_stale_host_without_a_build_command_fails_fast(self):
        del self.config["host_build"]
        with self.assertRaises(TasteError) as raised:
            self.grab()
        self.assertEqual(raised.exception.key, "repro.capture.stale_host")
        self.assertIn("no build of", raised.exception.detail)
        self.assertEqual(self.runs, [])

    def test_a_host_replaced_after_its_build_is_stale_and_a_failed_build_is_named(self):
        self.grab()
        (self.root / "host").write_bytes(b"other host")
        self.config["host_build"] = {"command": ["false"]}
        with self.assertRaises(TasteError) as raised:
            self.grab()
        self.assertEqual(raised.exception.key, "repro.capture.host_build")
        self.assertIn("was replaced", raised.exception.detail)

    def test_host_inputs_cover_extra_paths_and_a_directory_by_content(self):
        sources = self.root / "hostsrc"
        sources.mkdir()
        (sources / "main.cpp").write_text("int main(){}")
        self.config["host_inputs"] = ["hostsrc"]
        self.grab()
        (sources / "main.cpp").write_text("int main(){return 1;}")
        self.grab()
        self.assertEqual(self.builds(), 2)

    def test_a_cache_entry_whose_image_changed_or_is_missing_is_redone(self):
        first = self.grab()["capture"]
        Path(first["rdram_path"]).write_bytes(b"torn")
        again = self.grab()["capture"]
        self.assertEqual((again["cached"], len(self.runs)), (False, 2))
        Path(again["rdram_path"]).unlink()
        self.assertEqual(self.grab()["capture"]["cached"], False)
        self.assertEqual(len(self.runs), 3)

    def test_unknown_profile_keys_are_refused(self):
        self.config["timeout"] = 5
        with self.assertRaises(TasteError) as raised:
            self.grab()
        self.assertEqual(raised.exception.key, "config.schema")

    def test_a_display_that_is_not_running_and_cannot_be_started_is_named(self):
        self.config["env"] = {"WAYLAND_DISPLAY": "wayland-9", "XDG_RUNTIME_DIR": str(self.root / "rt")}
        with self.assertRaises(TasteError) as raised:
            self.grab()
        self.assertEqual(raised.exception.key, "repro.capture.display")
        self.assertEqual(self.runs, [])

    def test_an_x11_display_is_refused_and_headless_needs_none(self):
        self.config["env"] = {"DISPLAY": ":144"}
        with self.assertRaises(TasteError) as raised:
            self.grab()
        self.assertEqual(raised.exception.key, "repro.capture.display")
        self.assertIn("never X11", str(raised.exception))
        self.config["env"] = {"SDL_VIDEODRIVER": "offscreen"}
        self.grab()
        self.assertEqual(len(self.runs), 1)

    def test_a_display_command_brings_up_the_socket_before_the_oracle_runs(self):
        runtime = self.root / "rt"
        self.config["env"] = {"WAYLAND_DISPLAY": "wayland-9", "XDG_RUNTIME_DIR": str(runtime)}
        self.config["display_command"] = ["sh", "-c", 'touch "$XDG_RUNTIME_DIR/wayland-9"']
        self.grab()
        self.assertTrue((runtime / "wayland-9").exists())
        self.assertEqual(len(self.runs), 1)

    def test_several_functions_are_captured_in_parallel_each_with_its_status(self):
        root = '{ name = "root", vram = 0x80001000, size = 0x10 },'
        leaf = '{ name = "leaf", vram = 0x80002000, size = 0x10 },'
        (self.root / "gen" / "v1" / "symbols.toml").write_text(SYMBOLS.replace(root, root + "\n    " + leaf))
        with patch.object(capture, "load_recomp", return_value=self.settings), \
             patch.object(capture, "run_command", side_effect=self.fake_run):
            got = capture.capture_many(self.profile, functions=["root", "leaf", "0x80009999"], version="v1", jobs=2)
        statuses = {row["function"]: row["status"] for row in got["captures"]}
        self.assertEqual(statuses, {"root": "captured", "leaf": "captured", "0x80009999": "repro.function"})
        self.assertEqual((got["jobs"], got["counts"]["captured"]), (2, 2))

    def test_captures_take_slot_locks_beside_the_repro_lock_and_never_wait_behind_it(self):
        import fcntl
        import threading

        from cake_taste.repro import runner

        lock = self.root / "repro.lock"
        held = lock.open("a+b")
        fcntl.flock(held, fcntl.LOCK_EX)  # a probe holds the one-at-a-time lock
        inside, release = threading.Barrier(3), threading.Event()

        def take():
            with runner._held(lock, 2):
                inside.wait(timeout=5)  # both slots are held at once
                release.wait(timeout=5)

        workers = [threading.Thread(target=take) for _ in range(2)]
        for worker in workers:
            worker.start()
        inside.wait(timeout=5)
        third = lock.with_name("repro.lock.slot0").open("a+b")
        with self.assertRaises(BlockingIOError):  # a third capture waits for a slot
            fcntl.flock(third, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release.set()
        for worker in workers:
            worker.join(timeout=5)
        third.close()
        held.close()

    def test_a_profile_without_capture_is_a_named_refusal(self):
        profile = type("P", (), {"section": lambda s, name: {}, "resolve_path": lambda s, v: Path(v)})()
        with self.assertRaises(TasteError) as raised:
            capture.capture_probe(profile, function="root", version="v1")
        self.assertEqual(raised.exception.key, "config.schema")

    def test_a_seed_memory_file_item_is_the_captured_image_pinned_by_its_hash(self):
        path = self.root / "ram.bin"
        path.write_bytes(b"\x01\x02")
        digest = hashlib.sha256(b"\x01\x02").hexdigest()
        self.assertEqual(bounded._payload({"addr": 0, "file": str(path), "sha256": digest}, 0), b"\x01\x02")
        with self.assertRaises(TasteError):
            bounded._payload({"addr": 0, "file": str(path), "sha256": "0" * 64}, 0)
        with self.assertRaises(TasteError):
            bounded._payload({"addr": 0, "file": str(path), "hex": "00"}, 0)


if __name__ == "__main__":
    unittest.main()
