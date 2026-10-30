"""The rig deploy: .deploy shape, refusals before the rig is touched, a new build directory, an atomic swap of the
live link, the no-op path and the boot-idle readback, all through fake ssh and git seams."""

import importlib.util
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path, PurePosixPath

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
    def __init__(self, fetch_code=0, ancestor_code=0, head=None, events=None):
        self.fetch_code, self.ancestor_code, self.head, self.calls = fetch_code, ancestor_code, head, []
        self.events = events

    def __call__(self, *args):
        if self.events is not None:
            self.events.append("git")
        return self.answer(*args)

    def answer(self, *args):
        self.calls.append(args)
        if "rev-parse" in args:
            return 0, self.head or ""
        if "fetch" in args:
            return self.fetch_code, "fetch failed" if self.fetch_code else ""
        if "merge-base" in args:
            return self.ancestor_code, ""
        return 0, ""


class FakeLock:
    """The rig lock seam: records `lock` and `unlock` in `events` (shared with the fake rig to prove ordering)."""

    def __init__(self, events=None, notes=()):
        self.events = [] if events is None else events
        self.notes = list(notes)

    def __call__(self):
        return self

    def __enter__(self):
        self.events.append("lock")
        return list(self.notes)

    def __exit__(self, *exc):
        self.events.append("unlock")
        return False


CHECKOUT = "/home/abu/finish/DEPLOY-SUITE.AbCdEf/checkout"
SUMMARY = "1767 passed, 0 failed, 5 skipped in 204 s"


def suite_output(sha=SHA, imported=f"{CHECKOUT}/src/cake_taste/__init__.py", failed=None):
    lines = [f"suite-checkout: {CHECKOUT}", f"suite-sha: {sha}", f"suite-import: {imported}"]
    if failed:
        lines += ["suite-summary: 1766 passed, 1 failed, 5 skipped in 204 s", f"suite-first-failure: {failed}"]
    else:
        lines.append(f"suite-summary: {SUMMARY}")
    return "\n".join([*lines, "suite-log: /home/abu/finish/deploy-suite-7a6ab5ce4712.log"])


class FakeRig:
    """Answers the five kinds of rig command and records every command in order."""

    def __init__(
        self, live=OLD, hashes=(HASH, HASH), build_code=0, readback_code=0, suite_code=0, suite_out=None, events=None
    ):
        self.events = events
        self.live, self.hashes, self.build_code, self.readback_code = live, hashes, build_code, readback_code
        self.suite_code, self.suite_out = suite_code, suite_out
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        if self.events is not None:
            self.events.append("ssh")
        if command.startswith("readlink "):
            return 0, self.live
        if " build " in command and 'mkdir "$new"' in command:
            return self.build_code, "" if not self.build_code else "boom"
        if " suite " in command and "DEPLOY-SUITE" in command:
            if self.suite_out is not None:
                return self.suite_code, self.suite_out
            sha = re.search(r" suite ([0-9a-f]{40}) ", command).group(1)
            failed = "tests.test_x.Case.test_y" if self.suite_code else None
            return self.suite_code, suite_output(sha=sha, failed=failed)
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
            "readlink"
            if c.startswith("readlink")
            else "build"
            if " build " in c
            else "swap"
            if " swap " in c
            else "suite"
            if "DEPLOY-SUITE" in c
            else "readback"
            if "state-oracle" in c
            else "tail"
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
        for body in (deploy_rig.BUILD, deploy_rig.SWAP, deploy_rig.READBACK, deploy_rig.SUITE):
            self.assertEqual(subprocess.run(["bash", "-n"], input=body, text=True, check=False).returncode, 0)


