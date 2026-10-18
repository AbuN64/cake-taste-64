import json
import tempfile
import unittest
from pathlib import Path

from cake_taste.logs.events import EventReader, format_event
from cake_taste.repro.narration import narrated_probe, probe_story


class Profile:
    def __init__(self, root):
        self.root = root

    def section(self, name):
        return {"events_path": "events.jsonl", "header_kinds": ["RUN-FAIL"]}

    def resolve_path(self, value):
        return self.root / value


class ProbeNarrationTests(unittest.TestCase):
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
            self.assertEqual([r["kind"] for r in records], ["PROBE", "PROBE", "MATCH"])
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
