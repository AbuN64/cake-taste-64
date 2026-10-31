"""The rig deploy through Job Invoke: refusals before any job, the suite gate, the Activation build, the no-op path and
the live-link readback, all through fake job and git seams; the suite script and runner run for real in a tiny repo."""

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path, PurePosixPath
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci/deploy_rig.py"
spec = importlib.util.spec_from_file_location("deploy_rig", SCRIPT)
deploy_rig = importlib.util.module_from_spec(spec)
sys.modules["deploy_rig"] = deploy_rig
spec.loader.exec_module(deploy_rig)

SHA = "7a6ab5ce4712939ea310ee62e2fbc759920f6363"
NEWER = "c" * 40
HASH = "63576cbe05149fa7"
CHECKOUT = "/home/abu/runs/b.c/Projects/AbuN64/CakeTaste64"
TOOLKIT = deploy_rig.TOOLKIT


def suite_log(sha=SHA, imported=f"{CHECKOUT}/src/cake_taste/__init__.py", failed=None):
    lines = [f"suite-checkout: {CHECKOUT}", f"suite-sha: {sha}", f"suite-import: {imported}"]
    if failed:
        lines += ["suite-summary: 1766 passed, 1 failed, 5 skipped in 204 s", f"suite-first-failure: {failed}"]
    else:
        lines.append("suite-summary: 1767 passed, 0 failed, 5 skipped in 204 s")
    return "\n".join(lines)


def readback_log(live_sha, second=HASH):
    return f"readback-live: {TOOLKIT}.{live_sha}\nreadback-hash: {HASH}\nreadback-hash: {second}"


def head_of(repo):
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


class FakeGit:
    def __init__(self, fetch_code=0, head=SHA, ancestor_code=0):
        self.fetch_code, self.head, self.ancestor_code, self.calls = fetch_code, head, ancestor_code, []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "fetch":
            return self.fetch_code, "fetch failed" if self.fetch_code else ""
        if args[0] == "rev-parse":
            return 0, self.head
        return self.ancestor_code, ""


class FakeJobs:
    """The Job Invoke seam: one scripted answer per kind of job; records (kind, revision) and the bound sha."""

    def __init__(self, live=f"{TOOLKIT}.{'b' * 40}", suite=None, build=(0, ""), readback=None):
        suite = suite or (0, suite_log())
        self.live, self.suite, self.build, self.bound, self.calls, self.argvs = live, suite, build, [], [], []
        self.readback = readback

    def __call__(self, sha):
        self.bound.append(sha)
        return lambda argv, revision=None: self.run(sha, argv, revision)

    def run(self, sha, argv, revision):
        script = argv[2]
        self.argvs.append(argv)
        if script == deploy_rig.BUILD:
            self.calls.append(("build", revision))
            return self.build
        if script == deploy_rig.LIVE:
            self.calls.append(("live", None))
            return 0, f"readback-live: {self.live}"
        if script == deploy_rig.READBACK:
            self.calls.append(("readback", None))
            live = f"{TOOLKIT}.{sha}" if self.build[0] == 0 and self.calls[-2][0] == "build" else self.live
            return self.readback or (0, readback_log(live.removeprefix(TOOLKIT + ".")))
        self.calls.append(("suite", None))
        return self.suite

    def kinds(self):
        return [kind for kind, _ in self.calls]


