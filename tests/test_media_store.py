import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from cake_taste.media.cli import run
from cake_taste.media.publish import publish
from cake_taste.media.store import audit, import_verified


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

    def test_one_time_import_preserves_historical_id_catalog_bytes_and_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "old"
            (source / "assets").mkdir(parents=True)
            blob = b"\x89PNG\r\n\x1a\nold historical bytes"
            digest = hashlib.sha256(blob).hexdigest()
            relative = f"assets/{digest}.png"
            (source / relative).write_bytes(blob)
            catalog = {
                "timezone": "America/Vancouver",
                "items": [
                    {
                        "id": "historical-id",
                        "file": relative,
                        "sha256": digest,
                        "type": "image",
                        "date": "2026-10-16",
                        "captured_at": "2026-10-16T10:00:00-07:00",
                        "title": "Old capture",
                    }
                ],
            }
            raw = (json.dumps(catalog, indent=2) + "\n").encode()
            (source / "catalog.json").write_bytes(raw)
            (source / "index.html").write_text("<h1>Old index</h1>")
            target = base / "imported"
            result = import_verified(source, target)
            self.assertEqual((target / "catalog.json").read_bytes(), raw)
            self.assertEqual((target / relative).read_bytes(), blob)
            self.assertEqual(result["catalog_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(json.loads((target / "catalog.json").read_text())["items"][0]["id"], "historical-id")
            self.assertEqual(audit(target)["manifest_sha256"], audit(source)["manifest_sha256"])
            with self.assertRaises(FileExistsError):
                import_verified(source, target)

    def test_tampered_or_symlinked_assets_fail_before_import(self):
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
            with self.assertRaises(ValueError):
                import_verified(root, base / "copy")

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
