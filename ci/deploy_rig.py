#!/usr/bin/env python3
"""Deploys the landed CakeTaste64 commit to the rig oracle through Job Invoke: `.deploy` runs this after Land, with the
landed SHA in LANDED_Projects_AbuN64_CakeTaste64, the run's board in BUTLERMASTER_RUN_BOARD and its card in
BUTLERMASTER_RUN_CARD. Any refusal or failed step prints one line on stderr and exits 1. Every rig step is one
`mo-butlermaster Job Invoke --Host rig --Worktree <repo>@<sha>` job at the exact SHA (run in its Worktree with JOBS set
to Dos's width), so Dos admits the work and the per-host activation permit serializes deploys: no ssh, slot or lock.

  1. the SHA must be on gitea main, which is fetched into this checkout so Job Invoke can push the SHA to the rig. When
     main has moved past the landed SHA to a descendant, main's head is deployed instead and the log says the older
     deploy deferred; a SHA that is not on main refuses before any job;
  2. the suite gate, job one: the full test suite (unittest discover over tests/) runs in the Worktree with its src
     first on the import path. The imported cake_taste path is printed and must lie inside the Worktree. Any
     failure refuses (one line naming the first failing test) with the live oracle untouched;
  3. when the live oracle already is <Toolkit>.<sha>, nothing is built or flipped;
  4. otherwise job two is the Activation (Toolkit = the oracle link, Revision = the SHA): it builds n64host into
     $BUILD (<Toolkit>.<sha>, pinned deps copied from ~/src/oracle-deps and patched by hosts/n64recomp/deps.sh) with
     the build tool at -j"$JOBS" and runs the oracle self-check on that build: boot-idle twice, the two state hashes
     must be equal. Only on exit 0 does ButlerMaster flip the live link atomically, keeping the old one as
     <Toolkit>.previous;
  5. readback, always: the last job runs the self-check again through the live link, and the link must read
     <Toolkit>.<sha>."""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ENV = "LANDED_Projects_AbuN64_CakeTaste64"
BOARD_ENV = "BUTLERMASTER_RUN_BOARD"
CARD_ENV = "BUTLERMASTER_RUN_CARD"
HOST = "rig"
REPO = "Projects/AbuN64/CakeTaste64"
GITEA = "http://10.10.10.130:18556/Projects/CakeTaste64.git"
PACKAGE = "cake_taste"
PYTHON = "/usr/bin/python3"
TOOLKIT = "/home/abu/rig-state/tas/live/oracle"
SHA = re.compile(r"[0-9a-f]{40}")
HASH = re.compile(r"readback-hash: ([0-9a-f]{16})")

# git(args) runs git here and returns (exit code, output).
Seam = Callable[..., "tuple[int, str]"]
# job(argv, revision=None) runs one Job Invoke at the deploy's SHA and returns (job exit, the job's log text); a given
# revision makes it the toolkit Activation of <Toolkit>.<revision>. jobs_at(sha) binds the SHA and answers the job.
Job = Callable[..., "tuple[int, str]"]
JobsAt = Callable[[str], Job]

# selfcheck HOST: boot-idle twice through the oracle binary HOST, one `readback-hash: <first-16-hex>` line per run (env
# as tas_oracle.start); the job fails when the two state hashes differ.
SELFCHECK = r"""selfcheck() {
  host=$1
  movie=$HOME/rig-state/tas/runs/plays/20261030T142016Z-boot-idle/movie.raw
  spec=$HOME/rig-state/tas/runs/state-spec/b1d54eea5d7361537883/oracle.spec
  test -x "$host"; test -f "$movie"; test -f "$spec"
  work=$(mktemp -d)
  first=
  for n in 1 2; do
    out=$work/$n
    mkdir "$out"
    (
      cd "$out"
      export SDL_VIDEODRIVER=offscreen SDL_AUDIODRIVER=dummy
      export N64R_ROM=$HOME/code/ragewars-decomp/roms/baserom.us-rev1.z64 N64R_CONFIG=$HOME/rig-state/tas/oracle/cfg
      export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json
      export N64R_PAK=on N64R_PADS=1 N64R_TAS_PLAY=$movie N64R_TAS_EXIT_AFTER=300 N64R_VIEW=$out/view.bmp
      export N64R_AUDIO_OUT=/dev/null N64R_TAS_SHOT_EVERY=0 N64R_TAS_TRACE=$out/oracle-trace.txt
      export N64R_TAS_STATE=$spec N64R_TAS_STATE_HASH=$out/state-oracle.txt
      unset N64R_CPAK
      rc=0
      "$host" > oracle.txt 2>&1 || rc=$?
      test "$rc" = 0 || { tail -n 5 oracle.txt >&2; echo "boot-idle run $n exit $rc" >&2; exit 1; }
    ) || exit 1
    hash=$(sha256sum "$out/state-oracle.txt" | cut -c1-16)
    echo "readback-hash: $hash"
    test -z "$first" || test "$first" = "$hash" || { echo "boot-idle state hashes differ: $first $hash" >&2; exit 1; }
    first=$hash
  done
  rm -rf "$work"
}
"""

