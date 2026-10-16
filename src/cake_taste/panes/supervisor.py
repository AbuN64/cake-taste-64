"""Supervise a configured native process with an optional real GDB stop."""

from __future__ import annotations

import asyncio
import curses
import hashlib
import json
import os
import shlex
import signal
import sys
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cake_taste.logs.events import append_event
from cake_taste.logs.terminal import resize_to_tty
from cake_taste.logs.wrap import WrappedLog, wrap_line


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pane-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _fill(values: list[str], mapping: dict[str, str]) -> list[str]:
    return [str(value).format_map(mapping) for value in values]


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _put(screen, row: int, value: str, attr: int = 0) -> None:
    height, width = screen.getmaxyx()
    if row >= height or width < 3:
        return
    with suppress(curses.error):
        screen.addnstr(row, 1, value, width - 2, attr)


class Supervisor:
    def __init__(self, profile: Any, role_id: str, config: dict[str, Any], screen):
        self.profile, self.role_id, self.config, self.screen = profile, role_id, config, screen
        self.status_path = profile.resolve_path(config["status_path"])
        self.runs_root = profile.resolve_path(config["runs_root"])
        self.retry_delay = int(config["retry_delay_seconds"])
        self.watchdog = float(config["watchdog_seconds"])
        if self.retry_delay <= 0 or self.watchdog <= 0:
            raise ValueError("retry and watchdog durations must be positive")
        self.lines = WrappedLog(max_lines=int(config.get("max_lines", 2000)))
        self.phase = "starting"
        self.attempt = 0
        self.step = ""
        self.reason = ""
        self.stack = None
        self.capture = None
        self.last_result = ""
        self.last_exit = None
        self.signal_name = None
        self.hold_kind = None
        self.retry_remaining = 0
        self.paused = False
        self.proc = None
        self.restart = asyncio.Event()
        self.stack_ready = asyncio.Event()
        self.debug_stopped = False
        self.started_at = ""
        self.run_dir = None
        self.current_command: list[str] = []
        self.current_binary = None
        self.current_sha = None
        self.last_announced_failure = None

    def status(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "role": self.role_id,
            "phase": self.phase,
            "attempt": self.attempt,
            "step": self.step,
            "failure_reason": self.reason,
            "failure_kind": self.hold_kind,
            "debugger_stopped": self.debug_stopped,
            "stack": str(self.stack) if self.stack else None,
            "capture": str(self.capture) if self.capture else None,
            "last_result": self.last_result,
            "exit_code": self.last_exit,
            "retry_remaining_seconds": self.retry_remaining,
            "retry_delay_seconds": self.retry_delay,
            "paused": self.paused,
            "command": self.current_command,
            "binary_sha256": self.current_sha,
            "started_at": self.started_at,
            "terminal_rows_columns": list(self.screen.getmaxyx()),
            "updated_at": _now(),
        }

    def persist(self) -> None:
        _atomic_json(self.status_path, self.status())
        self.draw()

    def draw(self) -> None:
        screen = self.screen
        screen.erase()
        height, width = screen.getmaxyx()
        state = (
            str(self.config.get("debugger_hold_label", "debugger hold"))
            if self.hold_kind == "debugger"
            else ("signal " + self.signal_name if self.signal_name else self.phase.replace("_", " "))
        )
        headers = [
            str(self.config.get("title", self.role_id))
            + " | "
            + state
            + " | retry "
            + str(self.retry_remaining)
            + "/"
            + str(self.retry_delay)
            + "s",
            "Reason: " + (self.reason or "awaiting actual process output"),
            "Attempt " + str(self.attempt) + " | last " + (self.last_result or "none") + " exit " + str(self.last_exit),
            "ACTUAL stdout/stderr and debugger frames:",
        ]
        row = 0
        for header in headers:
            for part in wrap_line(header, width - 2):
                if row < height - 2:
                    _put(screen, row, part, curses.A_BOLD if row == 0 else 0)
                    row += 1
        for index, line in enumerate(self.lines.layout(width - 2, max(1, height - row - 1))):
            _put(screen, row + index, line)
        _put(screen, height - 1, "F5 restart F7 pause | PgUp/PgDn Home/End wheel | " + self.lines.position())
        screen.refresh()

    def handle_key(self, key: int) -> None:
        if key == curses.KEY_F5:
            self.paused = False
            self.restart.set()
            self.lines.append("[control] explicit cold restart requested")
        elif key == curses.KEY_F7:
            self.paused = not self.paused
            self.lines.append("[control] automatic retry " + ("paused" if self.paused else "resumed"))
        elif key == curses.KEY_PPAGE:
            self.lines.page(-1)
        elif key == curses.KEY_NPAGE:
            self.lines.page(1)
        elif key == curses.KEY_HOME:
            self.lines.home()
        elif key == curses.KEY_END:
            self.lines.end()
        elif key == curses.KEY_MOUSE:
            try:
                state = curses.getmouse()[4]
                if state & curses.BUTTON4_PRESSED:
                    self.lines.scroll(-3)
                elif state & curses.BUTTON5_PRESSED:
                    self.lines.scroll(3)
            except curses.error:
                pass
        self.persist()

    def _command(self, run_dir: Path) -> tuple[list[str], dict[str, str], Path]:
        link = self.profile.resolve_path(self.config["release_current"])
        release = link.resolve(strict=True)
        runtime = Path(self.profile.runtime_root).resolve()
        if not release.is_relative_to(runtime):
            raise ValueError("release resolves outside configured runtime root")
        mapping = {"release": str(release), "run_dir": str(run_dir), "runtime_root": str(runtime)}
        argv = _fill(self.config["command"], mapping)
        binary = Path(argv[0]).resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError("configured native executable is not executable")
        environment = os.environ.copy()
        environment.update({key: str(value).format_map(mapping) for key, value in self.config.get("env", {}).items()})
        environment["XDG_CONFIG_HOME"] = str(run_dir / "config")
        environment["XDG_DATA_HOME"] = str(run_dir / "data")
        (run_dir / "config").mkdir()
        (run_dir / "data").mkdir()
        return argv, environment, binary

    def _debug_command(self, command: list[str], run_dir: Path) -> list[str]:
        debug = self.config.get("debugger")
        if not debug:
            return command
        return [
            sys.executable,
            "-m",
            "cake_taste.panes.gdb",
            "--gdb",
            str(self.profile.resolve_path(debug["executable"])),
            "--library-dir",
            str(self.profile.resolve_path(debug["library_dir"])) if debug.get("library_dir") else "",
            "--breakpoint",
            str(debug["breakpoint"]),
            "--reason-expression",
            str(debug["reason_expression"]),
            "--run-dir",
            str(run_dir),
            "--",
            *command,
        ]

    async def _read_output(self, stream, output) -> None:
        while raw := await stream.readline():
            line = raw.decode(errors="replace").rstrip("\r\n")
            output.write(_now() + " " + line + "\n")
            output.flush()
            self.lines.append(line)
            for prefix in self.config.get("step_prefixes", []):
                if line.startswith(prefix):
                    self.step = line[len(prefix) :].strip()
            if line.startswith("debug-stop:"):
                self.hold_kind = "debugger"
                self.debug_stopped = True
                self.phase = "debugger_stopped"
            elif line.startswith("debug-reason:"):
                self.reason = line.split(":", 1)[1].strip()
            elif line.startswith("debug-stack:"):
                self.stack = Path(line.split(":", 1)[1].strip())
            elif line.startswith("debug-signal:"):
                self.signal_name = line.split(":", 1)[1].strip()
            elif line.startswith("C_STACK_END"):
                self.stack_ready.set()
            elif (complete_prefix := self.config.get("complete_prefix")) and line.startswith(complete_prefix):
                self.phase = "complete"
            self.persist()

    async def _countdown(self) -> bool:
        self.retry_remaining = self.retry_delay
        self.phase = "debugger_hold_countdown" if self.debug_stopped else "retry_countdown"
        self.persist()
        while self.retry_remaining:
            if self.restart.is_set():
                return False
            await asyncio.sleep(1)
            if not self.paused:
                self.retry_remaining -= 1
                self.persist()
        return True

    async def _stop_child(self) -> None:
        if self.proc and self.proc.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except TimeoutError:
                os.killpg(self.proc.pid, signal.SIGKILL)
                await self.proc.wait()

    async def _capture(self, run_dir: Path) -> None:
        configured = self.config.get("capture_command")
        if not configured:
            return
        target = run_dir / "failure.png"
        command = _fill(configured, {"capture": str(target), "run_dir": str(run_dir)})
        try:
            process = await asyncio.create_subprocess_exec(*command)
            code = await asyncio.wait_for(process.wait(), timeout=10)
            if code == 0 and target.is_file() and target.stat().st_size:
                self.capture = target
        except (TimeoutError, OSError) as error:
            self.lines.append("[capture] " + str(error))
        self.persist()

    async def run_once(self) -> bool:
        self.attempt += 1
        self.restart.clear()
        self.stack_ready.clear()
        self.step = self.reason = ""
        self.stack = self.capture = None
        self.signal_name = self.hold_kind = None
        self.debug_stopped = False
        self.retry_remaining = 0
        self.started_at = _now()
        self.proc = None
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + str(self.attempt)
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True)
        self.run_dir = run_dir
        self.lines.append("=== attempt " + str(self.attempt) + " ===")
        command, environment, binary = self._command(run_dir)
        self.current_binary = binary
        self.current_sha = _digest(binary)
        self.current_command = command
        launched = self._debug_command(command, run_dir)
        self.phase = "starting"
        self.persist()
        exit_code = 127
        outcome = "launch_failed"
        with (run_dir / "output.log").open("w", buffering=1) as output:
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *launched,
                    cwd=run_dir,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as error:
                self.lines.append("[launch] " + str(error))
                output.write("launch failed: " + str(error) + "\n")
            else:
                assert self.proc.stdout is not None
                reader = asyncio.create_task(self._read_output(self.proc.stdout, output))
                wait = asyncio.create_task(self.proc.wait())
                stack = asyncio.create_task(self.stack_ready.wait())
                manual = asyncio.create_task(self.restart.wait())
                try:
                    done, _ = await asyncio.wait(
                        {wait, stack, manual},
                        timeout=self.watchdog,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if manual in done:
                        outcome = "manual_restart"
                        await self._stop_child()
                    elif stack in done and self.debug_stopped:
                        await self._capture(run_dir)
                        if await self._countdown() and self.proc.returncode is None:
                            self.phase = "fatal_continuing"
                            self.persist()
                            assert self.proc.stdin is not None
                            self.proc.stdin.write(b"continue\n")
                            await self.proc.stdin.drain()
                            exit_code = await self.proc.wait()
                            outcome = "debugger_exit"
                        else:
                            outcome = "manual_restart"
                            await self._stop_child()
                    elif wait in done or stack in done:
                        exit_code = await asyncio.wait_for(wait, timeout=5)
                        outcome = "clean_exit" if exit_code == 0 else "nonzero_exit"
                    else:
                        outcome = "watchdog_timeout"
                        await self._stop_child()
                    await reader
                finally:
                    for task in (wait, stack, manual):
                        if not task.done():
                            task.cancel()
        if self.proc and self.proc.returncode is not None:
            exit_code = self.proc.returncode
        if self.signal_name and outcome == "nonzero_exit":
            outcome = "signal_exit"
        self.last_exit = exit_code
        self.last_result = outcome
        self.phase = "failed" if outcome not in {"clean_exit", "manual_restart"} else outcome
        self.debug_stopped = False
        self.persist()
        receipt = {
            **self.status(),
            "outcome": outcome,
            "ended_at": _now(),
            "run_dir": str(run_dir),
            "log": str(run_dir / "output.log"),
            "repro_command": shlex.join(command),
            "stack": str(self.stack) if self.stack else None,
            "capture": str(self.capture) if self.capture else None,
        }
        receipt_path = run_dir / "receipt.json"
        _atomic_json(receipt_path, receipt)
        failure = (outcome, self.reason, self.signal_name)
        if outcome not in {"clean_exit", "manual_restart"} and failure != self.last_announced_failure:
            append_event(
                self.profile,
                self.role_id,
                "blocked" if self.hold_kind == "debugger" else "failed",
                self.reason or outcome,
                str(receipt_path),
            )
            self.last_announced_failure = failure
        if outcome == "manual_restart":
            return True
        if outcome == "debugger_exit":
            return True
        return await self._countdown()

    async def manage(self) -> None:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                await self.run_once()
            except Exception as error:
                self.phase = "supervisor_error"
                self.reason = str(error)
                self.lines.append("[supervisor] " + str(error))
                self.persist()
                await self._countdown()


def run_supervisor(profile: Any, role_id: str, config: dict[str, Any]) -> None:
    def main(screen):
        with suppress(curses.error):
            curses.curs_set(0)
        screen.keypad(True)
        screen.nodelay(True)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)

        async def asynchronous():
            state = Supervisor(profile, role_id, config, screen)
            loop = asyncio.get_running_loop()

            def input_ready():
                while (key := screen.getch()) != -1:
                    state.handle_key(key)

            def resize():
                resize_to_tty(sys.stdin.fileno())
                state.draw()

            loop.add_reader(sys.stdin.fileno(), input_ready)
            loop.add_signal_handler(signal.SIGWINCH, resize)
            resize()  # The compositor may have resized foot before the handler existed.
            try:
                await state.manage()
            finally:
                loop.remove_reader(sys.stdin.fileno())
                loop.remove_signal_handler(signal.SIGWINCH)
                await state._stop_child()

        asyncio.run(asynchronous())

    curses.wrapper(main)