class Refusals(unittest.TestCase):
    def run_main(self, environ):
        calls = []
        old = deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock
        deploy_rig.ssh = lambda c: calls.append(c) or (0, "")
        deploy_rig.git = lambda *a: calls.append(a) or (0, "")
        deploy_rig.rig_lock = FakeLock()
        try:
            return deploy_rig.main(environ), calls
        finally:
            deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock = old

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
            deploy_rig.deploy(SHA, rig, FakeGit(ancestor_code=1), FakeLock())
        self.assertEqual(rig.commands, [])

    def test_unreachable_gitea_refuses_without_touching_the_rig(self):
        rig = FakeRig()
        with self.assertRaisesRegex(deploy_rig.Refused, "cannot fetch gitea main"):
            deploy_rig.deploy(SHA, rig, FakeGit(fetch_code=128), FakeLock())
        self.assertEqual(rig.commands, [])

    def test_refusal_is_one_stderr_line_exit_1(self):
        old = deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock
        deploy_rig.ssh, deploy_rig.git = FakeRig(), FakeGit(ancestor_code=1)
        deploy_rig.rig_lock = FakeLock()
        try:
            err = StringIO()
            with redirect_stderr(err):
                code = deploy_rig.main({deploy_rig.ENV: SHA})
        finally:
            deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock = old
        self.assertEqual((code, len(err.getvalue().splitlines())), (1, 1))


class Deploy(unittest.TestCase):
    def deploy(self, rig, git=None):
        return deploy_rig.deploy(SHA, rig, git or FakeGit(), FakeLock(), now=lambda: 1793400000.9)

    def test_builds_a_new_directory_and_swaps_atomically_keeping_the_old(self):
        rig = FakeRig()
        lines = self.deploy(rig)
        self.assertEqual(rig.kinds(), ["readlink", "build", "suite", "swap", "readlink", "readback"])
        build = rig.commands[1]
        self.assertTrue(build.endswith(f" build {SHA} {NEW} {deploy_rig.GITEA}"))
        self.assertNotIn("a9afb02e6e33", build)
        body = deploy_rig.BUILD
        self.assertIn('new=$live/snaps/$name\nmkdir "$new"', body)  # mkdir refuses an existing directory
        self.assertIn('"$HOME/finish/slot" nice -n 19 cmake --build', body)
        self.assertIn("rm -rf src deps build", body)
        self.assertNotRegex(body, r"rm -rf [^s]")  # only its own scratch inside the new directory is removed
        swap = rig.commands[3]
        self.assertIn(f" swap {NEW}", swap)
        self.assertIn('ln -sfn "snaps/$1" "$live/.oracle.new"', deploy_rig.SWAP)
        self.assertIn('mv -T "$live/.oracle.new" "$live/oracle"', deploy_rig.SWAP)
        self.assertNotIn("rm ", deploy_rig.SWAP)
        self.assertEqual(lines[0], f"live oracle before: {OLD}")
        self.assertEqual(lines[-2], f"live oracle after: snaps/{NEW} (previous {OLD} kept)")
        self.assertEqual(lines[-1], f"readback boot-idle {HASH} {HASH}")

    def test_live_already_at_the_sha_builds_and_swaps_nothing_but_reads_back(self):
        rig = FakeRig(live=f"snaps/oracle-{SHA[:12]}-1793000000")
        lines = self.deploy(rig)
        self.assertEqual(rig.kinds(), ["readlink", "suite", "readback"])
        self.assertIn("build and swap skipped", lines[1])
        self.assertIn(f"suite {SUMMARY}", lines[2])
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


