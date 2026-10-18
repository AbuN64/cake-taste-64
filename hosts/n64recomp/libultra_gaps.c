// Logging stand-ins for libultra internals absent from the pinned N64ModernRuntime.
// Each one is a recorded gap, not an implementation.
#include "recomp.h"
#include <stdio.h>
void n64host_unimplemented(const char *name) {
    static int n; if (n++ < 50) fprintf(stderr, "n64host: unimplemented %s\n", name);
}
#define GAP(f) void f(uint8_t* rdram, recomp_context* ctx) { n64host_unimplemented(#f); }
GAP(osUnmapTLB_recomp) GAP(__osAiDeviceBusy_recomp) GAP(__osDpDeviceBusy_recomp)
GAP(__osSpDeviceBusy_recomp) GAP(__osInsertTimer_recomp)
// Device-state queries: report idle / no TLB match so callers proceed.
void __osSiDeviceBusy_recomp(uint8_t* rdram, recomp_context* ctx) { n64host_unimplemented("__osSiDeviceBusy"); ctx->r2 = 0; }
void __osProbeTLB_recomp(uint8_t* rdram, recomp_context* ctx) { n64host_unimplemented("__osProbeTLB"); ctx->r2 = (gpr)-1; }
