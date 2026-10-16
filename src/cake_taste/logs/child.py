"""Keep finite child output off the one-envelope public stdout channel."""

from __future__ import annotations

import subprocess
import sys

from cake_taste import TasteError


def run_logged(argv: list[str], *, stage: str, failure_key: str) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, errors="replace", check=False)
    except OSError as error:
        raise TasteError(failure_key, f"{stage}: {error}") from error
    if result.stdout:
        sys.stderr.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode:
        raise TasteError(failure_key, f"{stage} child exited {result.returncode}")
