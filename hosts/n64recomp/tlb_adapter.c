// Validate SDK mappings against the profile-backed translation used by all guest accesses.
// Runtime changes outside that declared map must fail before guest execution continues.
#include "recomp.h"
#include "host_profile.h"
#include "addrmap.h"
#include <stdio.h>
#include <stdlib.h>

static void fail(void) {
    fprintf(stderr, "n64host: osMapTLB mapping is outside the declared profile TLB map\n");
    exit(4);
}
static void validate_page(uint32_t va, uint32_t pa, uint32_t size) {
    if (pa == UINT32_MAX) return; // SDK sentinel for an invalid half-entry.
    if ((va & (size - 1)) || (pa & (size - 1))) fail();
    for (unsigned i = 0; i < HOST_TLB_COUNT; ++i) {
        uint32_t offset = va - host_tlb[i][0];
        if (offset < host_tlb[i][2] && size <= host_tlb[i][2] - offset &&
            pa == host_tlb[i][1] + offset) return;
    }
    fail();
}
void osMapTLB_recomp(uint8_t *rdram, recomp_context *ctx) {
    uint32_t mask = (uint32_t)ctx->r5;
    switch (mask) {
        case 0: case 0x6000: case 0x1e000: case 0x7e000:
        case 0x1fe000: case 0x7fe000: case 0x1ffe000: break;
        default: fail();
    }
    if ((uint32_t)ctx->r4 >= 32) fail();
    uint32_t page = (mask + 0x2000) / 2;
    uint32_t va = (uint32_t)ctx->r6;
    if (va & (page * 2 - 1)) fail();
    uint32_t odd = *(uint32_t *)n64host_xlate_slow(rdram, (uint32_t)ctx->r29 + 16);
    validate_page(va, (uint32_t)ctx->r7, page);
    validate_page(va + page, odd, page);
}
