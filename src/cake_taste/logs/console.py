"""Curses console driven by JSONL append and terminal resize events."""

from __future__ import annotations

import ctypes
import curses
import os
import select
import signal
import sys
from contextlib import suppress
from pathlib import Path

from .events import EventReader, format_event
from .header import Header
from .terminal import resize_to_tty
from .wrap import WrappedLog, wrap_line

WATCH_MASK = 0x00000002 | 0x00000080 | 0x00000100


def _watch_directory(directory: Path) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_add_watch.restype = ctypes.c_int
    fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    if libc.inotify_add_watch(fd, os.fsencode(directory), WATCH_MASK) < 0:
        code = ctypes.get_errno()
        os.close(fd)
        raise OSError(code, "inotify_add_watch")
    return fd


def _put(screen, row: int, value: str, attr: int = 0) -> None:
    height, width = screen.getmaxyx()
    if row >= height or width < 3:
        return
    with suppress(curses.error):
        screen.addnstr(row, 1, value, width - 2, attr)


def _draw(screen, view: WrappedLog, count: int, title: str, header: Header) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    row = 0
    for part in wrap_line(f"{title} | {count} recorded events", width - 2):
        _put(screen, row, part, curses.A_BOLD)
        row += 1
    for text in header.lines(width - 2) if header.kinds else []:
        _put(screen, row, text)
        row += 1
    for index, line in enumerate(view.layout(width - 2, max(1, height - row - 1))):
        _put(screen, row + index, line)
    _put(screen, height - 1, "PgUp/PgDn Home/End wheel | " + view.position())
    screen.refresh()


def run_console(path: Path, *, title: str, max_lines: int = 4000, header: Header | None = None) -> None:
    header = header or Header([], [])
    path.parent.mkdir(parents=True, exist_ok=True)

    def main(screen):
        with suppress(curses.error):
            curses.curs_set(0)
        screen.keypad(True)
        screen.nodelay(True)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        reader = EventReader(path)
        view = WrappedLog(max_lines=max_lines)
        watcher = _watch_directory(path.parent)
        wake_read, wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        previous = signal.set_wakeup_fd(wake_write)
        signal.signal(signal.SIGWINCH, lambda _sig, _frame: None)
        poller = select.poll()
        poller.register(sys.stdin.fileno(), select.POLLIN)
        poller.register(watcher, select.POLLIN)
        poller.register(wake_read, select.POLLIN)
        try:
            for record in reader.read_new():
                header.update(record)
                for line in format_event(record):
                    view.append(line)
            resize_to_tty(sys.stdin.fileno())
            _draw(screen, view, reader.count, title, header)
            while True:
                for fd, _mask in poller.poll():
                    if fd == watcher:
                        with suppress(BlockingIOError):
                            os.read(watcher, 65536)
                        for record in reader.read_new():
                            header.update(record)
                            for line in format_event(record):
                                view.append(line)
                    elif fd == wake_read:
                        with suppress(BlockingIOError):
                            os.read(wake_read, 4096)
                        resize_to_tty(sys.stdin.fileno())
                    else:
                        while True:
                            key = screen.getch()
                            if key == -1:
                                break
                            if key == curses.KEY_PPAGE:
                                view.page(-1)
                            elif key == curses.KEY_NPAGE:
                                view.page(1)
                            elif key == curses.KEY_HOME:
                                view.home()
                            elif key == curses.KEY_END:
                                view.end()
                            elif key == curses.KEY_MOUSE:
                                try:
                                    state = curses.getmouse()[4]
                                    if state & curses.BUTTON4_PRESSED:
                                        view.scroll(-3)
                                    elif state & curses.BUTTON5_PRESSED:
                                        view.scroll(3)
                                except curses.error:
                                    pass
                _draw(screen, view, reader.count, title, header)
        finally:
            signal.set_wakeup_fd(previous)
            os.close(wake_read)
            os.close(wake_write)
            os.close(watcher)

    curses.wrapper(main)
