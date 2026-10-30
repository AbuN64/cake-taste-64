#!/usr/bin/env python3
"""Deploys the landed CakeTaste64 commit to the rig oracle: `.deploy` runs this after Land, with the landed SHA in
LANDED_Projects_AbuN64_CakeTaste64. Any refusal or failed step prints one line on stderr and exits 1.

  0. one rig-side flock per repo (~/finish/deploy-taste.lock), taken before anything else and held until
     the readback ends (or the deploy dies), so deploys of this repo never overlap. Under it gitea main is fetched:
     when main has moved past the landed SHA to a descendant, main's head is deployed instead and the log says the
     older deploy deferred; when main is not a descendant of the landed SHA, the deploy refuses;
  1. the SHA to deploy must be on gitea main (else nothing touches the rig);
  2. when ~/rig-state/tas/live/oracle already points at snaps/oracle-<sha12>-*, nothing is built or swapped;
  3. otherwise n64host is built at the SHA through ~/finish/slot into a new snaps/oracle-<sha12>-<epoch> directory
     (pinned deps copied from ~/src/oracle-deps and patched by hosts/n64recomp/deps.sh), never into an existing one;
  4. the suite gate, always, between the build and the swap: the full test suite (unittest discover over tests/) runs
     through ~/finish/slot on a clean checkout of exactly SHA with its src first on the import path. The imported
     cake_taste path is printed and must lie inside the checkout. Any failure refuses (one line naming the first
     failing test) with the live link untouched; the summary line, the SHA and the imported path go into the deploy
     log and into snaps/<name>/suite.txt. Each run has its own checkout, log, slots dir and tmp dir, so no state is
     shared between runs;
  5. the live link is swapped to the new directory by renaming a fresh link over it; previous directories stay;
  6. readback, always: boot-idle runs twice through the live oracle and the two state hashes must be equal."""

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import PurePosixPath

ENV = "LANDED_Projects_AbuN64_CakeTaste64"
RIG = "abu@192.168.0.101"
GITEA = "http://10.10.10.130:18556/Projects/CakeTaste64.git"
PACKAGE = "cake_taste"
PYTHON = "/usr/bin/python3"
LIVE = "~/rig-state/tas/live"
SHA = re.compile(r"[0-9a-f]{40}")
SNAP = re.compile(r"(?:.*/)?oracle-([0-9a-f]{12})-\d+")

# ssh(command) runs one shell line on the rig and git(args) runs git here; both return (exit code, output).
Seam = Callable[..., "tuple[int, str]"]
Lock = Callable[[], AbstractContextManager[list[str]]]  # rig_lock(): holds the flock for its with-block

# The per-repo rig lock: one ssh session takes the flock on its file and holds it while its stdin stays open, so the
# lock lives exactly as long as the deploy (a killed deploy closes the pipe and frees it). It prints `waiting` when
# another deploy holds the lock and `locked` once this one has it.
LOCK = "deploy-taste.lock"
HOLD = r"""exec 7>>"$HOME/finish/$1" || exit 9
flock -n 7 || echo waiting
flock 7 || exit 9
echo locked
exec cat
"""

