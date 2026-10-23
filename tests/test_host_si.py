import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / 'hosts/n64recomp'


class SiTests(unittest.TestCase):
    def test_transactions_refresh_input_and_report_absent_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / 'librecomp').mkdir()
            (p / 'ultramodern').mkdir()
            (p / 'host_profile.h').write_text('#define HOST_RDRAM_SIZE 4096u\n')
            (p / 'librecomp/game.hpp').write_text(
                '#pragma once\n#include <cstdint>\nstruct recomp_context {uint64_t r2,r4,r5;};\n'
            )
            (p / 'ultramodern/ultramodern.hpp').write_text('namespace ultramodern {void send_si_message();}\n')
            (p / 'ultramodern/input.hpp').write_text('''#pragma once
#include <cstdint>
namespace ultramodern::input {
enum class Device {None,Controller}; enum class Pak {None,RumblePak};
struct connected_device_info_t {Device connected_device;Pak connected_pak;};
struct callbacks_t {void(*poll_input)();bool(*get_input)(int,uint16_t*,float*,float*);
void(*set_rumble)(int,bool);connected_device_info_t(*get_connected_device_info)(int);};
}
''')
            (p / 'mock.cpp').write_text(r'''
#include <cstdint>
extern "C" bool n64host_golden_pad(unsigned, uint16_t *, int8_t *, int8_t *) { return false; }  // no movie
#include <cassert>
#include <cstring>
#include "librecomp/game.hpp"
#include "ultramodern/input.hpp"
using namespace ultramodern::input;
static unsigned completions,polls; static uint16_t buttons;
static void (*pending)();static uint64_t delay_ns;
void host_si_schedule(void (*callback)(),uint64_t ns){assert(!pending);pending=callback;delay_ns=ns;}
void finish(){assert(pending);auto callback=pending;pending=nullptr;callback();}
namespace ultramodern {void send_si_message(){++completions;}}
extern "C" uint8_t *n64host_xlate_slow(uint8_t*r,uint32_t a){return r+(a&0x1fffffff);}
void convert_to_n64_range(float x,float y,int8_t&sx,int8_t&sy){sx=x;sy=y;}
void poll(){++polls;}
bool input(int c,uint16_t*b,float*x,float*y){assert(c==0);*b=buttons;*x=17;*y=-23;return true;}
connected_device_info_t info(int c){return {c==0?Device::Controller:Device::None,Pak::None};}
callbacks_t host_input_callbacks(){return {poll,input,nullptr,info};}
extern "C" void __osSiRawStartDma_recomp(uint8_t*,recomp_context*);
extern "C" void __osSiDeviceBusy_recomp(uint8_t*,recomp_context*);
int main(int argc,char**argv){
 uint8_t ram[4096]={};recomp_context c{99,0,0x80000100};
 auto byte=[&](int i)->uint8_t&{return ram[256+(i^3)];};
 auto dma=[&](int dir){
 unsigned before=completions;c.r4=dir;__osSiRawStartDma_recomp(ram,&c);
 assert(c.r2==0&&completions==before);assert(delay_ns==(dir?64000:576000));
 __osSiDeviceBusy_recomp(ram,&c);assert(c.r2==1);
 __osSiRawStartDma_recomp(ram,&c);assert(c.r2==uint64_t(-1)&&completions==before);
 finish();assert(completions==before+1);__osSiDeviceBusy_recomp(ram,&c);assert(c.r2==0);
 };
 if(argc>1&&argv[1][0]=='c'){
 // N64R_CPAK names a card: the slot answers, reads return the file's block, writes land in the file.
 byte(0)=1;byte(1)=3;byte(6)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(byte(3)==5&&byte(4)==0&&byte(5)==1);
 auto crc5=[](unsigned block){
  unsigned r=0;
  for(int b=10;b>=-5;--b){unsigned in=b>=0?(block>>b)&1u:0u,top=(r>>4)&1u;r=((r<<1)|in)&0x1fu;if(top)r^=0x15u;}
  return r;};
 assert(crc5(0x400)==1); // the well-known accessory control address 0x8001
 auto field=[&](unsigned block,int bad){
  unsigned f=(block<<5)|crc5(block);
  if(bad)f^=1;
  byte(3)=f>>8;byte(4)=f;};
 for(int i=0;i<64;++i)byte(i)=0;
 byte(0)=3;byte(1)=33;byte(2)=2;field(3,0);byte(38)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(byte(5)==0x60&&byte(36)==0x7f);  // block 3 of the file: bytes 0x60..0x7f, then their CRC

 for(int i=0;i<64;++i)byte(i)=0;
 byte(0)=3;byte(1)=33;byte(2)=2;field(3,1);byte(38)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(byte(37)==0xff&&byte(5)==0);  // bad address CRC: nothing returned, CRC inverted
 for(int i=0;i<64;++i)byte(i)=0;
 byte(0)=35;byte(1)=1;byte(2)=3;field(7,0);for(int i=0;i<32;++i)byte(5+i)=0xA0+i;byte(38)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(byte(37)!=0);
 for(int i=0;i<64;++i)byte(i)=0;
 byte(0)=3;byte(1)=33;byte(2)=2;field(0x400,0);byte(38)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(byte(5)==0&&byte(36)==0);  // the accessory area reads zeros
 return 0;}
 if(argc>1){if(argv[1][0]=='a'){c.r5++;dma(1);}else dma(0);return 1;}
 byte(0)=1;byte(1)=4;byte(2)=1;byte(7)=0xfe;byte(63)=1;
 dma(1);assert(completions==1&&polls==0);
 buttons=0x8000;dma(0);assert(completions==2&&polls==1);
 assert(byte(3)==0x80&&byte(4)==0&&byte(5)==17&&byte(6)==uint8_t(-23));
 buttons=0x100;dma(0);assert(completions==3&&polls==2&&byte(3)==1&&byte(4)==0);
 // Query two channels; controller 1 has no accessory, controller 2 is absent.
 for(int i=0;i<64;++i)byte(i)=0;
 byte(0)=1;byte(1)=3;byte(6)=1;byte(7)=3;byte(12)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(completions==5&&byte(3)==5&&byte(4)==0&&byte(5)==2&&(byte(7)&0x80));
 // Reset has the same absent-accessory identity.
 byte(2)=0xff;byte(63)=1;dma(1);dma(0);assert(byte(3)==5&&byte(4)==0&&byte(5)==2);
 // Accessory write cannot succeed on a controller without a Pak.
 for(int i=0;i<64;++i)byte(i)=0;
 byte(0)=35;byte(1)=1;byte(2)=3;byte(38)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(completions==9&&byte(1)==1&&byte(37)==0xff);
 // Old SDK packed accessory command: dummy prefix and alignment before END.
 for(int i=0;i<64;++i)byte(i)=0;
 byte(0)=0xff;byte(1)=35;byte(2)=1;byte(3)=3;
 byte(39)=0x88;byte(40)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(completions==11&&byte(2)==1&&byte(38)==0xff);
 for(int i=6;i<38;++i)byte(i)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(byte(38)==0x1e);
 // An absent accessory read also responds with inverted CRC, not timeout.
 for(int i=0;i<64;++i)byte(i)=0;
 byte(0)=3;byte(1)=33;byte(2)=2;byte(38)=0xfe;byte(63)=1;
 dma(1);dma(0);assert(byte(1)==33&&byte(37)==0xff);
}
''')
            binary = p / 'test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-I',str(p),'-I',str(HOST/'probe'),str(HOST/'si_adapter.cpp'),str(p/'mock.cpp'),'-o',str(binary)],check=True,capture_output=True)
            result=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            card = p / 'card.mpk'
            card.write_bytes(bytes(range(256)) * 128)
            ran = subprocess.run([str(binary), 'card'], capture_output=True, text=True, env={'N64R_CPAK': str(card)})
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertEqual(card.read_bytes()[7 * 32 : 8 * 32], bytes(0xA0 + i for i in range(32)))
            self.assertEqual(card.read_bytes()[: 7 * 32], (bytes(range(256)) * 128)[: 7 * 32])
            self.assertEqual(len(card.read_bytes()), 0x8000)
            card.write_bytes(b'short')
            ran = subprocess.run([str(binary), 'card'], capture_output=True, text=True, env={'N64R_CPAK': str(card)})
            self.assertEqual(ran.returncode, 4)
            self.assertIn('N64R_CPAK is not a 32 KiB image', ran.stderr)
            for arg, message in [('read','read without a PIF request'),('align','invalid transfer')]:
                result=subprocess.run([str(binary),arg],capture_output=True,text=True)
                self.assertEqual(result.returncode,4)
                self.assertIn(message,result.stderr)