# build: the Activation job, run in the Worktree at the SHA with BUILD=<Toolkit>.<sha> and JOBS set. The build tree and
# copied deps live only while it builds; exit 0 (built and self-checked) is the gate for the flip, and any other exit
# removes $BUILD so the next activation of the sha starts clean. The copied deps are reset to their pins first: the
# shared copy may carry an older patch set, and deps.sh applies this SHA's patches onto the pristine pin.
BUILD = (
    SELFCHECK
    + r"""set -eu
sha=$(git rev-parse HEAD)
src=$PWD
mkdir "$BUILD"
trap 'rc=$?; test "$rc" = 0 || rm -rf "$BUILD"' EXIT
mkdir "$BUILD/deps"
cp -a "$HOME/src/oracle-deps/N64ModernRuntime" "$HOME/src/oracle-deps/rt64" "$BUILD/deps/"
git -C "$BUILD/deps/N64ModernRuntime" reset -q --hard
git -C "$BUILD/deps/rt64" reset -q --hard
sh hosts/n64recomp/deps.sh "$BUILD/deps"
cmake -S hosts/n64recomp -B "$BUILD/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-14 -DCMAKE_CXX_COMPILER=/usr/bin/g++-14 \
  -DN64R_GEN_DIR="$HOME/rig-state/behave/gen/us-rev1" -DN64R_PINS="$src/hosts/n64recomp/PINS" \
  -DN64R_PROFILE="$HOME/rig-state/behave/oracle-facts.json" \
  -DN64R_REFUSED="$HOME/rig-state/behave/gen/us-rev1/refused.tsv" \
  -DN64R_RT64_SRC="$BUILD/deps/rt64" -DN64R_RUNTIME_SRC="$BUILD/deps/N64ModernRuntime" \
  -DFETCHCONTENT_FULLY_DISCONNECTED=ON
cmake --build "$BUILD/build" -j"$JOBS"
install -m 755 "$BUILD/build/n64host" "$BUILD/n64host"
python3 - "$sha" "__TOOLKIT__/oracle-pins.json" > "$BUILD/oracle-pins.json" <<'PY'
import json, sys, time
old = json.load(open(sys.argv[2]))
print(json.dumps({"cake_taste": sys.argv[1], "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "wall": old["wall"], "cpu": old["cpu"]}, indent=1))
PY
rm -rf "$BUILD/deps" "$BUILD/build"
selfcheck "$BUILD/n64host"
"""
).replace("__TOOLKIT__", TOOLKIT)

# live: the live link's resolved target, for the already-live no-op path. A missing link, or one whose target holds no
# executable n64host, exits 1 with the cause on stderr (readlink -f alone answers a path even for a dangling link).
LIVE = f"""link={TOOLKIT}
test -L "$link" || {{ echo "live oracle $link is not a symlink" >&2; exit 1; }}
target=$(readlink -f "$link")
test -x "$target/n64host" || {{ echo "live oracle link $link is broken: $target holds no executable n64host" >&2; exit 1; }}
echo "readback-live: $target"
"""

# readback: the live link's target, then the self-check through the live link.
READBACK = SELFCHECK + f'set -eu\n{LIVE}selfcheck "{TOOLKIT}/n64host"\n'

# suite PYTHON: the suite run in the Worktree (a clean tree of exactly the SHA), one `suite-KEY: value` line per fact.
SUITE = r"""set -u
py=$1
runner=$(mktemp)
echo "suite-checkout: $(pwd -P)"
echo "suite-sha: $(git rev-parse HEAD)"
cat > "$runner" <<'PY'
__RUNNER__
PY
mkdir .tmp
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=init.templateDir GIT_CONFIG_VALUE_0=
export TMPDIR=$PWD/.tmp PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1
rc=0
"$py" "$runner" || rc=$?
rm -rf "$runner" .tmp
exit "$rc"
"""

