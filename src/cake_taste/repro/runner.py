"""Run one bounded, isolated command with immutable logs and receipt."""

from __future__ import annotations

import fcntl
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cake_taste.files import atomic_json, sha256_file
from cake_taste.logs.events import append_event


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _path(profile: Any, key: str) -> Path:
    return profile.resolve_path(profile.section("repro")[key])


_atomic_json = atomic_json
_digest = sha256_file


def _freeze_artifact(source: Path, destination: Path) -> dict[str, Any]:
    original = source.resolve(strict=True)
    if not original.is_file():
        raise ValueError("artifact is not a regular file: " + str(source))
    before = original.stat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".artifact-", dir=destination.parent)
    try:
        with original.open("rb") as inp, os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        copied = Path(temporary)
        after = original.stat()
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        checksum = _digest(copied)
        if identity_before != identity_after or source.resolve(strict=True) != original:
            raise ValueError("artifact changed while copying: " + str(source))
        if copied.stat().st_size != before.st_size or checksum != _digest(original):
            raise ValueError("artifact bytes changed while copying: " + str(source))
        os.replace(temporary, destination)
        dir_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        if _digest(destination) != checksum:
            raise ValueError("artifact readback hash mismatch: " + str(destination))
        return {
            "source": str(source),
            "path": str(destination),
            "sha256": checksum,
            "bytes": destination.stat().st_size,
        }
    finally:
        Path(temporary).unlink(missing_ok=True)


def run_command(
    profile: Any,
    *,
    name: str,
    command: list[str],
    function: str = "",
    cwd: Path | None = None,
    artifacts: list[Path] | None = None,
    expected_exit_code: int = 0,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not 0 <= expected_exit_code <= 255:
        raise ValueError("expected_exit_code must be 0..255")
    if not name.strip() or not command:
        raise ValueError("name and command are required")
    config = profile.section("repro")
    root = _path(profile, "runs_root")
    status_path = _path(profile, "status_path")
    lock_path = _path(profile, "lock_path")
    root.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        waiting = time.monotonic()
        # one isolated repro at a time: a later caller sleeps in the kernel until the holder ends
        fcntl.flock(lock, fcntl.LOCK_EX)
        waited = round(time.monotonic() - waiting, 3)
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir = root / run_id
        run_dir.mkdir()
        workdir = cwd.resolve() if cwd else run_dir
        if not workdir.is_dir():
            raise ValueError("repro cwd is not a directory")
        output = run_dir / "output.log"
        receipt = run_dir / "receipt.json"
        record: dict[str, Any] = {
            "schema": 1,
            "name": name,
            "function": function,
            "phase": "running",
            "command": shlex.join(command),
            "cwd": str(workdir),
            "log_path": str(output),
            "run_dir": str(run_dir),
            "started_at": _now(),
            "updated_at": _now(),
            "exit_code": None,
            "expected_exit_code": expected_exit_code,
            "artifacts": [],
            "error": None,
            "lock_wait_seconds": waited,
        }
        _atomic_json(status_path, record)
        append_event(profile, "repro", "started", name + ": " + shlex.join(command), str(receipt))
        child_env = os.environ.copy()
        for key in config.get("unset_env", []):
            child_env.pop(str(key), None)
        child_env.update({str(key): str(value) for key, value in config.get("env", {}).items()})
        child_env.update({str(key): str(value) for key, value in (env or {}).items()})
        config_dir, data_dir = run_dir / "config", run_dir / "data"
        config_dir.mkdir()
        data_dir.mkdir()
        child_env.update(XDG_CONFIG_HOME=str(config_dir), XDG_DATA_HOME=str(data_dir))
        try:
            with output.open("w", buffering=1) as stream:
                process = subprocess.Popen(
                    command,
                    cwd=workdir,
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert process.stdout is not None
                with process.stdout:
                    for line in process.stdout:
                        stream.write(line)
                        sys.stderr.write(line)
                code = process.wait()
        except OSError as error:
            code = 127
            output.write_text("launch failed: " + str(error) + "\n")
            record["error"] = str(error)
        frozen: list[dict[str, Any]] = []
        for index, item in enumerate(artifacts or [], start=1):
            source = item if item.is_absolute() else workdir / item
            name = f"{index:04d}-" + source.name
            try:
                frozen.append(_freeze_artifact(source, run_dir / "artifacts" / name))
            except (OSError, ValueError) as error:
                record["error"] = "artifact freeze failed: " + str(error)
                break
        record.update(
            phase="passed" if code == expected_exit_code and record["error"] is None else "failed",
            exit_code=code,
            artifacts=frozen,
            updated_at=_now(),
        )
        _atomic_json(status_path, record)
        _atomic_json(receipt, record)
        append_event(profile, "repro", record["phase"], name + " exited " + str(code), str(receipt))
        return {"receipt": str(receipt), "status": record}
