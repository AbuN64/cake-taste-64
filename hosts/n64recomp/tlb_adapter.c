// Guest ABI: the profile qualifies the sixth argument as global EntryLo flags.
#include "recomp.h"
#include "addrmap.h"
#include "tlb.h"
#include <stdio.h>
#include <stdlib.h>
void osMapTLB_recomp(uint8_t *rdram, recomp_context *ctx) {
    uint32_t odd = *(uint32_t *)n64host_xlate_slow(rdram, (uint32_t)ctx->r29 + 16);
    uint32_t flags = *(uint32_t *)n64host_xlate_slow(rdram, (uint32_t)ctx->r29 + 20);
    if ((flags & 7) != 7) {
        fprintf(stderr, "n64host: TLB mapping requires global valid writable pages\n");
        _Exit(4);
    }
    n64host_tlb_map((uint32_t)ctx->r4, (uint32_t)ctx->r5, (uint32_t)ctx->r6,
                   (uint32_t)ctx->r7, odd);
}
void osUnmapTLB_recomp(uint8_t *rdram, recomp_context *ctx) {
    (void)rdram; n64host_tlb_unmap((uint32_t)ctx->r4);
}
void __wrap_osUnmapTLBAll_recomp(uint8_t *rdram, recomp_context *ctx) {
    (void)rdram; (void)ctx;
    // SDK retains the wired boot entry31.
    for (unsigned i = 0; i < 31; ++i) n64host_tlb_unmap(i);
}
void __osProbeTLB_recomp(uint8_t *rdram, recomp_context *ctx) {
    (void)rdram;
    ctx->r2 = (gpr)(int32_t)n64host_tlb_probe((uint32_t)ctx->r4);
}
