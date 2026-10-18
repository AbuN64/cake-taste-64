"""Atomic writes and SHA-256 digests shared by every Cake Taste domain."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Write data beside the target, fsync, then rename over it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(
    path: str | os.PathLike[str], value: Any, *, sort_keys: bool = False, ensure_ascii: bool = False
) -> None:
    text = json.dumps(value, indent=2, sort_keys=sort_keys, ensure_ascii=ensure_ascii) + "\n"
    atomic_bytes(path, text.encode("utf-8"))
