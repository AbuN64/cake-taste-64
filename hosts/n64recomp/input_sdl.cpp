#include <SDL.h>
#include "host.h"
namespace {
SDL_GameController *pad;
void poll() {
    SDL_PumpEvents();
    if (!pad && SDL_NumJoysticks() > 0) pad = SDL_GameControllerOpen(0);
}
bool get(int n, uint16_t *buttons, float *x, float *y) {
    *buttons = 0; *x = *y = 0;
    if (n != 0) return false;
    const Uint8 *k = SDL_GetKeyboardState(nullptr);
    static const struct { SDL_Scancode s; uint16_t b; } map[] = {
        {SDL_SCANCODE_RETURN, 0x1000}, {SDL_SCANCODE_Z, 0x8000}, {SDL_SCANCODE_X, 0x4000},
        {SDL_SCANCODE_LSHIFT, 0x2000}, {SDL_SCANCODE_Q, 0x0020}, {SDL_SCANCODE_E, 0x0010},
        {SDL_SCANCODE_UP, 0x0800}, {SDL_SCANCODE_DOWN, 0x0400}, {SDL_SCANCODE_LEFT, 0x0200}, {SDL_SCANCODE_RIGHT, 0x0100}};
    for (auto &m : map) if (k[m.s]) *buttons |= m.b;
    if (k[SDL_SCANCODE_A]) *x -= 1;
    if (k[SDL_SCANCODE_D]) *x += 1;
    if (k[SDL_SCANCODE_S]) *y -= 1;
    if (k[SDL_SCANCODE_W]) *y += 1;
    if (pad) {
        *x += SDL_GameControllerGetAxis(pad, SDL_CONTROLLER_AXIS_LEFTX) / 32768.0f;
        *y -= SDL_GameControllerGetAxis(pad, SDL_CONTROLLER_AXIS_LEFTY) / 32768.0f;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_START)) *buttons |= 0x1000;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_A)) *buttons |= 0x8000;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_X)) *buttons |= 0x4000;
    }
    return true;
}
void rumble(int, bool) {}
ultramodern::input::connected_device_info_t info(int n) {
    if (n == 0) return {ultramodern::input::Device::Controller, ultramodern::input::Pak::None};
    return {ultramodern::input::Device::None, ultramodern::input::Pak::None};
}
}
ultramodern::input::callbacks_t host_input_callbacks() { return {poll, get, rumble, info}; }
