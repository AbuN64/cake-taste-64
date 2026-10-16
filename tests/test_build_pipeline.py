import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cake_taste.build.runner import Runner


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, text=True).stdout.strip()


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class Profile:
    def __init__(self, path, data):
        self.source_file = path
        self.project_root = path.parent / "project"
        self.runtime_root = path.parent / "runtime"
        self.output_root = path.parent / "output"
        path.write_text(json.dumps(data))
        self.data = data

    def section(self, name):
        return copy.deepcopy(self.data.get(name, {}))

    def resolve_path(self, value):
        path = Path(value)
        return path if path.is_absolute() else self.source_file.parent / path


class BuildPipelineTest(unittest.TestCase):
    def make_profile(self, directory):
        root = Path(directory)
        repositories = {}
        commits = {}
        for name in ("engine", "adapter"):
            repo = root / name
            repo.mkdir()
            git(repo, "init", "-b", "main")
            (repo / "source.c").write_text(f"committed {name}\n")
            commits[name] = commit(repo, f"initial {name}")
            repositories[name] = repo
        arena = root / "arena.pack"
        ui = root / "frontend.pack"
        arena.write_bytes(b"arena bytes")
        ui.write_bytes(b"frontend bytes")
        build_script = (
            "from pathlib import Path; p=Path('${workspace}')/'compiled'; "
            "p.mkdir(parents=True,exist_ok=True); "
            "(p/'port').write_bytes(b'port binary'); "
            "(p/'probe').write_bytes(b'probe binary')"
        )
        data = {
            "build": {
                "workspace": str(root / "runtime" / "build"),
                "sources": [
                    {"name": name, "repo": str(repo), "ref": "refs/heads/main", "protected_top": ["build"]}
                    for name, repo in repositories.items()
                ],
                "inputs": [
                    {
                        "name": "arena",
                        "path": str(arena),
                        "sha256": hashlib.sha256(arena.read_bytes()).hexdigest(),
                        "filename": "arena.pack",
                    },
                    {
                        "name": "ui",
                        "path": str(ui),
                        "sha256": hashlib.sha256(ui.read_bytes()).hexdigest(),
                        "filename": "frontend.pack",
                    },
                ],
                "commands": [
                    {"name": "compile", "argv": [sys.executable, "-c", build_script]},
                    {
                        "name": "public-fatal",
                        "argv": [sys.executable, "-c", "print('expected owner'); raise SystemExit(1)"],
                        "expected_exit_codes": [1],
                        "expect_all": ["expected owner"],
                        "forbid": ["boot-complete"],
                    },
                ],
                "outputs": [
                    {"name": "port", "path": "${workspace}/compiled/port"},
                    {"name": "probe", "path": "${workspace}/compiled/probe"},
                ],
                "releases": [
                    {
                        "name": "port",
                        "root": "${runtime_root}/releases/port",
                        "revision": "${commit.engine.short7}-${commit.adapter.short7}",
                        "files": [
                            {"source": "output.port", "target": "ragewars"},
                            {"source": "input.ui", "target": "data/frontend.pack"},
                        ],
                        "activate": [{"argv": [sys.executable, "-c", "print('port ready')"]}],
                    },
                    {
                        "name": "experiment",
                        "root": "${runtime_root}/releases/experiment",
                        "revision": "${commit.engine.short7}-${commit.adapter.short7}",
                        "files": [
                            {"source": "output.probe", "target": "scene-probe"},
                            {"source": "input.arena", "target": "data/arena.pack"},
                        ],
                        "activate": [{"argv": [sys.executable, "-c", "print('probe ready')"]}],
                    },
                ],
            }
        }
        profile = Profile(root / "profile.json", data)
        return profile, repositories, commits

    def test_two_committed_sources_promote_one_verified_release_mechanism(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, repos, commits = self.make_profile(directory)
            (repos["engine"] / "source.c").write_text("WRITER WIP\n")
            (repos["adapter"] / "untracked.txt").write_text("ADAPTER WIP\n")
            runner = Runner(profile)
            first = runner.attempt()
            self.assertEqual(first["status"], "promoted")
            source_root = runner.workspace / "sources"
            self.assertEqual((source_root / "engine/source.c").read_text(), "committed engine\n")
            self.assertFalse((source_root / "adapter/untracked.txt").exists())
            mtime = (source_root / "engine/source.c").stat().st_mtime_ns
            revision = commits["engine"][:7] + "-" + commits["adapter"][:7]
            for role, binary, asset in (
                ("port", "ragewars", "frontend.pack"),
                ("experiment", "scene-probe", "arena.pack"),
            ):
                current = profile.runtime_root / "releases" / role / "current"
                self.assertEqual(current.resolve().name, revision)
                self.assertTrue((current / binary).is_file())
                self.assertTrue((current / "data" / asset).is_file())
            second = Runner(profile).attempt()
            self.assertEqual(second["status"], "promoted")
            self.assertEqual((source_root / "engine/source.c").stat().st_mtime_ns, mtime)
            receipt = json.loads(Path(second["receipt"]).read_text())
            self.assertEqual(receipt["sources"]["adapter"]["commit"], commits["adapter"])
            self.assertEqual(receipt["steps"][1]["returncode"], 1)

    def test_failed_gate_never_switches_current(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, _, _ = self.make_profile(directory)
            profile.data["build"]["commands"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(2)"]
            profile.source_file.write_text(json.dumps(profile.data))
            result = Runner(profile).attempt()
            self.assertEqual(result["status"], "failed")
            self.assertFalse((profile.runtime_root / "releases/port/current").exists())
            self.assertFalse((profile.runtime_root / "releases/experiment/current").exists())

    def test_superseded_adapter_commit_never_promotes_stale_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, repos, commits = self.make_profile(directory)
            repo = repos["adapter"]
            code = (
                "from pathlib import Path; import subprocess; p=Path(" + repr(str(repo)) + "); "
                "(p/'new.c').write_text('new committed source'); "
                "subprocess.run(['git','-C',str(p),'add','-A'],check=True); "
                "subprocess.run(['git','-C',str(p),'-c','user.name=Test','-c',"
                "'user.email=test@example.invalid','commit','-m','newer'],check=True,stdout=subprocess.DEVNULL)"
            )
            profile.data["build"]["commands"].append({"name": "supersede", "argv": [sys.executable, "-c", code]})
            profile.source_file.write_text(json.dumps(profile.data))
            result = Runner(profile).attempt()
            self.assertEqual(result["status"], "failed")
            self.assertNotEqual(git(repo, "rev-parse", "HEAD"), commits["adapter"])
            self.assertFalse((profile.runtime_root / "releases/port/current").exists())

    def test_activation_failure_restores_both_currents(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, _, _ = self.make_profile(directory)
            profile.data["build"]["releases"][1]["activate"] = [{"argv": [sys.executable, "-c", "raise SystemExit(3)"]}]
            profile.source_file.write_text(json.dumps(profile.data))
            result = Runner(profile).attempt()
            self.assertEqual(result["status"], "failed")
            self.assertFalse((profile.runtime_root / "releases/port/current").exists())
            self.assertFalse((profile.runtime_root / "releases/experiment/current").exists())
            receipt = json.loads(Path(result["receipt"]).read_text())
            self.assertIn(
                ("activate-experiment-0", 3), [(step["name"], step["returncode"]) for step in receipt["steps"]]
            )


if __name__ == "__main__":
    unittest.main()
