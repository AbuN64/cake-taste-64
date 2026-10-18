#include <SDL.h>
#include "host.h"
namespace {
SDL_AudioDeviceID dev;
uint32_t rate = 32000;
void open_dev() {
    if (dev) SDL_CloseAudioDevice(dev);
    SDL_AudioSpec want{}, have{};
    want.freq = rate; want.format = AUDIO_S16SYS; want.channels = 2; want.samples = 512;
    dev = SDL_OpenAudioDevice(nullptr, 0, &want, &have, 0);
    if (dev) SDL_PauseAudioDevice(dev, 0);
}
void queue(int16_t *s, size_t n) { if (dev) SDL_QueueAudio(dev, s, (Uint32)(n * sizeof(int16_t))); }
size_t remaining() { return dev ? SDL_GetQueuedAudioSize(dev) / (2 * sizeof(int16_t)) : 0; }
void set_freq(uint32_t f) { rate = f; open_dev(); }
}
ultramodern::audio_callbacks_t host_audio_callbacks() { return {queue, remaining, set_freq}; }