class Shape(unittest.TestCase):
    def test_deploy_file_names_one_executable_shebang_script(self):
        text = (ROOT / ".deploy").read_text()
        self.assertEqual(text, "ci/deploy_rig.py\n")
        self.assertTrue((ROOT / "ci/deploy_rig.py").stat().st_mode & stat.S_IXUSR)
        self.assertTrue(SCRIPT.read_text().startswith("#!/usr/bin/env python3\n"))

    def test_scripts_parse_and_the_retired_path_is_gone(self):
        for body in (deploy_rig.BUILD, deploy_rig.READBACK, deploy_rig.LIVE, deploy_rig.SUITE):
            self.assertEqual(subprocess.run(["bash", "-n"], input=body, text=True, check=False).returncode, 0)
        source = SCRIPT.read_text()
        for retired in ("finish/", "flock", "waitfor", "ssh", "-j8"):
            self.assertNotIn(retired, source.replace("no ssh, slot or lock", ""))

    def test_the_build_resets_the_copied_deps_and_removes_a_failed_build_folder(self):
        for needle in ("reset -q --hard", 'trap \'rc=$?; test "$rc" = 0 || rm -rf "$BUILD"\' EXIT'):
            self.assertIn(needle, deploy_rig.BUILD)
        self.assertLess(deploy_rig.BUILD.index("cp -a"), deploy_rig.BUILD.index("reset -q --hard"))
        self.assertLess(deploy_rig.BUILD.index("reset -q --hard"), deploy_rig.BUILD.index("deps.sh"))

    def test_the_build_uses_the_job_width_and_the_activation_folder(self):
        self.assertIn('cmake --build "$BUILD/build" -j"$JOBS"', deploy_rig.BUILD)
        self.assertIn('mkdir "$BUILD"', deploy_rig.BUILD)
        self.assertTrue(deploy_rig.BUILD.rstrip().endswith('selfcheck "$BUILD/n64host"'))
        self.assertIn(f'selfcheck "{TOOLKIT}/n64host"', deploy_rig.READBACK)

    def test_the_charge_headers_ship_in_the_snapshot_and_are_hashed_in_its_pins(self):
        build = deploy_rig.BUILD
        copy = 'install -m 644 "$BUILD/build/gen/cpu_profile.h" "$BUILD/build/gen/cpu_native.h" "$BUILD/gen/"'
        self.assertIn(copy, build)
        self.assertLess(build.index(copy), build.index('rm -rf "$BUILD/deps" "$BUILD/build"'))
        self.assertLess(build.index(copy), build.index("oracle-pins.json"))
        self.assertIn('"gen": gen', build)

    def test_the_pins_script_hashes_both_headers_for_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "gen").mkdir()
            (tmp / "gen/cpu_profile.h").write_bytes(b"profile")
            (tmp / "gen/cpu_native.h").write_bytes(b"native")
            (tmp / "facts.json").write_bytes(b"facts")
            (tmp / "old.json").write_text('{"wall": 2, "cpu": 2}')
            script = deploy_rig.BUILD.split("<<'PY'\n")[1].split("\nPY\n")[0]
            argv = ["python3", "-", "sha", *(str(tmp / n) for n in ("old.json", "gen", "facts.json"))]
            out = subprocess.run(argv, input=script, capture_output=True, text=True, check=True).stdout
            pins = json.loads(out)
            gen = pins["gen"]
            self.assertEqual(pins["facts"], hashlib.sha256(b"facts").hexdigest())
            self.assertEqual(gen["cpu_profile.h"], hashlib.sha256(b"profile").hexdigest())
            self.assertEqual(gen["cpu_native.h"], hashlib.sha256(b"native").hexdigest())


class Refusals(unittest.TestCase):
    def run_main(self, environ, jobs, git=None, argv=("--Subject.Board", "b", "--Subject.Card", "c")):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = deploy_rig.main(list(argv), environ, jobs, git or FakeGit())
        return code, out.getvalue(), err.getvalue()

    def test_unset_or_malformed_sha_refuses_before_any_seam(self):
        for value in (None, "", "abc", SHA.upper(), SHA[:39]):
            jobs, git = FakeJobs(), FakeGit()
            environ = {} if value is None else {deploy_rig.ENV: value}
            code, _, err = self.run_main(environ, jobs, git)
            self.assertEqual((code, err.count("\n"), jobs.calls, git.calls), (1, 1, [], []))
            self.assertIn("is not a 40-hex SHA", err)

    def test_missing_board_or_card_argument_exits_2_before_any_seam(self):
        for argv in ([], ["--Subject.Board", "b"], ["--Subject.Card", "c"]):
            jobs, git = FakeJobs(), FakeGit()
            with self.assertRaises(SystemExit) as stop, redirect_stderr(StringIO()) as err:
                deploy_rig.main(argv, {deploy_rig.ENV: SHA}, jobs, git)
            self.assertEqual((stop.exception.code, jobs.calls, git.calls), (2, [], []))
            self.assertIn("--Subject.", err.getvalue())

    def test_the_arguments_reach_job_invoke_and_no_run_env_is_read(self):
        environ = {deploy_rig.ENV: SHA, "BUTLERMASTER_RUN_BOARD": "other", "BUTLERMASTER_RUN_CARD": "other"}
        with patch.object(deploy_rig, "butler_job", return_value=FakeJobs()(SHA)) as bound, redirect_stdout(StringIO()):
            code = deploy_rig.main(["--Subject.Board", "7-board", "--Subject.Card", "card-1"], environ, None, FakeGit())
        self.assertEqual(code, 0)
        bound.assert_called_once_with("7-board", "card-1", SHA)
        self.assertNotIn("BUTLERMASTER_RUN", SCRIPT.read_text())

    def test_sha_not_on_gitea_main_refuses_without_a_job(self):
        jobs = FakeJobs()
        code, _, err = self.run_main({deploy_rig.ENV: SHA}, jobs, FakeGit(ancestor_code=1))
        self.assertEqual((code, jobs.calls, jobs.bound), (1, [], []))
        self.assertIn("is not on gitea main", err)

    def test_unreachable_gitea_refuses_without_a_job(self):
        jobs = FakeJobs()
        code, _, err = self.run_main({deploy_rig.ENV: SHA}, jobs, FakeGit(fetch_code=128))
        self.assertEqual((code, jobs.calls), (1, []))
        self.assertIn("cannot fetch gitea main", err)

    def test_main_is_fetched_into_this_checkout(self):
        git = FakeGit()
        self.run_main({deploy_rig.ENV: SHA}, FakeJobs(), git)
        self.assertEqual(git.calls[0], ("fetch", "-q", "--no-tags", deploy_rig.GITEA, "main"))


