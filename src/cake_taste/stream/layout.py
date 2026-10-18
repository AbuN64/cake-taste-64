"""Headless compositor config and a fixed 2x2 tiling tree built from profile tiles."""

from __future__ import annotations

import re
from typing import Any


class LayoutError(ValueError):
    pass


def validate(up: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (column_one, column_two) role ids: tiles are [[TL, TR], [BL, BR]]."""
    rows = up["tiles"]
    if len(rows) != 2 or any(len(row) != 2 for row in rows):
        raise LayoutError("tiles must be two rows of two role ids")
    flat = [rows[0][0], rows[1][0], rows[0][1], rows[1][1]]
    if len(set(flat)) != 4:
        raise LayoutError("four tile roles must be distinct")
    missing = [role for role in flat if role not in up["roles"]]
    if missing:
        raise LayoutError("tiles name undefined roles: " + ", ".join(missing))
    return [rows[0][0], rows[1][0]], [rows[0][1], rows[1][1]]


def app_id(role: str, prefix: str = "tile-") -> str:
    return prefix + role


def exact(value: str) -> str:
    """Quoted, anchored regular-expression criterion matching exactly this app_id."""
    return '"^' + re.escape(value) + '$"'


def sway_config(up: dict[str, Any]) -> str:
    """Sway config: one fixed-size virtual output, no decoration, overlay rules for role windows.

    Criteria are regular expressions, so each is anchored: a role named like its tile's app_id must not match it.
    An overlay is a window a role opens itself (a game); it floats exactly over its role's tile, so the tile's
    terminal shows through whenever the window is absent.
    """
    out = up["output"]
    width, height = int(out["width"]), int(out["height"])
    lines = [
        "default_border none",
        "default_floating_border none",
        "titlebar_border_thickness 0",
        "focus_follows_mouse no",
        f"output {out['name']} mode {width}x{height} position 0 0 bg #1e1e2e solid_color",
    ]
    for _role, overlay, x, y, _w, _h in overlays(up):
        lines.append(
            f"for_window [app_id={exact(overlay)}] floating enable, border none, move absolute position {x} {y}"
        )
    return "\n".join(lines) + "\n"


def overlays(up: dict[str, Any]) -> list[tuple[str, str, int, int, int, int]]:
    """(role, app_id, x, y, width, height) of every role window that must sit exactly over its tile."""
    tile_w, tile_h = int(up["output"]["width"]) // 2, int(up["output"]["height"]) // 2
    found = []
    for col, ids in enumerate(validate(up)):
        for row, role in enumerate(ids):
            overlay = up["roles"][role].get("overlay_app_id")
            if overlay:
                found.append((role, overlay, col * tile_w, row * tile_h, tile_w, tile_h))
    return found


def overlay_fixes(tree: dict[str, Any], up: dict[str, Any]) -> list[str]:
    """Commands that move a role window onto its tile; a client may resize itself after the map-time rule."""
    wanted = {app: (x, y, w, h) for _role, app, x, y, w, h in overlays(up)}
    commands: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        app = node.get("app_id")
        if app in wanted:
            x, y, w, h = wanted[app]
            rect = node["rect"]
            if (rect["x"], rect["y"], rect["width"], rect["height"]) != (x, y, w, h):
                commands.append(f"[con_id={node['id']}] resize set {w} {h}, move absolute position {x} {y}")
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            walk(child)

    walk(tree)
    return commands


def build_tree(ipc: Any, up: dict[str, Any], spawn: Any, wait_window: Any) -> None:
    """Create the 2x2 tree deterministically: TL, TR beside it, BL under TL, BR under TR.

    spawn(role) starts the role's terminal; wait_window(app_id) blocks until its window exists.
    """
    left, right = validate(up)
    top_left, bottom_left = left
    top_right, bottom_right = right
    prefix = up.get("app_id_prefix", "tile-")
    for role in (top_left, top_right):
        spawn(role)
        wait_window(app_id(role, prefix))
    ipc.command(f"[app_id={exact(app_id(top_left, prefix))}] focus; splitv")
    spawn(bottom_left)
    wait_window(app_id(bottom_left, prefix))
    ipc.command(f"[app_id={exact(app_id(top_right, prefix))}] focus; splitv")
    spawn(bottom_right)
    wait_window(app_id(bottom_right, prefix))
