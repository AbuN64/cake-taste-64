// Export the runtime counter frequency through the guest osInitialize boundary.
// The pinned HLE initializer does not populate the guest's osClockRate global.
// The Expansion Pak switch lives here too: the runtime always reports 8 MB, N64R_PAK=off makes the guest see the
// 4 MB console (osMemSize and osGetMemSize); on or unset leaves the runtime's 8 MB untouched.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "recomp.h"
#include "boot_profile.h"

void __real_osInitialize_recomp(uint8_t *, recomp_context *);
void __real___osInitialize_common_recomp(uint8_t *, recomp_context *);
void __real_osGetMemSize_recomp(uint8_t *, recomp_context *);

static uint32_t ram_bytes(void) {
    const char *pak = getenv("N64R_PAK");
    if (!pak || !*pak || !strcmp(pak, "on")) return 0x800000u;
    if (!strcmp(pak, "off")) return 0x400000u;
    fprintf(stderr, "n64host: N64R_PAK must be on or off, got %s\n", pak);
    exit(2);
}
static void publish_memory(uint8_t *rdram) {
    const uint32_t bytes = ram_bytes();
    if (bytes != 0x800000u) MEM_W(0, (int64_t)(int32_t)0x80000318u) = bytes;  // osMemSize, set by the IPL3 on a console
}

static void publish_clock(uint8_t *rdram) {
    // RDRAM stores each word in host order; an N64 u64 has the high word first.
    const int64_t address = (int32_t)HOST_OS_CLOCK_RATE;
    MEM_W(0, address) = 0;
    MEM_W(4, address) = HOST_COUNT_HZ;
}

void __wrap_osInitialize_recomp(uint8_t *rdram, recomp_context *ctx) {
    __real_osInitialize_recomp(rdram, ctx);
    publish_clock(rdram);
    publish_memory(rdram);
}

void __wrap___osInitialize_common_recomp(uint8_t *rdram, recomp_context *ctx) {
    __real___osInitialize_common_recomp(rdram, ctx);
    publish_clock(rdram);
    publish_memory(rdram);
}
void __wrap_osGetMemSize_recomp(uint8_t *rdram, recomp_context *ctx) {
    __real_osGetMemSize_recomp(rdram, ctx);
    ctx->r2 = ram_bytes();
}
