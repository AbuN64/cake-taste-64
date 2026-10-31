#include <SDL.h>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <mutex>
#include <string>
#include "librecomp/game.hpp"
#include "ultramodern/ultramodern.hpp"
extern "C" void n64host_golden_pcm(const int16_t *, size_t);
extern "C" void n64host_golden_rate(unsigned);
namespace {
SDL_AudioDeviceID dev;
uint32_t rate = 32000;
uint64_t samples = 0, nonzero = 0;
std::mutex audio_mutex;
// The AI plays queued samples in guest time, as VI and timers run, so the
// FIFO the game sees never depends on how fast the host consumes sound.
// The AI FIFO in CPU Count ticks (46.875 MHz, the port's unit): each held buffer's end, oldest first. A buffer starts
// at the end of the one ahead of it, or now, and plays ceil(frames * Count rate / sample rate) ticks; the AI interrupt
// is raised at its end (RUNTIME.md "Virtual guest time").
constexpr uint64_t counter_hz = 46875000;
std::deque<uint64_t> guest_ends;
uint64_t ticks_now() { return ultramodern::vclock_now_ns() * 3 / 64; }
uint64_t tick_ns(uint64_t ticks) { return (ticks * 64 + 2) / 3; } // the first ns at which ticks_now() reaches ticks
void drain(uint64_t now) { while (!guest_ends.empty() && guest_ends.front() <= now) guest_ends.pop_front(); }
// N64R_AUDIO_OUT=<path>: every queued block appended as raw s16 stereo (the port's N64C_AUDIO_OUT layout); <path>.rate
// holds "<rate>\n", replaced whole when the game sets the frequency. No wall-time cap: the oracle runs in guest time,
// so the file holds all the sound of the run at whatever speed it replays.
std::FILE *tap;
bool tap_open;
std::string tap_path;
void tap_init() {
    if (tap_open) return;
    tap_open = true;
    const char *path = std::getenv("N64R_AUDIO_OUT");
    if (!path || !*path) return;
    tap_path = path;
    tap = std::fopen(path, "ab");
    if (!tap) std::fprintf(stderr, "n64host: cannot write N64R_AUDIO_OUT %s\n", path);
}
void tap_rate(uint32_t f) {
    tap_init();
    if (!tap) return;
    const std::string tmp = tap_path + ".rate.tmp";
    std::FILE *r = std::fopen(tmp.c_str(), "w");
    if (!r) return;
    std::fprintf(r, "%u\n", f);
    std::fclose(r);
    std::rename(tmp.c_str(), (tap_path + ".rate").c_str());
}
void tap_write(const int16_t *s, size_t n) {
    tap_init();
    if (tap && (std::fwrite(s, sizeof *s, n, tap) != n || std::fflush(tap))) { std::fclose(tap); tap = nullptr; }
}
[[noreturn]] void fail(const char *operation) {
    std::fprintf(stderr, "n64host: audio %s failed: %s\n", operation, SDL_GetError());
    std::_Exit(3);
}
void open_dev() {
    if (dev) SDL_CloseAudioDevice(dev);
    SDL_AudioSpec want{}, have{};
    want.freq = rate; want.format = AUDIO_S16SYS; want.channels = 2; want.samples = 512;
    dev = SDL_OpenAudioDevice(nullptr, 0, &want, &have, 0);
    if (!dev) fail("open default device");
    SDL_PauseAudioDevice(dev, 0);
    std::fprintf(stderr, "n64host: audio driver=%s rate=%d channels=%u default_device=1\n",
                 SDL_GetCurrentAudioDriver(), have.freq, have.channels);
}
void queue(int16_t *s, size_t n) {
    n64host_golden_pcm(s, n);
    tap_write(s, n);
    std::lock_guard lock(audio_mutex);
    if (!dev) open_dev();
    if (n > UINT32_MAX / sizeof(int16_t) || SDL_QueueAudio(dev, s, Uint32(n * sizeof(int16_t))) != 0)
        fail("queue");
    const uint64_t now = ticks_now();
    drain(now);
    const uint64_t end = (guest_ends.empty() ? now : guest_ends.back()) + (uint64_t(n / 2) * counter_hz + rate - 1) / rate;
    guest_ends.push_back(end);
    ultramodern::vclock_schedule(tick_ns(end), ultramodern::ai_buffer_done);
    const auto previous = samples;
    samples += n;
    for (size_t i=0; i<n; ++i) nonzero += s[i] != 0;
    if (!previous || previous / (rate * 20u) != samples / (rate * 20u))
        std::fprintf(stderr, "n64host: audio queued_samples=%llu nonzero_samples=%llu\n",
            (unsigned long long)samples, (unsigned long long)nonzero);
}
size_t remaining() {
    std::lock_guard lock(audio_mutex);
    const uint64_t now = ticks_now();
    drain(now);
    return guest_ends.empty() ? 0 : size_t((guest_ends.back() - now) * rate / counter_hz);
}
uint32_t status() {
    std::lock_guard lock(audio_mutex);
    drain(ticks_now());
    return (guest_ends.empty() ? 0 : 0x40000000u) | (guest_ends.size() >= 2 ? 0x80000000u : 0);
}
void set_freq(uint32_t f) { n64host_golden_rate(f); tap_rate(f); std::lock_guard lock(audio_mutex); rate = f; open_dev(); }
}
ultramodern::audio_callbacks_t host_audio_callbacks() { return {queue, remaining, set_freq}; }

// The pinned runtime reports AI permanently idle. Original producers rely on its two-buffer FIFO.
extern "C" void __real_osAiSetNextBuffer_recomp(uint8_t*, recomp_context*);
extern "C" void __wrap_osAiGetStatus_recomp(uint8_t*, recomp_context *ctx) {
    ctx->r2 = status();
}
extern "C" void __wrap_osAiSetNextBuffer_recomp(uint8_t *rdram, recomp_context *ctx) {
    if (status() & 0x80000000u) { ctx->r2 = (uint64_t)-1; return; }
    __real_osAiSetNextBuffer_recomp(rdram, ctx);
}
