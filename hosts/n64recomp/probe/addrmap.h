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
#ifdef __cplusplus
}
#endif
static inline uint8_t *n64host_ptr(uint8_t *rdram, uint64_t va) {
    uint32_t a = (uint32_t)va;
    return (a - 0x80000000u) < 0x20000000u ? rdram + (a - 0x80000000u) : n64host_xlate_slow(rdram, a);
}
#undef MEM_W
#undef MEM_H
#undef MEM_B
#undef MEM_HU
#undef MEM_BU
#undef SD
#undef LD
#define MEM_W(offset, reg) (*(int32_t*)n64host_ptr(rdram, (reg) + (offset)))
#define MEM_H(offset, reg) (*(int16_t*)n64host_ptr(rdram, ((reg) + (offset)) ^ 2))
#define MEM_B(offset, reg) (*(int8_t*)n64host_ptr(rdram, ((reg) + (offset)) ^ 3))
#define MEM_HU(offset, reg) (*(uint16_t*)n64host_ptr(rdram, ((reg) + (offset)) ^ 2))
#define MEM_BU(offset, reg) (*(uint8_t*)n64host_ptr(rdram, ((reg) + (offset)) ^ 3))
#define SD(val, offset, reg) { \
    *(uint32_t*)n64host_ptr(rdram, (reg) + (offset) + 4) = (uint32_t)((gpr)(val) >> 0); \
    *(uint32_t*)n64host_ptr(rdram, (reg) + (offset) + 0) = (uint32_t)((gpr)(val) >> 32); \
}
#define LD(offset, reg) \
    (((uint64_t)(uint32_t)MEM_W((offset) + 4, reg)) | ((uint64_t)(uint32_t)MEM_W((offset), reg) << 32))
#undef LOOKUP_FUNC
#define LOOKUP_FUNC(val) get_function(n64host_func_vram((int32_t)(val)))

#ifdef __RECOMP_H__
#include "unaligned.h"
#endif
