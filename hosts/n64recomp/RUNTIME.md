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
are profile data. Controller 1 includes keyboard input; the Controller Pak is absent unless `N64R_CPAK` names a card.

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
allows the same public `decomp stream send` operation to complete without a crash.
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

## Virtual guest time

The oracle's clock is virtual (`patches/n64modernruntime-virtual-time.diff`). Count, VI
retraces, `osSetTimer` timers, SI completion and RSP/graphics task completion are events on one
virtual clock in nanoseconds. The clock advances only when a guest thread's function-entry
tick (`cpu_scheduler.cpp`) charges the native thread CPU time it just used, times
`HOST_NATIVE_CPU_SCALE`; due events run on that guest thread in time order. Wall time and host
load do not move it, and nothing sleeps to pace the guest.

An RSP task completes a fixed guest duration after submission (graphics 8 ms, audio 2 ms,
hardware-like constants in `events.cpp`), and only once the host has finished the work: if
the host renderer is slower, the guest thread waits in wall time at that event while guest
time stands still. So a slow renderer or a loaded host changes how long a run takes, never the
guest-visible order or spacing of events. VI is 60 Hz of guest time from retrace 1 at time 0.
The CPU tick is the only source of guest time: a host-side call that blocks the running guest
thread with no other guest thread runnable would stall the clock (the original idle thread
spins in guest code, so this does not occur).

## Graphics input lifetime

N64ModernRuntime pin `cdf5abbd5026fef5c364c676e4667c45e42b6863` requires
`patches/n64modernruntime-virtual-time.diff`: send SP completion only after
`send_dl` has consumed guest display-list memory (and see Virtual guest time). The original game can recycle
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


## CPU service boundary and timing model

Every generated function entry services pending external interrupts and lets a
higher-priority guest thread run. Nested `__osDisableInt` / `__osRestoreInt`
regions defer this added service until interrupts are restored. Runtime SDK
message operations retain their existing blocking semantics.

The required profile fact `cpu.native_time_scale` defines a diagnostic ratio of
guest CPU duration to native thread CPU time. Count/VI/timers run on the virtual clock
that this charge advances (see Virtual guest time); nothing paces the guest in wall time. The
host charges thread CPU time, never wall time spent blocked or running another guest thread,
carries the sub-nanosecond remainder, and excludes device events and scheduler work from the
next charge. CPU-clock failure is fatal.

The scale is an explicit approximation, not an instruction/cycle-accurate model
or a hardware calibration. Native compiler/CPU and function granularity remain
limitations for timing-sensitive conclusions. No game-specific branch, address,
state repair, or guest source mutation is involved. The old experimental
N64R_CPU_SCALE and N64R_CPU_NOPREEMPT switches are not read.

Apply the maintained runtime thread-queue removal patch before building. The
idle-until-interrupt experiment is not required and is not part of this host.


Canonical acceptance: run `20261018T152220584093Z` used profile scale2.5,
no function/queue tracing, and the original guest. Graphics tasks advanced
2308 at90s -> 2908 at110s -> 3800 at140s; all13 checkpoint images differed.
Viewed90s/102s captures show first-person gameplay, and140s shows the ordinary
mission-failed results after two deaths. The baseline froze at character
selection with1767 tasks. Queue removal alone and entry service alone did not
remove that freeze; both the queue correction and paced guest CPU service are
needed by this measured route. The full suite passed143 tests.

## Measured CPU budget model

The host reports aggregate guest function entries, charged native thread CPU
time, VI callback count and unwrapped Count ticks once per 60 callbacks. These
are workload observations, not original instructions or hardware cycles. Native
time excludes the pacing/scheduler section; SDK execution and clock-read cost
can still contribute. Concurrent snapshots can straddle a guest publication.

