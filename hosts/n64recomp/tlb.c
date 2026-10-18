// Mutable R4300 paired pages. Only the emulated CPU accesses this state.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "host_profile.h"
#include "tlb_profile.h"
#include "tlb.h"
static struct { uint32_t va, page, pa[2]; } entries[32];
static void fail(const char *why) {
    fprintf(stderr, "n64host: TLB %s\n", why); _Exit(4);
}
void n64host_tlb_unmap(uint32_t i) {
    if (i >= 32) fail("index outside 0..31");
    entries[i].page = 0;
}
void n64host_tlb_map(uint32_t i, uint32_t mask, uint32_t va, uint32_t even, uint32_t odd) {
    if (i >= 32) fail("index outside 0..31");
    switch (mask) {
        case 0: case 0x6000: case 0x1e000: case 0x7e000:
        case 0x1fe000: case 0x7fe000: case 0x1ffe000: break;
        default: fail("invalid page mask");
    }
    uint32_t page = (mask + 0x2000) / 2;
    if (va & (page * 2 - 1)) fail("misaligned virtual pair");
    uint32_t halves[2] = {even, odd};
    for (unsigned j = 0; j < 2; ++j)
        if (halves[j] != UINT32_MAX &&
            ((halves[j] & (page - 1)) || (uint64_t)halves[j] + page > HOST_RDRAM_SIZE))
            fail("physical page outside RDRAM or misaligned");
    entries[i].va = va; entries[i].page = page;
    entries[i].pa[0] = even; entries[i].pa[1] = odd;
}
void n64host_tlb_init(void) {
    memset(entries, 0, sizeof(entries));
    for (unsigned i = 0; i < HOST_BOOT_TLB_COUNT; ++i) {
        const uint32_t *p = host_boot_tlb[i];
        n64host_tlb_map(p[0], p[1], p[2], p[3], p[4]);
    }
}
uint32_t n64host_tlb_probe(uint32_t va) {
    uint32_t found = UINT32_MAX;
    for (unsigned i = 0; i < 32; ++i) {
        uint32_t page = entries[i].page, off = va - entries[i].va;
        if (!page || off / 2 >= page) continue;
        uint32_t pa = entries[i].pa[off / page];
        if (pa == UINT32_MAX) continue;
        if (found != UINT32_MAX) fail("overlapping valid entries");
        found = pa + off % page;
    }
    return found;
}
uint32_t n64host_guest_translate(uint8_t *ram, uint32_t va) {
    uint32_t pa = n64host_tlb_probe(va);
    if (pa != UINT32_MAX) return pa;
    n64host_tlb_fault(ram, va);
    pa = n64host_tlb_probe(va);
    if (pa == UINT32_MAX) fail("thread resumed without mapping fault address");
    return pa;
}
