"""The rig deploy: .deploy shape, refusals before the rig is touched, a new build directory, an atomic swap of the
live link, the no-op path and the boot-idle readback, all through fake ssh and git seams."""

import importlib.util
import os
import stat
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci/deploy_rig.py"
spec = importlib.util.spec_from_file_location("deploy_rig", SCRIPT)
deploy_rig = importlib.util.module_from_spec(spec)
sys.modules["deploy_rig"] = deploy_rig
spec.loader.exec_module(deploy_rig)

SHA = "7a6ab5ce4712939ea310ee62e2fbc759920f6363"
OLD = "snaps/oracle-a9afb02e6e33-1793086357"
NEW = f"oracle-{SHA[:12]}-1793400000"
HASH = "63576cbe05149fa7"


class FakeGit:
    def __init__(self, fetch_code=0, ancestor_code=0):
        self.fetch_code, self.ancestor_code, self.calls = fetch_code, ancestor_code, []

    def __call__(self, *args):
        self.calls.append(args)
        if "fetch" in args:
            return self.fetch_code, "fetch failed" if self.fetch_code else ""
        if "merge-base" in args:
            return self.ancestor_code, ""
        return 0, ""


class FakeRig:
    """Answers the four kinds of rig command and records every command in order."""

    def __init__(self, live=OLD, hashes=(HASH, HASH), build_code=0, readback_code=0):
        self.live, self.hashes, self.build_code, self.readback_code = live, hashes, build_code, readback_code
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        if command.startswith("readlink "):
            return 0, self.live
        if " build " in command and "mkdir \"$new\"" in command:
            return self.build_code, "" if not self.build_code else "boom"
        if "tail -n 3" in command:
            return 0, "ninja: build stopped"
        if " swap " in command:
            self.live = f"snaps/{command.split(' swap ')[1].strip()}"
            return 0, ""
        if "boot-idle" in command or "state-oracle.txt" in command:
            return self.readback_code, "\n".join(self.hashes)
        raise AssertionError(command)

    def kinds(self):
        return [
            "readlink" if c.startswith("readlink") else "build" if " build " in c else "swap" if " swap " in c
            else "readback" if "state-oracle" in c else "tail"
            for c in self.commands
        ]


class DeployShape(unittest.TestCase):
    def test_deploy_file_names_one_executable_shebang_script(self):
        text = (ROOT / ".deploy").read_text()
        self.assertEqual(text, "ci/deploy_rig.py\n")
        self.assertFalse(any(ch.isspace() for ch in text.strip()))
        mode = (ROOT / text.strip()).stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        self.assertTrue((ROOT / text.strip()).read_text().startswith("#!/usr/bin/env python3\n"))

    def test_remote_scripts_parse(self):
        for body in (deploy_rig.BUILD, deploy_rig.SWAP, deploy_rig.READBACK):
            self.assertEqual(subprocess.run(["bash", "-n"], input=body, text=True, check=False).returncode, 0)


class Refusals(unittest.TestCase):
    def run_main(self, environ):
        calls = []
        old = deploy_rig.ssh, deploy_rig.git
        deploy_rig.ssh = lambda c: calls.append(c) or (0, "")
        deploy_rig.git = lambda *a: calls.append(a) or (0, "")
        try:
            return deploy_rig.main(environ), calls
        finally:
            deploy_rig.ssh, deploy_rig.git = old

    def test_unset_or_malformed_sha_refuses_before_any_seam(self):
        for environ in ({}, {deploy_rig.ENV: "abc123"}, {deploy_rig.ENV: SHA.upper()}):
            with redirect_stderr(StringIO()):
                code, calls = self.run_main(environ)
            self.assertEqual((code, calls), (1, []))

    def test_unset_prints_one_stderr_line_exit_1(self):
        env = {k: v for k, v in os.environ.items() if k != deploy_rig.ENV}
        done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(len(done.stderr.splitlines()), 1)

    def test_sha_not_on_gitea_main_refuses_without_touching_the_rig(self):
        rig = FakeRig()
        with self.assertRaisesRegex(deploy_rig.Refused, "not on gitea main"):
            deploy_rig.deploy(SHA, rig, FakeGit(ancestor_code=1))
        self.assertEqual(rig.commands, [])

    def test_unreachable_gitea_refuses_without_touching_the_rig(self):
        rig = FakeRig()
        with self.assertRaisesRegex(deploy_rig.Refused, "cannot fetch gitea main"):
            deploy_rig.deploy(SHA, rig, FakeGit(fetch_code=128))
        self.assertEqual(rig.commands, [])

    def test_refusal_is_one_stderr_line_exit_1(self):
        old = deploy_rig.ssh, deploy_rig.git
        deploy_rig.ssh, deploy_rig.git = FakeRig(), FakeGit(ancestor_code=1)
        try:
            err = StringIO()
            with redirect_stderr(err):
                code = deploy_rig.main({deploy_rig.ENV: SHA})
        finally:
            deploy_rig.ssh, deploy_rig.git = old
        self.assertEqual((code, len(err.getvalue().splitlines())), (1, 1))


