import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cake_taste.logs import cli
from cake_taste.logs.events import (
    KINDS,
    LANE_KINDS,
    LANE_TRACKS,
    SCHEMA1_KINDS,
    SCHEMA_FILE,
    STATUSES,
    SUBJECT_KEYS,
    EventReader,
    append_event,
    canonical_kind,
    evidence_of,
    format_event,
    make_event,
    validate_event,
    validate_lines,
)

FIXTURES = Path(__file__).parent / "fixtures" / "events"


class Profile:
    def __init__(self, root):
        self.root = root

    def section(self, name):
        return {"events_path": "events.jsonl"}

    def resolve_path(self, value):
        return self.root / value


class EventSchemaTests(unittest.TestCase):
    def test_schema_file_states_the_same_vocabulary_as_the_writer(self):
        document = json.loads(SCHEMA_FILE.read_text())
        defs = document["$defs"]
        two = defs["schema2"]["properties"]
        self.assertEqual(tuple(two["kind"]["enum"]), KINDS)
        self.assertEqual(set(defs["status"]["enum"]), STATUSES)
        self.assertEqual(set(two["subject"]["properties"]), SUBJECT_KEYS)
        self.assertEqual(set(two["lane"]["properties"]["kind"]["enum"]), LANE_KINDS)
        self.assertEqual(set(two["lane"]["properties"]["track"]["enum"]), LANE_TRACKS)
        self.assertEqual(document["x-schema1-kinds"], SCHEMA1_KINDS)
        self.assertEqual(defs["schema1"]["required"], defs["schema2"]["required"])
        self.assertTrue(set(defs["schema1"]["properties"]) < set(two))

    def test_live_schema1_feed_shapes_stay_valid_as_written(self):
        with (FIXTURES / "schema1-live.jsonl").open("rb") as stream:
            report = validate_lines(stream)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["schemas"], {"schema1": report["events"]})
        records = EventReader(FIXTURES / "schema1-live.jsonl").read_new()
        kinds = {record.get("kind"): canonical_kind(record) for record in records}
        self.assertEqual(kinds["PORT-FAIL"], "RUN-FAIL")
        self.assertEqual(kinds["DECOMP"], "MATCH")
        self.assertEqual(kinds["COMMIT"], "LAND")
        self.assertEqual(kinds["RUNG"], "RUNG")
        for record in records:
            self.assertTrue(format_event(record))

    def test_schema2_fixture_covers_every_kind_and_is_valid(self):
        with (FIXTURES / "schema2.jsonl").open("rb") as stream:
            report = validate_lines(stream)
        self.assertEqual(report["findings"], [])
        records = EventReader(FIXTURES / "schema2.jsonl").read_new()
        self.assertEqual({r["kind"] for r in records if "kind" in r}, set(KINDS))
        self.assertIn("sha256 3f5a0c9e1b7d", format_event(records[0])[1])

    def test_writer_emits_schema2_and_refuses_schema1_names(self):
        record = make_event("a", "info", "m", kind="LAND", subject={"repo": "r", "ref": "abc"})
        self.assertEqual(record["schema"], 2)
        self.assertEqual(validate_event(record), [])
        for name, replacement in SCHEMA1_KINDS.items():
            with self.assertRaisesRegex(ValueError, f"{name} is a schema 1 kind name; write {replacement}"):
                make_event("a", "info", "m", kind=name)
        bad = (
            {"kind": "RUNG"},
            {"subject": {"address": "80253908"}},
            {"subject": {"unknown": "x"}},
            {"lane": {"model": "sol"}},
            {"lane": {"name": "L", "track": "side"}},
            {"evidence": {"path": "p", "sha256": "ABC"}},
        )
        for extra in bad:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                make_event(
                    "a",
                    "info",
                    "m",
                    extra.get("evidence"),
                    kind=extra.get("kind"),
                    subject=extra.get("subject"),
                    lane=extra.get("lane"),
                )

    def test_validator_reports_each_problem_with_its_line(self):
        lines = [
            '{"schema":1,"time":"2026-01-01T00:00:00+00:00","actor":"a","status":"info","message":"m","evidence":null}',
            "",
            '{"schema":1,"time":"2026-01-01T00:00:00+00:00","actor":"a","status":"info","message":"m",'
            '"evidence":null,"lane":{"name":"x"}}',
            '{"schema":3}',
            "not json",
            '{"schema":2,"time":"yesterday","actor":"","status":"loud","message":"m","evidence":null}',
        ]
        report = validate_lines(lines)
        self.assertEqual(report["events"], 5)
        by_line = {}
        for finding in report["findings"]:
            by_line.setdefault(finding["line"], []).append(finding["message"])
        self.assertEqual(by_line[3], ["unknown key lane in schema 1"])
        self.assertEqual(by_line[4], ["unsupported schema 3"])
        self.assertTrue(by_line[5][0].startswith("invalid JSON"))
        self.assertEqual(
            by_line[6], ["time is not ISO-8601", "actor must be a nonempty string", "unknown status 'loud'"]
        )

    def test_public_append_writes_hashed_evidence_and_validate_reads_the_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "receipt.json"
            receipt.write_text("{}")
            digest = hashlib.sha256(b"{}").hexdigest()
            self.assertEqual(evidence_of(receipt), {"path": str(receipt.resolve()), "sha256": digest})
            profile = Profile(root)
            written = cli.run(
                profile,
                (
                    "append",
                    "--actor",
                    "lane",
                    "--message",
                    "packet sent",
                    "--kind",
                    "HANDOFF",
                    "--evidence",
                    str(receipt),
                    "--evidence-sha256",
                    digest,
                    "--lane",
                    '{"name": "EXAMPLE-1", "kind": "impl", "track": "experiment"}',
                ),
            )
            self.assertEqual(written["event"]["evidence"], {"path": str(receipt), "sha256": digest})
            append_event(profile, "build", "passed", "Gate passed")
            with (root / "events.jsonl").open("a") as stream:
                stream.write((FIXTURES / "schema1-live.jsonl").read_text())
            report = cli.run(profile, ("validate",))
            self.assertEqual(report["events"], 19)
            self.assertEqual(report["schemas"], {"schema2": 2, "schema1": 17})
            with (root / "events.jsonl").open("a") as stream:
                stream.write('{"schema":2}\n')
            with self.assertRaisesRegex(ValueError, "events.jsonl: 5 findings; first: .*line.: 20"):
                cli.run(profile, ("validate",))


if __name__ == "__main__":
    unittest.main()
