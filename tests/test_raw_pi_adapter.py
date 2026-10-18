import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class RawPiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "librecomp").mkdir()
        (root / "ultramodern").mkdir()
        (root / "host_profile.h").write_text("#define HOST_RDRAM_SIZE 4096u\n")
        (root / "librecomp/game.hpp").write_text("""
#pragma once
#include <cstdint>
#include <cstddef>
using gpr=uint64_t;
struct recomp_context {gpr r2, r4, r5, r6, r7;};
namespace recomp { void do_rom_read(uint8_t*,gpr,uint32_t,size_t); }
""")
        (root / "librecomp/addresses.hpp").write_text(
            "namespace recomp { constexpr uint32_t rom_base=0x10000000; }\n")
        (root / "ultramodern/ultramodern.hpp").write_text("""
#define OS_EVENT_PI 8
#define OS_EVENT_FAULT 12
namespace ultramodern {
enum class EventMessageSource {Pi};
void enqueue_external_message_src(uint32_t,uint32_t,bool,EventMessageSource);
}
""")
        (root / "mock.cpp").write_text("""
#include <cassert>
#include <cstring>
#include "librecomp/game.hpp"
#include "ultramodern/ultramodern.hpp"
size_t n64host_rom_size=8192;
static int step=0;
extern "C" uint8_t *n64host_xlate_slow(uint8_t *ram,uint32_t va) { return ram+(va&0x1fffffff); }
extern "C" void __real_osSetEventMesg_recomp(uint8_t*,recomp_context*) { assert(step++==0); }
extern "C" void n64host_fault_event(uint32_t,uint32_t) { assert(!"no fault event in these cases"); }
namespace recomp {
void do_rom_read(uint8_t*,gpr ram,uint32_t rom,size_t size) {
 assert(step++==1); assert(ram==0xffffffff80000100ULL); assert(rom==0x10000200); assert(size==16);
}}
namespace ultramodern {
void enqueue_external_message_src(uint32_t mq,uint32_t msg,bool jam,EventMessageSource src) {
 assert(step++==2); assert(mq==0x80000300); assert(msg==0x12345678); assert(!jam);
 assert(src==EventMessageSource::Pi);
}}
extern "C" void __wrap_osSetEventMesg_recomp(uint8_t*,recomp_context*);
extern "C" void __wrap_osPiRawStartDma_recomp(uint8_t*,recomp_context*);
int main(int argc,char **argv) {
 uint8_t ram[4096]={}; recomp_context ctx{};
 if (argc==1 || strcmp(argv[1],"queue")) {
   ctx.r4=8;ctx.r5=0x80000300;ctx.r6=0x12345678;
   __wrap_osSetEventMesg_recomp(ram,&ctx);
 }
 ctx.r4=0;ctx.r5=0x200;ctx.r6=0x80000100;ctx.r7=16;
 if (argc>1 && !strcmp(argv[1],"rom")) ctx.r5=8192;
 if (argc>1 && !strcmp(argv[1],"ram")) ctx.r6=0x80000ff8;
 if (argc>1 && !strcmp(argv[1],"align")) ctx.r6=0x80000101;
 if (argc>1 && !strcmp(argv[1],"write")) ctx.r4=1;
 __wrap_osPiRawStartDma_recomp(ram,&ctx);
 assert(step==3 && ctx.r2==0);
}
""")
        cls.binary = root / "test"
        subprocess.run(["c++", "-std=c++17", "-I", str(root), "-I", str(HOST / "probe"),
                        str(HOST / "pi_adapter.cpp"), str(root / "mock.cpp"), "-o", str(cls.binary)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_copy_precedes_exact_registered_message(self):
        subprocess.run([str(self.binary)], check=True, capture_output=True)

    def test_invalid_transfers_fail_before_copy_or_notification(self):
        for case in ("queue", "rom", "ram", "align", "write"):
            result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn("raw PI DMA:", result.stderr)


if __name__ == "__main__":
    unittest.main()
