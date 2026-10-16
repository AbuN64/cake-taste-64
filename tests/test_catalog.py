"""Regression checks for dishonest census and unsafe embedded ledger text."""

import hashlib
import json
import unittest

from cake_taste.catalog.cli import document, render


class CatalogTests(unittest.TestCase):
    def fixture(self, records=None):
        rows = records or [
            {
                "asset_id": "test:image:1",
                "name": "</script><script>bad()</script>",
                "aliases": [],
                "type": "texture",
                "format": "I4",
                "revision": "test",
                "rom_sha256": "a" * 64,
                "verification_status": "checked",
                "locations": [{"space": "rom", "range": [0, 16]}],
            }
        ]
        raw = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
        summary = {
            "distinct_assets_jsonl_sha256": hashlib.sha256(raw).hexdigest(),
            "rom_sha256": "a" * 64,
            "rom_size": 32,
            "revision": "test",
            "identified_total_including_observed": 1,
            "confirmed": {
                "distinct_assets": 1,
                "named": 1,
                "unnamed": 0,
                "counts_by_type": {"texture": 1},
                "counts_by_format": {"I4": 1},
            },
            "observed_pending_validation": {
                "distinct_assets": 0,
                "named": 0,
                "unnamed": 0,
                "counts_by_type": {},
                "counts_by_format": {},
            },
        }
        return raw, summary, {"confirmed": ["checked"], "observed_pending_validation": ["pending"]}

    def test_census_and_script_text(self):
        raw, summary, groups = self.fixture()
        data = document(raw, json.dumps(summary), groups)
        page = render(data, "Test <catalog>").decode()
        self.assertNotIn("</script><script>bad()", page)
        self.assertIn("Test &lt;catalog&gt;", page)
        encoded = page.split('id="catalog-data" type="application/json">')[1].split("</script>")[0]
        self.assertEqual(json.loads(encoded)["rows"], data["rows"])

    def test_mismatched_hash_and_counts_refused(self):
        raw, summary, groups = self.fixture()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            document(raw + b" ", json.dumps(summary), groups)
        summary["confirmed"]["distinct_assets"] = 9
        with self.assertRaisesRegex(ValueError, "census"):
            document(raw, json.dumps(summary), groups)

    def test_duplicate_identity_refused(self):
        raw, _, groups = self.fixture()
        raw, summary, _ = self.fixture([json.loads(raw), json.loads(raw)])
        with self.assertRaisesRegex(ValueError, "Duplicate asset"):
            document(raw, json.dumps(summary), groups)

    def test_unclassified_status_and_rom_bounds_refused(self):
        raw, summary, groups = self.fixture()
        with self.assertRaisesRegex(ValueError, "Unclassified"):
            document(raw, json.dumps(summary), {"confirmed": ["other"], "observed_pending_validation": ["pending"]})
        summary["rom_size"] = 8
        with self.assertRaisesRegex(ValueError, "exceeds ROM"):
            document(raw, json.dumps(summary), groups)


if __name__ == "__main__":
    unittest.main()