# build SHA SNAP_NAME: the new directory holds the source, copied deps and build tree only while it builds.
BUILD = r"""set -eu
sha=$1 name=$2 url=$3
live=$HOME/rig-state/tas/live
new=$live/snaps/$name
mkdir "$new"
cd "$new"
exec > build.log 2>&1
git init -q src
git -C src fetch -q "$url" main
git -C src checkout -q --detach "$sha"
mkdir deps
cp -a "$HOME/src/oracle-deps/N64ModernRuntime" "$HOME/src/oracle-deps/rt64" deps/
sh src/hosts/n64recomp/deps.sh "$new/deps"
cmake -S src/hosts/n64recomp -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-14 -DCMAKE_CXX_COMPILER=/usr/bin/g++-14 \
  -DN64R_GEN_DIR="$HOME/rig-state/behave/gen/us-rev1" -DN64R_PINS="$new/src/hosts/n64recomp/PINS" \
  -DN64R_PROFILE="$HOME/rig-state/behave/oracle-facts.json" \
  -DN64R_REFUSED="$HOME/rig-state/behave/gen/us-rev1/refused.tsv" \
  -DN64R_RT64_SRC="$new/deps/rt64" -DN64R_RUNTIME_SRC="$new/deps/N64ModernRuntime" \
  -DFETCHCONTENT_FULLY_DISCONNECTED=ON
"$HOME/finish/slot" nice -n 19 cmake --build build -- -j8
install -m 755 build/n64host n64host
python3 - "$sha" "$live/oracle/oracle-pins.json" > oracle-pins.json <<'PY'
import json, sys, time
old = json.load(open(sys.argv[2]))
print(json.dumps({"cake_taste": sys.argv[1], "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "wall": old["wall"], "cpu": old["cpu"]}, indent=1))
PY
rm -rf src deps build
"""

# swap SNAP_NAME: a new link renamed over the live one, so a reader sees the old or the new directory, never none.
SWAP = r"""set -eu
live=$HOME/rig-state/tas/live
test -x "$live/snaps/$1/n64host"
ln -sfn "snaps/$1" "$live/.oracle.new"
mv -T "$live/.oracle.new" "$live/oracle"
"""

# readback: boot-idle twice through the live oracle, one first-16-hex state hash per line (env as tas_oracle.start).
READBACK = r"""set -eu
host=$(readlink -f "$HOME/rig-state/tas/live/oracle")/n64host
movie=$HOME/rig-state/tas/runs/plays/20261030T142016Z-boot-idle/movie.raw
spec=$HOME/rig-state/tas/runs/state-spec/b1d54eea5d7361537883/oracle.spec
test -x "$host"; test -f "$movie"; test -f "$spec"
work=$(mktemp -d "$HOME/finish/DEPLOY-READBACK.XXXXXX")
trap 'rm -rf "$work"' EXIT
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
    "$HOME/finish/slot" "$host" > oracle.txt 2>&1 || rc=$?
    test "$rc" = 0 || { tail -n 5 oracle.txt >&2; echo "boot-idle run $n exit $rc" >&2; exit 1; }
  )
  sha256sum "$out/state-oracle.txt" | cut -c1-16
done
"""


