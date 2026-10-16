from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cake_taste.cli import main
from cake_taste.config import ConfigError, load_profile


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "source").mkdir()
        (self.root / "host.toml").write_text("host = 'fixture'\n")
        self.file = self.root / "profile.json"
        self.value = {
            "schema_version": 1,
            "name": "fixture",
            "project_root": "source",
            "runtime_root": "state",
            "output_root": "evidence",
            "tooling": {"project": "source", "host": "host.toml", "timeout_seconds": 5},
            "build": {"steps": [{"argv": ["tool", "argument"]}]},
            "target": {
                "asset_ledger": "evidence/asset-ledger.json",
                "asset_observations": "evidence/asset-observations.jsonl",
            },
        }

    def load(self):
        self.file.write_text(json.dumps(self.value))
        return load_profile(self.file)

    def test_explicit_paths_and_fresh_sections(self) -> None:
        profile = self.load()
        self.assertEqual(profile.project_root, self.root / "source")
        self.assertEqual(
            profile.resolve_path(profile.section("target")["asset_observations"]),
            self.root / "evidence/asset-observations.jsonl",
        )
        self.assertFalse(profile.runtime_root.exists())
        self.assertFalse(profile.output_root.exists())
        changed = profile.section("build")
        changed["steps"][0]["argv"].append("modified")
        self.assertEqual(profile.section("build")["steps"][0]["argv"], ["tool", "argument"])
        with self.assertRaises(FrozenInstanceError):
            profile.name = "modified"
        self.assertEqual(len(profile.digest), 64)

    def test_unknown_missing_and_ill_typed_keys_fail(self) -> None:
        for key, value in (("schema_version", True), ("name", ""), ("build", []), ("tooling", None)):
            original = self.value[key]
            self.value[key] = value
            with self.assertRaises(ConfigError):
                self.load()
            self.value[key] = original
        self.value["unknown"] = 1
        with self.assertRaisesRegex(ConfigError, "unknown keys"):
            self.load()
        del self.value["unknown"]
        del self.value["name"]
        with self.assertRaisesRegex(ConfigError, "missing keys"):
            self.load()

    def test_tooling_requires_finite_explicit_timeout(self) -> None:
        for value in (True, 0, -1, float("inf"), 10**1000):
            self.value["tooling"]["timeout_seconds"] = value
            with self.assertRaises(ConfigError):
                self.load()
        self.value["tooling"]["timeout_seconds"] = 5
        self.value["tooling"]["host"] = "absent.toml"
        with self.assertRaises(ConfigError):
            self.load()

    def test_runtime_does_not_enter_target_source_tree(self) -> None:
        self.value["runtime_root"] = "source/runtime"
        with self.assertRaisesRegex(ConfigError, "outside the target source"):
            self.load()

    def test_duplicate_keys_and_missing_sections_fail(self) -> None:
        self.file.write_text('{"name":"a","name":"b"}')
        with self.assertRaisesRegex(ConfigError, "duplicate JSON key"):
            load_profile(self.file)
        profile = self.load()
        with self.assertRaisesRegex(ConfigError, "absent"):
            profile.section("stream")
        with self.assertRaisesRegex(ConfigError, "unknown component"):
            profile.section("plugin")

    def test_optional_tooling_is_not_inferred(self) -> None:
        del self.value["tooling"]
        self.assertIsNone(self.load().tooling)

    def test_public_profile_command_and_owned_domain_dispatch(self) -> None:
        self.load()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--profile", str(self.file), "profile"])
        self.assertEqual(code, 0)
        envelope = json.loads(output.getvalue())
        self.assertTrue(envelope["ok"])
        self.assertEqual(set(envelope), {"ok", "result", "findings", "stages", "invocation"})
        component = SimpleNamespace(run=mock.Mock(return_value={"completed": True}))
        with (
            mock.patch("cake_taste.cli.importlib.import_module", return_value=component) as imported,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(["--profile", str(self.file), "media", "publish", "file.png"]), 0)
        imported.assert_called_once_with("cake_taste.media.cli")
        self.assertEqual(component.run.call_args.args[1], ("publish", "file.png"))

    def test_public_failure_is_one_named_envelope(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["profile"]), 1)
        envelope = json.loads(output.getvalue())
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["findings"][0]["key"], "config.missing")


if __name__ == "__main__":
    unittest.main()
