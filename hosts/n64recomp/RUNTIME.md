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

## Graphics input lifetime

N64ModernRuntime pin `cdf5abbd5026fef5c364c676e4667c45e42b6863` requires
`patches/n64modernruntime-gfx-completion.diff`: send SP completion only after
`send_dl` has consumed guest display-list memory. The original game can recycle
that input after SP completion. Before this patch, RT64 eventually parsed boot
memory indefinitely even though the later RDRAM capture contained a finite,
valid 6474-command display list. After deferral, graphics continued beyond that
point and exposed an independent missing original function at0x802ACBCC.
The runtime pin is unchanged; the exact patch is mirrored under oracle/tools/patches.

Sourced local computed branches
-------------------------------
`recomp.local_indirect_jumps[version]` lists `function`, `pc`, `targets`, and
`source` facts for local branches the upstream analyser cannot recognize.
Addresses identify original instructions. Generation validates every target
inside its owning function, checks the original `jr` and its `nop` delay slot,
and emits upstream function hooks with local labels. Unknown runtime targets
fail through the existing switch diagnostic. Effectful delay slots are rejected.

## Runtime state ownership and queue diagnosis

Map both halves of a controller query to the same runtime: replacing
osContStartQuery while executing the original osContGetQuery reads stale
original PIF memory. Thread identity must also come from the runtime that owns
thread scheduling. Original osGetThreadId(0) reads the original kernel's
current-thread global, which a host scheduler does not maintain. Address/name
mappings and original-instruction sources belong in the project profile.

For a queue stall, set N64R_QUEUE_TRACE to a JSONL output path and
N64R_QUEUE_ADDRESS to an aligned guest queue address. The host records entry
and return around queue receive/send/create and controller query/read operations,
with monotonic microseconds, guest thread, arguments, result and the six queue
words. The queue address filters queue-taking calls; query getters are included
to connect completion to result consumption. A blocked receive has an entry
without a return. The trace reads guest memory and forwards every call unchanged;
it does not synthesize completion or alter scheduling. Unset the trace path for
normal runs. Invalid addresses or an unwritable trace destination fail explicitly.

## Raw controller transactions

Keep the original controller SDK family together when using the raw SI adapter.
Its request buffer, last-command cache, accessory records and access queue are
shared state; replacing individual members with host shortcuts loses that state
and can remove the blocking transactions on which callers depend. Map the raw
SI DMA boundary, not a mixture of original and host controller operations.
The adapter stages each 64-byte write and executes the saved command on every
read, so a repeated read samples fresh input even without a new write. Both
directions deliver exactly one SI completion. Absent devices and accessories
are reported in the PIF response for the original SDK to interpret.

The old SDK accessory packet includes a trailing alignment byte before END.
Ignore that byte when followed immediately by 0xFE. Captured motor-probe
packet: dummy FF, tx35/rx1, command03, 32 data bytes, CRC, padding88, END.
Independent facts-only comparison: Mupen64Plus src/device/pif/pif.c,
setup_channels_format lines204-212, tolerates a byte immediately before END:
https://github.com/mupen64plus/mupen64plus-core/blob/master/src/device/pif/pif.c#L204
No reference implementation was copied. Fatal SI diagnostics print the bounded
64-byte request and exit directly, without worker-destructor SIGABRT.

SI admits one transfer, reports busy until completion, and rejects overlapping
DMA with -1. Its worker waits on a condition variable and then performs the DMA
and emits one SI event. Completion includes PIF processing time, not just the
64-byte copy. The timing model uses 6000 CPU cycles for writes and 24000 plus
30000 per active Joybus channel for reads (93.75 MHz CPU clock). This is a
sourced hardware approximation, not a cycle-accurate claim. Facts-only sources:
https://github.com/gopher64/gopher64/blob/main/src/device/si.rs#L59
https://github.com/gopher64/gopher64/blob/main/src/device/pif.rs#L89
The write timing cites https://github.com/rasky/n64-systembench hardware tests.
The 0x900-tick legacy emulator approximation did not resolve the observed stall.
No reference code was copied. SDK restoration alone was also insufficient.

A controller with no Pak still responds to accessory commands. Return an
inverted data CRC, not the no-controller response flag; this lets the original
SDK perform its follow-up status query. CRC is the polynomial remainder for
32 data bytes and eight trailing zero bits under polynomial0x185. Facts-only
cross-check: gopher64 src/device/controller.rs:92-141 (absent accessory CRC
inversion and polynomial), no source code copied.

The source emulator stores CPU cycles internally and shifts that counter right
once when exposing the COP0 Count register (gopher64 src/device/cop0.rs:258-259).
Events use the unshifted counter (src/device/events.rs:27-31); CPU rate is
93750000 (src/device/cpu.rs:222-223). Therefore write duration is64us and a
one-controller read576us. The previous128us/1152us conversion doubled delays.
Those are source timing facts only; no implementation was copied.

A controller with an accessory port but no Pak reports identify status2, not0.
Status0 means accessories unsupported; status1 means present. The same identify
response applies to both status00 and resetFF. Protocol facts: libdragon
include/joybus.h:208-231 (JOYBUS_IDENTIFY_STATUS_ACCESSORY_*), corroborated by
gopher64 src/device/controller.rs:20-21,45-55. URLs:
https://github.com/DragonMinded/libdragon/blob/trunk/include/joybus.h#L208
https://github.com/gopher64/gopher64/blob/main/src/device/controller.rs#L20
The earlier doubled-duration correction did not itself resolve the SI stall.
