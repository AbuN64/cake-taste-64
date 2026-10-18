#pragma once
// P1 probe: TRACE_ENTRY()/TRACE_RETURN() emitted by N64Recomp trace_mode.
// Writes "frame thread name" lines when N64R_TRACE=<file> is set (no-op otherwise).
#ifdef __cplusplus
extern "C" {
#endif
void n64host_trace(const char *name);
#ifdef __cplusplus
}
#endif
#define TRACE_ENTRY() n64host_trace(__func__);
#define TRACE_RETURN()
