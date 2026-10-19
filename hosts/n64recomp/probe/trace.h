#pragma once
// P1 probe: TRACE_ENTRY()/TRACE_RETURN() emitted by N64Recomp trace_mode.
// Writes "frame thread name" lines when N64R_TRACE=<file> is set (no-op otherwise).
#ifdef __cplusplus
extern "C" {
#endif
void n64host_trace(const char *name);
void n64host_cpu_tick(void);
#ifdef __cplusplus
}
#endif
#define TRACE_ENTRY() n64host_trace(__func__);
#define TRACE_RETURN()
// Backward branch: a guest spin-wait never reaches a function entry, so service interrupts every 1024th pass.
#define TRACE_LOOP() { static __thread unsigned n64host_spin; if ((++n64host_spin & 1023u) == 0) n64host_cpu_tick(); }
