# RT64 Acclaim registration

Pin: `23cab603c4f9f4a8b369b38e036f1aa484603878`.
Apply `rt64-acclaim-f3dex2-registration.diff` with `git apply` in that pinned
checkout before building the host. The pin stays fixed; the diff is also kept
in `$STATE/oracle/tools/patches/`. Do not apply it to a different revision.

The patch changes only the existing Acclaim F3DEX2 registration. Existing text
hash `07287E3B3682EFBB` and data hash `D62BDC3A200CBB35` remain unchanged.
The NoN flag remains set. This does not establish support for extended Acclaim
light records or all graphics paths.

Facts-only dispatch comparison (no reference implementation copied):
- GLideN64 `src/uCodes/F3DEX2ACCLAIM.cpp:42-75` and
  `src/uCodes/F3DEX2.cpp:164-199`: common command numbers; MOVEMEM is specialized.
- Acclaim file lines 24-36: light offsets through 72 use the common records;
  later records use a different layout. Viewport and matrix commands agree.
- Local GPL reference `pc_runtime/src/xr64_fast3d/n64_microcode_registry.cpp:123`
  identifies this banner as F3DEX2 ABI. Its
  `n64_raw_fast3d_renderer.cpp:497` cautions about extended light records.

First captured task: ucode `0x800DCD10`, data `0x800E0050`, display list
`0x806E9000`, 2464 bytes. The offline walk executes 296 commands, including
four nested display-list calls; MOVEMEM light offsets are 0, 24, 48, 72.
No Acclaim-only light record is encountered. Trace command:

```
python hosts/n64recomp/tools/trace_f3dex2.py first-rdram.bin \
  --start 0x806e9000 --size 2464 > first-dl.json
```

The dump contains original game data and belongs in private oracle artifacts,
never in the source repository. The trace rejects loops, out-of-range lists,
conditional branches, and ucode changes that it cannot resolve statically.

Sources: https://github.com/gonetz/GLideN64/blob/master/src/uCodes/F3DEX2ACCLAIM.cpp
and https://github.com/gonetz/GLideN64/blob/master/src/uCodes/F3DEX2.cpp .

# Runtime thread queue removal

Pin: `cdf5abbd5026fef5c364c676e4667c45e42b6863`.
Apply `n64modernruntime-thread-queue-remove.diff` in that runtime checkout.
Removal must advance the pointer to the current link, including links inside
thread records. The previous loop repeatedly inspected the queue head, so
reprioritizing a non-head thread could reinsert a still-linked node and corrupt
the ready queue. With entry preemption and paced execution this stranded main
before its first graphics task. The patch is generic and changes no guest code.

`tests/test_runtime_thread_queue.py` applies this patch to the tiny pinned source
fixture and checks ready and blocked queues, including repeated reprioritization.