# suite SHA URL PYTHON DEST: a clean checkout of exactly SHA, the suite run in it, one `suite-KEY: value` line per fact.
SUITE = r"""set -u
sha=$1 url=$2 py=$3 dest=$4
work=$(mktemp -d "$HOME/finish/DEPLOY-SUITE.XXXXXX") || exit 9
trap 'rm -rf "$work"' EXIT
log=$(mktemp "$HOME/finish/deploy-suite-${sha:0:12}.XXXXXX.log") || exit 9
git -c init.templateDir= init -q "$work/checkout" || exit 9
cd "$work/checkout" || exit 9
git fetch -q "$url" main || { echo "suite-error: cannot fetch main from $url" >&2; exit 9; }
git checkout -q --detach "$sha" || { echo "suite-error: cannot check out $sha" >&2; exit 9; }
echo "suite-checkout: $(pwd -P)"
echo "suite-sha: $(git rev-parse HEAD)"
cat > "$work/runner.py" <<'PY'
__RUNNER__
PY
mkdir .tmp
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=init.templateDir GIT_CONFIG_VALUE_0=
export TMPDIR=$PWD/.tmp ABU_SLOTS_DIR=$(mktemp -d "$work/slots.XXXXXX")
export PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1
rc=0
"$py" "$work/runner.py" > "$log" 2>&1 || rc=$?
grep '^suite-' "$log"
echo "suite-log: $log"
if [ "$rc" = 0 ]; then
  { echo "suite-sha: $(git rev-parse HEAD)"; grep '^suite-\(summary\|import\)' "$log"; } > "$dest/suite.txt" || exit 9
fi
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


def run(argv: list[str]) -> tuple[int, str]:
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    return done.returncode, (done.stdout + done.stderr).strip()


def ssh(command: str) -> tuple[int, str]:
    return run(["ssh", "-o", "BatchMode=yes", RIG, command])


def git(*args: str) -> tuple[int, str]:
    return run(["git", *args])


def suite_command(sha: str, python: str, dest: str, url: str = GITEA) -> str:
    script = shlex.quote(SUITE.replace("__RUNNER__", RUNNER))
    return f"~/finish/slot bash -c {script} suite {sha} {url} {python} {dest}"


@contextlib.contextmanager
def rig_lock() -> Iterator[list[str]]:
    """Holds the rig flock for the with-block; yields the log lines about the wait (empty when the lock was free)."""
    holder = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", RIG, f"bash -c {shlex.quote(HOLD)} hold {LOCK}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    notes = []
    try:
        for line in holder.stdout:
            if line.strip() == "locked":
                break
            if line.strip() == "waiting":
                notes.append(f"another deploy holds ~/finish/{LOCK}, waiting for it")
        else:
            raise Refused(f"cannot take ~/finish/{LOCK} on the rig (ssh exit {holder.wait()})")
        yield notes
    finally:
        holder.stdin.close()  # the rig side reads its stdin until EOF, then exits and frees the lock
        holder.wait()


def landed_sha(environ: dict[str, str]) -> str:
    sha = environ.get(ENV, "")
    if not SHA.fullmatch(sha):
        raise Refused(f"{ENV} is not a 40-hex SHA: {sha!r}")
    return sha


def last_line(text: str, default: object) -> str:
    return text.splitlines()[-1] if text else str(default)


def newest_main(sha: str, git: Seam) -> str:
    """The SHA to deploy: gitea main's head when it descends from sha (sha itself when it is the head), else Refused."""
    work = tempfile.mkdtemp(prefix="deploy-rig-")
    try:
        git("-C", work, "init", "-q", "--bare")
        code, out = git("-C", work, "fetch", "-q", "--no-tags", "--filter=blob:none", GITEA, "main")
        if code:
            raise Refused(f"cannot fetch gitea main ({GITEA}): {last_line(out, code)}")
        if git("-C", work, "merge-base", "--is-ancestor", sha, "FETCH_HEAD")[0]:
            raise Refused(f"{sha[:12]} is not on gitea main")
        code, out = git("-C", work, "rev-parse", "FETCH_HEAD")
        return out if code == 0 and SHA.fullmatch(out) else sha
    finally:
        shutil.rmtree(work, ignore_errors=True)


def read_live(ssh: Seam) -> str:
    """The live oracle link target, e.g. snaps/oracle-<sha12>-<epoch>."""
    code, out = ssh(f"readlink {LIVE}/oracle")
    if code or not SNAP.fullmatch(out):
        raise Refused(f"cannot read the live oracle link: {last_line(out, code)}")
    return out


def build(sha: str, name: str, ssh: Seam) -> None:
    code, out = ssh(f"bash -c {shlex.quote(BUILD)} build {sha} {name} {GITEA}")
    if code:
        tail = ssh(f"tail -n 3 {LIVE}/snaps/{name}/build.log")[1].replace("\n", " | ")
        raise Refused(f"build of {sha[:12]} exit {code} in snaps/{name}: {tail or out or 'no output'}")


def swap(name: str, ssh: Seam) -> None:
    code, out = ssh(f"bash -c {shlex.quote(SWAP)} swap {name}")
    if code:
        raise Refused(f"cannot swap the live oracle to snaps/{name}: {last_line(out, code)}")
    if read_live(ssh) != f"snaps/{name}":
        raise Refused(f"readback: the live oracle link must read snaps/{name}")


