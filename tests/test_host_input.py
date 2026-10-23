import copy
import json
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "hosts/n64recomp"
RENDER = runpy.run_path(str(HOST / "input_profile.py"))["render"]
FACTS = json.loads((ROOT / "tests/fixtures/host_input.json").read_text())


class ProfileTests(unittest.TestCase):
    def test_help_tracks_remapping(self):
        self.assertIn("Enter Start · X A · C B · Z/Shift Z", RENDER(FACTS))
        facts = copy.deepcopy(FACTS)
        facts["input"]["keyboard_buttons"]["A"] = ["Space"]
        header = RENDER(facts)
        self.assertIn("Space A", header)
        self.assertIn('{"Space", 32768}', header)

    def test_invalid_axis_and_missing_button_fail(self):
        facts = copy.deepcopy(FACTS)
        facts["input"]["gamepad_axis_buttons"][0]["threshold"] = 0
        with self.assertRaises(ValueError):
            RENDER(facts)
        facts = copy.deepcopy(FACTS)
        del facts["input"]["keyboard_buttons"]["START"]
        with self.assertRaises(ValueError):
            RENDER(facts)

    def test_keyboard_gamepad_snapshot_and_disconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "ultramodern").mkdir()
            (p / "host_input_profile.h").write_text(RENDER(FACTS))
            (p / "ultramodern/input.hpp").write_text(r"""
#pragma once
#include <cstdint>
namespace ultramodern::input {
enum class Device {None,Controller}; enum class Pak {None,RumblePak};
struct connected_device_info_t {Device connected_device; Pak connected_pak;};
struct callbacks_t {void (*poll_input)(); bool (*get_input)(int,uint16_t*,float*,float*);
 void (*set_rumble)(int,bool); connected_device_info_t (*get_connected_device_info)(int);};}
""")
            (p / "SDL.h").write_text(r"""
#pragma once
#include <cstdint>
using Uint64=uint64_t; Uint64 SDL_GetTicks64();
using Uint8=uint8_t; using SDL_Scancode=int; using SDL_GameControllerButton=int;
using SDL_GameControllerAxis=int; struct SDL_GameController{};
constexpr int SDL_KEYDOWN=0x300;
struct SDL_Keysym {int scancode;};
union SDL_Event {int type;struct {int type;int repeat;SDL_Keysym keysym;} key;};
constexpr int SDL_SCANCODE_UNKNOWN=0,SDL_CONTROLLER_BUTTON_INVALID=-1,SDL_CONTROLLER_AXIS_INVALID=-1;
int SDL_GetScancodeFromName(const char*); int SDL_GameControllerGetButtonFromString(const char*);
int SDL_GameControllerGetAxisFromString(const char*); bool SDL_GameControllerGetAttached(SDL_GameController*);
void SDL_GameControllerClose(SDL_GameController*); int SDL_NumJoysticks(); bool SDL_IsGameController(int);
SDL_GameController *SDL_GameControllerOpen(int); const Uint8 *SDL_GetKeyboardState(int*);
int16_t SDL_GameControllerGetAxis(SDL_GameController*,int);
Uint8 SDL_GameControllerGetButton(SDL_GameController*,int);
""")
            (p / "mock.cpp").write_text(r"""
#include <cassert>
#include <map>
#include <string>
#include <cstring>
#include "SDL.h"
#include "ultramodern/input.hpp"
static Uint64 now=100;Uint64 SDL_GetTicks64(){return now;}
static std::map<std::string,int> keys,bs,as;
static Uint8 state[64]={},buttons[64]={};static int16_t axes[64]={};
static SDL_GameController pad;static bool connected=false;static int closed=0;
int index(std::map<std::string,int>&m,const char*n){auto it=m.find(n);
if(it!=m.end())return it->second;
int i=m.size()+1;
return m[n]=i;
}
int SDL_GetScancodeFromName(const char*n){return index(keys,n);}
int SDL_GameControllerGetButtonFromString(const char*n){return index(bs,n);}
int SDL_GameControllerGetAxisFromString(const char*n){
 if(strcmp(n,"leftx")&&strcmp(n,"lefty")&&strcmp(n,"rightx")&&strcmp(n,"righty")&&
    strcmp(n,"lefttrigger")&&strcmp(n,"righttrigger"))return -1;
 return index(as,n);
}
bool SDL_GameControllerGetAttached(SDL_GameController*){return connected;}
void SDL_GameControllerClose(SDL_GameController*){++closed;}
int SDL_NumJoysticks(){return connected?1:0;}bool SDL_IsGameController(int){return true;}
SDL_GameController *SDL_GameControllerOpen(int){return &pad;}
const Uint8 *SDL_GetKeyboardState(int*){return state;}
int16_t SDL_GameControllerGetAxis(SDL_GameController*,int n){return axes[n];}
Uint8 SDL_GameControllerGetButton(SDL_GameController*,int n){return buttons[n];}
void n64host_update_input();
void n64host_input_event(const SDL_Event&);
extern "C" unsigned n64host_golden_pads() { return 1; }
ultramodern::input::callbacks_t host_input_callbacks();
int main(){
 auto cb=host_input_callbacks();uint16_t b;float x,y;
 n64host_update_input();
 const char* names[]={"Up","Down","Left","Right","Return","X","C","Z","Left Shift","Q","E","I","J","K","L"};
 uint16_t bits[]={0x800,0x400,0x200,0x100,0x1000,0x8000,0x4000,0x2000,0x2000,0x20,0x10,8,2,4,1};
 for(int i=0;
i<15;
++i){state[keys[names[i]]]=1;
n64host_update_input();
assert(cb.get_input(0,&b,&x,&y));
assert(b==bits[i]);
state[keys[names[i]]]=0;
}
 state[keys["W"]]=1;state[keys["D"]]=1;n64host_update_input();cb.get_input(0,&b,&x,&y);assert(x==1&&y==1);
 memset(state,0,sizeof(state));connected=true;buttons[bs["a"]]=1;axes[as["rightx"]]=32767;axes[as["lefttrigger"]]=32767;
 n64host_update_input();cb.get_input(0,&b,&x,&y);assert(b==0xa001);
 connected=false;n64host_update_input();cb.get_input(0,&b,&x,&y);assert(b==0&&closed==1);
 SDL_Event event{};event.type=SDL_KEYDOWN;event.key.keysym.scancode=keys["X"];
 n64host_input_event(event);n64host_update_input();cb.get_input(0,&b,&x,&y);assert(b==0x8000);
 cb.get_input(0,&b,&x,&y);assert(b==0x8000);
 now+=81;cb.get_input(0,&b,&x,&y);assert(b==0);
 assert(!cb.get_input(1,&b,&x,&y));assert(cb.get_connected_device_info(0).connected_pak==ultramodern::input::Pak::None);
}
""")
            binary = p / "test"
            subprocess.run(
                ["c++", "-std=c++17", "-I", tmp, str(HOST / "input_sdl.cpp"), str(p / "mock.cpp"), "-o", str(binary)],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)
