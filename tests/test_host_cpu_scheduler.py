import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class CpuSchedulerTests(unittest.TestCase):
    def test_logical_clock_charges_guest_work_only_and_nests_interrupts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ultramodern").mkdir()
            (root / "cpu_profile.h").write_text("#define HOST_GUEST_NS_PER_ENTRY 2.5\n")
            (root / "cpu_native.h").write_text(
                'static const struct { const char *name; unsigned char charged; } host_functions[] = {\n'
                '    {"__udivdi3", 1}, {"game", 1}, {"gameplay", 0}, {"osSendMesg", 0},\n};\n'
            )
            (root / "recomp.h").write_text(
                "#pragma once\n#include <cstdint>\nstruct recomp_context { uint64_t r2, r4; };\n"
            )
            (root / "ultramodern/ultramodern.hpp").write_text(
                "#include <cstdint>\nnamespace ultramodern {\n"
                "int this_thread(); void check_running_queue(unsigned char*); void vclock_advance(uint64_t);\n}\n"
            )
            harness = root / "test.cpp"
            harness.write_text(r"""
#include <cassert>
#include <cstring>
#include <thread>
#include "recomp.h"
extern uint8_t* n64host_rdram;
extern "C" void n64host_cpu_tick(int);
extern "C" int n64host_charged(const char*);
extern "C" void __wrap___osDisableInt_recomp(uint8_t*,recomp_context*);
extern "C" void __wrap___osRestoreInt_recomp(uint8_t*,recomp_context*);
int guest=0, drains=0, checks=0; uint64_t clock_ns=0, advances=0;
void dequeue_external_messages(uint8_t*){++drains;}
namespace ultramodern {
int this_thread(){return guest;}
void check_running_queue(uint8_t*){++checks;}
void vclock_advance(uint64_t ns){clock_ns+=ns; ++advances;}
}
int main(int argc, char **argv){
 if (argc > 1) return n64host_charged(argv[1]);
 // Game code charges; the SDK and port-native code do not; a host-libc collision resolves by its stem.
 assert(n64host_charged("game")==1 && n64host_charged("gameplay")==0 && n64host_charged("osSendMesg")==0);
 assert(n64host_charged("__udivdi3_recomp")==1);
 uint8_t ram[8]{}; recomp_context ctx{};
 n64host_cpu_tick(1); assert(advances==0&&checks==0);
 n64host_rdram=ram; n64host_cpu_tick(1); assert(advances==0&&checks==0);
 guest=1;
 // The clock is the work count: a fixed charge per charged entry, the fraction carried (2.5 ns twice is 5 ns).
 n64host_cpu_tick(1); assert(clock_ns==2&&checks==1&&drains==1);
 n64host_cpu_tick(1); assert(clock_ns==5);
 // Service points (uncharged entries, loop passes, restores) deliver interrupts but never move the clock.
 for(int i=0;i<100;++i)n64host_cpu_tick(0);
 assert(clock_ns==5&&checks==102);
 __wrap___osDisableInt_recomp(ram,&ctx); assert(ctx.r2==1);
 __wrap___osDisableInt_recomp(ram,&ctx); assert(ctx.r2==0);
 int before=checks;
 // Guest time still advances (devices keep their schedule) but no interrupt is serviced.
 n64host_cpu_tick(1); assert(clock_ns==7&&checks==before);
 ctx.r4=0; __wrap___osRestoreInt_recomp(ram,&ctx); assert(checks==before);
 ctx.r4=1; __wrap___osRestoreInt_recomp(ram,&ctx); assert(checks==before+1&&clock_ns==7);
 assert(drains==checks);
 // Interrupt masks belong to the guest's native thread, not to the whole process.
 __wrap___osDisableInt_recomp(ram,&ctx); before=checks;
 std::thread other([]{n64host_cpu_tick(0);}); other.join(); assert(checks==before+1);
 n64host_cpu_tick(0); assert(checks==before+1);
}
""")
            binary = root / "test"
            subprocess.run(
                ["c++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
                 "-I", str(root), "-I", str(HOST), str(HOST / "cpu_scheduler.cpp"), str(harness), "-o", str(binary)],
                check=True, capture_output=True,
            )
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary), "missing"], capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, 2)
            self.assertIn("missing is not in symbols.toml", result.stderr)

    def test_cpu_profile_renders_the_charge_and_the_charged_set(self):
        spec = importlib.util.spec_from_file_location("cpu_profile", HOST / "cpu_profile.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        facts = {"cpu": {"timing": {"count_hz": 46875000, "vi_hz": 60},
                         "calibration": {"window": {"vi": 60, "count_ticks": 46875000, "entries": 100}},
                         "native_code": [[0x802AE000, 0x802C8000], [0x80300010, 0x80300011]]}}
        self.assertEqual(module.render(facts), "#pragma once\n#define HOST_GUEST_NS_PER_ENTRY 10000000.0\n")
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
        symbols = ('{ name = "osSendMesg", vram = 0x802B0000, size = 0x10 },\n'
                   '{ name = "game_b", vram = 0x80300000, size = 0x10 },\n'
                   '{ name = "bound", vram = 0x80300010, size = 0x10 },\n'
                   '{ name = "Game_a", vram = 0x80300020, size = 0x10 },\n')
        native = module.render_native(facts, symbols)
        # Sorted by strcmp, so the host's binary search holds; only game code outside every native range charges.
        self.assertIn('{"Game_a", 1},\n    {"bound", 0},\n    {"game_b", 1},\n    {"osSendMesg", 0},', native)
        for ranges in [[], [[2, 1]], [[1, True]], "x"]:
            with self.subTest(ranges=ranges), self.assertRaisesRegex(ValueError, "native_code"):
                module.render_native({"cpu": {"native_code": ranges}}, symbols)
        with self.assertRaisesRegex(ValueError, "two addresses"):
            module.render_native(facts, symbols + '{ name = "bound", vram = 0x80300030, size = 0x10 },\n')
        with self.assertRaisesRegex(ValueError, "no functions"):
            module.render_native(facts, "")
