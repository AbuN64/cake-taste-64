#pragma once
// Bounded probe interface shared by the harness and the selected closure unit (see harness.c).
#include <stdint.h>
#include "recomp.h"
typedef struct {
    uint32_t vram;
    recomp_func_t *fn;
    const char *name;
} probe_func;
void probe_trace(const char *name);
void probe_stub(const char *name);
