"""Two oracle runs must store the same OSThread::context in guest memory.

The pointer is what made ENGINE symbols 1455, 1512, 1518, 1604, 1605 and 1644 differ between runs.
"""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "hosts/n64recomp/patches/n64modernruntime-thread-context-slab.diff"
THREADS = ROOT / "tests/fixtures/runtime_threads.cpp"
SLICE = json.loads((ROOT / "tests/fixtures/oracle_osthread_slice.json").read_text())
CONTEXT_OFFSET = 0x20  # OSThread::context, a host pointer in guest RDRAM
SLAB_BASE = 0x200000000000

PRELUDE = r"""
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <thread>
#include <sys/mman.h>
struct Semaphore { alignas(8) char state[48]; };  // stands in for moodycamel::LightweightSemaphore
struct UltraThreadContext { std::thread host_thread; Semaphore running; Semaphore initialized; };
"""

HARNESS = r"""
int main(int argc, char** argv) {
    void* shifted = std::malloc(std::strtoul(argv[1], nullptr, 10));  // each run starts with another heap layout
    for (int i = 0; i < 3; i++) std::printf("%p\n", static_cast<void*>(make_context()));
    return shifted == nullptr;
}
"""


def host_pointer(slice_hex: str) -> int:
    """The 64-bit host pointer at OSThread+0x20 from canonical bytes (RDRAM is word-swapped: ram[k ^ 3])."""
    raw = bytes.fromhex(slice_hex)[CONTEXT_OFFSET : CONTEXT_OFFSET + 8]
    ram = bytes(raw[k ^ 3] for k in range(4)) + bytes(raw[4 + (k ^ 3)] for k in range(4))
    return int.from_bytes(ram, "little")


def context_allocation(source: str) -> tuple[str, str]:
    """The (slab helper, expression) osCreateThread uses to allocate the context: what the source really does."""
    expression = re.search(r"UltraThreadContext\* context = (.+);", source).group(1)
    start = source.find("static constexpr uintptr_t context_slab_base")
    if start < 0:
        return "", expression
    end = source.index("\n}\n", source.index("static UltraThreadContext* new_thread_context")) + 3
    return source[start:end], expression


class ThreadContextTests(unittest.TestCase):
    def pointers(self, source: str) -> list[list[str]]:
        helper, expression = context_allocation(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "t.cpp").write_text(
                PRELUDE + helper + f"\nstatic UltraThreadContext* make_context() {{ return {expression}; }}\n" + HARNESS
            )
            subprocess.run(
                ["c++", "-std=c++20", "-O2", "-Wall", "-Werror", "-pthread", str(root / "t.cpp"), "-o",
                 str(root / "t")],
                check=True, capture_output=True,
            )
            runs = []
            for heap_shift in ("100", "300000"):
                result = subprocess.run([str(root / "t"), heap_shift], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                runs.append(result.stdout.split())
            return runs

    def patched(self) -> str:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "ultramodern/src/threads.cpp"
            target.parent.mkdir(parents=True)
            target.write_bytes(THREADS.read_bytes())
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "apply", str(PATCH)], check=True, capture_output=True)
            return target.read_text()

    def test_real_slice_differs_only_in_the_host_pointer(self):
        first, second = (bytes.fromhex(x) for x in SLICE["before"])
        differing = [k for k in range(len(first)) if first[k] != second[k]]
        self.assertTrue(differing)
        self.assertTrue(all(CONTEXT_OFFSET <= k < CONTEXT_OFFSET + 8 for k in differing), differing)
        for text in SLICE["before"]:
            self.assertGreater(host_pointer(text), 1 << 40)  # a heap or mmap address of the host process

    def test_unpatched_runtime_stores_a_different_pointer_per_run(self):
        first, second = self.pointers(THREADS.read_text())
        self.assertNotEqual(first, second)

    def test_patched_runtime_stores_the_same_pointer_every_run(self):
        first, second = self.pointers(self.patched())
        self.assertEqual(first, second)
        self.assertTrue(all(int(p, 16) >= SLAB_BASE and int(p, 16) < SLAB_BASE + (1 << 24) for p in first), first)
        self.assertEqual(len(set(first)), 3)

    def test_real_slice_after_the_fix_is_the_same_in_both_runs(self):
        if not SLICE["after"]:
            self.skipTest("no post-fix slice recorded")
        first, second = SLICE["after"]
        self.assertEqual(first, second)
        pointer = host_pointer(first)
        self.assertGreaterEqual(pointer, SLAB_BASE)
        self.assertLess(pointer, SLAB_BASE + (1 << 24))


if __name__ == "__main__":
    unittest.main()
