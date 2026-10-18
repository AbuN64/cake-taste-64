import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste.logs import cli
from cake_taste.logs.console import selected_events
from cake_taste.logs.events import EventReader, format_event, make_event
from cake_taste.logs.host_output import HostOutput
from cake_taste.repro.narration import narrated_probe, probe_story


class Profile:
    def __init__(self, root):
        self.root = root

    def section(self, name):
        return {"events_path": "events.jsonl", "header_kinds": ["PORT-FAIL", "RUNG"]}

    def resolve_path(self, value):
        return self.root / value


class MissionConsoleTests(unittest.TestCase):
    def test_public_kind_filter_and_sticky_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "run_console") as console:
            root = Path(directory)
            cli.run(
                Profile(root), ("console", "--kinds", "PORT-FAIL,PROBE,DECOMP,HANDOFF,COMMIT", "--host-log", "host.log")
            )
            options = console.call_args.kwargs
            self.assertEqual(options["kinds"], {"PORT-FAIL", "PROBE", "DECOMP", "HANDOFF", "COMMIT"})
            self.assertEqual(options["host_log"], root / "host.log")
            self.assertEqual(options["header"].kinds, ["PORT-FAIL"])
            records = [make_event("a", "info", "noise"), make_event("a", "info", "finding", kind="DECOMP")]
            self.assertEqual(selected_events(records, options["kinds"]), records[1:])
            self.assertEqual(selected_events(records, None), records)
            header = options["header"]
            header.update(
                make_event(
                    "port",
                    "failed",
                    "load finished. ERROR: caller boot: owner0x123 has no native implementation",
                    kind="PORT-FAIL",
                )
            )
            self.assertIn("caller boot: owner0x123", " ".join(header.lines(45)))
            self.assertNotIn("load finished", " ".join(header.lines(45)))

    def test_host_output_suppresses_spam_and_rate_limits_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.log"
            path.write_text(
                "n64host: cpu_cadence vi=600 count_ticks=42 entries=22 native_ns=9\n"
                "n64host: screen updates=600 elapsed_ms=10000 graphics_tasks=200\n"
                "n64host: keydown scancode=2\nn64host: WARNING bad task\n"
            )
            reader = HostOutput(path)
            self.assertEqual(
                reader.read_new(now=0),
                ["HOST n64host: WARNING bad task", "HOST heartbeat frames=600 graphics_tasks=200 VI=600"],
            )
            with path.open("a") as stream:
                stream.write("n64host: cpu_cadence vi=660\nn64host: fatal shader error\n")
            self.assertEqual(reader.read_new(now=59), ["HOST n64host: fatal shader error"])
            with path.open("a") as stream:
                stream.write("n64host: cpu_cadence vi=720\n")
            self.assertEqual(reader.read_new(now=60), ["HOST heartbeat frames=600 graphics_tasks=200 VI=720"])
            self.assertEqual(reader.read_new(now=120), [])
            path.write_text("n64host: failed open\n")
            self.assertEqual(reader.read_new(now=121), ["HOST n64host: failed open"])

    def test_probe_story_records_question_owner_source_result_and_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "request.json"
            script.write_text(
                json.dumps(
                    {
                        "function": "read",
                        "story": {
                            "question": "Does a zero-byte read advance the cursor?",
                            "decomp_path": "src/read.c",
                            "match_state": "stand-in",
                        },
                    }
                )
            )
            story = probe_story(script, None, {"owner_address": "0x80001234"})
            result = {
                "probe": {"function": "read", "vram": "0x80001234", "checked": 2, "differences": 0, "stubs_hit": []},
                "run_id": "run1",
                "receipt": str(root / "run1/receipt.json"),
            }
            self.assertEqual(narrated_probe(Profile(root), story, lambda: result), result)
            records = EventReader(root / "events.jsonl").read_new()
            self.assertEqual([r["kind"] for r in records], ["PROBE", "PROBE", "DECOMP"])
            payload = records[1]["payload"]
            self.assertEqual(payload["question"], story["question"])
            self.assertEqual(payload["owner_address"], "0x80001234")
            self.assertEqual(payload["decomp_path"], "src/read.c")
            self.assertEqual(payload["match_state"], "stand-in")
            self.assertIn("2 checks, 0 differences", " ".join(format_event(records[1])))
            script.write_text('{"function":"read"}')
            unknown = probe_story(script, None, {})
            self.assertEqual(unknown["match_state"], "unknown")
            with self.assertRaisesRegex(RuntimeError, "probe exited"):
                narrated_probe(Profile(root), unknown, lambda: (_ for _ in ()).throw(RuntimeError("probe exited")))
            failure = EventReader(root / "events.jsonl").read_new()[-1]
            self.assertEqual(failure["status"], "failed")
            self.assertIn("probe exited", failure["payload"]["result"])