class Deploy(unittest.TestCase):
    def deploy(self, jobs, git=None, sha=SHA):
        return deploy_rig.deploy(sha, jobs, git or FakeGit(head=sha))

    def test_suite_then_activation_build_then_live_readback_in_that_order(self):
        jobs = FakeJobs()
        lines = self.deploy(jobs)
        self.assertEqual(jobs.kinds(), ["suite", "live", "build", "readback"])
        self.assertEqual(jobs.calls[2], ("build", SHA))  # the Activation revision is the exact SHA
        self.assertEqual(jobs.bound, [SHA])
        self.assertIn(f"live oracle activated: {TOOLKIT}.{SHA} (previous {TOOLKIT}.{'b' * 40} kept", lines[-2])
        self.assertEqual(lines[-1], f"readback boot-idle {HASH} {HASH} through {TOOLKIT}.{SHA}")

    def test_the_suite_log_carries_summary_sha_and_import(self):
        lines = self.deploy(FakeJobs())
        self.assertIn(f"{SHA[:12]}: suite 1767 passed, 0 failed, 5 skipped in 204 s", lines)
        self.assertIn(f"{SHA[:12]}: suite sha {SHA}", lines)

    def test_a_moved_main_deploys_the_head_and_binds_the_jobs_to_it(self):
        jobs = FakeJobs(suite=(0, suite_log(sha=NEWER, imported=f"{CHECKOUT}/src/cake_taste/__init__.py")))
        lines = self.deploy(jobs, FakeGit(head=NEWER))
        self.assertEqual(jobs.bound, [NEWER])
        self.assertIn("main has moved to cccccccccccc, deploying main's head; the older deploy deferred", lines[0])
        self.assertEqual(jobs.calls[2], ("build", NEWER))

    def test_live_already_at_the_sha_builds_nothing_but_reads_back(self):
        jobs = FakeJobs(live=f"{TOOLKIT}.{SHA}")
        lines = self.deploy(jobs)
        self.assertEqual(jobs.kinds(), ["suite", "live", "readback"])
        self.assertTrue(any("build and flip skipped" in line for line in lines))

    def test_a_failing_build_or_self_check_refuses_with_the_log_tail_and_never_reads_back(self):
        jobs = FakeJobs(build=(1, "ninja: error\nboot-idle state hashes differ: aa bb"))
        with self.assertRaisesRegex(deploy_rig.Refused, "build of 7a6ab5ce4712 .* exit 1: boot-idle state"):
            self.deploy(jobs)
        self.assertEqual(jobs.kinds(), ["suite", "live", "build"])

    def test_readback_mismatch_refuses(self):
        jobs = FakeJobs(readback=(0, readback_log(SHA, second="0" * 16)))
        with self.assertRaisesRegex(deploy_rig.Refused, "state hashes differ"):
            self.deploy(jobs)

    def test_readback_live_link_at_another_build_refuses(self):
        jobs = FakeJobs(readback=(0, readback_log("b" * 40)))
        with self.assertRaisesRegex(deploy_rig.Refused, "the live oracle must read"):
            self.deploy(jobs)

    def test_readback_run_failure_or_missing_hashes_refuses(self):
        for answer in ((1, "boot-idle run 1 exit 139"), (0, f"readback-hash: {HASH}")):
            with self.assertRaises(deploy_rig.Refused):
                self.deploy(FakeJobs(readback=answer))

    def test_an_unreadable_live_link_refuses_before_the_build(self):
        jobs = FakeJobs()

        def run(sha, argv, revision):
            if argv[2] == deploy_rig.LIVE:
                return 1, "live oracle link is broken: /x holds no executable n64host"
            return FakeJobs.run(jobs, sha, argv, revision)

        jobs.run = run
        with self.assertRaisesRegex(deploy_rig.Refused, "cannot read the live oracle link.*is broken"):
            self.deploy(jobs)
        self.assertEqual(jobs.kinds(), ["suite"])


