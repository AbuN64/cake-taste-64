# Oracle runtime contracts

The host consumes original ROM code, never the recompiler analysis image.
Input mappings are build facts in the host profile `input` section. Run the
admitted warm CMake build after editing them. `input_profile.py` validates the
shape and generates both bindings and the single controls line; the host checks
SDL names before starting runtime threads, prints that line once, and writes it
to `N64R_CONFIG/controls.txt`.

Keyboard snapshots are taken on the SDL window thread. Guest callbacks consume
those snapshots under a mutex. `minimum_press_ms` preserves quick presses across
the game's multiple polls (80 ms in this oracle profile); held keys still use
SDL state. Gamepad hotplug, stick axes, axis-to-button thresholds and buttons
are profile data. Controller 1 includes keyboard input; Controller Pak is absent.

Audio opens SDL's default stereo S16 output; no dummy driver is selected by the
host. The pinned runtime's always-idle AI status is wrapped with a two-buffer
FIFO based on SDL consumption. A full FIFO rejects a third DMA with -1. Raw SI
returns controller/Pak status and delivers SI completion for both DMA directions.
`osMapTLB` accepts only mappings equivalent to the declared profile map; this is
not a dynamic TLB implementation.

## Wayland keyboard dependency

Use libxkbcommon **1.8.1** for this rig's virtual keyboard input. System1.7.0
crashed in `xkb_keymap_key_get_syms_by_level` with both SDL2.30.9 and2.32.4.
The 1.8.1 release fixes a negative-layout-index segfault. The private1.8.1 build
allows the same public `taste stream send` operation to complete without a crash.
No system package was changed; the oracle uses its bundled SDL2.30.9 with the
private XKB library before it in `LD_LIBRARY_PATH`.

- Source: https://github.com/xkbcommon/libxkbcommon/archive/refs/tags/xkbcommon-1.8.1.tar.gz
- SHA256: `c65c668810db305c4454ba26a10b6d84a96b5469719fe3c729e1c6542b8d0d87`
- Upstream explanation: https://github.com/xkbcommon/libxkbcommon/blob/xkbcommon-1.8.1/NEWS.md
- Private source/build/install: `$STATE/oracle/tools/xkb/`.
- Meson: libdir=lib; disable x11, wayland tools, tools, docs, xkbregistry,
  bash-completion; x-locale-root=/usr/share/X11/locale;
  xkb-config-root=/usr/share/X11/xkb.
- Build only `libxkbcommon.so.0.8.1` using admitted jobs, then
  `meson install --no-rebuild`. Private bison/m4 packages supplied parser tools.

## Scenario evidence

`taste --profile <repro-profile> repro scenario <name>` reads named steps from
`repro.scenario.facts` (`scenarios` in the host profile). Each step has absolute
seconds `at` plus `keys` or a named `checkpoint`. Transport commands, role,
PID/log paths and capture environment are data; subprocesses receive argument
arrays without a shell. A pidfd interrupts timed waits immediately on exit.

Each run freezes the request, JSONL input/checkpoint trace, host log and PNGs.
Checkpoint narration invokes the configured public `taste logs append` command.
A completed command means delivery/capture succeeded; labels alone do not prove
a game state. Inspect captured images before accepting front-end or gameplay.
