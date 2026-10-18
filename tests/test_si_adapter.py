import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class SiTests(unittest.TestCase):
    def test_pif_status_read_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "librecomp").mkdir()
            (p / "ultramodern").mkdir()
            (p / "host_profile.h").write_text("#define HOST_RDRAM_SIZE 4096u\n")
            (p / "librecomp/game.hpp").write_text(
                "#pragma once\n#include <cstdint>\nstruct recomp_context {uint64_t r2,r4,r5;};\n"
            )
            (p / "ultramodern/ultramodern.hpp").write_text("namespace ultramodern { void send_si_message(); }\n")
            (p / "ultramodern/input.hpp").write_text(r"""
#pragma once
#include <cstdint>
namespace ultramodern::input {
enum class Device {None,Controller}; enum class Pak {None,RumblePak};
struct connected_device_info_t {Device connected_device; Pak connected_pak;};
struct callbacks_t {
 void (*poll_input)(); bool (*get_input)(int,uint16_t*,float*,float*);
 void (*set_rumble)(int,bool); connected_device_info_t (*get_connected_device_info)(int);
}; }
""")
            (p / "mock.cpp").write_text(r"""
#include <cassert>
#include <cstring>
#include "librecomp/game.hpp"
#include "ultramodern/input.hpp"
using namespace ultramodern::input;
static unsigned events=0;
static void (*pending)();
void host_si_schedule(void (*callback)(),uint64_t) {assert(!pending);pending=callback;}

static uint8_t ram[4096]={};
extern "C" uint8_t *n64host_xlate_slow(uint8_t *r,uint32_t va) { return r+(va&0x1fffffff); }
namespace ultramodern { void send_si_message() { ++events; } }
void convert_to_n64_range(float x,float y,int8_t &sx,int8_t &sy) {sx=12;sy=-34;}
connected_device_info_t info(int n) {return {n==0?Device::Controller:Device::None,Pak::None};}
bool get(int n,uint16_t *b,float*x,float*y) {*b=0x9000;*x=*y=0;return n==0;}
callbacks_t host_input_callbacks() {return {nullptr,get,nullptr,info};}
extern "C" void __osSiRawStartDma_recomp(uint8_t*,recomp_context*);
void put(unsigned n,unsigned v) {ram[(256+n)^3]=v;}
unsigned byte(unsigned n) {return ram[(256+n)^3];}
void transfer(recomp_context* ctx) {
 unsigned before=events;
 __osSiRawStartDma_recomp(ram,ctx);
 assert(ctx->r2==0 && events==before && pending);
 auto callback=pending;pending=nullptr;callback();
 assert(events==before+1);
}
int main(int argc,char **argv) {
 recomp_context ctx{99,1,0x80000100};
 // Channel 0 query followed by a channel 1 query, including filler and end marker.
 unsigned packet[]={0xff,1,3,0,0xcc,0xcc,0xcc,1,3,0,0xdd,0xdd,0xdd,0xfe};
 for(unsigned i=0;i<sizeof(packet)/sizeof(packet[0]);++i) put(i,packet[i]);
 put(63,1);
 if(argc>1) {
   if(!strcmp(argv[1],"range"))ctx.r5=0x80000ff0;
   if(!strcmp(argv[1],"length"))put(2,63);
   if(!strcmp(argv[1],"command"))put(3,7);
   if(!strcmp(argv[1],"read"))ctx.r4=0;
 }
 transfer(&ctx);assert(ctx.r2==0 && events==1);
 assert(byte(4)==0xcc); // write DMA must not overwrite the guest buffer
 ctx.r4=0;transfer(&ctx);
 assert(events==2 && ctx.r2==0);
 assert(byte(4)==5 && byte(5)==0 && byte(6)==2);
 assert(byte(8)==0x83 && byte(10)==0xdd && byte(63)==0);
 // Raw controller read retains big-endian buttons and signed stick bytes.
 put(0,1);put(1,4);put(2,1);put(7,0xfe);put(63,1);
 ctx.r4=1;transfer(&ctx);
 ctx.r4=0;transfer(&ctx);
 assert(events==4 && byte(3)==0x90 && byte(4)==0 && byte(5)==12 && byte(6)==222);
}
""")
            binary = p / "test"
            subprocess.run(
                [
                    "c++",
                    "-std=c++17",
                    "-I",
                    tmp,
                    "-I",
                    str(HOST / "probe"),
                    str(HOST / "si_adapter.cpp"),
                    str(p / "mock.cpp"),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)
            for case in ("range", "length", "command", "read"):
                result = subprocess.run([str(binary), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertIn("raw SI DMA", result.stderr)
