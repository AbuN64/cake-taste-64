"""`taste stream up`: headless compositor, 2x2 role tiles, VNC and browser endpoint, all from the profile."""

from __future__ import annotations

import os
import select
import signal
import sys
from pathlib import Path
from typing import Any

from cake_taste.panes.layout import SwayIPC, _receive

from . import layout
from .inotify import wait_for_path
from .supervise import FatalUnitExit, Group, Unit, hold_loop


def endpoint_url(up: dict[str, Any]) -> str:
    return f"http://{up['public_host']}:{up['http_port']}/vnc.html?autoconnect=1&resize=scale&view_only=1"


def substitutions(profile: Any, up: dict[str, Any], run_dir: Path, display: str = "") -> dict[str, str]:
    return {
        "python": sys.executable,
        "profile": str(profile.source_file),
        "run_dir": str(run_dir),
        "wayland_display": display,
        "output_name": str(up["output"]["name"]),
        "vnc_port": str(up["vnc_port"]),
        "http_port": str(up["http_port"]),
        "web_root": str(up["web_root"]),
    }


def format_command(values: list[str], mapping: dict[str, str]) -> list[str]:
    if not values or not all(isinstance(item, str) for item in values):
        raise ValueError("configured command must be a nonempty string list")
    return [item.format_map(mapping) for item in values]


def format_env(values: dict[str, Any], mapping: dict[str, str]) -> dict[str, str]:
    return {str(key): str(value).format_map(mapping) for key, value in values.items()}


def role_command(up: dict[str, Any], role: str, mapping: dict[str, str]) -> list[str]:
    return format_command(up["roles"][role]["command"], mapping)


def wait_window(ipc: SwayIPC, wanted: str, timeout: float = 60.0) -> None:
    """Block on sway window events until a window with this app_id exists."""

    def present(node: dict[str, Any]) -> bool:
        if node.get("app_id") == wanted:
            return True
        return any(present(child) for child in node.get("nodes", []) + node.get("floating_nodes", []))

    connection = ipc.subscribe()
    try:
        while not present(ipc.tree()):
            if not select.select([connection], [], [], timeout)[0]:
                raise TimeoutError("window never appeared: " + wanted)
            _receive(connection)
    finally:
        connection.close()


def up(profile: Any) -> dict[str, Any]:
    config = profile.section("stream")["up"]
    layout.validate(config)
    run_dir = profile.resolve_path(config["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    run_dir.chmod(0o700)
    conf = run_dir / "sway.conf"
    ready = run_dir / "tiles-ready"
    ready.unlink(missing_ok=True)
    for stale in [*run_dir.glob("sway-ipc.*"), *run_dir.glob("wayland-*")]:
        stale.unlink(missing_ok=True)
    conf.write_text(layout.sway_config(config))
    base_env = {"XDG_RUNTIME_DIR": str(run_dir)}
    group = Group()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        sway_env = {**base_env, **format_env(config.get("compositor_env", {}), substitutions(profile, config, run_dir))}
        sway = Unit("compositor", [*config.get("compositor", ["sway"]), "-c", str(conf)], sway_env, None)
        group.start(sway)
        assert sway.process is not None
        ipc_path = run_dir / f"sway-ipc.{os.getuid()}.{sway.process.pid}.sock"
        if not wait_for_path(ipc_path, 30):
            raise RuntimeError("compositor IPC socket never appeared: " + str(ipc_path))
        sockets = sorted(p.name for p in run_dir.glob("wayland-*") if not p.name.endswith(".lock"))
        if not sockets:
            raise RuntimeError("compositor created no wayland socket")
        display = sockets[0]
        mapping = substitutions(profile, config, run_dir, display)
        child_env = {**base_env, "WAYLAND_DISPLAY": display, "SWAYSOCK": str(ipc_path)}
        for name, service in config.get("services", {}).items():
            group.start(
                Unit(
                    name,
                    format_command(service["command"], mapping),
                    {**child_env, **format_env(service.get("env", {}), mapping)},
                    float(service.get("restart_seconds", 3)),
                )
            )
        ipc = SwayIPC(ipc_path)
        prefix = config.get("app_id_prefix", "tile-")
        terminal = config.get("terminal", ["foot", "--app-id={app_id}"])

        def spawn(role: str) -> None:
            argv = format_command(terminal, {**mapping, "app_id": layout.app_id(role, prefix)})
            argv += [
                sys.executable,
                "-m",
                "cake_taste",
                "--profile",
                str(profile.source_file),
                "stream",
                "role",
                "--id",
                role,
            ]
            group.start(Unit("tile-" + role, argv, {**child_env, "STREAM_READY": str(ready)}, None))

        layout.build_tree(ipc, config, spawn, lambda wanted: wait_window(ipc, wanted))
        ready.touch()
        events = ipc.subscribe()

        def fix_overlays() -> None:
            change = _receive(events)[1]
            if isinstance(change, dict) and change.get("change") != "close":
                for command in layout.overlay_fixes(ipc.tree(), config):
                    ipc.command(command)

        group.watch(events.fileno(), fix_overlays)
        print(endpoint_url(config), flush=True)
        group.run_forever()
    except (KeyboardInterrupt, FatalUnitExit) as exc:
        group.stop()
        if isinstance(exc, FatalUnitExit):
            print(str(exc), file=sys.stderr, flush=True)
            raise SystemExit(1) from exc
    finally:
        group.stop()
    return {"stopped": True}


def run_role(profile: Any, role: str) -> dict[str, Any]:
    """Executed inside a tile terminal: run the role command forever, holding its last screen on exit."""
    config = profile.section("stream")["up"]
    if role not in config["roles"]:
        raise ValueError("unknown stream role: " + role)
    spec = config["roles"][role]
    gate = os.environ.get("STREAM_READY")
    if gate and not wait_for_path(Path(gate), 120):
        raise TimeoutError("tile tree never became ready")
    run_dir = profile.resolve_path(config["run_dir"])
    mapping = substitutions(profile, config, run_dir, os.environ.get("WAYLAND_DISPLAY", ""))
    os.environ.update(format_env(spec.get("env", {}), mapping))
    hold_loop(role_command(config, role, mapping), float(spec.get("restart_seconds", 5)), title=spec.get("title", role))
    return {"role": role}