# The runner: puts the checkout's src first, proves the package comes from there, runs unittest discover over tests/.
RUNNER = r"""import os
import sys
import time
import unittest

root = os.path.realpath(os.getcwd())
sys.path.insert(0, os.path.join(root, "src"))
PACKAGE = "cake_taste"


def emit(line):
    # a test may leave its own output without a newline: every fact starts on a fresh line
    sys.stdout.write("\n" + line + "\n")
    sys.stdout.flush()


def imported():
    return sorted({os.path.realpath(m.__file__) for n, m in sys.modules.items()
                   if (n == PACKAGE or n.startswith(PACKAGE + ".")) and getattr(m, "__file__", None)})


def inside(path):
    return path.startswith(root + os.sep)


__import__(PACKAGE)
emit("suite-import: " + os.path.realpath(sys.modules[PACKAGE].__file__))


class Result(unittest.TextTestResult):
    first = None

    def addFailure(self, test, err):
        self.first = self.first or test.id()
        super().addFailure(test, err)

    def addError(self, test, err):
        self.first = self.first or test.id()
        super().addError(test, err)

    def addUnexpectedSuccess(self, test):
        self.first = self.first or test.id()
        super().addUnexpectedSuccess(test)


start = time.monotonic()
runner = unittest.TextTestRunner(stream=sys.stdout, resultclass=Result)
result = runner.run(unittest.defaultTestLoader.discover("tests"))
seconds = time.monotonic() - start
outside = [path for path in imported() if not inside(path)]
if outside:
    emit("suite-import: " + outside[0])
    sys.exit(3)
failed = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
passed = result.testsRun - failed - len(result.skipped)
emit(f"suite-summary: {passed} passed, {failed} failed, {len(result.skipped)} skipped in {seconds:.0f} s")
if failed or not passed:
    emit("suite-first-failure: " + (result.first or f"{passed} tests passed"))
    sys.exit(1)
"""


class Refused(Exception):
    """One line cause of a refusal or failed step."""


def git(*args: str) -> tuple[int, str]:
    done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False)
    return done.returncode, (done.stdout + done.stderr).strip()


