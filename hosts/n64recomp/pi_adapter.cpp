// Raw PI DMA adapter for runtimes that only implement the queued PI-manager API.
// The guest owns its worker and OS_EVENT_PI queue. Copy first, then deliver its configured message.
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include "host_profile.h"
#include "librecomp/game.hpp"
#include "librecomp/addresses.hpp"
#include "ultramodern/ultramodern.hpp"
#include "addrmap.h"
#include "tlb.h"

extern size_t n64host_rom_size;
extern "C" void __real_osSetEventMesg_recomp(uint8_t *, recomp_context *);
namespace {
std::mutex pi_mutex;
uint32_t pi_queue, pi_message;
[[noreturn]] void fail(const char *reason) {
    std::fprintf(stderr, "n64host: raw PI DMA: %s\n", reason);
    std::exit(4);
}
}
extern "C" void __wrap_osSetEventMesg_recomp(uint8_t *rdram, recomp_context *ctx) {
    if (ctx->r4 == OS_EVENT_FAULT) n64host_fault_event((uint32_t)ctx->r5, (uint32_t)ctx->r6);
    if (ctx->r4 == OS_EVENT_PI) {
        std::lock_guard lock(pi_mutex);
        pi_queue = (uint32_t)ctx->r5;
        pi_message = (uint32_t)ctx->r6;
    }
    __real_osSetEventMesg_recomp(rdram, ctx);
}
extern "C" void __wrap_osPiRawStartDma_recomp(uint8_t *rdram, recomp_context *ctx) {
    const uint32_t physical = ((uint32_t)ctx->r5 | recomp::rom_base) & 0x1FFFFFFFu;
    const uint32_t length = (uint32_t)ctx->r7;
    const uintptr_t ram = (uintptr_t)n64host_xlate_slow(rdram, (uint32_t)ctx->r6) - (uintptr_t)rdram;
    if (ctx->r4 != 0) fail("only ROM reads are supported");
    if (physical < recomp::rom_base || (uint64_t)(physical - recomp::rom_base) + length > n64host_rom_size)
        fail("source exceeds original ROM");
    if (!length || (physical & 1) || (ram & 7) || ram + length > HOST_RDRAM_SIZE)
        fail("invalid RDRAM range or DMA alignment");
    uint32_t queue, message;
    { std::lock_guard lock(pi_mutex); queue = pi_queue; message = pi_message; }
    if (!queue) fail("OS_EVENT_PI has no registered queue");
    recomp::do_rom_read(rdram, (gpr)(int32_t)(0x80000000u | (uint32_t)ram), physical, length);
    ultramodern::enqueue_external_message_src(queue, message, false, ultramodern::EventMessageSource::Pi);
    ctx->r2 = 0;
}