class LiveLinkScript(unittest.TestCase):
    """The LIVE script run for real against a temp toolkit link: what the rig reads when the link is bad."""

    def run_live(self, prepare):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp, "oracle")
            prepare(link, Path(tmp))
            body = deploy_rig.LIVE.replace(TOOLKIT, str(link))
            done = subprocess.run(["bash", "-c", body], capture_output=True, text=True, check=False)
            return done.returncode, done.stdout.strip(), done.stderr.strip(), Path(tmp).resolve()

    def test_a_link_to_a_built_oracle_answers_its_resolved_target(self):
        def prepare(link, tmp):
            build = tmp / "oracle.abc"
            build.mkdir()
            (build / "n64host").write_text("#!/bin/sh\n")
            (build / "n64host").chmod(0o755)
            link.symlink_to(build)

        code, out, _, tmp = self.run_live(prepare)
        self.assertEqual((code, out), (0, f"readback-live: {tmp}/oracle.abc"))

    def test_a_dangling_link_exits_1_naming_the_link_and_the_cause(self):
        code, out, err, _ = self.run_live(lambda link, tmp: link.symlink_to(tmp / "gone"))
        self.assertEqual((code, out), (1, ""))
        self.assertRegex(err, r"live oracle link .*oracle is broken: .*gone holds no executable n64host")

    def test_a_missing_link_or_a_plain_directory_is_not_a_symlink(self):
        code, _, err, _ = self.run_live(lambda link, tmp: None)
        self.assertEqual(code, 1)
        self.assertIn("is not a symlink", err)


class SuiteGate(unittest.TestCase):
    def deploy(self, jobs):
        return deploy_rig.deploy(SHA, jobs, FakeGit())

    def test_a_failing_suite_names_the_first_failing_test_and_never_builds(self):
        jobs = FakeJobs(suite=(1, suite_log(failed="tests.test_x.Case.test_y")))
        with self.assertRaisesRegex(deploy_rig.Refused, "suite failed at tests.test_x.Case.test_y"):
            self.deploy(jobs)
        self.assertEqual(jobs.kinds(), ["suite"])

    def test_an_import_outside_the_worktree_refuses_even_when_every_test_passed(self):
        installed = "/home/abu/venv-decomp/lib/python3.13/site-packages/cake_taste/__init__.py"
        with self.assertRaisesRegex(deploy_rig.Refused, "outside its checkout"):
            self.deploy(FakeJobs(suite=(0, suite_log(imported=installed))))

    def test_a_sibling_directory_sharing_the_checkout_prefix_is_outside(self):
        with self.assertRaisesRegex(deploy_rig.Refused, "outside its checkout"):
            self.deploy(FakeJobs(suite=(0, suite_log(imported=f"{CHECKOUT}-old/src/cake_taste/__init__.py"))))

    def test_a_suite_at_another_sha_or_with_no_facts_refuses(self):
        for answer in ((0, suite_log(sha=NEWER)), (0, "fatal: nothing"), (0, "")):
            jobs = FakeJobs(suite=answer)
            with self.assertRaises(deploy_rig.Refused):
                self.deploy(jobs)
            self.assertEqual(jobs.kinds(), ["suite"])

    def test_a_suite_exiting_nonzero_without_a_named_failure_still_refuses(self):
        with self.assertRaisesRegex(deploy_rig.Refused, "suite failed at"):
            self.deploy(FakeJobs(suite=(1, suite_log())))


