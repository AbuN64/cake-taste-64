#pragma once
// N64Recomp trace_mode hooks for bounded probes: ordered entry names go to the probe trace file.
void probe_trace(const char *name);
#define TRACE_ENTRY() probe_trace(__func__);
#define TRACE_RETURN()
#define TRACE_LOOP()
