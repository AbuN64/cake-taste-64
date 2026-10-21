"""Exercise the virtual guest clock and timers the maintained runtime patch adds to ultramodern."""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "hosts/n64recomp/patches/n64modernruntime-virtual-time.diff"
FIXTURE = ROOT / "tests/fixtures/runtime_timer.cpp"

HEADER = r'''
#pragma once
#include <chrono>
#include <cstdint>
#include <functional>
#include <thread>
#include <cassert>
#define PTR(T) int32_t
#define RDRAM_ARG uint8_t* rdram,
#define RDRAM_ARG1 uint8_t* rdram
#define TO_PTR(T, A) reinterpret_cast<T*>(rdram + (uint32_t(A)))
typedef uint32_t u32; typedef uint64_t u64; typedef uint64_t OSTime; typedef int32_t OSMesg;
struct OSMesgQueue; struct OSTimer;
extern "C" u32 osGetCount();
extern "C" OSTime osGetTime();
extern "C" void osSetTime(OSTime);
extern "C" int osSetTimer(uint8_t*, int32_t, OSTime, OSTime, int32_t, OSMesg);
extern "C" int osStopTimer(uint8_t*, int32_t);
namespace ultramodern {
enum class EventMessageSource : int { Timer };
void enqueue_external_message_src(int32_t mq, OSMesg msg, bool jam, EventMessageSource src);
void init_timers(uint8_t* rdram);
uint32_t get_speed_multiplier();
std::chrono::high_resolution_clock::time_point get_start();
std::chrono::high_resolution_clock::duration time_since_start();
void sleep_milliseconds(uint32_t);
void sleep_until(const std::chrono::high_resolution_clock::time_point&);
uint64_t vclock_now_ns();
void vclock_advance(uint64_t);
uint64_t vclock_schedule(uint64_t, std::function<void()>);
void vclock_cancel(uint64_t);
bool vclock_idle(uint64_t);
void set_vclock_idle_hook(std::function<void()>);
}
'''

HARNESS = r'''
#include <vector>
#include "ultramodern/ultramodern.hpp"
struct Timer { int32_t a, b; uint64_t interval, timestamp; int32_t mq; int32_t msg; };
std::vector<std::pair<int32_t, uint64_t>> fired;
void ultramodern::enqueue_external_message_src(int32_t mq, OSMesg msg, bool, EventMessageSource) {
    fired.push_back({msg, ultramodern::vclock_now_ns()});
}
int main() {
    using namespace ultramodern;
    std::vector<uint64_t> seen;
    // Events fire in time order and observe the time they were due, whatever one advance spans.
    vclock_schedule(30, [&] { seen.push_back(vclock_now_ns()); });
    vclock_schedule(10, [&] { seen.push_back(vclock_now_ns()); });
    uint64_t cancelled = vclock_schedule(20, [&] { seen.push_back(999); });
    vclock_cancel(cancelled);
    vclock_cancel(cancelled);
    assert(vclock_now_ns() == 0 && seen.empty());
    vclock_advance(5);
    assert(vclock_now_ns() == 5 && seen.empty());
    // Wall time never moves the clock.
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    assert(vclock_now_ns() == 5);
    vclock_advance(100);
    assert(vclock_now_ns() == 105 && seen.size() == 2 && seen[0] == 10 && seen[1] == 30);
    // An event may schedule the next one (the retrace pattern).
    int n = 0;
    std::function<void()> again = [&] { if (++n < 3) vclock_schedule(vclock_now_ns() + 10, again); };
    vclock_schedule(vclock_now_ns(), again);
    vclock_advance(0); assert(n == 1);
    vclock_advance(25); assert(n == 3 && vclock_now_ns() == 130);
    // Count is 46875 ticks per guest millisecond.
    vclock_advance(1000000 - 130);
    assert(vclock_now_ns() == 1000000 && osGetCount() == 46875 && osGetTime() == 46875);
    // Timers: first at the countdown, then at the interval; stopping cancels.
    alignas(16) uint8_t ram[256]{};
    Timer* t = reinterpret_cast<Timer*>(ram + 64);
    osSetTimer(ram, 64, 46875 * 10, 46875 * 5, 7, 42);  // fires 10 ms from now, then every 5 ms
    vclock_advance(9999999); assert(fired.empty());
    vclock_advance(1); assert(fired.size() == 1 && fired[0].first == 42 && fired[0].second == 11000000);
    vclock_advance(10000000);
    assert(fired.size() == 3 && fired[1].second == 16000000 && fired[2].second == 21000000);
    osStopTimer(ram, 64);
    vclock_advance(50000000); assert(fired.size() == 3);
    // Re-arming replaces the pending expiry instead of duplicating it.
    osSetTimer(ram, 64, 46875, 0, 7, 43);
    osSetTimer(ram, 64, 46875 * 2, 0, 7, 44);
    vclock_advance(5000000);
    assert(fired.size() == 4 && fired[3].first == 44);
    (void)t;
    // An idle guest jumps to the next event, however far off; with a bound it only advances that far.
    int idle_hooks = 0;
    set_vclock_idle_hook([&] { ++idle_hooks; });
    const uint64_t before = vclock_now_ns();
    int woke = 0;
    vclock_schedule(before + 700, [&] { ++woke; });
    assert(!vclock_idle(100) && woke == 0 && vclock_now_ns() == before + 100);
    assert(vclock_idle(UINT64_MAX) && woke == 1 && vclock_now_ns() == before + 700);
    assert(idle_hooks == 2);
    return 0;
}
'''


class RuntimeVirtualTimeTests(unittest.TestCase):
    def test_virtual_clock_events_and_timers_follow_guest_time_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ultramodern/src/timer.cpp"
            source.parent.mkdir(parents=True)
            source.write_bytes(FIXTURE.read_bytes())
            (root / "ultramodern/include/ultramodern").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(root), "apply", "--include=ultramodern/src/timer.cpp", str(PATCH)],
                check=True, capture_output=True)
            stubs = root / "stubs"
            (stubs / "ultramodern").mkdir(parents=True)
            (stubs / "ultramodern/ultramodern.hpp").write_text(HEADER)
            (stubs / "ultramodern/ultra64.h").write_text("#pragma once\n")
            (stubs / "blockingconcurrentqueue.h").write_text("")
            harness = root / "test.cpp"
            harness.write_text(HARNESS)
            binary = root / "test"
            subprocess.run(
                ["c++", "-std=c++20", "-Wall", "-Wno-unused-parameter", "-Werror", "-pthread", "-I", str(stubs),
                 str(source), str(harness), "-o", str(binary)],
                check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
