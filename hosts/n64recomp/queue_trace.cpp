// Optional SDK queue boundary trace. No guest memory or scheduling changes.
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <execinfo.h>
#include <mutex>
#include "librecomp/game.hpp"
#include "ultramodern/ultramodern.hpp"
#include "addrmap.h"
#include "host_profile.h"
namespace {
struct Trace {
    FILE *file = nullptr;
    uint32_t address = 0;
    std::mutex mutex;
    Trace() {
        const char *path = std::getenv("N64R_QUEUE_TRACE");
        if (!path || !*path) return;
        const char *value = std::getenv("N64R_QUEUE_ADDRESS");
        char *end = nullptr;
        errno = 0;
        auto parsed = value ? std::strtoull(value, &end, 0) : 0;
        if (!value || end == value || *end || errno || parsed > UINT32_MAX || (parsed & 3)) {
            std::fprintf(stderr, "n64host: queue trace needs an aligned N64R_QUEUE_ADDRESS\n");
            std::_Exit(4);
        }
        address = uint32_t(parsed);
        file = std::fopen(path, "w");
        if (!file) {
            std::fprintf(stderr, "n64host: cannot open queue trace\n");
            std::_Exit(4);
        }
        setvbuf(file, nullptr, _IOLBF, 0);
    }
};
void record(uint8_t *ram, recomp_context *ctx, const char *name, const char *phase, bool queue_arg) {
    static Trace trace;
    if (!trace.file || (queue_arg && uint32_t(ctx->r4) != trace.address)) return;
    uint32_t words[6];
    const auto *queue = n64host_xlate_slow(ram, trace.address);
    const uintptr_t offset = uintptr_t(queue) - uintptr_t(ram);
    if (offset > HOST_RDRAM_SIZE - sizeof(words)) {
        std::fprintf(stderr, "n64host: queue trace address exceeds RDRAM\n");
        std::_Exit(4);
    }
    std::memcpy(words, queue, sizeof(words));
    const auto ticks = std::chrono::steady_clock::now().time_since_epoch();
    const auto micros = std::chrono::duration_cast<std::chrono::microseconds>(ticks).count();
    void *frames[10];
    const int depth = std::strcmp(name, "osRecvMesg") == 0 && std::strcmp(phase, "enter") == 0
        ? backtrace(frames, 10) : 0;
    std::lock_guard lock(trace.mutex);
    std::fprintf(trace.file,
        "{\"us\":%lld,\"name\":\"%s\",\"phase\":\"%s\",\"thread\":%u,"
        "\"a0\":%u,\"a1\":%u,\"a2\":%u,\"v0\":%u,"
        "\"queue\":[%u,%u,%u,%u,%u,%u],\"stack\":[",
        (long long)micros, name, phase, uint32_t(ultramodern::this_thread()),
        uint32_t(ctx->r4),uint32_t(ctx->r5),uint32_t(ctx->r6),uint32_t(ctx->r2),
        words[0],words[1],words[2],words[3],words[4],words[5]);
    for (int i=0; i<depth; ++i) std::fprintf(trace.file, "%s%llu", i ? "," : "",
        (unsigned long long)(uintptr_t)frames[i]);
    std::fputs("]}\n", trace.file);
}
}
#define WRAP(name, queue_arg) \
extern "C" void __real_##name##_recomp(uint8_t*,recomp_context*); \
extern "C" void __wrap_##name##_recomp(uint8_t *ram,recomp_context *ctx) { \
    record(ram,ctx,#name,"enter",queue_arg); \
    __real_##name##_recomp(ram,ctx); \
    record(ram,ctx,#name,"return",queue_arg); \
}
// Hooks of the golden recorder (weak: absent in unit-test mocks). Send fires before the real call,
// receive after it, so the recorder can pair each delivered message with its sender.
extern "C" void n64host_golden_send(uint8_t *, uint32_t, uint32_t) __attribute__((weak));
extern "C" void n64host_golden_recv(uint8_t *, uint32_t, uint32_t) __attribute__((weak));
extern "C" void __real_osSendMesg_recomp(uint8_t*,recomp_context*);
extern "C" void __wrap_osSendMesg_recomp(uint8_t *ram,recomp_context *ctx) {
    if (n64host_golden_send) n64host_golden_send(ram, uint32_t(ctx->r4), uint32_t(ctx->r5));
    record(ram,ctx,"osSendMesg","enter",true);
    __real_osSendMesg_recomp(ram,ctx);
    record(ram,ctx,"osSendMesg","return",true);
}
extern "C" void __real_osRecvMesg_recomp(uint8_t*,recomp_context*);
extern "C" void __wrap_osRecvMesg_recomp(uint8_t *ram,recomp_context *ctx) {
    const uint32_t queue = uint32_t(ctx->r4), out = uint32_t(ctx->r5);
    record(ram,ctx,"osRecvMesg","enter",true);
    __real_osRecvMesg_recomp(ram,ctx);
    record(ram,ctx,"osRecvMesg","return",true);
    if (n64host_golden_recv && out) n64host_golden_recv(ram, queue, out);
}
WRAP(osCreateMesgQueue,true)
WRAP(osContStartQuery,true)
WRAP(osContStartReadData,true)
WRAP(osContGetQuery,false)
