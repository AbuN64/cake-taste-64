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
        (root / "rom.z64").write_bytes(b"rom")
        (root / "host").write_bytes(b"host")
        (root / "facts.json").write_text(json.dumps({"version": "v1", "boot": {"rdram_size": RDRAM}}))
        self.settings = type("S", (), {"out": lambda s, v: gen, "rom": lambda s, v: root / "rom.z64"})()
        self.profile = type("P", (), {
            "section": lambda s, name: {"capture": {"facts": "facts.json", "host": "host", "work_root": "work",
                                                    "env": {"WAYLAND_DISPLAY": "wayland-1"}}},
            "resolve_path": lambda s, value: root / value,
        })()
        self.runs = []
        self.reached = True

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
                "status": {"phase": "passed", "run_dir": str(run_dir), "log_path": str(run_dir / "output.log")}}

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
