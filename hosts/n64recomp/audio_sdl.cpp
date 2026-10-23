#include <SDL.h>
#include <cstdio>
#include <algorithm>
#include <cstdlib>
#include <deque>
#include <mutex>
#include "librecomp/game.hpp"
#include "ultramodern/ultramodern.hpp"
extern "C" void n64host_golden_pcm(const int16_t *, size_t);
extern "C" void n64host_golden_rate(unsigned);
bool n64host_entries_clock();  // cpu_scheduler.cpp: N64R_CLOCK=entries
namespace {
SDL_AudioDeviceID dev;
uint32_t rate = 32000;
uint64_t samples = 0, nonzero = 0, submitted_bytes = 0;
std::deque<uint64_t> buffer_ends;
std::mutex audio_mutex;
// Under the deterministic clock the AI plays queued samples in guest time, as VI and timers already run, so the
// FIFO the game sees never depends on how fast the host consumes sound. Guest nanoseconds each buffer ends at.
std::deque<uint64_t> guest_ends;
uint64_t guest_end = 0;
[[noreturn]] void fail(const char *operation) {
    std::fprintf(stderr, "n64host: audio %s failed: %s\n", operation, SDL_GetError());
    std::_Exit(3);
}
void open_dev() {
    if (dev) SDL_CloseAudioDevice(dev);
    submitted_bytes=0; buffer_ends.clear();
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
    std::lock_guard lock(audio_mutex);
    if (!dev) open_dev();
    if (n > UINT32_MAX / sizeof(int16_t) || SDL_QueueAudio(dev, s, Uint32(n * sizeof(int16_t))) != 0)
        fail("queue");
    submitted_bytes += n * sizeof(int16_t);
    buffer_ends.push_back(submitted_bytes);
    if (n64host_entries_clock()) {
        guest_end = std::max(guest_end, ultramodern::vclock_now_ns()) + uint64_t(n / 2) * 1000000000ull / rate;
        guest_ends.push_back(guest_end);
    }
    const auto previous = samples;
    samples += n;
    for (size_t i=0; i<n; ++i) nonzero += s[i] != 0;
    if (!previous || previous / (rate * 20u) != samples / (rate * 20u))
        std::fprintf(stderr, "n64host: audio queued_samples=%llu nonzero_samples=%llu\n",
            (unsigned long long)samples, (unsigned long long)nonzero);
}
size_t remaining() {
    std::lock_guard lock(audio_mutex);
    if (n64host_entries_clock()) {
        const uint64_t now = ultramodern::vclock_now_ns();
        return guest_end > now ? size_t((guest_end - now) * rate / 1000000000ull) : 0;
    }
    return dev ? SDL_GetQueuedAudioSize(dev) / (2 * sizeof(int16_t)) : 0;
}
uint32_t status() {
    std::lock_guard lock(audio_mutex);
    if (n64host_entries_clock()) {
        const uint64_t now = ultramodern::vclock_now_ns();
        while (!guest_ends.empty() && guest_ends.front() <= now) guest_ends.pop_front();
        return (guest_ends.empty() ? 0 : 0x40000000u) | (guest_ends.size() >= 2 ? 0x80000000u : 0);
    }
    const auto pending = dev ? SDL_GetQueuedAudioSize(dev) : 0;
    const auto consumed = submitted_bytes - pending;
    while (!buffer_ends.empty() && buffer_ends.front() <= consumed) buffer_ends.pop_front();
    return (pending ? 0x40000000u : 0) | (buffer_ends.size() >= 2 ? 0x80000000u : 0);
}
void set_freq(uint32_t f) { n64host_golden_rate(f); std::lock_guard lock(audio_mutex); rate = f; open_dev(); }
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