def readback(ssh: Seam) -> str:
    code, out = ssh(f"bash -c {shlex.quote(READBACK)}")
    hashes = out.splitlines()
    if code or len(hashes) != 2 or not all(re.fullmatch(r"[0-9a-f]{16}", h) for h in hashes):
        raise Refused(f"readback boot-idle failed (exit {code}): {last_line(out, 'no output')}")
    if hashes[0] != hashes[1]:
        raise Refused(f"readback boot-idle state hashes differ: {hashes[0]} {hashes[1]}")
    return f"readback boot-idle {hashes[0]} {hashes[1]}"


def suite_facts(out: str) -> dict[str, str]:
    facts = {}
    for line in out.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key.startswith("suite-"):
            facts[key] = value
    return facts


def suite(sha: str, dest: str, ssh: Seam) -> list[str]:
    """The full suite at exactly sha on a clean rig checkout; returns the log lines, raises Refused on any failure."""
    code, out = ssh(suite_command(sha, PYTHON, dest))
    facts = suite_facts(out)
    for key in ("suite-checkout", "suite-sha", "suite-import"):
        if key not in facts:
            raise Refused(f"suite gate did not start at {sha[:12]} (exit {code}): {last_line(out, 'no output')}")
    if facts["suite-sha"] != sha:
        raise Refused(f"suite ran at {facts['suite-sha']}, not the landed {sha}")
    if not PurePosixPath(facts["suite-import"]).is_relative_to(facts["suite-checkout"]):
        raise Refused(
            f"suite imported {PACKAGE} from {facts['suite-import']}, outside its checkout {facts['suite-checkout']}"
        )
    if code or "suite-first-failure" in facts or "suite-summary" not in facts:
        first = facts.get("suite-first-failure") or f"no summary, exit {code}: {last_line(out, 'no output')}"
        summary, log = facts.get("suite-summary", "no summary"), facts.get("suite-log", "no log")
        raise Refused(f"suite failed at {first}: {summary} ({log})")
    return [
        f"{sha[:12]}: suite {facts['suite-summary']}",
        f"{sha[:12]}: suite sha {facts['suite-sha']}",
        f"{sha[:12]}: suite imported {facts['suite-import']}",
    ]


def deploy(sha: str, ssh: Seam, git: Seam, lock: Lock, now: Callable[[], float] = time.time) -> list[str]:
    """Takes the rig lock, then deploys the newest landed main; returns the log lines to print, the readback last."""
    with lock() as waited:
        return [*waited, *deploy_locked(sha, ssh, git, now)]


def deploy_locked(sha: str, ssh: Seam, git: Seam, now: Callable[[], float]) -> list[str]:
    """The mechanism under the lock. Raises Refused on the first failure."""
    lines = []
    head = newest_main(sha, git)
    if head != sha:
        lines.append(f"{sha[:12]}: main has moved to {head[:12]}, deploying main's head; the older deploy deferred")
        sha = head
    before = read_live(ssh)
    lines.append(f"live oracle before: {before}")
    match = SNAP.fullmatch(before)
    if match and match.group(1) == sha[:12]:
        lines.append(f"{sha[:12]}: the live oracle already reads {before}, build and swap skipped")
        lines += suite(sha, f"{LIVE}/{before}", ssh)
    else:
        name = f"oracle-{sha[:12]}-{int(now())}"
        build(sha, name, ssh)
        lines += suite(sha, f"{LIVE}/snaps/{name}", ssh)
        swap(name, ssh)
        lines.append(f"live oracle after: snaps/{name} (previous {before} kept)")
    lines.append(readback(ssh))
    return lines


def main(environ: dict[str, str] | None = None) -> int:
    try:
        for line in deploy(landed_sha(dict(os.environ) if environ is None else environ), ssh, git, rig_lock):
            print(line, flush=True)
    except Refused as refusal:
        print(f"deploy refused: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