`cpu_calibrate.py <immutable-host-log> <oracle-facts> <source-run>` selects the
complete 60-VI window with most native CPU work per VI. The profile stores that
window, the source run/hash, explicit Count/VI rates, and the full-budget model
assumption. `cpu_profile.py` derives the scale as nominal Count ticks per entry
divided by native nanoseconds per entry, converted using Count Hz. Algebraically
this is `(VI / VI_Hz) / native_CPU_seconds`. Count observations independently
show the host peripheral cadence; Count Hz cancels in the scale calculation.

This is a **frame-budget model**, not a measurement of R4300 cycle costs or
original-console utilization. Choosing the busiest sampled window assumes its
work may consume the entire CPU budget and ignores host scheduling overhead.
An original-hardware or cycle-model instruction/entry trace is still required
for hardware calibration. Validate the derived model with an untraced canonical
scenario and viewed gameplay before accepting its operational value. The retired
standalone numeric scale is rejected. The current pinned runtime emits only
60-Hz VI callbacks; the profile generator rejects PAL timing facts rather than silently
claiming 50-Hz support.

## Mission console and probe context

`logs console --kinds RUN-FAIL,PROBE,MATCH,HANDOFF,LAND` filters the
append-only event stream (schema 1 lines with PORT-FAIL, DECOMP and COMMIT are
read as RUN-FAIL, MATCH and LAND). `--host-log <path>` reads warnings/errors plus at most one heartbeat every
60 seconds (screen frames, graphics tasks and VI). Per-VI cadence remains only
in the original host log. The role script only invokes this public command.

`repro probe` emits PROBE start/result/failure events. Supply `--question`,
`--owner-address`, `--decomp-path`, `--match-state`, and optionally
`--decomp-finding`, or the corresponding snake_case keys under request `story`.
Match states are `matched-c`, `stand-in`, `unmatched`, `missing`, `unknown`.
The call-probe owner address comes from the actual generated symbol table.
Available assembly source paths are resolved when no source is supplied.
Neither a C body nor a matching expected value proves the C is matched: absent
provenance stays `unknown`. A MATCH finding can be supplied explicitly; a
successful checked original probe also characterizes a declared unmatched,
missing or stand-in function. Findings and checks retain their evidence/run link.
Configure the profile logs.events_path to the shared mission stream when that
is its intended destination; the logger does not guess a global output path.

## Guest resource demand paging

KUSEG accesses now consult mutable paired TLB entries. A missing page publishes
OSThread Cause/BadVAddr and the profile-qualified faulted-thread global, sends
OS_EVENT_FAULT without a scheduling gap, and suspends the current host thread.
The original guest pager queues its ROM transfer, maps the completed page, and
starts that thread. The suspended C access then retries translation. A resume
without a mapping, missing fault receiver, or full fault queue fails immediately.
No sound id, resource offset, definition count, or voice state is injected.

Boot mappings explicitly declare entry indices/page sizes. UnmapTLBAll clears
0..30 and retains wired entry31; replacement/unmapping invalidate both halves.
The current profile qualifies the guest MapTLB sixth argument as EntryLo flags
(global, valid, writable), not the SDK ASID parameter. Other flags are rejected.
Original facts: asm/us-rev1/func_802AD370_de.s:14-58;
osUnmapTLBAll.s:14-30; func_802BAD70_de.s:14-15;
func_8023C1F0_de.s:26-39. No reference implementation was copied.

Bounded call probes have static mappings and no guest pager: unmapped KUSEG
accesses now fail instead of silently supplying zero definitions. Complete boot
uses the original pager and replacement policy. KSEG0/KSEG1 and the preexisting
non-RDRAM/device-shadow boundary are unchanged. In particular, cold frontend
rendering writes to KSEG2 addressFFFFFFC0 before its scratch allocator has a
nonzero end pointer; this existing graphics issue is outside the KUSEG pager.
This host is a diagnostic instrument, not a complete CPU exception emulator.

Unaligned LWL/LWR/SWL/SWR and 64-bit variants use the same translator. Upstream
inline helpers capture MEM_* before this adapter redefines it; simply replacing
MEM_* leaves those accesses outside the map. The adapter translates each aligned
word/doubleword block and calls the original helper using a normalized KSEG0
window, preserving the upstream merge and sign-extension semantics. Sound voice
setup exposed this in original func_8025BB84_de at LWL/LWR (actual8025BC00/BC04).

