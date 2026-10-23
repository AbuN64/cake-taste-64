#pragma once
// Symmetric address adapter: every generated guest load/store resolves its virtual address through
// the host address map (KSEG0/KSEG1 -> RDRAM, profile TLB mappings for KUSEG, other physical
// addresses -> a zero-filled device shadow). Generated C and vendor headers stay untouched; this
// header is included after recomp.h and re-defines the accessors.
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
uint8_t *n64host_xlate_slow(uint8_t *rdram, uint32_t va);
int32_t n64host_func_vram(int32_t va);
// Function-replay recorder (replay.cpp): while a sampled call runs on this thread n64host_rec_active is non-zero and
// every guest access goes through it; otherwise the accessors below are the plain translation.
extern __thread int n64host_rec_active;
uint8_t *n64host_replay_ptr(uint8_t *rdram, uint32_t va, int width);
uint64_t n64host_replay_ld(uint8_t *rdram, uint32_t va);
void n64host_replay_sd(uint8_t *rdram, uint32_t va, uint64_t value);
void n64host_replay_taint(const char *why);
#ifdef __cplusplus
}
#endif
static inline uint8_t *n64host_ptr(uint8_t *rdram, uint64_t va) {
    uint32_t a = (uint32_t)va;
    return (a - 0x80000000u) < 0x20000000u ? rdram + (a - 0x80000000u) : n64host_xlate_slow(rdram, a);
}
// The pointer for a guest access of `width` bytes at va (byte and halfword accesses address the host-order word
// through the xor of the lanes).
static inline uint8_t *n64host_acc(uint8_t *rdram, uint64_t va, int width) {
    if (__builtin_expect(n64host_rec_active, 0)) return n64host_replay_ptr(rdram, (uint32_t)va, width);
    return n64host_ptr(rdram, (uint32_t)va ^ (width == 1 ? 3u : width == 2 ? 2u : 0u));
}
static inline uint64_t n64host_ld(uint8_t *rdram, uint64_t va) {
    if (__builtin_expect(n64host_rec_active, 0)) return n64host_replay_ld(rdram, (uint32_t)va);
    return ((uint64_t)*(uint32_t *)n64host_ptr(rdram, va + 4)) | ((uint64_t)*(uint32_t *)n64host_ptr(rdram, va) << 32);
}
static inline void n64host_sd(uint8_t *rdram, uint64_t va, uint64_t value) {
    if (__builtin_expect(n64host_rec_active, 0)) { n64host_replay_sd(rdram, (uint32_t)va, value); return; }
    *(uint32_t *)n64host_ptr(rdram, va + 4) = (uint32_t)value;
    *(uint32_t *)n64host_ptr(rdram, va) = (uint32_t)(value >> 32);
}
#undef MEM_W
#undef MEM_H
#undef MEM_B
#undef MEM_HU
#undef MEM_BU
#undef SD
#undef LD
#define MEM_W(offset, reg) (*(int32_t*)n64host_acc(rdram, (reg) + (offset), 4))
#define MEM_H(offset, reg) (*(int16_t*)n64host_acc(rdram, (reg) + (offset), 2))
#define MEM_B(offset, reg) (*(int8_t*)n64host_acc(rdram, (reg) + (offset), 1))
#define MEM_HU(offset, reg) (*(uint16_t*)n64host_acc(rdram, (reg) + (offset), 2))
#define MEM_BU(offset, reg) (*(uint8_t*)n64host_acc(rdram, (reg) + (offset), 1))
#define SD(val, offset, reg) n64host_sd(rdram, (reg) + (offset), (uint64_t)(gpr)(val))
#define LD(offset, reg) n64host_ld(rdram, (reg) + (offset))
#undef LOOKUP_FUNC
#define LOOKUP_FUNC(val) get_function(n64host_func_vram((int32_t)(val)))

#ifdef __RECOMP_H__
#include "unaligned.h"
#endif
