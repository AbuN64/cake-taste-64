#!/bin/sh
# deps.sh DEST: N64ModernRuntime and RT64 at the PINS commits with this host's patches applied, in DEST/<name>.
# Idempotent: an existing checkout is moved to its pin (refused if it carries other edits) and patched once.
# Configure the host with -DN64R_RUNTIME_SRC=DEST/N64ModernRuntime -DN64R_RT64_SRC=DEST/rt64.
set -eu
dest=${1:?usage: deps.sh DEST}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$dest"
prepare() {  # name url patch...
  name=$1 url=$2
  shift 2
  pin=$(awk -v n="$name" '$1 == n { print $2 }' "$here/PINS")
  dir=$dest/$name
  [ -d "$dir/.git" ] || git clone -q "$url" "$dir"
  if [ "$(git -C "$dir" rev-parse HEAD)" != "$pin" ]; then
    git -C "$dir" diff --quiet || { echo "deps: $dir has edits and is not at $pin" >&2; exit 1; }
    git -C "$dir" fetch -q origin
    git -C "$dir" checkout -q --detach "$pin"
  fi
  git -C "$dir" submodule update -q --init --recursive
  for patch in "$@"; do
    if git -C "$dir" apply --reverse --check "$here/patches/$patch" 2>/dev/null; then
      continue  # already applied
    fi
    git -C "$dir" apply "$here/patches/$patch"
  done
  echo "deps: $dir at $pin, patched: $*"
}
prepare N64ModernRuntime https://github.com/N64Recomp/N64ModernRuntime \
  n64modernruntime-thread-queue-remove.diff n64modernruntime-virtual-time.diff \
  n64modernruntime-thread-context-slab.diff
prepare rt64 https://github.com/rt64/rt64 rt64-acclaim-f3dex2-registration.diff rt64-headless-surface.diff
