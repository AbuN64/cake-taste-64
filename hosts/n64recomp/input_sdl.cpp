// SDL state is sampled on the window thread; guest callbacks consume a locked snapshot.
#include <SDL.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <iterator>
#include <mutex>
#include "ultramodern/input.hpp"
#include "host_input_profile.h"
namespace {
SDL_GameController *pad = nullptr;
std::mutex input_mutex;
uint16_t buttons = 0;
Uint64 tap_until[16] = {}, stick_tap_until[4] = {};
float stick_x = 0, stick_y = 0;
SDL_Scancode keyboard[std::size(host_keyboard_bindings)], stick_keys[4];
SDL_GameControllerButton gamepad[std::size(host_gamepad_bindings)];
SDL_GameControllerAxis axis_buttons[std::size(host_axis_bindings)], stick_axes[2];
bool initialized = false;
[[noreturn]] void invalid(const char *name) {
    std::fprintf(stderr, "n64host: unknown SDL input binding: %s\n", name);
    std::_Exit(4);
}
void init() {
    for (size_t i=0; i<std::size(keyboard); ++i) {
        keyboard[i]=SDL_GetScancodeFromName(host_keyboard_bindings[i].name);
        if (keyboard[i]==SDL_SCANCODE_UNKNOWN) invalid(host_keyboard_bindings[i].name);
    }
    for (size_t i=0; i<4; ++i) {
        stick_keys[i]=SDL_GetScancodeFromName(host_stick_keys[i]);
        if (stick_keys[i]==SDL_SCANCODE_UNKNOWN) invalid(host_stick_keys[i]);
    }
    for (size_t i=0; i<std::size(gamepad); ++i) {
        gamepad[i]=SDL_GameControllerGetButtonFromString(host_gamepad_bindings[i].name);
        if (gamepad[i]==SDL_CONTROLLER_BUTTON_INVALID) invalid(host_gamepad_bindings[i].name);
    }
    for (size_t i=0; i<std::size(axis_buttons); ++i) {
        axis_buttons[i]=SDL_GameControllerGetAxisFromString(host_axis_bindings[i].name);
        if (axis_buttons[i]==SDL_CONTROLLER_AXIS_INVALID) invalid(host_axis_bindings[i].name);
    }
    for (size_t i=0; i<2; ++i) {
        stick_axes[i]=SDL_GameControllerGetAxisFromString(host_stick_axes[i]);
        if (stick_axes[i]==SDL_CONTROLLER_AXIS_INVALID) invalid(host_stick_axes[i]);
    }
    initialized=true;
}
void poll() {} // SDL_PollEvent in the host window loop pumps events before each snapshot.
bool get(int n, uint16_t *b, float *x, float *y) {
    *b=0; *x=*y=0;
    if (n!=0) return false;
    std::lock_guard lock(input_mutex);
    const Uint64 now=SDL_GetTicks64();
    *b=buttons;
    for (unsigned i=0; i<16; ++i) if (now < tap_until[i]) *b |= uint16_t(1u<<i);
    float tx=float(now < stick_tap_until[1])-float(now < stick_tap_until[0]);
    float ty=float(now < stick_tap_until[2])-float(now < stick_tap_until[3]);
    *x=std::clamp(stick_x+tx,-1.0f,1.0f); *y=std::clamp(stick_y+ty,-1.0f,1.0f);
    return true;
}
void rumble(int, bool) {}
ultramodern::input::connected_device_info_t info(int n) {
    using namespace ultramodern::input;
    return {n==0 ? Device::Controller : Device::None, Pak::None};
}
}
void n64host_input_init() { init(); }
void n64host_input_event(const SDL_Event &);
// Press a key by SDL name without a window event (scripted runs); same latch as a real keydown.
void n64host_script_key(const char *name) {
    SDL_Event e{};
    e.type = SDL_KEYDOWN;
    e.key.keysym.scancode = SDL_GetScancodeFromName(name);
    if (e.key.keysym.scancode == SDL_SCANCODE_UNKNOWN) { std::fprintf(stderr, "n64host: unknown script key %s\n", name); return; }
    n64host_input_event(e);
}
void n64host_input_event(const SDL_Event &event) {
    if (event.type != SDL_KEYDOWN || event.key.repeat) return;
    if (!initialized) init();
    std::lock_guard lock(input_mutex);
    const Uint64 until=SDL_GetTicks64()+HOST_INPUT_TAP_MS;
    for (size_t i=0; i<std::size(keyboard); ++i) {
        if (event.key.keysym.scancode == keyboard[i])
            for (unsigned bit=0; bit<16; ++bit)
                if (host_keyboard_bindings[i].button & (1u<<bit)) tap_until[bit]=until;
    }
    for (unsigned i=0; i<4; ++i)
        if (event.key.keysym.scancode == stick_keys[i]) stick_tap_until[i]=until;
    std::fprintf(stderr, "n64host: keydown scancode=%d latch_ms=%u\n",
        int(event.key.keysym.scancode), unsigned(HOST_INPUT_TAP_MS));
}
void n64host_update_input() {
    if (!initialized) init();
    if (pad && !SDL_GameControllerGetAttached(pad)) {
        SDL_GameControllerClose(pad); pad=nullptr;
    }
    if (!pad) {
        for (int i=0; i<SDL_NumJoysticks(); ++i) {
            if (SDL_IsGameController(i) && (pad=SDL_GameControllerOpen(i))) break;
        }
    }
    const Uint8 *keys=SDL_GetKeyboardState(nullptr);
    uint16_t b=0;
    for (size_t i=0; i<std::size(keyboard); ++i)
        if (keys[keyboard[i]]) b |= host_keyboard_bindings[i].button;
    float x=float(keys[stick_keys[1]])-float(keys[stick_keys[0]]);
    float y=float(keys[stick_keys[2]])-float(keys[stick_keys[3]]);
    if (pad) {
        x += SDL_GameControllerGetAxis(pad,stick_axes[0])/32768.0f;
        y += SDL_GameControllerGetAxis(pad,stick_axes[1])/32768.0f * (host_invert_y ? -1 : 1);
        for (size_t i=0; i<std::size(gamepad); ++i)
            if (SDL_GameControllerGetButton(pad,gamepad[i])) b |= host_gamepad_bindings[i].button;
        for (size_t i=0; i<std::size(axis_buttons); ++i) {
            const auto &binding=host_axis_bindings[i];
            if (SDL_GameControllerGetAxis(pad,axis_buttons[i])/32768.0f * binding.direction >= binding.threshold)
                b |= binding.button;
        }
    }
    std::lock_guard lock(input_mutex);
    buttons=b; stick_x=std::clamp(x,-1.0f,1.0f); stick_y=std::clamp(y,-1.0f,1.0f);
}
ultramodern::input::callbacks_t host_input_callbacks() { return {poll,get,rumble,info}; }