Acceptance: public scenario20261018T172835586136Z ran original gameplay through
110s; viewed80s and110s captures show first-person play. Host at112s reported
4410112 queued samples,3210629 nonzero. The sole PulseAudio sink input was
n64host on m2_stream;48kHz stereo16-bit monitor capture measured RMS4096.93
(-18.06dBFS) at90..95s and5499.16 (-15.50dBFS) at95..100s. This proves PCM
production during gameplay, not audio fidelity or end-to-end browser playback.
54 host/recomp tests plus12 subtests pass, including paired-page eviction,
fault delivery/resumption and all eight unaligned helper adapters. Evidence,
failed diagnostic runs and full per-window metrics live in the run handoff.

## Golden recorder and scripted input
`golden.cpp` is a test instrument, off unless `N64R_GOLDEN_DIR` is set; with it
unset the host behaves as before. It knows nothing about any game.

- `N64R_GOLDEN_DIR`: output directory (must exist). Receives `events.jsonl`
  (checkpoint, key, gfx, acmd, rate, pi and quit events), `dl-<checkpoint>.bin`
  (first graphics-task display list at or after a checkpoint; `boot` is the
  first task), `acmd-all.bin` (every audio command list), `pcm-all.s16` (queued
  PCM, rate in the `rate` events) and `trace-<checkpoint>.tsv` (functions in
  first-entry order). An unusable directory exits with status 5.
- `N64R_SCRIPT`: input script, one line per step: `<vi> key <SDL key name>`,
  `<vi> checkpoint <name>` or `<vi> quit`. `<vi>` is the VI retrace count, so
  steps are paced by emulated video, not wall time. Needs `N64R_GOLDEN_DIR`.
- `N64R_SHOT_CMD`: shell template run at each checkpoint; `%s` is the PNG path
  (`<dir>/vi-<checkpoint>.png`). Needs `N64R_GOLDEN_DIR`.
- `N64R_TAS_PLAY`: a raw controller movie, one `BUTTONS X Y` line per guest
  controller poll (hex buttons), the same file a native port replays. It
  replaces channel 0 poll by poll, so both replays are keyed to the guest's own
  polls. `N64R_TAS_SHOT_EVERY` (N) checkpoints `poll-<n>` every N polls;
  `N64R_TAS_TRACE` (file) writes "poll vi" per controller poll, the same line the port's `N64C_TAS_TRACE` writes.
  `N64R_TAS_EXIT_AFTER` (N) quits N polls after the movie ends. The movie replays
  without `N64R_GOLDEN_DIR` too (then only the pictures below are written).
