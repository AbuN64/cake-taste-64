import tempfile
import unittest
from pathlib import Path

from cake_taste.logs.events import EventReader, append_event, format_event


class Profile:
    def __init__(self, root):
        self.root = root

    def section(self, name):
        assert name == "logs"
        return {"events_path": "events.jsonl"}

    def resolve_path(self, value):
        return self.root / value


class LogsTests(unittest.TestCase):
    def test_append_and_incremental_read_preserve_real_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = Profile(root)
            first = append_event(profile, "build", "started", "Gate began", "/tmp/receipt.json")
            reader = EventReader(root / "events.jsonl")
            assert reader.read_new() == [first["event"]]
            assert reader.read_new() == []
            append_event(profile, "build", "passed", "Gate passed")
            new = reader.read_new()
            assert len(new) == 1 and new[0]["status"] == "passed"
            assert "evidence: /tmp/receipt.json" in format_event(first["event"])[1]
