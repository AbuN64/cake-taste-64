import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste.repro.scenario import execute, validate


class ScenarioTests(unittest.TestCase):
    def test_rejects_nonmonotonic_times_and_unsafe_captures(self):
        for steps in (
            [{"at": -1, "keys": ["x"]}],
            [{"at": 1, "keys": ["x"]}, {"at": 0, "keys": ["x"]}],
            [{"at": 0, "checkpoint": "../bad"}],
            [{"at": 0, "checkpoint": "a"}, {"at": 1, "checkpoint": "a"}],
        ):
            with self.assertRaises(ValueError):
                validate({"steps": steps})

    def test_records_inputs_capture_hash_and_host_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "pid").write_text("123")
            (p / "live.log").write_text("graphics tasks=600\n")
            request = p / "request.json"
            request.write_text(
                json.dumps(
                    {
                        "name": "walk",
                        "facts_sha256": "abc",
                        "spec": {
                            "role": "oracle",
                            "steps": [{"at": 0, "keys": ["Down"]}, {"at": 1, "checkpoint": "front"}],
                        },
                        "transport": {
                            "pid_file": str(p / "pid"),
                            "log_template": str(p / "live.log"),
                            "send_command": ["send", "{role}"],
                            "capture_command": ["capture", "{output}"],
                            "narrate_command": ["narrate", "{message}"],
                        },
                    }
                )
            )

            def command(argv, **kwargs):
                if argv[0] == "capture":
                    Path(argv[1]).write_bytes(b"screenshot bytes")

            with (
                patch("os.pidfd_open", return_value=100),
                patch("os.close"),
                patch("select.select", return_value=([], [], [])),
                patch("subprocess.run", side_effect=command) as run,
            ):
                execute(request)
            events = [json.loads(line) for line in (p / "trace.jsonl").read_text().splitlines()]
            self.assertEqual([e["event"] for e in events], ["start", "input", "checkpoint", "complete"])
            self.assertEqual(run.call_args_list[0].args[0], ["send", "oracle", "Down"])
            self.assertEqual(len(events[2]["image_sha256"]), 64)
            self.assertEqual((p / "host.log").read_text(), "graphics tasks=600\n")
            with (
                patch("os.pidfd_open", return_value=100),
                patch("os.close"),
                patch("select.select", return_value=([100], [], [])),self.assertRaisesRegex(RuntimeError, "exited")
            ):
                execute(request)
            self.assertIn('"event": "failed"', (p / "trace.jsonl").read_text())