- `N64R_TAS_STATE`: a state spec (`tas_state.h`, the port's `N64C_TAS_STATE` twin). `N64R_TAS_STATE_HASH` (file)
  writes `<poll> <symbol>:<hash> ...` per controller poll; `N64R_TAS_STATE_AT` (P1,P2) with `N64R_TAS_STATE_DIR`
  (dir) writes `state-<poll>.bin` there. The words are read from the word-swizzled RDRAM (`TS_ORACLE`), so the two
  sides' traces compare: `decomp tas play --oracle` diffs the game state poll by poll.
- `N64R_PADS` (1..4, default 1): controllers the guest sees. A movie line then holds that many `BUTTONS X Y` triples
  (pad 1 first), and channel 0's read of a poll advances the movie for every channel; the port's `N64C_PADS` is the twin.

`N64R_PAK` is the Expansion Pak switch (the port's `--pak`), read by the host at every start: `on` or unset is the
8 MB the runtime reports; `off` makes the guest see the 4 MB console (`osMemSize` at 0x80000318 and `osGetMemSize`),
so the game takes its no-pak path. Anything else exits 2. A movie's `# pak` header line says which one it ran with.
`N64R_CPAK` is the Controller Pak (the port's `--cpak`): unset or empty is an empty slot (the game's no-card path); a path
is a 32 KiB card image (anything else exits 4) that the controller 1 slot answers with, read and written through the
Joybus accessory packets (address CRC, data CRC) and saved to that file block by block. A movie's `# cpak` header
line says which card it started with; the TAS tooling hands the oracle its own copy of the port's start card.

Every ROM DMA is a `pi` event: `rom` offset, `ram` physical destination, `len`, `thread`, and `req`,
the requesters, nearest first. Each level is the thread that sent the message the DMA thread last
received (`thread`) and the last functions it entered before that send (`fns`, oldest first). The next
level is the same for the message that sender had itself received, so a loader thread's own callers
show up behind it. Empty `req` means no send was paired.

`N64R_TRACE` lines now carry the VI retrace count in the frame column.
A `quit` step flushes every file and ends the process with status 0 without runtime
teardown, which races the running guest threads.
Recordings under the default CPU-time clock are not bit-reproducible after boot (guest time follows
measured CPU work), so compare structure, not bytes; `N64R_CLOCK` `entries` (below) makes them reproducible.

## Headless pictures and the deterministic clock (TAS oracle)
The TAS page compares the native port with this oracle poll by poll, so the oracle speaks the port's frame protocol
under `N64R_` names and runs with no display server (no X11, no Wayland compositor):
- Headless: with SDL's `offscreen` or `dummy` video driver (`SDL_VIDEODRIVER`) the host creates no window and RT64
  presents to a `VK_EXT_headless_surface` swap chain (`patches/rt64-headless-surface.diff`; headless WSI has no
  present wait, so presents are not tagged or waited on), at the original resolution and rate. Mesa's RADV and
  lavapipe both provide the extension. `deps.sh DEST` checks out N64ModernRuntime and RT64 at `PINS` with every patch
  applied, for `-DN64R_RUNTIME_SRC` / `-DN64R_RT64_SRC`.
- `N64R_VIEW` (`view.cpp`): the picture on screen as a 320x240 24-bit BMP, replaced whole (written beside it, then
  renamed) at most `N64R_VIEW_FPS` (1..120, default 15) times a second of wall time and once more at a TAS exit, the
  same file the port's `N64C_VIEW` writes. `N64R_TAS_SHOT_DIR` with `N64R_TAS_SHOT_EVERY`: `poll-NNNNNN.ppm`
  (320x240 binary PPM, never replacing a file) at every Nth controller poll, as the port's `N64C_TAS_SHOT_DIR`; the
  guest thread waits for it, so the picture is that poll's at any host speed. Both work without `N64R_GOLDEN_DIR`.
- `N64R_AUDIO_OUT` (`audio_sdl.cpp`): the sound the game queues, appended as raw 16-bit stereo to that file, and
  `<file>.rate` holding the game's `osAiSetFrequency` rate (replaced whole); the port's `N64C_AUDIO_OUT` layout, so one
  reader plays either. Every block is written (no wall-time cap), at any host speed.
- The picture is the colour image the VI shows (`osViGetCurrentFramebuffer`) after every submitted graphics task has
  rendered: RT64's render target for that address converted to the RDP's own pixel format (no dither), or guest RDRAM
  when the CPU drew it, scaled bilinearly to 320x240 like the port's pictures. Width and format come from the VI
  registers, the height from the rows the game drew into that colour image (else the VI's estimate).
- `N64R_CLOCK`: `entries` makes guest time a fixed `HOST_GUEST_NS_PER_ENTRY` per tick (a function entry, a spin loop's
  1024th pass, an interrupt restore), derived from the same CPU calibration (`cpu_profile.py`), instead of measured
  thread CPU time; the AI then plays queued samples in guest time too. Guest timing no longer depends on the host,
  so the same movie gives the same pictures and the same poll/VI trace every run (`cpu` or unset: the CPU-time
  clock above). Measured on the rig (16 cores, RADV): `pak-continue-legal` 2591 polls / 3611 VIs in 2.85 s wall,
  two runs byte-identical in all 103 shots and the trace.

## Lockstep recorder
`lockstep.cpp` is a test instrument for comparing a native port's boot with this
oracle, off unless `N64R_LOCKSTEP_DIR` is set. The trace probe (`probe/trace.h`)
hands it each function entry (name, a0-a3, f12, f14, sp) and return (v0, v1, f0).
- `N64R_LOCKSTEP_DIR`: output directory (created). Receives `lockstep.jsonl`
  (`meta`, then `hit` at the first entry of each function with the 4 KiB pages
  whose hash changed since the previous hit, `call`/`ret` for its first calls,
  `watch`) and `pages.bin` (each distinct page once: u32 page, u32 0, u64 hash,
  4096 bytes). Pages are hashed and stored in canonical big-endian byte order
  (each RDRAM word byte-swapped back); the hash is FNV-1a over little-endian
  64-bit words.
- `N64R_LOCKSTEP_RAM`: RDRAM bytes hashed (default 0x800000).
- `N64R_LOCKSTEP_CALLS`: calls recorded per function (default 8).
- `N64R_LOCKSTEP_WATCH`: an RDRAM address; every change of that word seen at an
  entry or return is a `watch` line naming the function.
Returns pair with entries by name and stack pointer; frames left above a return
(tail calls) are dropped. The port's own tool does the comparison.

## Function-replay recorder
`replay.cpp` is a test instrument for `decomp behave --replay`, off unless `N64R_REPLAY_DIR` is set. It samples a few
real calls of every function the run reaches and writes `replay.jsonl`: per call the arguments (`a`, `f12`, `f14`,
8 `stack` words), every guest location the call and its callees read before writing (`in`: address, width 1/2/4/8,
value as the generated code saw it) and stored (`out`, last value), and the return (`ret`, `f0`). While a sampled call
runs, the accessors in `probe/addrmap.h` go through the recorder; otherwise they are the plain translation plus one
thread-local test. A call is written off (`skip`: `interleaved` another thread ran meanwhile, `frames` a retrace passed,
`non-ram`, `unaligned`, `mixed-width` one location read at two widths, `events`) when its memory was not its own.
- `N64R_REPLAY_DIR`: output directory (created).
- `N64R_REPLAY_CALLS`: kept samples per function (default 3).
- `N64R_REPLAY_GAP`: VI retraces between two samples of one function (default 25); samples also differ in a0-a3.
- `N64R_REPLAY_EVENTS`: accesses one call may make (default 200000).
- `N64R_REPLAY_RAM`: RDRAM bytes (default 0x800000).
The recorder only reads: guest time (`N64R_CLOCK=entries`) and the run are unchanged.

## Generation facts for a new game
- `recomp.symbol_names[version]` maps a vram to a runtime name. A value is the name, or
  `{"name", "source"}` so the evidence travels with the fact. Every name must have a `<name>_recomp`
  in the runtime or the host; the generator also lists them in `funcs/replaced_funcs.inl`, which the host
  registers so a stored function pointer (a device-manager table) reaches the replacement.
- `recomp.rsp_audio` takes either `segment` (a layout segment) or `rom` + `size` + `execution_vram` +
  `source` when the decomp does not delimit the microcode.
- Code that a decomp keeps as a data split is found by N64Recomp only as `static_<n>_<vram>`. A refused
  static is declared as a function whose size runs to the next function, function symbol or split
  (`statics.tsv` records name, vram, size and the boundary used) and is then ignored by name.
- Data that decodes as code can make N64Recomp emit C that does not compile (a store to the zero register,
  a branch to a label of another function). The generator refuses those functions like any other.
- Backward branches carry `TRACE_LOOP()` (trace mode): a guest spin-wait that stays inside one function
  would never reach a function entry, so interrupts would never be serviced.
- `funcs/recomp_decls.h` declares every `<name>_recomp` the generated code calls.
