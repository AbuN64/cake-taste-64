import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class UnalignedTests(unittest.TestCase):
    def test_every_merge_helper_translates_its_aligned_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            source = r"""
#include <stdint.h>
#include <assert.h>
typedef uint64_t gpr;
__thread int n64host_rec_active; void n64host_replay_taint(const char *why) { (void)why; }
static uint8_t backing[32]; static unsigned translated, invoked;
static uint32_t wanted_address; static gpr wanted_reg;
uint8_t *n64host_ptr(uint8_t *ram, uint64_t va) {
 assert(ram==0 && va==wanted_address); ++translated; return backing;
}
#define LOAD(op) static gpr do_##op(uint8_t *r,gpr old,gpr off,gpr reg) { \
 assert(r==backing && off==0 && reg==wanted_reg && old==0x76543210); \
 assert(translated==invoked+1); ++invoked; return 0xfedcba98; }
#define STORE(op) static void do_##op(uint8_t *r,gpr off,gpr reg,gpr val) { \
 assert(r==backing && off==0 && reg==wanted_reg && val==0x76543210); \
 assert(translated==invoked+1); ++invoked; }
LOAD(lwl) LOAD(lwr) LOAD(ldl) LOAD(ldr)
STORE(swl) STORE(swr) STORE(sdl) STORE(sdr)
#include "unaligned.h"
#define CHECK_LOAD(op,mask) for(unsigned n=0;n<=mask;++n) { \
 wanted_address=0x81fff8; wanted_reg=(gpr)(int32_t)(0x80000000|n); \
 assert(do_##op(0,0x76543210,0x1000+n,0x81eff8)==0xfedcba98); }
#define CHECK_STORE(op,mask) for(unsigned n=0;n<=mask;++n) { \
 wanted_address=0x81fff8; wanted_reg=(gpr)(int32_t)(0x80000000|n); \
 do_##op(0,0x1000+n,0x81eff8,0x76543210); }
int main(void) {
 CHECK_LOAD(lwl,3) CHECK_LOAD(lwr,3) CHECK_LOAD(ldl,7) CHECK_LOAD(ldr,7)
 CHECK_STORE(swl,3) CHECK_STORE(swr,3) CHECK_STORE(sdl,7) CHECK_STORE(sdr,7)
 assert(invoked==48 && translated==48);
}
"""
            (p / "test.c").write_text(source)
            binary = p / "test"
            subprocess.run(["cc", "-Wall", "-Werror", "-I", str(HOST / "probe"),
                            str(p / "test.c"), "-o", str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)
