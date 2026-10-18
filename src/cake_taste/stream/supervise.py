"""Event-driven process supervision: pidfd readiness, restart delays, hold-the-screen loops."""

from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Unit:
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    restart_seconds: float | None = 3.0  # None: exit of this unit is fatal to the whole group
    process: subprocess.Popen | None = None
    pidfd: int | None = None
    restart_at: float | None = None
    starts: int = 0


class FatalUnitExit(RuntimeError):
    def __init__(self, name: str, code: int):
        super().__init__(f"unit {name} exited with {code}")
        self.name, self.code = name, code


class Group:
    """Runs units, restarts those with a delay, and raises when a fatal unit exits."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen, clock: Callable[[], float] = time.monotonic):
        self.units: dict[str, Unit] = {}
        self.selector = selectors.DefaultSelector()
        self._popen, self._clock = popen, clock
        self._handlers: dict[int, Callable[[], None]] = {}

    def watch(self, fd: int, handler: Callable[[], None]) -> None:
        """Call handler whenever fd is readable, inside the same event loop as process exits."""
        self._handlers[fd] = handler
        self.selector.register(fd, selectors.EVENT_READ, fd)

    def start(self, unit: Unit) -> None:
        self.units[unit.name] = unit
        self._spawn(unit)

    def _spawn(self, unit: Unit) -> None:
        env = {**os.environ, **unit.env}
        unit.process = self._popen(unit.command, env=env, start_new_session=True)
        unit.starts += 1
        unit.restart_at = None
        unit.pidfd = os.pidfd_open(unit.process.pid)
        self.selector.register(unit.pidfd, selectors.EVENT_READ, unit.name)

    def _reap(self, unit: Unit) -> int:
        assert unit.process is not None and unit.pidfd is not None
        self.selector.unregister(unit.pidfd)
        os.close(unit.pidfd)
        unit.pidfd = None
        code = unit.process.wait()
        unit.process = None
        return code

    def step(self, max_wait: float | None = None) -> list[str]:
        """Handle one round of events; return names of units that exited. Raises on fatal exit."""
        now = self._clock()
        due = [u.restart_at for u in self.units.values() if u.restart_at is not None]
        timeout = max(0.0, min(due) - now) if due else max_wait
        if due and max_wait is not None:
            timeout = min(timeout, max_wait)
        exited: list[str] = []
        for key, _ in self.selector.select(timeout):
            if isinstance(key.data, int):
                try:
                    self._handlers[key.data]()
                except ConnectionError:
                    self.selector.unregister(key.data)
                continue
            unit = self.units[key.data]
            code = self._reap(unit)
            exited.append(unit.name)
            if unit.restart_seconds is None:
                raise FatalUnitExit(unit.name, code)
            unit.restart_at = self._clock() + unit.restart_seconds
        now = self._clock()
        for unit in self.units.values():
            if unit.restart_at is not None and unit.restart_at <= now:
                self._spawn(unit)
        return exited

    def run_forever(self) -> None:
        while True:
            self.step()

    def stop(self) -> None:
        for unit in self.units.values():
            if unit.process is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(unit.process.pid, signal.SIGTERM)
        deadline = self._clock() + 5
        for unit in self.units.values():
            if unit.process is None:
                continue
            try:
                unit.process.wait(timeout=max(0.1, deadline - self._clock()))
            except subprocess.TimeoutExpired:
                os.killpg(unit.process.pid, signal.SIGKILL)
                unit.process.wait()
            if unit.pidfd is not None:
                self.selector.unregister(unit.pidfd)
                os.close(unit.pidfd)
                unit.pidfd = None
            unit.process = None


def hold_loop(
    command: Sequence[str],
    restart_seconds: float,
    *,
    title: str = "",
    run: Callable[..., Any] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    out: Any = None,
    max_runs: int | None = None,
) -> int:
    """Run command forever inside a terminal tile; on exit keep its last output on screen and restart.

    Nothing is cleared between runs, so the final screen of a dead role stays visible during the delay.
    """
    out = out or sys.stdout
    runs = 0
    while max_runs is None or runs < max_runs:
        runs += 1
        code = run(list(command)).returncode
        stamp = time.strftime("%H:%M:%S")
        print(
            f"\n[{stamp}] {title or command[0]} exited rc={code}; restarting in {restart_seconds:g}s",
            file=out,
            flush=True,
        )
        sleep(restart_seconds)
    return 0
