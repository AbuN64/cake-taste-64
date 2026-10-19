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

#define TRACE_ENTRY() behave_enter(__func__, rdram, ctx);
#define TRACE_RETURN()
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
