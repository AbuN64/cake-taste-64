import json
import os
import tempfile
import unittest
from pathlib import Path

from cake_taste.media.cli import run
from cake_taste.media.publish import publish
from cake_taste.media.store import audit


class Profile:
    def __init__(self, path, root):
        self.source_file = path
        self.root = root

    def section(self, name):
        return (
            {
                "root": str(self.root),
                "timezone": "UTC",
                "title": "Test gallery",
                "viewer_url": "https://example.test/view?a=1&b=2",
            }
            if name == "media"
            else {}
        )

    def resolve_path(self, value):
        path = Path(value)
        return path if path.is_absolute() else self.source_file.parent / path


def ppm():
    return b"P6\n# tiny\n2 1\n255\n" + bytes([255, 0, 0, 0, 255, 0])


class MediaStoreTest(unittest.TestCase):
    def test_publish_converts_ppm_escapes_text_and_keeps_location_stable_id(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source_a = base / "first" / "capture.ppm"
            source_b = base / "second" / "capture.ppm"
            source_a.parent.mkdir()
            source_b.parent.mkdir()
            source_a.write_bytes(ppm())
            source_b.write_bytes(ppm())
            os.utime(source_a, (1000000000, 1000000000))
            os.utime(source_b, (1000000000, 1000000000))
            first = base / "gallery-a"
            second = base / "gallery-b"
            result = publish(
                first,
                [source_a],
                timezone_name="UTC",
                page_title="<Gallery>",
                viewer_url="https://example.test/?a=1&b=2",
                title="<script>",
                description="red & green",
                revision="abc",
            )
            publish(second, [source_b], timezone_name="UTC", page_title="<Gallery>")
            a = json.loads((first / "catalog.json").read_text())["items"][0]
            b = json.loads((second / "catalog.json").read_text())["items"][0]
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(a["date"], "2001-09-09")
            self.assertEqual(a["revision"], "abc")
            self.assertEqual(result["items"], 1)
            page = (first / "index.html").read_text()
            self.assertIn("&lt;script&gt;", page)
            self.assertIn("red &amp; green", page)
            self.assertIn("a=1&amp;b=2", page)
            self.assertNotIn("<script>", page)
            report = audit(first)
            self.assertEqual(report["items"], 1)
            self.assertEqual(report["blobs"], 1)

    def test_tampered_assets_fail_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "capture.ppm"
            source.write_bytes(ppm())
            root = base / "gallery"
            publish(root, [source], timezone_name="UTC", page_title="Gallery")
            item = json.loads((root / "catalog.json").read_text())["items"][0]
            blob = root / item["file"]
            blob.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "changed media blob"):
                audit(root)

    def test_public_media_cli_uses_profile_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "capture.ppm"
            source.write_bytes(ppm())
            profile = Profile(base / "profile.json", base / "media")
            result = run(profile, ["publish", "--source", str(source), "--title", "One"])
            self.assertEqual(result["action"], "publish")
            report = run(profile, ["audit"])
            self.assertEqual(report["items"], 1)
            self.assertEqual(report["blobs"], 1)


if __name__ == "__main__":
    unittest.main()
