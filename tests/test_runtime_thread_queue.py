"""Exercise the pinned runtime queue implementation with the maintained patch."""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "hosts/n64recomp/patches/n64modernruntime-thread-queue-remove.diff"
FIXTURE = ROOT / "tests/fixtures/runtime_threadqueue.cpp"


class RuntimeThreadQueueTests(unittest.TestCase):
    def test_remove_and_reprioritize_without_losing_or_duplicating_threads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ultramodern/src/threadqueue.cpp"
            source.parent.mkdir(parents=True)
            source.write_bytes(FIXTURE.read_bytes())
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            guard = root / "guard.cmake"
            helper = ROOT / "hosts/n64recomp/require_runtime_patch.cmake"
            guard.write_text(
                f'include("{helper}")\n'
                f'n64host_require_runtime_patch("{root}" "{PATCH}")\n'
            )
            missing = subprocess.run(["cmake", "-P", str(guard)], capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("queue removal patch is absent", missing.stderr)
            subprocess.run(["git", "-C", str(root), "apply", str(PATCH)], check=True, capture_output=True)
            subprocess.run(["cmake", "-P", str(guard)], check=True, capture_output=True)
            header = root / "ultramodern/ultramodern.hpp"
            header.write_text(r'''
#pragma once
#include <cstdint>
#define PTR(T) int32_t
#define RDRAM_ARG uint8_t* rdram,
#define PASS_RDRAM rdram,
#define TO_PTR(T, A) reinterpret_cast<T*>(rdram + (uint32_t(A) & 0x1fffffff))
#define NULLPTR 0
#define debug_printf(...) ((void)0)
struct OSThread { int32_t next, priority, queue, pad; };
namespace ultramodern {
constexpr int32_t running_queue = -1;
void thread_queue_insert(uint8_t*, int32_t, int32_t);
int32_t thread_queue_pop(uint8_t*, int32_t);
bool thread_queue_remove(uint8_t*, int32_t, int32_t);
bool thread_queue_empty(uint8_t*, int32_t);
int32_t thread_queue_peek(uint8_t*, int32_t);
}
''')
            harness = root / "test.cpp"
            harness.write_text(r'''
#include <array>
#include <cassert>
#include "ultramodern/ultramodern.hpp"
using namespace ultramodern;
int main() {
 alignas(16) uint8_t ram[4096]{};
 auto node = [&](int32_t address) { return reinterpret_cast<OSThread*>(ram+address); };
 // Exercise both the host ready queue and a guest message queue's blocked list.
 for (int32_t queue : {-1, 64}) {
  for (int32_t removed : {256, 512, 768, 1024}) {
   node(256)->priority=30; node(512)->priority=20; node(768)->priority=10;
   for (int32_t address : {256, 512, 768}) thread_queue_insert(ram,queue,address);
   assert(thread_queue_remove(ram,queue,removed)==(removed!=1024));
   for (int32_t address : {256, 512, 768}) {
    if(address==removed) continue;
    assert(thread_queue_pop(ram,queue)==address);
   }
   assert(thread_queue_empty(ram,queue));
   assert(!thread_queue_remove(ram,queue,removed));
  }
  // A non-head worker changes priority while other runnable threads surround it.
  node(256)->priority=30; node(512)->priority=20; node(768)->priority=10;
  for (int32_t address : {256,512,768}) thread_queue_insert(ram,queue,address);
  for (int priority : {5,35,20,15}) {
   node(512)->priority=priority;
   assert(thread_queue_remove(ram,queue,512));
   thread_queue_insert(ram,queue,512);
  }
  assert(thread_queue_pop(ram,queue)==256);
  assert(thread_queue_pop(ram,queue)==512);
  assert(thread_queue_pop(ram,queue)==768);
  assert(thread_queue_empty(ram,queue));
 }
}
''')
            binary = root / "test"
            subprocess.run(
                ["c++", "-std=c++20", "-O2", "-Wall", "-Werror", "-I", str(root),
                 str(source), str(harness), "-o", str(binary)],
                check=True, capture_output=True,
            )
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr)
