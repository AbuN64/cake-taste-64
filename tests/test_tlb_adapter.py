import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class TlbTests(unittest.TestCase):
    def test_dynamic_pages_eviction_probe_and_fault_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "recomp.h").write_text(
                "#include <stdint.h>\ntypedef uint64_t gpr;\n"
                "typedef struct { uint64_t r2,r4,r5,r6,r7,r29; } recomp_context;\n"
            )
            (p / "host_profile.h").write_text(
                "#define HOST_RDRAM_SIZE 0x800000u\n#define HOST_TLB_COUNT 1\n"
                "static const uint32_t host_tlb[][3]={{0x200000,0,0x200000}};\n"
            )
            (p / "tlb_profile.h").write_text(
                "#define HOST_BOOT_TLB_COUNT 1\n"
                "static const uint32_t host_boot_tlb[][5]={{31,0x1fe000,0x200000,0,0x100000}};\n"
            )
            (p / "mock.c").write_text(r"""
#include "recomp.h"
#include "tlb.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
static unsigned faults;
static uint8_t ram[0x800000];
void n64host_addrmap_init(void);
uint8_t *n64host_xlate_slow(uint8_t*,uint32_t);
void osMapTLB_recomp(uint8_t*,recomp_context*);
void osUnmapTLB_recomp(uint8_t*,recomp_context*);
void __wrap_osUnmapTLBAll_recomp(uint8_t*,recomp_context*);
void __osProbeTLB_recomp(uint8_t*,recomp_context*);
void n64host_tlb_fault(uint8_t *r,uint32_t va) {
 assert(r==ram && va==0x810034); ++faults;
 if (getenv("NO_MAP")) return;
 n64host_tlb_map(3,0,0x810000,0x10000,0xffffffff);
}
int main(int argc,char **argv) {
 n64host_addrmap_init();
 recomp_context c={0,2,0,0x810000,0x20000,0x80000100};
 *(uint32_t*)(ram+0x110)=0x21000; *(uint32_t*)(ram+0x114)=7;
 if (argc>1) switch(atoi(argv[1])) {
 case 1: c.r7=0x800000; break;
 case 2: c.r5=0x2000; break;
 case 3: c.r4=32; break;
 case 4: c.r6+=0x1000; break;
 case 5: *(uint32_t*)(ram+0x110)=0x21001; break;
 case 6: *(uint32_t*)(ram+0x114)=6; break;
 }
 osMapTLB_recomp(ram,&c);
 assert(n64host_xlate_slow(ram,0x810037)==ram+0x20037);
 assert(n64host_xlate_slow(ram,0x811fff)==ram+0x21fff);
 c.r4=0x811044; __osProbeTLB_recomp(ram,&c); assert(c.r2==0x21044);
 c.r4=2; osUnmapTLB_recomp(ram,&c);
 assert(n64host_tlb_probe(0x810000)==0xffffffff);
 assert(n64host_xlate_slow(ram,0x810034)==ram+0x10034 && faults==1);
 assert(n64host_xlate_slow(ram,0x810034)==ram+0x10034 && faults==1);
 assert(n64host_tlb_probe(0x811000)==0xffffffff);
 // Replacing an entry invalidates its previous virtual pair and both halves.
 n64host_tlb_map(3,0,0x900000,0xffffffff,0x33000);
 assert(n64host_tlb_probe(0x810034)==0xffffffff);
 assert(n64host_tlb_probe(0x900000)==0xffffffff);
 assert(n64host_tlb_probe(0x901012)==0x33012);
 __wrap_osUnmapTLBAll_recomp(ram,&c);
 assert(n64host_tlb_probe(0x901012)==0xffffffff);
 assert(n64host_tlb_probe(0x200010)==0x10); // Wired entry survives.
 assert(n64host_tlb_probe(0x3fffff)==0x1fffff);
 assert(n64host_xlate_slow(ram,0xa0000020)==ram+0x20);
 assert(n64host_xlate_slow(ram,0xffffffc0)!=ram && faults==1);
 c.r4=0x810000; __osProbeTLB_recomp(ram,&c); assert(c.r2==(gpr)-1);
}
""")
            binary = p / "test"
            subprocess.run(
                ["cc", "-Wall", "-Wextra", "-I", tmp, "-I", str(HOST), "-I", str(HOST / "probe"),
                 str(HOST / "tlb_adapter.c"), str(HOST / "tlb.c"), str(HOST / "addrmap.c"),
                 str(p / "mock.c"), "-o", str(binary)], check=True, capture_output=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)
            for case in range(1, 7):
                result = subprocess.run([str(binary), str(case)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertIn("TLB", result.stderr)
            result = subprocess.run([str(binary)], env={**os.environ, "NO_MAP": "1"}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 4)
            self.assertIn("resumed without mapping", result.stderr)

    def test_fault_handoff_suspends_without_preemption(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "librecomp").mkdir()
            (p / "ultramodern").mkdir()
            (p / "librecomp/game.hpp").write_text("#pragma once\n#include <cstdint>\n")
            (p / "host_profile.h").write_text("#define HOST_RDRAM_SIZE 0x800000\n")
            (p / "tlb_profile.h").write_text("#define HOST_FAULTED_THREAD 0x80000200u\n")
            (p / "ultramodern/ultramodern.hpp").write_text(r"""
#pragma once
#include <cstdint>
#define PTR(t) int32_t
#define TO_PTR(t,a) ((t*)(rdram+((uint32_t)(a)&0x1fffffff)))
#define MEM_W(o,a) (*(int32_t*)(rdram+((uint64_t)(a)+(o)-0xFFFFFFFF80000000ull)))
using OSMesg=int32_t; struct OSMesgQueue {};
enum class OSThreadState {STOPPED,QUEUED,RUNNING,BLOCKED};
struct OSThread {uint32_t words[4];uint16_t flags;OSThreadState state;};
namespace ultramodern { int32_t this_thread(); void run_next_thread_and_wait(uint8_t*); }
""")
            (p / "mock.cpp").write_text(r"""
#include "ultramodern/ultramodern.hpp"
#include "tlb.h"
#include <cassert>
#include <cstdlib>
static uint8_t ram[0x800000]; static bool sent=false, resumed=false;
int32_t ultramodern::this_thread(){return int32_t(0x80001000);}
bool do_send(uint8_t *r,int32_t q,OSMesg m,bool jam,bool block){
 assert(r==ram && q==int32_t(0x80003000) && m==123 && !jam && !block);
 assert(*(uint32_t*)(ram+0x200)==0x80001000);
 assert(*(uint32_t*)(ram+0x1120)==8 && *(uint32_t*)(ram+0x1124)==0x810000);
 auto *t=(OSThread*)(ram+0x1000); assert(t->flags==2 && t->state==OSThreadState::STOPPED);
 sent=true; return !getenv("FULL");
}
void ultramodern::run_next_thread_and_wait(uint8_t *r){assert(r==ram && sent);resumed=true;}
int main(int argc,char**){
 if(argc==1) n64host_fault_event(0x80003000,123);
 n64host_tlb_fault(ram,0x810000);assert(resumed);
}
""")
            binary = p / "fault"
            subprocess.run(["c++", "-std=c++17", "-I", tmp, "-I", str(HOST),
                            str(HOST / "fault_adapter.cpp"), str(p / "mock.cpp"), "-o", str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary), "missing"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 4)
            self.assertIn("no guest thread or OS_EVENT_FAULT", result.stderr)

            result = subprocess.run([str(binary)], env={**os.environ, "FULL": "1"}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 4)
            self.assertIn("queue full", result.stderr)