class SuiteGate(unittest.TestCase):
    """The full suite at exactly the landed SHA, between the build and the swap."""

    def deploy(self, rig):
        return deploy_rig.deploy(SHA, rig, FakeGit(), FakeLock(), now=lambda: 1793400000.9)

    def test_a_failing_suite_refuses_naming_the_first_failing_test_and_keeps_the_old_live_build(self):
        rig = FakeRig(suite_code=1)
        with self.assertRaises(deploy_rig.Refused) as caught:
            self.deploy(rig)
        message = str(caught.exception)
        self.assertIn("suite failed at tests.test_x.Case.test_y: 1766 passed, 1 failed", message)
        self.assertEqual(len(message.splitlines()), 1)
        self.assertEqual(rig.kinds(), ["readlink", "build", "suite"])
        self.assertEqual(rig.live, OLD)

    def test_main_exits_1_with_one_stderr_line_and_no_swap_on_a_failing_suite(self):
        rig, err = FakeRig(suite_code=1), StringIO()
        old = deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock
        deploy_rig.ssh, deploy_rig.git = rig, FakeGit()
        deploy_rig.rig_lock = FakeLock()
        try:
            with redirect_stderr(err):
                code = deploy_rig.main({deploy_rig.ENV: SHA})
        finally:
            deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock = old
        self.assertEqual((code, len(err.getvalue().splitlines()), rig.live), (1, 1, OLD))
        self.assertNotIn("swap", rig.kinds())

    def test_a_failing_suite_also_refuses_when_the_live_oracle_already_reads_the_sha(self):
        rig = FakeRig(live=f"snaps/oracle-{SHA[:12]}-1793000000", suite_code=1)
        with self.assertRaisesRegex(deploy_rig.Refused, "suite failed at tests.test_x.Case.test_y"):
            self.deploy(rig)
        self.assertEqual(rig.kinds(), ["readlink", "suite"])

    def test_an_import_outside_the_checkout_refuses_even_when_every_test_passed(self):
        installed = "/home/abu/venv-decomp/lib/python3.13/site-packages/cake_taste/__init__.py"
        rig = FakeRig(suite_out=suite_output(imported=installed))
        with self.assertRaisesRegex(deploy_rig.Refused, f"cake_taste from {installed}, outside its checkout"):
            self.deploy(rig)
        self.assertNotIn("swap", rig.kinds())

    def test_a_sibling_directory_sharing_the_checkout_prefix_is_outside(self):
        rig = FakeRig(suite_out=suite_output(imported=f"{CHECKOUT}-old/src/cake_taste/__init__.py"))
        with self.assertRaisesRegex(deploy_rig.Refused, "outside its checkout"):
            self.deploy(rig)

    def test_a_suite_at_another_sha_or_with_no_facts_refuses(self):
        for out in (suite_output(sha="c" * 40), "fatal: unable to access gitea", ""):
            rig = FakeRig(suite_code=0, suite_out=out)
            with self.assertRaises(deploy_rig.Refused):
                self.deploy(rig)
            self.assertNotIn("swap", rig.kinds())

    def test_a_suite_exiting_nonzero_without_a_named_failure_still_refuses(self):
        rig = FakeRig(suite_code=1, suite_out=suite_output())
        with self.assertRaisesRegex(deploy_rig.Refused, "suite failed at"):
            self.deploy(rig)

    def test_the_log_carries_the_summary_the_sha_and_the_imported_path(self):
        lines = self.deploy(FakeRig())
        self.assertIn(f"{SHA[:12]}: suite {SUMMARY}", lines)
        self.assertIn(f"{SHA[:12]}: suite sha {SHA}", lines)
        self.assertIn(f"{SHA[:12]}: suite imported {CHECKOUT}/src/cake_taste/__init__.py", lines)

    def test_the_suite_runs_in_a_slot_at_the_exact_sha_into_the_new_snap_directory(self):
        rig = FakeRig()
        self.deploy(rig)
        command = rig.commands[2]
        self.assertTrue(command.startswith("~/finish/slot bash -c "))
        tail = f" suite {SHA} {deploy_rig.GITEA} {deploy_rig.PYTHON} {deploy_rig.LIVE}/snaps/{NEW}"
        self.assertTrue(command.endswith(tail))


def git(*args, cwd):
    identity = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", *identity, *args], cwd=cwd, check=True, capture_output=True)


