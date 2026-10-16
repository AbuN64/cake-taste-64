from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cake_taste.config import Profile, Tooling
from cake_taste.dependencies import DEPENDENCIES, DependencyError, invoke, verify_dependencies


def distribution(name: str, *, revision: str | None = None, missing_origin: bool = False):
    dependency = next(d for d in DEPENDENCIES.values() if d.distribution == name)
    raw = json.dumps({"url": dependency.url, "vcs_info": {"vcs": "git", "commit_id": revision or dependency.revision}})
    objective = DEPENDENCIES["unbake"]
    return SimpleNamespace(
        version="fixture",
        read_text=lambda _: None if missing_origin else raw,
        requires=[f"unbake @ git+{objective.url}@{objective.revision}"] if name == "recipemaker" else [],
    )


class DependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.profile = Profile(
            "fixture",
            root / "source",
            root / "runtime",
            root / "evidence",
            Tooling(root, root / "host.toml", 5.0),
            root / "profile.json",
            "0" * 64,
            {},
        )
        patched = mock.patch("cake_taste.dependencies.metadata.distribution", side_effect=distribution)
        self.metadata = patched.start()
        self.addCleanup(patched.stop)

    def test_both_direct_pins_and_declared_package_requirements(self) -> None:
        result = verify_dependencies()
        self.assertEqual(set(result), {"unbake", "recipe"})
        self.assertEqual([c.args[0] for c in self.metadata.call_args_list], ["unbake", "recipemaker"])
        document = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
        requirements = document["project"]["dependencies"]
        for dependency in DEPENDENCIES.values():
            self.assertIn(f"{dependency.distribution} @ git+{dependency.url}@{dependency.revision}", requirements)

    def test_missing_or_wrong_provenance_fails(self) -> None:
        for kwargs in ({"revision": "1" * 40}, {"missing_origin": True}):
            self.metadata.side_effect = lambda name, kwargs=kwargs: distribution(name, **kwargs)
            with self.assertRaises(DependencyError):
                verify_dependencies()

    def test_recipe_pin_compatibility_is_not_bypassed(self) -> None:
        def incompatible(name):
            found = distribution(name)
            if name == "recipemaker":
                found.requires = ["unbake>=0"]
            return found

        self.metadata.side_effect = incompatible
        with self.assertRaisesRegex(DependencyError, "requirement differs"):
            verify_dependencies()

    def test_unbake_uses_isolated_public_cli_and_preserves_envelope(self) -> None:
        payload = {"ok": True, "result": {"count": 3}, "findings": [], "stages": {}, "invocation": "objective"}
        process = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"objective log\n")
        with mock.patch("cake_taste.dependencies.subprocess.run", return_value=process) as run:
            result = invoke("unbake", self.profile, ["report", "--next"], stdin=b"input")
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [str(Path(sys.executable).absolute()), "-I", "-c"])
        self.assertIn("from unbake.cli import main", command[3])
        self.assertEqual(
            command[4:],
            [
                "--project",
                str(self.profile.tooling.project),
                "report",
                "--next",
            ],
        )
        self.assertEqual(run.call_args.kwargs["input"], b"input")
        self.assertEqual(run.call_args.kwargs["timeout"], 5.0)
        self.assertEqual(run.call_args.kwargs["env"]["UNBAKE_HOST"], str(self.profile.tooling.host))
        self.assertTrue(result.ok)
        self.assertEqual(result.document, payload)

    def test_recipe_flat_json_is_preserved_and_nonzero_exit_is_failure(self) -> None:
        payload = {"ok": True, "open": 4}
        with mock.patch(
            "cake_taste.dependencies.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, json.dumps(payload).encode(), b""),
        ) as run:
            result = invoke("recipe", self.profile, ["status"])
        self.assertIn("from recipemaker.cli import main", run.call_args.args[0][3])
        self.assertEqual(run.call_args.args[0][-2:], ["status", "--json"])
        self.assertEqual(result.document, payload)
        self.assertFalse(result.ok)

    def test_malformed_and_incomplete_machine_output_fails(self) -> None:
        for raw in (b"not JSON", b'{"ok":true}', b'{"ok":"yes"}', b"{}\n{}"):
            with (
                mock.patch(
                    "cake_taste.dependencies.subprocess.run", return_value=subprocess.CompletedProcess([], 0, raw, b"")
                ),
                self.assertRaises(DependencyError),
            ):
                invoke("unbake", self.profile, ["report"])

    def test_timeout_and_project_override_fail_explicitly(self) -> None:
        with (
            mock.patch("cake_taste.dependencies.subprocess.run", side_effect=subprocess.TimeoutExpired("tool", 5)),
            self.assertRaisesRegex(DependencyError, "timeout"),
        ):
            invoke("recipe", self.profile, ["status"])
        with mock.patch("cake_taste.dependencies.subprocess.run") as run:
            for args in (["report", "--project", "/elsewhere"], ["report", "--host=x"], []):
                with self.assertRaises(DependencyError):
                    invoke("unbake", self.profile, args)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