class Deploy(unittest.TestCase):
    def deploy(self, rig, git=None):
        return deploy_rig.deploy(SHA, rig, git or FakeGit(), now=lambda: 1793400000.9)

    def test_builds_a_new_directory_and_swaps_atomically_keeping_the_old(self):
        rig = FakeRig()
        lines = self.deploy(rig)
        self.assertEqual(rig.kinds(), ["readlink", "build", "swap", "readlink", "readback"])
        build = rig.commands[1]
        self.assertTrue(build.endswith(f" build {SHA} {NEW} {deploy_rig.GITEA}"))
        self.assertNotIn("a9afb02e6e33", build)
        body = deploy_rig.BUILD
        self.assertIn('new=$live/snaps/$name\nmkdir "$new"', body)  # mkdir refuses an existing directory
        self.assertIn('"$HOME/finish/slot" nice -n 19 cmake --build', body)
        self.assertIn("rm -rf src deps build", body)
        self.assertNotRegex(body, r"rm -rf [^s]")  # only its own scratch inside the new directory is removed
        swap = rig.commands[2]
        self.assertIn(f" swap {NEW}", swap)
        self.assertIn('ln -sfn "snaps/$1" "$live/.oracle.new"', deploy_rig.SWAP)
        self.assertIn('mv -T "$live/.oracle.new" "$live/oracle"', deploy_rig.SWAP)
        self.assertNotIn("rm ", deploy_rig.SWAP)
        self.assertEqual(lines[0], f"live oracle before: {OLD}")
        self.assertEqual(lines[1], f"live oracle after: snaps/{NEW} (previous {OLD} kept)")
        self.assertEqual(lines[-1], f"readback boot-idle {HASH} {HASH}")

    def test_live_already_at_the_sha_builds_and_swaps_nothing_but_reads_back(self):
        rig = FakeRig(live=f"snaps/oracle-{SHA[:12]}-1793000000")
        lines = self.deploy(rig)
        self.assertEqual(rig.kinds(), ["readlink", "readback"])
        self.assertIn("build and swap skipped", lines[1])
        self.assertEqual(lines[-1], f"readback boot-idle {HASH} {HASH}")

    def test_readback_mismatch_refuses(self):
        rig = FakeRig(hashes=(HASH, "0123456789abcdef"))
        with self.assertRaisesRegex(deploy_rig.Refused, f"hashes differ: {HASH} 0123456789abcdef"):
            self.deploy(rig)

    def test_readback_run_failure_refuses(self):
        for rig in (FakeRig(readback_code=1), FakeRig(hashes=(HASH,))):
            with self.assertRaisesRegex(deploy_rig.Refused, "readback boot-idle failed"):
                self.deploy(rig)

    def test_failed_build_refuses_and_never_swaps(self):
        rig = FakeRig(build_code=2)
        with self.assertRaisesRegex(deploy_rig.Refused, f"build of {SHA[:12]} exit 2 in snaps/{NEW}"):
            self.deploy(rig)
        self.assertNotIn("swap", rig.kinds())
        self.assertEqual(rig.live, OLD)

    def test_readback_requires_two_runs_of_boot_idle_through_the_live_link(self):
        body = deploy_rig.READBACK
        self.assertIn('readlink -f "$HOME/rig-state/tas/live/oracle"', body)
        self.assertIn("for n in 1 2", body)
        self.assertIn("boot-idle", body)
        self.assertIn("cut -c1-16", body)


if __name__ == "__main__":
    unittest.main()
