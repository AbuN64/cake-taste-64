// Host address map runtime (see probe/addrmap.h). Mappings come from the profile via host_profile.h.
#include <stdint.h>
#include <sys/mman.h>
#include "host_profile.h"
#include "addrmap.h"
#include <stdio.h>
#include <stdlib.h>
extern void n64host_tlb_init(void) __attribute__((weak));
extern uint32_t n64host_guest_translate(uint8_t*, uint32_t) __attribute__((weak));
#define SHADOW_SIZE 0x20000000u
static uint8_t *shadow;
void n64host_addrmap_init(void) {
    if (n64host_tlb_init) n64host_tlb_init();
    shadow = mmap(0, SHADOW_SIZE, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE | MAP_NORESERVE, -1, 0);
}
uint8_t *n64host_xlate_slow(uint8_t *rdram, uint32_t va) {
    uint32_t pa;
    if (va >= 0x80000000u && va < 0xC0000000u) pa = va & 0x1FFFFFFFu;
    else if (va >= 0xC0000000u) pa = va; // Existing non-RDRAM/device shadow boundary.
    else if (n64host_guest_translate) pa = n64host_guest_translate(rdram, va);
    else {
        pa = UINT32_MAX; // Bounded probes have static KUSEG mappings and no guest pager.
        for (unsigned i = 0; i < HOST_TLB_COUNT; ++i)
            if (va - host_tlb[i][0] < host_tlb[i][2]) { pa = host_tlb[i][1] + (va - host_tlb[i][0]); break; }
    }
    if (pa == UINT32_MAX) {
        fprintf(stderr, "n64host: unmapped virtual address %08x without guest pager\n", va);
        _Exit(4);
    }
    if (pa < HOST_RDRAM_SIZE) return rdram + pa;
    return shadow + (pa & (SHADOW_SIZE - 1));
}

// Code labels live in KSEG0 form; a branch target inside a mapped KUSEG window is the same function.
int32_t n64host_func_vram(int32_t va) {
    uint32_t a = (uint32_t)va;
    if (a < 0x80000000u)
        for (unsigned i = 0; i < HOST_TLB_COUNT; ++i)
            if (a - host_tlb[i][0] < host_tlb[i][2]) return (int32_t)(a | 0x80000000u);
    return va;
}
