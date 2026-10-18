import json
import tempfile
import unittest
from pathlib import Path

from cake_taste.files import atomic_bytes, atomic_json, sha256_bytes, sha256_file


class FilesTest(unittest.TestCase):
    def test_digests_match(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "blob"
            path.write_bytes(b"abc")
            self.assertEqual(sha256_file(path), sha256_bytes(b"abc"))
            self.assertEqual(sha256_bytes(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_atomic_bytes_creates_parents_replaces_and_leaves_no_temporary(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "a" / "b" / "out.bin"
            atomic_bytes(path, b"one")
            atomic_bytes(path, b"two")
            self.assertEqual(path.read_bytes(), b"two")
            self.assertEqual([item.name for item in path.parent.iterdir()], ["out.bin"])

    def test_atomic_json_options(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "x.json"
            atomic_json(path, {"b": 1, "a": "é"})
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("}\n"))
            self.assertIn("é", text)
            self.assertLess(text.index('"b"'), text.index('"a"'))
            atomic_json(path, {"b": 1, "a": "é"}, sort_keys=True, ensure_ascii=True)
            text = path.read_text(encoding="utf-8")
            self.assertIn("\\u00e9", text)
            self.assertLess(text.index('"a"'), text.index('"b"'))
            self.assertEqual(json.loads(text), {"a": "é", "b": 1})

    def test_atomic_json_failure_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "x.json"
            atomic_json(path, {"ok": True})
            with self.assertRaises(TypeError):
                atomic_json(path, {"bad": object()})
            self.assertEqual(json.loads(path.read_text()), {"ok": True})
