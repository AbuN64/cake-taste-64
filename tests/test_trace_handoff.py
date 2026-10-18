import tempfile
import unittest
from pathlib import Path

from cake_taste import TasteError
from cake_taste.handoff import cli as handoff_cli
from cake_taste.repro import cli as repro_cli
from cake_taste.trace import cli as trace_cli


class Profile:
    def __init__(self, root: Path):
        self.root = root
        self.sections = {
            "trace": {"platform": ["osSendMesg", "osRecvMesg"], "work_list": 2},
            "logs": {"events_path": "events.jsonl"},
            "handoff": {
                "repo": "evidence",
                "rom": "rom.z64",
                "version": "us-1.0",
                "tools": {"n64recomp": "tool.bin"},
            },
            "repro": {
                "runs_root": "runs",
                "status_path": "status.json",
                "lock_path": "repro.lock",
                "probe": {
                    "symbols": "symbols.toml",
                    "overlay_path": "overlay.toml",
                    "range_env": "WATCH_RANGE",
                    "templates": {
                        "P1": "trace_mode = true",
                        "P2": '[[patches.hook]]\nfunction = "{function}"',
                        "P3": 'recomp_include = "watch.h"',
                        "P4": "snapshot_event = {frames}",
                    },
                    "build": {"command": ["/bin/sh", "-c", "cat {overlay} > /dev/null"]},
                    "run": {
                        "command": ["/bin/sh", "-c", 'printf %s "$WATCH_RANGE" > out.txt; cat {input} >> out.txt'],
                        "outputs": [],
                    },
                },
            },
        }

    def section(self, name):
        return self.sections[name]

    def resolve_path(self, value):
        return self.root / value


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


class TraceTests(unittest.TestCase):
    def test_platform_dropped_threads_aligned_first_divergence_and_work_list(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            a = write(root / "a", "1 main A\n1 main osSendMesg\n2 main B\n3 main C\n4 main D\n1 aud X\n")
            b = write(root / "b", "1 main A\n2 main osRecvMesg\n2 main Z\n1 aud X\n")
            result = trace_cli.run(Profile(root), ("diff", str(a), str(b)))
        first = result["first"]
        assert first["thread"] == "main" and first["index"] == 1 and first["frame"] == 2
        assert (first["expected"], first["actual"]) == ("B", "Z")
        assert first["work_list"] == ["B", "C"]
        assert len(result["divergences"]) == 1

    def test_identical_and_short_actual_and_bad_line(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            a = write(root / "a", "1 m A\n2 m B\n")
            assert trace_cli.run(Profile(root), ("diff", str(a), str(a)))["identical"]
            b = write(root / "b", "1 m A\n")
            first = trace_cli.run(Profile(root), ("diff", str(a), str(b), "--next", "5"))["first"]
            assert first["actual"] == "<end of trace>" and first["work_list"] == ["B"]
            bad = write(root / "bad", "oops\n")
            with self.assertRaises(TasteError):
                trace_cli.run(Profile(root), ("diff", str(bad), str(a)))


def setup_probe(root: Path) -> Profile:
    write(root / "symbols.toml", '[[section]]\nname = "s"\n[[section.functions]]\nname = "func_80001000"\n')
    write(root / "script.txt", "press A\n")
    write(root / "rom.z64", "rom")
    write(root / "tool.bin", "tool")
    return Profile(root)


class ProbeHandoffTests(unittest.TestCase):
    def test_probe_overlays_unknown_function_and_hook(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            profile = setup_probe(root)
            script = str(root / "script.txt")
            with self.assertRaises(TasteError):
                repro_cli.run(profile, ("probe", "--kind", "P1", "--function", "nope", "--input", script))
            repro_cli.run(profile, ("probe", "--kind", "P2", "--function", "func_80001000", "--input", script))
            assert 'function = "func_80001000"' in (root / "overlay.toml").read_text()
            with self.assertRaises(TasteError):
                repro_cli.run(profile, ("probe", "--kind", "P3", "--function", "func_80001000", "--input", script))
            result = repro_cli.run(
                profile, ("probe", "--kind", "P3", "--function", "func_80001000", "--range", "10:20", "--input", script)
            )
            assert result["probe"]["env"] == {"WATCH_RANGE": "10:20"}
            result = repro_cli.run(
                profile, ("probe", "--kind", "P4", "--function", "func_80001000", "--frames", "7", "--input", script)
            )
            assert "snapshot_event = 7" in result["probe"]["overlay"]

    def test_packet_is_immutable_readable_by_hash_and_verified(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            profile = setup_probe(root)
            profile.sections["repro"]["probe"]["run"]["outputs"] = ["out.txt"]
            profile.sections["repro"]["probe"]["run"]["command"] = [
                "/bin/sh",
                "-c",
                "cd {overlay}/.. && echo hi > out.txt",
            ]
            profile.sections["repro"]["probe"]["run"]["outputs"] = [str(root / "out.txt")]
            probed = repro_cli.run(
                profile, ("probe", "--kind", "P1", "--function", "func_80001000", "--input", str(root / "script.txt"))
            )
            made = handoff_cli.run(
                profile,
                (
                    "new",
                    "--kind",
                    "name",
                    "--subject",
                    "func_80001000",
                    "--evidence",
                    probed["run_id"],
                    "--scope",
                    "one function",
                    "--gap",
                    "no name",
                ),
            )
            again = handoff_cli.run(
                profile,
                (
                    "new",
                    "--kind",
                    "name",
                    "--subject",
                    "func_80001000",
                    "--evidence",
                    probed["run_id"],
                    "--scope",
                    "one function",
                    "--gap",
                    "no name",
                ),
            )
            assert made["hash"] == again["hash"]
            shown = handoff_cli.run(profile, ("show", made["hash"][:12]))
            packet = shown["packet"]
            assert packet["version"] == "us-1.0" and len(packet["rom_sha1"]) == 40
            assert packet["tool_sha256"]["n64recomp"]
            assert packet["evidence"][0]["probe"]["kind"] == "P1" and packet["evidence"][0]["outputs"][0]["sha256"]
            events = (root / "events.jsonl").read_text()
            assert "HANDOFF name func_80001000 -> decomp" in events
            assert handoff_cli.run(profile, ("list",))["packets"][0]["state"] == "verified"
            (root / "out.txt").write_text("changed")
            frozen = Path(packet["evidence"][0]["outputs"][0]["path"])
            frozen.write_text("tampered")
            assert handoff_cli.run(profile, ("list",))["packets"][0]["state"] == "outputs-changed"
            with self.assertRaises(TasteError):
                handoff_cli.run(
                    profile,
                    ("new", "--kind", "type", "--subject", "s", "--evidence", "missing", "--scope", "x", "--gap", "y"),
                )