class SuiteScript(unittest.TestCase):
    """The remote SUITE script and RUNNER, run for real against a tiny local repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.repo, self.home, self.dest = base / "repo", base / "home", base / "dest"
        for path in (self.repo / "src/cake_taste", self.repo / "tests", self.home / "finish", self.dest):
            path.mkdir(parents=True)
        (self.repo / "src/cake_taste/__init__.py").write_text("VALUE = 1\n")
        self.write_test("self.assertEqual(cake_taste.VALUE, 1)")
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "one", cwd=self.repo)

    def write_test(self, body):
        (self.repo / "tests/test_it.py").write_text(
            "import unittest\nimport cake_taste\n\n\nclass Case(unittest.TestCase):\n"
            f"    def test_it(self):\n        {body}\n"
        )

    def commit(self):
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "two", cwd=self.repo)
        return self.sha()

    def sha(self):
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    def run_suite(self, sha, env=None):
        command = deploy_rig.suite_command(sha, sys.executable, self.dest, str(self.repo))
        command = command.removeprefix("~/finish/slot ")
        done = subprocess.run(
            ["bash", "-c", command],
            env={"PATH": os.environ["PATH"], "HOME": str(self.home), **(env or {})},
            capture_output=True,
            text=True,
            check=False,
        )
        return done, deploy_rig.suite_facts(done.stdout)

    def test_a_passing_suite_reports_the_import_from_the_checkout_and_writes_suite_txt(self):
        sha = self.sha()
        done, facts = self.run_suite(sha, env={"PYTHONPATH": str(self.repo / "src")})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(facts["suite-sha"], sha)
        self.assertTrue(facts["suite-summary"].startswith("1 passed, 0 failed, 0 skipped in "))
        self.assertTrue(PurePosixPath(facts["suite-import"]).is_relative_to(facts["suite-checkout"]))
        self.assertNotIn(str(self.repo / "src"), facts["suite-import"])  # the checkout, not the source repo
        text = (self.dest / "suite.txt").read_text()
        self.assertIn(f"suite-sha: {sha}", text)
        self.assertIn("suite-import: ", text)
        self.assertEqual(list((self.home / "finish").glob("DEPLOY-SUITE.*")), [])  # the checkout is removed

    def test_a_failing_test_exits_1_names_the_first_failure_and_writes_no_suite_txt(self):
        self.write_test("self.assertEqual(cake_taste.VALUE, 2)")
        done, facts = self.run_suite(self.commit())
        self.assertEqual(done.returncode, 1)
        self.assertEqual(facts["suite-first-failure"], "test_it.Case.test_it")
        self.assertTrue(facts["suite-summary"].startswith("0 passed, 1 failed, 0 skipped in "))
        self.assertFalse((self.dest / "suite.txt").exists())

    def test_a_test_printing_without_a_newline_does_not_swallow_the_facts(self):
        self.write_test("import sys; sys.stdout.write('kept-by-default')")
        done, facts = self.run_suite(self.commit())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(facts["suite-summary"].startswith("1 passed, 0 failed, 0 skipped in "))

    def test_the_suite_runs_exactly_the_landed_sha_not_the_branch_tip(self):
        landed = self.sha()
        self.write_test("self.fail('a later commit')")
        self.commit()
        done, facts = self.run_suite(landed)
        self.assertEqual((done.returncode, facts["suite-sha"]), (0, landed))

    def test_an_unknown_sha_fails_before_any_test_runs(self):
        done, facts = self.run_suite("f" * 40)
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("suite-import", facts)

    def test_an_installed_copy_ahead_on_the_path_is_never_imported(self):
        installed = Path(self.tmp.name) / "site/cake_taste"
        installed.mkdir(parents=True)
        (installed / "__init__.py").write_text("VALUE = 99\n")
        done, facts = self.run_suite(self.sha(), env={"PYTHONPATH": str(installed.parent)})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(PurePosixPath(facts["suite-import"]).is_relative_to(facts["suite-checkout"]))


NEWER = "ddddddddddddeeeeeeeeeeeeeeeeeeeeeeeeeeee"


class Serialized(unittest.TestCase):
    """One rig lock per deploy: taken before any seam, held through the readback, freed on every ending."""

    def test_the_lock_wraps_every_git_and_ssh_call_from_the_first_to_the_readback(self):
        lock = FakeLock()
        rig, git = FakeRig(events=lock.events), FakeGit(events=lock.events)
        deploy_rig.deploy(SHA, rig, git, lock, now=lambda: 1793400000.9)
        self.assertEqual(lock.events[0], "lock")
        self.assertEqual(lock.events[-1], "unlock")
        self.assertEqual(lock.events.count("lock") + lock.events.count("unlock"), 2)
        self.assertEqual(rig.kinds()[-1], "readback")

    def test_the_lock_is_freed_when_the_deploy_refuses(self):
        lock = FakeLock()
        with self.assertRaises(deploy_rig.Refused):
            deploy_rig.deploy(SHA, FakeRig(suite_code=1), FakeGit(), lock, now=lambda: 1793400000.9)
        self.assertEqual(lock.events[-1], "unlock")

    def test_the_wait_note_leads_the_log(self):
        lines = deploy_rig.deploy(
            SHA, FakeRig(), FakeGit(), FakeLock(notes=["another deploy holds it"]), now=lambda: 1793400000.9
        )
        self.assertEqual(lines[0], "another deploy holds it")

    def test_two_deploys_through_one_lock_never_interleave_and_the_second_skips(self):
        mutex, events, rig = threading.Lock(), [], FakeRig()
        names = {}

        class Shared(FakeLock):
            def __enter__(self):
                mutex.acquire()
                events.append(("lock", threading.current_thread().name))
                return []

            def __exit__(self, *exc):
                events.append(("unlock", threading.current_thread().name))
                mutex.release()
                return False

        def seam(kind, inner):
            def call(*args):
                events.append((kind, threading.current_thread().name))
                return inner(*args)

            return call

        def work():
            names[threading.current_thread().name] = deploy_rig.deploy(
                SHA, seam("ssh", rig), seam("git", FakeGit()), Shared(), now=lambda: 1793400000.9
            )

        threads = [threading.Thread(target=work, name=n) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        first = events[0][1]
        held = [who for _, who in events[: events.index(("unlock", first))]]
        self.assertEqual(set(held), {first})
        self.assertEqual(sum(1 for c in rig.commands if " build " in c), 1)
        self.assertTrue(any("build and swap skipped" in line for lines in names.values() for line in lines))

    def test_main_takes_the_rig_lock_seam(self):
        lock = FakeLock()
        old = deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock
        deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock = FakeRig(events=lock.events), FakeGit(), lock
        try:
            with redirect_stdout(StringIO()):
                code = deploy_rig.main({deploy_rig.ENV: SHA})
        finally:
            deploy_rig.ssh, deploy_rig.git, deploy_rig.rig_lock = old
        self.assertEqual((code, lock.events[0], lock.events[-1]), (0, "lock", "unlock"))

    def test_the_hold_script_parses_and_prints_waiting_then_locked(self):
        self.assertEqual(subprocess.run(["bash", "-n"], input=deploy_rig.HOLD, text=True, check=False).returncode, 0)
        self.assertLess(deploy_rig.HOLD.index("waiting"), deploy_rig.HOLD.index("locked"))
        self.assertIn("flock 7", deploy_rig.HOLD)
        self.assertEqual(deploy_rig.LOCK, "deploy-taste.lock")


class Promotion(unittest.TestCase):
    """Under the lock the deploy builds the newest landed main, never an older one."""

    def test_a_descendant_main_head_is_built_instead_and_the_log_says_the_older_deploy_deferred(self):
        rig = FakeRig()
        lines = deploy_rig.deploy(SHA, rig, FakeGit(head=NEWER), FakeLock(), now=lambda: 1793400000.9)
        build = next(c for c in rig.commands if " build " in c)
        self.assertIn(f" build {NEWER} oracle-{NEWER[:12]}-1793400000 ", build)
        self.assertNotIn(SHA, " ".join(rig.commands))
        moved = f"{SHA[:12]}: main has moved to {NEWER[:12]}, deploying main's head; the older deploy deferred"
        self.assertIn(moved, lines)
        self.assertEqual(rig.live, f"snaps/oracle-{NEWER[:12]}-1793400000")

    def test_an_older_deploy_after_the_newer_one_ends_on_the_newer_directory_without_a_build(self):
        rig = FakeRig(live=f"snaps/oracle-{NEWER[:12]}-1793400000")
        lines = deploy_rig.deploy(SHA, rig, FakeGit(head=NEWER), FakeLock(), now=lambda: 1793400999.9)
        self.assertEqual([c for c in rig.commands if " build " in c], [])
        self.assertEqual(rig.live, f"snaps/oracle-{NEWER[:12]}-1793400000")
        self.assertTrue(any("deferred" in line for line in lines))

    def test_the_head_itself_is_deployed_without_a_promotion_line(self):
        lines = deploy_rig.deploy(SHA, FakeRig(), FakeGit(head=SHA), FakeLock(), now=lambda: 1793400000.9)
        self.assertFalse(any("deferred" in line for line in lines))

    def test_a_main_that_is_not_a_descendant_refuses_and_touches_no_rig_command(self):
        rig = FakeRig()
        with self.assertRaisesRegex(deploy_rig.Refused, "not on gitea main"):
            deploy_rig.deploy(SHA, rig, FakeGit(head=NEWER, ancestor_code=1), FakeLock())
        self.assertEqual(rig.commands, [])


SUITE_REPO_FILES = {
    "src/cake_taste/__init__.py": "",
    "tests/test_ok.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n",
}


class SuiteIsolation(unittest.TestCase):
    """Two suite runs of the same SHA at once (the real SUITE script and runner) each import only their own checkout."""

    def test_concurrent_runs_of_one_sha_use_their_own_checkout_log_and_slots(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            tmp = Path(tmp)
            (tmp / "home/finish").mkdir(parents=True)
            repo = tmp / "repo"
            for name, body in SUITE_REPO_FILES.items():
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_text(body)
            git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(["git", "-c", "init.templateDir=", "init", "-q", "-b", "main", str(repo)], check=True)
            subprocess.run([*git, "add", "-A"], check=True)
            subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
            sha = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
            script = deploy_rig.SUITE.replace("__RUNNER__", deploy_rig.RUNNER)
            procs = []
            for n in (1, 2):
                dest = tmp / f"dest{n}"
                dest.mkdir()
                procs.append(
                    subprocess.Popen(
                        ["bash", "-c", script, "suite", sha, str(repo), sys.executable, str(dest)],
                        env={**os.environ, "HOME": str(tmp / "home")},
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                )
            facts = []
            for proc in procs:
                out = proc.communicate()[0]
                self.assertEqual(proc.returncode, 0, out)
                facts.append(deploy_rig.suite_facts(out))
            for fact in facts:
                self.assertTrue(PurePosixPath(fact["suite-import"]).is_relative_to(fact["suite-checkout"]), fact)
            self.assertNotEqual(facts[0]["suite-checkout"], facts[1]["suite-checkout"])
            self.assertNotEqual(facts[0]["suite-log"], facts[1]["suite-log"])
            for fact in facts:
                self.assertIn(fact["suite-checkout"], Path(fact["suite-log"]).read_text())

    def test_the_suite_script_names_no_path_that_two_runs_of_one_sha_share(self):
        self.assertNotIn(".log\n", deploy_rig.SUITE.replace('.XXXXXX.log")', ""))
        self.assertIn('log=$(mktemp "$HOME/finish/deploy-suite-', deploy_rig.SUITE)
        self.assertIn("ABU_SLOTS_DIR=$(mktemp -d", deploy_rig.SUITE)
        self.assertIn("TMPDIR=$PWD/.tmp", deploy_rig.SUITE)
        self.assertIn("PYTHONPATH=$PWD/src", deploy_rig.SUITE)


if __name__ == "__main__":
    unittest.main()