class JobSeam(unittest.TestCase):
    """butler_job builds the Job Invoke request and reads its answer, with the CLI replaced by a fake executable."""

    def fake_cli(self, answer):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        log = base / "job.log"
        log.write_text("the job's output\n")
        (base / "mo-butlermaster").write_text(
            "#!/bin/sh\ncat > request.json\nprintf '%s' '" + json.dumps({"Response": answer(log)}) + "'\n"
        )
        (base / "mo-butlermaster").chmod(0o755)
        old = os.environ["PATH"], os.getcwd()
        os.environ["PATH"] = f"{base}:{old[0]}"
        os.chdir(base)
        self.addCleanup(lambda: (os.environ.update(PATH=old[0]), os.chdir(old[1])))
        return base

    def test_the_request_names_host_card_worktree_argv_and_the_activation(self):
        base = self.fake_cli(lambda log: {"Exit": 0, "Log": str(log), "Width": 16})
        job = deploy_rig.butler_job("b", "c", SHA)
        self.assertEqual(job(["make", "-j16"]), (0, "the job's output\n"))
        job(["true"], revision=SHA)
        request = json.loads((base / "request.json").read_text())["Request"]
        self.assertEqual(request["Host"], "rig")
        self.assertEqual(request["Subject"], {"Board": "b", "Card": "c"})
        self.assertEqual(request["Worktree"], f"Projects/AbuN64/CakeTaste64@{SHA}")
        self.assertEqual(request["Argv"], ["true"])
        self.assertEqual(request["Activation"], {"Toolkit": TOOLKIT, "Revision": SHA})

    def test_a_plain_job_carries_no_activation(self):
        base = self.fake_cli(lambda log: {"Exit": 3, "Log": str(log)})
        self.assertEqual(deploy_rig.butler_job("b", "c", SHA)(["false"])[0], 3)
        self.assertNotIn("Activation", json.loads((base / "request.json").read_text())["Request"])

    def test_a_refusal_raises_with_its_code_and_stack(self):
        self.fake_cli(lambda log: {"Error": {"Code": "Native", "Message": "short…", "Stack": "ssh: full cause"}})
        with self.assertRaisesRegex(deploy_rig.Refused, "refused Native: ssh: full cause"):
            deploy_rig.butler_job("b", "c", SHA)(["true"])


def git(*args, cwd):
    identity = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", *identity, *args], cwd=cwd, check=True, capture_output=True)


class SuiteScript(unittest.TestCase):
    """The SUITE script and RUNNER run for real in the Worktree, here a tiny local repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        for path in (self.repo / "src/cake_taste", self.repo / "tests"):
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

    def run_suite(self):
        script = deploy_rig.SUITE.replace("__RUNNER__", deploy_rig.RUNNER)
        done = subprocess.run(
            ["bash", "-c", script, "suite", sys.executable],
            cwd=self.repo,
            env={"PATH": os.environ["PATH"], "HOME": self.tmp.name},
            capture_output=True,
            text=True,
            check=False,
        )
        return done, deploy_rig.facts(done.stdout, "suite-")

    def test_a_passing_suite_reports_the_import_from_the_worktree_and_leaves_it_clean(self):
        done, facts = self.run_suite()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(facts["suite-checkout"], str(self.repo.resolve()))
        self.assertTrue(facts["suite-summary"].startswith("1 passed, 0 failed, 0 skipped in "))
        self.assertTrue(PurePosixPath(facts["suite-import"]).is_relative_to(facts["suite-checkout"]))
        status = subprocess.run(["git", "status", "--short"], cwd=self.repo, capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_a_failing_test_exits_1_and_names_the_first_failure(self):
        self.write_test("self.assertEqual(cake_taste.VALUE, 2)")
        done, facts = self.run_suite()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(facts["suite-first-failure"], "test_it.Case.test_it")

    def test_a_test_printing_without_a_newline_does_not_swallow_the_facts(self):
        self.write_test("import sys; sys.stdout.write('kept-by-default')")
        done, facts = self.run_suite()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(facts["suite-summary"].startswith("1 passed, 0 failed, 0 skipped in "))


class Newest(unittest.TestCase):
    def test_real_git_fetches_main_into_the_checkout_and_finds_the_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = Path(tmp) / "origin"
            origin.mkdir()
            git("init", "-q", "-b", "main", cwd=origin)
            git("commit", "-q", "--allow-empty", "-m", "one", cwd=origin)
            first = head_of(origin)
            git("commit", "-q", "--allow-empty", "-m", "two", cwd=origin)
            head = head_of(origin)
            mine = Path(tmp) / "mine"
            mine.mkdir()
            git("init", "-q", cwd=mine)

            def local_git(*args):
                args = [str(origin) if a == deploy_rig.GITEA else a for a in args]
                done = subprocess.run(["git", "-C", str(mine), *args], capture_output=True, text=True, check=False)
                return done.returncode, (done.stdout + done.stderr).strip()

            self.assertEqual(deploy_rig.newest_main(first, local_git), head)
            with self.assertRaisesRegex(deploy_rig.Refused, "is not on gitea main"):
                deploy_rig.newest_main("d" * 40, local_git)


if __name__ == "__main__":
    unittest.main()
