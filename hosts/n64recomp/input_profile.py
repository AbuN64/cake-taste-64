"""Compile explicit controller bindings and their displayed help from profile data."""

import json
import sys
from pathlib import Path

BUTTONS = {
    "A": 0x8000,
    "B": 0x4000,
    "Z": 0x2000,
    "START": 0x1000,
    "D_UP": 0x800,
    "D_DOWN": 0x400,
    "D_LEFT": 0x200,
    "D_RIGHT": 0x100,
    "L": 0x20,
    "R": 0x10,
    "C_UP": 8,
    "C_DOWN": 4,
    "C_LEFT": 2,
    "C_RIGHT": 1,
}


def render(facts):
    config = facts["input"]
    minimum_press = config["minimum_press_ms"]
    if not isinstance(minimum_press, int) or not 0 <= minimum_press <= 250:
        raise ValueError("minimum_press_ms must be an integer in 0..250")
    keyboard = config["keyboard_buttons"]
    stick = config["keyboard_stick"]
    gamepad = config["gamepad_buttons"]
    axes = config["gamepad_stick"]
    axis_buttons = config["gamepad_axis_buttons"]

    def name(value):
        if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
            raise ValueError("input binding must be a nonempty SDL name")
        return json.dumps(value)

    def rows(bindings):
        result = []
        for button, keys in bindings.items():
            if button not in BUTTONS or not isinstance(keys, list) or not keys:
                raise ValueError("input binding must name an N64 button and SDL keys")
            for key in keys:
                result.append(f"{{{name(key)}, {BUTTONS[button]}}}")
        return ",".join(result)

    if set(keyboard) != set(BUTTONS):
        raise ValueError("keyboard must bind every N64 button")
    keyboard_rows, gamepad_rows = rows(keyboard), rows(gamepad)
    if not gamepad_rows:
        raise ValueError("gamepad button bindings cannot be empty")
    stick_rows = ",".join(name(stick[k]) for k in ("left", "right", "up", "down"))
    if not isinstance(axes["invert_y"], bool):
        raise ValueError("gamepad invert_y must be boolean")
    axis_rows = []
    for binding in axis_buttons:
        if binding["button"] not in BUTTONS or binding["direction"] not in (-1, 1):
            raise ValueError("invalid gamepad axis button")
        threshold = binding["threshold"]
        if not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
            raise ValueError("axis button threshold must be in (0,1]")
        axis_rows.append(
            f"{{{name(binding['axis'])},{BUTTONS[binding['button']]},{binding['direction']},{float(threshold)}f}}"
        )
    if not axis_rows:
        raise ValueError("gamepad axis button bindings cannot be empty")

    def label(button):
        return "/".join({"Return": "Enter", "Left Shift": "Shift"}.get(k, k) for k in keyboard[button])

    directions = [label(k) for k in ("D_UP", "D_DOWN", "D_LEFT", "D_RIGHT")]
    dpad = "arrows" if directions == ["Up", "Down", "Left", "Right"] else "/".join(directions)
    analog = "".join(stick[k] for k in ("up", "left", "down", "right"))
    ckeys = "".join(label(k) for k in ("C_UP", "C_LEFT", "C_DOWN", "C_RIGHT"))
    help_line = (
        f"Keys: {dpad} D-pad · {analog} stick · {label('START')} Start · "
        f"{label('A')} A · {label('B')} B · {label('Z')} Z · "
        f"{label('L')}/{label('R')} L/R · {ckeys} C"
    )
    return (
        f"#pragma once\n#include <cstdint>\n#define HOST_INPUT_TAP_MS {minimum_press}\n"
        "struct HostButtonBinding {const char *name; uint16_t button;};\n"
        "struct HostAxisBinding {const char *name; uint16_t button; int direction; float threshold;};\n"
        f"static const HostButtonBinding host_keyboard_bindings[] = {{{keyboard_rows}}};\n"
        f"static const HostButtonBinding host_gamepad_bindings[] = {{{gamepad_rows}}};\n"
        f"static const HostAxisBinding host_axis_bindings[] = {{{','.join(axis_rows)}}};\n"
        f"static const char *host_stick_keys[] = {{{stick_rows}}};\n"
        f"static const char *host_stick_axes[] = {{{name(axes['x'])},{name(axes['y'])}}};\n"
        f"static const bool host_invert_y = {str(axes['invert_y']).lower()};\n"
        f"static const char host_controls[] = {json.dumps(help_line, ensure_ascii=False)};\n"
    )


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(render(json.loads(Path(sys.argv[1]).read_text())))