def butler_job(board: str, card: str, sha: str) -> Job:
    """The job seam: `mo-butlermaster Job Invoke` of the card at sha, answering (exit, log text)."""

    def job(argv: list[str], revision: str | None = None) -> tuple[int, str]:
        request = {"Host": HOST, "Subject": {"Board": board, "Card": card}, "Worktree": f"{REPO}@{sha}", "Argv": argv}
        if revision:
            request["Activation"] = {"Toolkit": TOOLKIT, "Revision": revision}
        done = subprocess.run(
            ["mo-butlermaster", "Job", "Invoke", "--output=json", "--input=json"],
            input=json.dumps({"Metadata": {}, "Request": request}),
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            answer = json.loads(done.stdout)["Response"]
        except (ValueError, KeyError) as bad:
            cause = last_line(done.stderr, "no output")
            raise Refused(f"Job Invoke answered no JSON (exit {done.returncode}): {cause}") from bad
        if "Error" in answer:
            error = answer["Error"]
            raise Refused(f"Job Invoke refused {error['Code']}: {error.get('Stack') or error['Message']}")
        return answer["Exit"], Path(answer["Log"]).read_text(errors="replace")

    return job


def landed_sha(environ: dict[str, str]) -> str:
    sha = environ.get(ENV, "")
    if not SHA.fullmatch(sha):
        raise Refused(f"{ENV} is not a 40-hex SHA: {sha!r}")
    return sha


def last_line(text: str, default: object) -> str:
    return text.strip().splitlines()[-1] if text.strip() else str(default)


def newest_main(sha: str, git: Seam) -> str:
    """The SHA to deploy: gitea main's head when it descends from sha (sha itself when it is the head), else Refused.
    Main is fetched into this checkout, so the head's objects are here for Job Invoke to push."""
    code, out = git("fetch", "-q", "--no-tags", GITEA, "main")
    if code:
        raise Refused(f"cannot fetch gitea main ({GITEA}): {last_line(out, code)}")
    code, head = git("rev-parse", "FETCH_HEAD")
    if code or not SHA.fullmatch(head):
        raise Refused(f"cannot read the fetched gitea main head: {last_line(head, code)}")
    if git("merge-base", "--is-ancestor", sha, head)[0]:
        raise Refused(f"{sha[:12]} is not on gitea main")
    return head


def facts(log: str, prefix: str) -> dict[str, str]:
    found = {}
    for line in log.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key.startswith(prefix):
            found[key] = value
    return found


def fail(what: str, code: int, log: str) -> Refused:
    return Refused(f"{what} exit {code}: {last_line(log, 'no output')}")


def suite(sha: str, job: Job) -> list[str]:
    """The full suite at exactly sha in the Worktree; returns the log lines, raises Refused on any failure."""
    code, log = job(["bash", "-c", SUITE.replace("__RUNNER__", RUNNER), "suite", PYTHON])
    found = facts(log, "suite-")
    for key in ("suite-checkout", "suite-sha", "suite-import"):
        if key not in found:
            raise Refused(f"suite gate did not start at {sha[:12]} (exit {code}): {last_line(log, 'no output')}")
    if found["suite-sha"] != sha:
        raise Refused(f"suite ran at {found['suite-sha']}, not the landed {sha}")
    if not PurePosixPath(found["suite-import"]).is_relative_to(found["suite-checkout"]):
        raise Refused(
            f"suite imported {PACKAGE} from {found['suite-import']}, outside its checkout {found['suite-checkout']}"
        )
    if code or "suite-first-failure" in found or "suite-summary" not in found:
        first = found.get("suite-first-failure") or f"no summary, exit {code}: {last_line(log, 'no output')}"
        raise Refused(f"suite failed at {first}: {found.get('suite-summary', 'no summary')}")
    return [
        f"{sha[:12]}: suite {found['suite-summary']}",
        f"{sha[:12]}: suite sha {found['suite-sha']}",
        f"{sha[:12]}: suite imported {found['suite-import']}",
    ]


def live_target(job: Job) -> str:
    code, log = job(["bash", "-c", LIVE])
    target = facts(log, "readback-").get("readback-live", "")
    if code or not target:
        raise fail("cannot read the live oracle link", code, log)
    return target


def activate(sha: str, job: Job) -> None:
    """Job two: the Activation builds <Toolkit>.<sha> and self-checks it; only exit 0 lets it flip the link."""
    code, log = job(["bash", "-c", BUILD], revision=sha)
    if code:
        raise fail(f"build of {sha[:12]} at {TOOLKIT}.{sha}", code, log)


def readback(sha: str, job: Job) -> str:
    code, log = job(["bash", "-c", READBACK])
    live = facts(log, "readback-").get("readback-live", "")
    hashes = HASH.findall(log)
    if code or len(hashes) != 2:
        raise fail("readback boot-idle", code, log)
    if hashes[0] != hashes[1]:
        raise Refused(f"readback boot-idle state hashes differ: {hashes[0]} {hashes[1]}")
    if live != f"{TOOLKIT}.{sha}":
        raise Refused(f"readback: the live oracle must read {TOOLKIT}.{sha}, it reads {live}")
    return f"readback boot-idle {hashes[0]} {hashes[1]} through {live}"


def deploy(sha: str, jobs_at: JobsAt, git: Seam) -> list[str]:
    """Deploys the newest landed main; returns the log lines to print, the readback last. Raises Refused on the first
    failure."""
    lines = []
    head = newest_main(sha, git)
    if head != sha:
        lines.append(f"{sha[:12]}: main has moved to {head[:12]}, deploying main's head; the older deploy deferred")
        sha = head
    job = jobs_at(sha)
    lines += suite(sha, job)
    before = live_target(job)
    lines.append(f"live oracle before: {before}")
    if before == f"{TOOLKIT}.{sha}":
        lines.append(f"{sha[:12]}: the live oracle already reads {before}, build and flip skipped")
    else:
        activate(sha, job)
        lines.append(f"live oracle activated: {TOOLKIT}.{sha} (previous {before} kept as {TOOLKIT}.previous)")
    lines.append(readback(sha, job))
    return lines


def main(environ: dict[str, str] | None = None, jobs_at: JobsAt | None = None, git: Seam = git) -> int:
    environ = dict(os.environ) if environ is None else environ
    try:
        sha = landed_sha(environ)
        if jobs_at is None:
            board, card = environ.get(BOARD_ENV, ""), environ.get(CARD_ENV, "")
            if not board or not card:
                raise Refused(f"{BOARD_ENV} and {CARD_ENV} must name the card whose jobs the deploy runs as")
            jobs_at = lambda at: butler_job(board, card, at)  # noqa: E731
        for line in deploy(sha, jobs_at, git):
            print(line, flush=True)
    except Refused as refusal:
        print(f"deploy refused: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
