import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class AudioTests(unittest.TestCase):
    def test_default_output_queues_pcm_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "ultramodern").mkdir()
            (p / "librecomp").mkdir()
            (p / "librecomp/game.hpp").write_text(
                "#pragma once\n#include <cstdint>\nstruct recomp_context {uint64_t r2;};\n"
            )
            (p / "ultramodern/ultramodern.hpp").write_text(
                "#include <cstdint>\n"
                "#include <cstddef>\n"
                "namespace ultramodern { struct audio_callbacks_t {\n"
                "void(*queue)(int16_t*,size_t);size_t(*remaining)();void(*rate)(uint32_t);};\n"
                "uint64_t vclock_now_ns();}\n"
                ""
            )
            (p / "SDL.h").write_text(r"""
#pragma once
#include <cstdint>
using Uint32=uint32_t;using SDL_AudioDeviceID=uint32_t;
constexpr int AUDIO_S16SYS=0x8010;
struct SDL_AudioSpec {int freq;uint16_t format;uint8_t channels;uint16_t samples;};
void SDL_CloseAudioDevice(SDL_AudioDeviceID);
 SDL_AudioDeviceID SDL_OpenAudioDevice(const char*,int,SDL_AudioSpec*,SDL_AudioSpec*,int);

void SDL_PauseAudioDevice(SDL_AudioDeviceID,int); int SDL_QueueAudio(SDL_AudioDeviceID,const void*,Uint32);
Uint32 SDL_GetQueuedAudioSize(SDL_AudioDeviceID); const char *SDL_GetError();const char *SDL_GetCurrentAudioDriver();
""")
            (p / "mock.cpp").write_text(r"""
#include <cstddef>
#include <cstdint>
extern "C" void n64host_golden_pcm(const int16_t*, size_t) {}
extern "C" void n64host_golden_rate(unsigned) {}
#include <cassert>
#include <cstring>
#include "SDL.h"
#include "librecomp/game.hpp"
#include "ultramodern/ultramodern.hpp"
static int submissions=0;static int closed=0;static bool bad=false;static Uint32 queued=0;
static uint64_t vnow=0;
uint64_t ultramodern::vclock_now_ns(){return vnow;}
void SDL_CloseAudioDevice(SDL_AudioDeviceID){++closed;}
SDL_AudioDeviceID SDL_OpenAudioDevice(const char*n,int capture,SDL_AudioSpec*w,SDL_AudioSpec*h,int flags){
 assert(!n&&!capture&&!flags&&w->format==AUDIO_S16SYS&&w->channels==2);*h=*w;return bad?0:1;}
void SDL_PauseAudioDevice(SDL_AudioDeviceID,int pause){assert(!pause);}
int SDL_QueueAudio(SDL_AudioDeviceID,const void*,Uint32 n){queued+=n;return 0;}
Uint32 SDL_GetQueuedAudioSize(SDL_AudioDeviceID){return queued;}
const char *SDL_GetError(){return "mock missing device";}const char *SDL_GetCurrentAudioDriver(){return "pulseaudio";}
ultramodern::audio_callbacks_t host_audio_callbacks();
extern "C" void __real_osAiSetNextBuffer_recomp(uint8_t*,recomp_context *ctx){++submissions;ctx->r2=0;}
extern "C" void __wrap_osAiSetNextBuffer_recomp(uint8_t*,recomp_context*);
extern "C" void __wrap_osAiGetStatus_recomp(uint8_t*,recomp_context*);
int main(int argc,char**){bad=argc>1;
auto cb=host_audio_callbacks();
cb.rate(32000);
int16_t pcm[]={1,-2,3,-4};
recomp_context ctx{};
// The FIFO drains in guest time, whatever the device has played: a device holding 1000 bytes changes nothing.
queued=1000;
cb.queue(pcm,4);  // 2 frames at 32 kHz: ends at 62500 ns
__wrap_osAiGetStatus_recomp(nullptr,&ctx);assert(ctx.r2==0x40000000u&&cb.remaining()==2);
cb.queue(pcm,4);__wrap_osAiGetStatus_recomp(nullptr,&ctx);assert(ctx.r2==0xc0000000u&&cb.remaining()==4);
__wrap_osAiSetNextBuffer_recomp(nullptr,&ctx);assert(ctx.r2==uint64_t(-1)&&submissions==0);
vnow=62500;__wrap_osAiGetStatus_recomp(nullptr,&ctx);assert(ctx.r2==0x40000000u&&cb.remaining()==2);
vnow=125000;__wrap_osAiGetStatus_recomp(nullptr,&ctx);assert(ctx.r2==0&&cb.remaining()==0);
__wrap_osAiSetNextBuffer_recomp(nullptr,&ctx);assert(ctx.r2==0&&submissions==1);
cb.rate(44100);
assert(closed==1);
cb.rate(32000);cb.queue(pcm,4);cb.queue(pcm,4);
}
""")
            binary = p / "test"
            subprocess.run(
                ["c++", "-std=c++17", "-I", tmp, str(HOST / "audio_sdl.cpp"), str(p / "mock.cpp"), "-o", str(binary)],
                check=True,
                capture_output=True,
            )
            tap = p / "tap.pcm"
            subprocess.run([str(binary)], check=True, capture_output=True, env={"N64R_AUDIO_OUT": str(tap)})
            # N64R_AUDIO_OUT: every queued block (four of 4 samples), and the last frequency the game set.
            self.assertEqual(tap.stat().st_size, 4 * 4 * 2)
            self.assertEqual((p / "tap.pcm.rate").read_text(), "32000\n")
            result = subprocess.run([str(binary), "fail"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 3)
            self.assertIn("open default device failed", result.stderr)
