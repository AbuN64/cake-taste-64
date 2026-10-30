#pragma once
// P1 probe: TRACE_ENTRY()/TRACE_RETURN() emitted by N64Recomp trace_mode.
// Writes "frame thread name" lines when N64R_TRACE=<file> is set (no-op otherwise).
// Lockstep (lockstep.cpp, N64R_LOCKSTEP_DIR): page hashes at first entries, first calls' arguments and returns.
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void n64host_trace(const char *name, int charged);
void n64host_cpu_tick(int charged);
int n64host_charged(const char *name);
extern int n64host_lockstep_on;
extern int n64host_replay_on;
void n64host_replay_entry(const char *name, const uint8_t *rdram, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3,
                          uint64_t f12, uint64_t f14, uint32_t sp);
void n64host_replay_return(const char *name, const uint8_t *rdram, uint32_t v0, uint32_t v1, uint64_t f0, uint32_t sp);
void n64host_lockstep_entry(const char *name, const uint8_t *rdram, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3,
                            uint64_t f12, uint64_t f14, uint32_t sp);
void n64host_lockstep_return(const char *name, const uint8_t *rdram, uint32_t v0, uint32_t v1, uint64_t f0, uint32_t sp);
#ifdef __cplusplus
}
#endif
// Each call site resolves once whether its function's entry is guest work (RUNTIME.md "Virtual guest time").
#define TRACE_ENTRY() { static int n64host_site = -1; if (n64host_site < 0) n64host_site = n64host_charged(__func__); \
    n64host_trace(__func__, n64host_site); } \
    if (n64host_lockstep_on) n64host_lockstep_entry(__func__, rdram, (uint32_t)ctx->r4, (uint32_t)ctx->r5, \
        (uint32_t)ctx->r6, (uint32_t)ctx->r7, ctx->f12.u64, ctx->f14.u64, (uint32_t)ctx->r29); \
    if (n64host_replay_on) n64host_replay_entry(__func__, rdram, (uint32_t)ctx->r4, (uint32_t)ctx->r5, \
        (uint32_t)ctx->r6, (uint32_t)ctx->r7, ctx->f12.u64, ctx->f14.u64, (uint32_t)ctx->r29);
#define TRACE_RETURN() \
    if (n64host_replay_on) n64host_replay_return(__func__, rdram, (uint32_t)ctx->r2, (uint32_t)ctx->r3, \
        ctx->f0.u64, (uint32_t)ctx->r29); \
    if (n64host_lockstep_on) n64host_lockstep_return(__func__, rdram, (uint32_t)ctx->r2, (uint32_t)ctx->r3, \
        ctx->f0.u64, (uint32_t)ctx->r29);
// Backward branch: a guest spin-wait never reaches a function entry, so service interrupts every 1024th pass; a loop
// pass is not guest work.
#define TRACE_LOOP() { static __thread unsigned n64host_spin; if ((++n64host_spin & 1023u) == 0) n64host_cpu_tick(0); }
