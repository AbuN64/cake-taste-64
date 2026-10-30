#!/usr/bin/env python3
"""Deploys the landed CakeTaste64 commit to the rig oracle: `.deploy` runs this after Land, with the landed SHA in
LANDED_Projects_AbuN64_CakeTaste64. Any refusal or failed step prints one line on stderr and exits 1.

  1. the SHA must be on gitea main (else nothing touches the rig);
  2. when ~/rig-state/tas/live/oracle already points at snaps/oracle-<sha12>-*, nothing is built or swapped;
  3. otherwise n64host is built at the SHA through ~/finish/slot into a new snaps/oracle-<sha12>-<epoch> directory
     (pinned deps copied from ~/src/oracle-deps and patched by hosts/n64recomp/deps.sh), never into an existing one;
  4. the live link is swapped to the new directory by renaming a fresh link over it; previous directories stay;
  5. readback, always: boot-idle runs twice through the live oracle and the two state hashes must be equal."""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable

ENV = "LANDED_Projects_AbuN64_CakeTaste64"
RIG = "abu@192.168.0.101"
GITEA = "http://10.10.10.130:18556/Projects/CakeTaste64.git"
LIVE = "~/rig-state/tas/live"
SHA = re.compile(r"[0-9a-f]{40}")
SNAP = re.compile(r"(?:.*/)?oracle-([0-9a-f]{12})-\d+")

# ssh(command) runs one shell line on the rig and git(args) runs git here; both return (exit code, output).
Seam = Callable[..., "tuple[int, str]"]

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
    export SDL_VIDEODRIVER=offscreen SDL_AUDIODRIVER=dummy N64R_CLOCK=entries
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


class Refused(Exception):
    """One line cause of a refusal or failed step."""


def run(argv: list[str]) -> tuple[int, str]:
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    return done.returncode, (done.stdout + done.stderr).strip()


def ssh(command: str) -> tuple[int, str]:
    return run(["ssh", "-o", "BatchMode=yes", RIG, command])


def git(*args: str) -> tuple[int, str]:
    return run(["git", *args])


def landed_sha(environ: dict[str, str]) -> str:
    sha = environ.get(ENV, "")
    if not SHA.fullmatch(sha):
        raise Refused(f"{ENV} is not a 40-hex SHA: {sha!r}")
    return sha


def last_line(text: str, default: object) -> str:
    return text.splitlines()[-1] if text else str(default)


def check_on_main(sha: str, git: Seam) -> None:
    """Refuse unless sha is an ancestor of main on gitea (fetched into a scratch bare repo)."""
    work = tempfile.mkdtemp(prefix="deploy-rig-")
    try:
        git("-C", work, "init", "-q", "--bare")
        code, out = git("-C", work, "fetch", "-q", "--no-tags", "--filter=blob:none", GITEA, "main")
        if code:
            raise Refused(f"cannot fetch gitea main ({GITEA}): {last_line(out, code)}")
        if git("-C", work, "merge-base", "--is-ancestor", sha, "FETCH_HEAD")[0]:
            raise Refused(f"{sha[:12]} is not on gitea main")
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


def deploy(sha: str, ssh: Seam, git: Seam, now: Callable[[], float] = time.time) -> list[str]:
    """The whole mechanism; returns the log lines to print, the readback last. Raises Refused on the first failure."""
    check_on_main(sha, git)
    before = read_live(ssh)
    lines = [f"live oracle before: {before}"]
    match = SNAP.fullmatch(before)
    if match and match.group(1) == sha[:12]:
        lines.append(f"{sha[:12]}: the live oracle already reads {before}, build and swap skipped")
    else:
        name = f"oracle-{sha[:12]}-{int(now())}"
        build(sha, name, ssh)
        swap(name, ssh)
        lines.append(f"live oracle after: snaps/{name} (previous {before} kept)")
    lines.append(readback(ssh))
    return lines


def main(environ: dict[str, str] | None = None) -> int:
    try:
        for line in deploy(landed_sha(dict(os.environ) if environ is None else environ), ssh, git):
            print(line, flush=True)
    except Refused as refusal:
        print(f"deploy refused: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
