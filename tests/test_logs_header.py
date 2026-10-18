import unittest
from datetime import UTC, datetime

from cake_taste.logs.events import make_event
from cake_taste.logs.header import Header

KINDS = ["RUNG", "PORT-FAIL", "PROBE", "HANDOFF"]
COUNTERS = ["path_blockers", "ledger_open", "version_debt"]


class HeaderTests(unittest.TestCase):
    def test_header_keeps_latest_per_kind_and_counters(self):
        header = Header(KINDS, COUNTERS)
        when = datetime(2026, 1, 1, tzinfo=UTC)
        header.update(make_event("a", "info", "first", kind="RUNG", timestamp=when))
        header.update(make_event("a", "info", "second", kind="RUNG", payload={"path_blockers": 3}, timestamp=when))
        header.update(
            make_event("a", "failed", "boom", kind="PORT-FAIL", payload={"ledger_open": 7, "version_debt": 1})
        )
        lines = header.lines(60)
        self.assertTrue(len(lines) == 5)
        self.assertTrue("second" in lines[0] and "first" not in lines[0])
        self.assertTrue("boom" in lines[1])
        self.assertTrue(lines[2] == "PROBE: -" and lines[3] == "HANDOFF: -")
        self.assertTrue(lines[4] == "path_blockers=3  ledger_open=7  version_debt=1")

    def test_header_truncates_to_width(self):
        header = Header(["RUNG"], [])
        header.update(make_event("a", "info", "x" * 200, kind="RUNG"))
        self.assertTrue(all(len(line) <= 30 for line in header.lines(30)))
