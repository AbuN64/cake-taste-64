import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class CpuSchedulerTests(unittest.TestCase):
    def test_cpu_time_budget_interrupt_nesting_and_preemption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ultramodern").mkdir()
            (root / "cpu_profile.h").write_text("#define HOST_NATIVE_CPU_SCALE 12.0\n")
            (root / "recomp.h").write_text(
                "#pragma once\n#include <cstdint>\nstruct recomp_context { uint64_t r2, r4; };\n"
            )
            (root / "ultramodern/ultramodern.hpp").write_text(
                "namespace ultramodern { int this_thread(); void check_running_queue(unsigned char*); }\n"
            )
            harness = root / "test.cpp"
            harness.write_text(r'''
#include <cassert>
#include <thread>
#include "recomp.h"
extern uint8_t* n64host_rdram;
extern "C" void n64host_cpu_tick();
extern "C" void __wrap___osDisableInt_recomp(uint8_t*,recomp_context*);
extern "C" void __wrap___osRestoreInt_recomp(uint8_t*,recomp_context*);
int64_t cpu=1000; int guest=0, reads=0, drains=0, checks=0, waits=0;
int64_t host_thread_cpu_ns(){++reads; return cpu;}
int64_t host_cpu_wait_ns(int64_t ns){assert(ns==100000); ++waits; cpu+=500; return ns;}
void dequeue_external_messages(uint8_t*){++drains;}
namespace ultramodern {
int this_thread(){return guest;}
void check_running_queue(uint8_t*){++checks; cpu+=1000;}
}
int main(){
 uint8_t ram[8]{}; recomp_context ctx{};
 n64host_cpu_tick(); assert(reads==0);
 n64host_rdram=ram; n64host_cpu_tick(); assert(reads==0);
 guest=1; n64host_cpu_tick(); assert(checks==1&&drains==1&&waits==0);
 cpu+=5000; n64host_cpu_tick(); assert(waits==0);
 cpu+=5000; n64host_cpu_tick(); assert(waits==1);
 // Blocking/preemption changes wall time, not this thread's CPU clock.
 // Scheduler CPU overhead and our own wait overhead must not create new debt.
 for(int i=0;i<100;++i)n64host_cpu_tick();
 assert(waits==1);
 __wrap___osDisableInt_recomp(ram,&ctx); assert(ctx.r2==1);
 __wrap___osDisableInt_recomp(ram,&ctx); assert(ctx.r2==0);
 int before=checks;
 cpu+=20000; n64host_cpu_tick(); assert(waits==3&&checks==before);
 ctx.r4=0; __wrap___osRestoreInt_recomp(ram,&ctx); assert(checks==before);
 ctx.r4=1; __wrap___osRestoreInt_recomp(ram,&ctx); assert(checks==before+1);
 assert(drains==checks);
 // Interrupt masks belong to the guest's native thread, not to the whole process.
 __wrap___osDisableInt_recomp(ram,&ctx); before=checks;
 std::thread other([]{n64host_cpu_tick();}); other.join(); assert(checks==before+1);
 n64host_cpu_tick(); assert(checks==before+1);
}
''')
            binary = root / "test"
            subprocess.run(
                ["c++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
                 "-I", str(root), "-I", str(HOST), str(HOST / "cpu_scheduler.cpp"), str(HOST / "cpu_metrics.cpp"),
                 str(harness), "-o", str(binary)],
                check=True, capture_output=True,
            )
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_cpu_profile_requires_calibration_and_supported_vi(self):
        spec = importlib.util.spec_from_file_location("cpu_profile", HOST / "cpu_profile.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        facts = {"cpu": {"timing": {"count_hz": 46875000, "vi_hz": 60},
                         "calibration": {"model": module.MODEL,
                                         "window": {"vi": 60, "count_ticks": 46875000,
                                                    "entries": 100, "native_ns": 400000000}}}}
        self.assertIn("HOST_NATIVE_CPU_SCALE 2.5", module.render(facts))
        with self.assertRaises(KeyError):
            module.render({})
        for value in [True, "12", 0, -1]:
            facts["cpu"]["calibration"]["window"]["entries"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.render(facts)
        facts["cpu"]["calibration"]["window"]["entries"] = 100
        facts["cpu"]["timing"]["vi_hz"] = 50
        with self.assertRaisesRegex(ValueError, "only 60 Hz"):
            module.render(facts)
        facts["cpu"]["native_time_scale"] = 2.5
        with self.assertRaisesRegex(ValueError, "retired"):
            module.render(facts)
