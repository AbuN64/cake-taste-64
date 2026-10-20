// Export the runtime counter frequency through the guest osInitialize boundary.
// The pinned HLE initializer does not populate the guest's osClockRate global.
#include "recomp.h"
#include "boot_profile.h"

void __real_osInitialize_recomp(uint8_t *, recomp_context *);
void __real___osInitialize_common_recomp(uint8_t *, recomp_context *);

static void publish_clock(uint8_t *rdram) {
    // RDRAM stores each word in host order; an N64 u64 has the high word first.
    const int64_t address = (int32_t)HOST_OS_CLOCK_RATE;
    MEM_W(0, address) = 0;
    MEM_W(4, address) = HOST_COUNT_HZ;
}

void __wrap_osInitialize_recomp(uint8_t *rdram, recomp_context *ctx) {
    __real_osInitialize_recomp(rdram, ctx);
    publish_clock(rdram);
}

void __wrap___osInitialize_common_recomp(uint8_t *rdram, recomp_context *ctx) {
    __real___osInitialize_common_recomp(rdram, ctx);
    publish_clock(rdram);
}
