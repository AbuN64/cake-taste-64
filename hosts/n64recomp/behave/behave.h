#pragma once
// Behaviour probe interface shared by the harness and the closure unit (see harness.c). It reuses the host
// address map and adds an event record: every call entry (with its argument registers), every stub hit, every
// guest load and store. The generated C stays untouched; the accessors are redefined here after recomp.h.
#include <stdint.h>
#include "recomp.h"
#include "addrmap.h"

typedef struct {
    uint32_t vram;
    recomp_func_t *fn;
    const char *name;
} probe_func;

#ifdef __cplusplus
extern "C" {
#endif
void behave_enter(const char *name, uint8_t *rdram, recomp_context *ctx);
void behave_stub(const char *name, uint8_t *rdram, recomp_context *ctx);
uint8_t *behave_ptr(uint8_t *rdram, uint64_t va, int width);
#ifdef __cplusplus
}
#endif

// The unaligned load/store helpers (lwl/lwr/swl/swr and the doubleword forms) translate their aligned block through
// the host map directly, so a wild pointer ended the whole harness (exit 4) instead of the case, and their reads
// and stores were never recorded. Here each helper takes its aligned block from behave_ptr, like every other
// access: an unmapped block stops the case (or reads zero when lenient), mmio stops it, and the access is an event.
#undef do_lwl
#undef do_lwr
#undef do_ldl
#undef do_ldr
#undef do_swl
#undef do_swr
#undef do_sdl
#undef do_sdr
#define BEHAVE_UNALIGNED_LOAD(op, mask) \
    static inline gpr behave_##op(uint8_t *rdram, gpr initial, gpr offset, gpr reg) { \
        uint32_t address = (uint32_t)(offset + reg); \
        uint8_t *block = behave_ptr(rdram, (uint64_t)(address & ~(uint32_t)(mask)), 4); \
        return do_##op(block, initial, 0, (gpr)(int32_t)(0x80000000u | (address & (mask)))); \
    }
#define BEHAVE_UNALIGNED_STORE(op, mask) \
    static inline void behave_##op(uint8_t *rdram, gpr offset, gpr reg, gpr value) { \
        uint32_t address = (uint32_t)(offset + reg); \
        uint8_t *block = behave_ptr(rdram, (uint64_t)(address & ~(uint32_t)(mask)), 4); \
        do_##op(block, 0, (gpr)(int32_t)(0x80000000u | (address & (mask))), value); \
    }
BEHAVE_UNALIGNED_LOAD(lwl, 3u)
BEHAVE_UNALIGNED_LOAD(lwr, 3u)
BEHAVE_UNALIGNED_LOAD(ldl, 7u)
BEHAVE_UNALIGNED_LOAD(ldr, 7u)
BEHAVE_UNALIGNED_STORE(swl, 3u)
BEHAVE_UNALIGNED_STORE(swr, 3u)
BEHAVE_UNALIGNED_STORE(sdl, 7u)
BEHAVE_UNALIGNED_STORE(sdr, 7u)
#define do_lwl behave_lwl
#define do_lwr behave_lwr
#define do_ldl behave_ldl
#define do_ldr behave_ldr
#define do_swl behave_swl
#define do_swr behave_swr
#define do_sdl behave_sdl
#define do_sdr behave_sdr

#define TRACE_ENTRY() behave_enter(__func__, rdram, ctx);
#define TRACE_RETURN()
#define TRACE_LOOP() /* trace-mode backward branches; a spinning original is bounded by the case timeout */
#define probe_stub(name) behave_stub(name, rdram, ctx)

#undef MEM_W
#undef MEM_H
#undef MEM_B
#undef MEM_HU
#undef MEM_BU
#undef SD
#undef LD
#define MEM_W(offset, reg) (*(int32_t*)behave_ptr(rdram, (reg) + (offset), 4))
#define MEM_H(offset, reg) (*(int16_t*)behave_ptr(rdram, (reg) + (offset), 2))
#define MEM_B(offset, reg) (*(int8_t*)behave_ptr(rdram, (reg) + (offset), 1))
#define MEM_HU(offset, reg) (*(uint16_t*)behave_ptr(rdram, (reg) + (offset), 2))
#define MEM_BU(offset, reg) (*(uint8_t*)behave_ptr(rdram, (reg) + (offset), 1))
#define SD(val, offset, reg) { \
    *(uint32_t*)behave_ptr(rdram, (reg) + (offset) + 4, 4) = (uint32_t)((gpr)(val) >> 0); \
    *(uint32_t*)behave_ptr(rdram, (reg) + (offset) + 0, 4) = (uint32_t)((gpr)(val) >> 32); \
}
#define LD(offset, reg) \
    (((uint64_t)(uint32_t)MEM_W((offset) + 4, reg)) | ((uint64_t)(uint32_t)MEM_W((offset), reg) << 32))
