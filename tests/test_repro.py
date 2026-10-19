import fcntl
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cake_taste.cli import main as public_main
from cake_taste.repro.runner import _freeze_artifact, run_command


class Profile:
    def __init__(self, root):
        self.root = root

    def section(self, name):
        if name == "repro":
            return {
                "runs_root": "runs",
                "status_path": "status.json",
                "lock_path": "repro.lock",
                "unset_env": ["WAYLAND_DISPLAY"],
                "env": {"DISPLAY": ":99"},
            }
        if name == "logs":
            return {"events_path": "events.jsonl"}
        raise KeyError(name)

    def resolve_path(self, value):
        return self.root / value


class ReproTests(unittest.TestCase):
    def test_a_second_isolated_repro_waits_for_the_lock_and_reports_the_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            held, release = threading.Event(), threading.Event()

            def other_repro():
                with (root / "repro.lock").open("a+b") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    held.set()
                    release.wait(10)

            thread = threading.Thread(target=other_repro)
            (root / "repro.lock").touch()
            thread.start()
            held.wait(10)
            threading.Timer(0.4, release.set).start()
            result = run_command(Profile(root), name="queued", command=["/bin/true"])
            thread.join()
            assert result["status"]["phase"] == "passed"
            assert result["status"]["lock_wait_seconds"] >= 0.3
            alone = run_command(Profile(root), name="alone", command=["/bin/true"])
            assert alone["status"]["lock_wait_seconds"] < 0.3

    def test_real_isolated_process_receipt_and_factual_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = run_command(
                Profile(root),
                name="one bounded check",
                function="init",
                command=["/bin/sh", "-c", "printf 'actual output\\n'"],
            )
            receipt = json.loads(Path(result["receipt"]).read_text())
            assert receipt["phase"] == "passed" and receipt["exit_code"] == 0
            assert "actual output" in Path(receipt["log_path"]).read_text()
            assert (Path(receipt["run_dir"]) / "config").is_dir()
            events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
            assert [item["status"] for item in events] == ["started", "passed"]
            assert events[-1]["evidence"] == result["receipt"]

    def test_public_failure_envelope_and_explicit_negative_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            profile_file = root / "profile.json"
            profile_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "repro-test",
                        "project_root": str(project),
                        "runtime_root": str(root / "runtime"),
                        "output_root": str(root / "output"),
                        "logs": {"events_path": str(root / "events.jsonl")},
                        "repro": {
                            "runs_root": str(root / "runs"),
                            "status_path": str(root / "status.json"),
                            "lock_path": str(root / "repro.lock"),
                        },
                    }
                )
            )
            base = ["--profile", str(profile_file), "repro", "run", "--name", "negative"]
            for extra in (
                ["--", "/bin/sh", "-c", "exit 7"],
                ["--artifact", str(root / "missing.png"), "--", "/bin/true"],
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = public_main([*base, *extra])
                envelope = json.loads(output.getvalue())
                self.assertEqual(code, 1)
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["findings"][0]["key"], "repro.failed")
                receipt = Path(envelope["findings"][0]["message"].split("receipt: ", 1)[1])
                self.assertTrue(receipt.is_file())
                self.assertEqual(json.loads(receipt.read_text())["phase"], "failed")
            output = io.StringIO()
            with redirect_stdout(output):
                code = public_main([*base, "--expect-exit-code", "7", "--", "/bin/sh", "-c", "exit 7"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["result"]["status"]["phase"], "passed")

    def test_wayland_environment_is_preserved_unless_profile_unsets_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = Profile(root)
            value = profile.section("repro")
            value.pop("unset_env")
            profile.section = lambda name: value if name == "repro" else {"events_path": "events.jsonl"}
            with patch.dict(os.environ, {"WAYLAND_DISPLAY": "kept-by-default"}):
                receipt = run_command(
                    profile, name="env check", command=["/bin/sh", "-c", 'printf %s "$WAYLAND_DISPLAY"']
                )
            log = Path(receipt["status"]["log_path"]).read_text()
            self.assertEqual(log, "kept-by-default")

    def test_public_receipt_freezes_artifact_against_later_source_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            work = root / "work"
            work.mkdir()
            profile_file = root / "profile.json"
            profile_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "artifact-test",
                        "project_root": str(project),
                        "runtime_root": str(root / "runtime"),
                        "output_root": str(root / "output"),
                        "logs": {"events_path": str(root / "events.jsonl")},
                        "repro": {
                            "runs_root": str(root / "runs"),
                            "status_path": str(root / "status.json"),
                            "lock_path": str(root / "repro.lock"),
                        },
                    }
                )
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = public_main(
                    [
                        "--profile",
                        str(profile_file),
                        "repro",
                        "run",
                        "--name",
                        "source file proof",
                        "--cwd",
                        str(work),
                        "--artifact",
                        "result.png",
                        "--",
                        "/bin/sh",
                        "-c",
                        "printf original-bytes > result.png",
                    ]
                )
            self.assertEqual(code, 0)
            envelope = json.loads(output.getvalue())
            receipt = json.loads(Path(envelope["result"]["receipt"]).read_text())
            item = receipt["artifacts"][0]
            frozen = Path(item["path"])
            self.assertEqual(frozen.read_bytes(), b"original-bytes")
            self.assertEqual(item["sha256"], hashlib.sha256(b"original-bytes").hexdigest())
            (work / "result.png").write_bytes(b"changed-later")
            self.assertEqual(frozen.read_bytes(), b"original-bytes")
            self.assertEqual(frozen.stat().st_size, item["bytes"])

    def test_source_mutation_during_copy_rejects_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, frozen = root / "source.png", root / "frozen.png"
            source.write_bytes(b"before")
            original_copy = shutil.copyfileobj

            def change_after_copy(inp, out):
                original_copy(inp, out)
                source.write_bytes(b"after")

            with (
                patch("cake_taste.repro.runner.shutil.copyfileobj", side_effect=change_after_copy),
                self.assertRaisesRegex(ValueError, "changed while copying"),
            ):
                _freeze_artifact(source, frozen)
            self.assertFalse(frozen.exists())
