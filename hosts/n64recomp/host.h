#pragma once
#include "ultramodern/ultramodern.hpp"
#include "ultramodern/input.hpp"
#include "librecomp/game.hpp"
ultramodern::audio_callbacks_t host_audio_callbacks();
ultramodern::input::callbacks_t host_input_callbacks();
ultramodern::renderer::callbacks_t host_renderer_callbacks();

void n64host_update_input();
union SDL_Event;
void n64host_input_event(const SDL_Event &);
void n64host_input_init();
