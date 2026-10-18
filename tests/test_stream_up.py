import io
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from cake_taste.logs.gitrefs import RefSource
from cake_taste.stream import layout
from cake_taste.stream.inotify import wait_for_path
from cake_taste.stream.supervise import FatalUnitExit, Group, Unit, hold_loop

UP = {
    "output": {"name": "HEADLESS-1", "width": 1920, "height": 1080},
    "tiles": [["a", "b"], ["c", "d"]],
    "roles": {"a": {"overlay_app_id": "game"}, "b": {}, "c": {}, "d": {}},
}


class FakeIpc:
    def __init__(self):
        self.commands = []

    def command(self, value):
        self.commands.append(value)


class LayoutTests(unittest.TestCase):
    def test_validate_rejects_bad_grids(self):
        with self.assertRaises(layout.LayoutError):
            layout.validate({**UP, "tiles": [["a", "b"]]})
        with self.assertRaises(layout.LayoutError):
            layout.validate({**UP, "tiles": [["a", "a"], ["c", "d"]]})
        with self.assertRaises(layout.LayoutError):
            layout.validate({**UP, "tiles": [["a", "b"], ["c", "zz"]]})

    def test_config_declares_output_and_overlay_over_its_tile(self):
        text = layout.sway_config(UP)
        assert "output HEADLESS-1 mode 1920x1080" in text
        assert 'for_window [app_id="^game$"]' in text
        assert "floating enable, border none, move absolute position 0 0" in text

    def test_overlay_of_bottom_right_tile_is_offset(self):
        up = {**UP, "roles": {**UP["roles"], "d": {"overlay_app_id": "x"}}}
        assert "position 960 540" in layout.sway_config(up)

    def test_tree_is_built_in_fixed_order(self):
        ipc, spawned = FakeIpc(), []
        layout.build_tree(ipc, UP, spawned.append, lambda wanted: spawned.append("wait " + wanted))
        assert spawned == [
            "a",
            "wait tile-a",
            "b",
            "wait tile-b",
            "c",
            "wait tile-c",
            "d",
            "wait tile-d",
        ]
        assert ipc.commands == [
            '[app_id="^tile\\-a$"] focus; splitv',
            '[app_id="^tile\\-b$"] focus; splitv',
        ]


class OverlayTests(unittest.TestCase):
    def test_window_off_its_tile_gets_one_fix_and_a_placed_one_none(self):
        def tree(rect):
            return {"nodes": [{"app_id": "game", "id": 9, "rect": rect, "nodes": []}], "floating_nodes": []}

        off = {"x": 5, "y": 5, "width": 1280, "height": 960}
        assert layout.overlay_fixes(tree(off), UP) == ["[con_id=9] resize set 960 540, move absolute position 0 0"]
        assert layout.overlay_fixes(tree({"x": 0, "y": 0, "width": 960, "height": 540}), UP) == []


class SuperviseTests(unittest.TestCase):
    def test_exited_unit_is_restarted_after_delay(self):
        group = Group()
        unit = Unit("quick", ["/bin/sh", "-c", "exit 0"], {}, 0.05)
        group.start(unit)
        try:
            while unit.starts < 3:
                group.step(max_wait=2)
        finally:
            group.stop()
        assert unit.starts >= 3

    def test_watched_fd_runs_handler_in_the_loop(self):
        import os

        group, seen = Group(), []
        read, write = os.pipe()
        group.watch(read, lambda: seen.append(os.read(read, 8)))
        os.write(write, b"x")
        group.step(max_wait=2)
        assert seen == [b"x"]

    def test_fatal_unit_raises(self):
        group = Group()
        group.start(Unit("sway", ["/bin/sh", "-c", "exit 7"], {}, None))
        try:
            with self.assertRaises(FatalUnitExit) as caught:
                group.step(max_wait=5)
        finally:
            group.stop()
        assert caught.exception.code == 7

    def test_hold_loop_reports_exit_and_restarts_without_clearing(self):
        out, codes, sleeps = io.StringIO(), iter([3, 0]), []
        hold_loop(
            ["role"],
            4,
            title="oracle",
            run=lambda argv: SimpleNamespace(returncode=next(codes)),
            sleep=sleeps.append,
            out=out,
            max_runs=2,
        )
        assert sleeps == [4, 4]
        assert "oracle exited rc=3; restarting in 4s" in out.getvalue()
        assert "\x1b[2J" not in out.getvalue()


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit(repo, subject):
    (repo / "f").write_text(subject)
    git(repo, "add", "f")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", subject)


class RefSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        for subject in ("one", "two", "three", "four"):
            commit(self.repo, subject)
        self.events = []
        self.profile = SimpleNamespace(resolve_path=lambda value: self.root / value)
        self.config = {"repos": [{"name": "r", "path": "repo"}], "state_path": "seen.json", "seed_commits": 2}

    def tearDown(self):
        self.tmp.cleanup()

    def source(self):
        return RefSource(
            self.profile, self.config, lambda _p, actor, status, message, **kw: self.events.append(message)
        )

    def test_first_scan_seeds_latest_commits_then_only_new_ones(self):
        source = self.source()
        assert source.scan() == 2
        assert [m.split(" ", 2)[2] for m in self.events] == ["three", "four"]
        assert source.scan() == 0
        commit(self.repo, "five")
        commit(self.repo, "six")
        assert source.scan() == 2
        assert [m.split(" ", 2)[2] for m in self.events[2:]] == ["five", "six"]
        assert self.events[-1].startswith("r ")

    def test_seen_state_survives_restart(self):
        self.source().scan()
        self.events.clear()
        assert self.source().scan() == 0

    def test_new_commit_wakes_serve_via_inotify(self):
        source = self.source()
        source.scan()
        self.events.clear()
        worker = threading.Thread(target=source.serve, kwargs={"iterations": 1}, daemon=True)
        worker.start()
        commit(self.repo, "late")
        worker.join(5)
        assert not worker.is_alive()
        assert any(m.endswith("late") for m in self.events)


class WaitTests(unittest.TestCase):
    def test_wait_for_path_wakes_on_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sock"
            threading.Timer(0.1, target.touch).start()
            assert wait_for_path(target, 5)
            assert not wait_for_path(Path(tmp) / "never", 0.1)


if __name__ == "__main__":
    unittest.main()
