import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste.logs.events import EventReader, append_event, format_event
from cake_taste.logs.terminal import resize_to_tty
from cake_taste.logs.wrap import WrappedLog


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

    def test_wrapped_scroll_stays_anchored_then_follows_tail(self):
        view = WrappedLog()
        for number in range(12):
            view.append(f"actual event {number} /long/path/to/evidence")
        view.layout(20, 4)
        view.page(-1)
        anchor = view.layout(20, 4)[0]
        view.append("new outcome")
        assert view.layout(20, 4)[0] == anchor
        view.end()
        assert "new outcome" in " ".join(view.layout(20, 4))

    def test_terminal_resize_passes_lines_before_columns(self):
        with (
            patch("cake_taste.logs.terminal.os.get_terminal_size", return_value=os.terminal_size((106, 28))),
            patch("cake_taste.logs.terminal.curses.resizeterm") as resize,
        ):
            resize_to_tty(0)
        resize.assert_called_once_with(28, 106)
