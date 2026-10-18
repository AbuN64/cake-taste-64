#include "cpu_metrics.h"
#include <cstdio>

HostCpuMetrics host_cpu_metrics;
void host_cpu_vi(uint32_t count) {
    // Called only by the VI thread. Unsigned subtraction includes Count wrap.
    static uint64_t vi = 0, count_ticks = 0;
    static uint32_t previous_count = count;
    count_ticks += uint32_t(count - previous_count);
    previous_count = count;
    ++vi;
    if (vi == 1 || vi % 60 == 0)
        std::fprintf(stderr,
            "n64host: cpu_cadence vi=%llu count_ticks=%llu entries=%llu native_ns=%llu\n",
            (unsigned long long)vi, (unsigned long long)count_ticks,
            (unsigned long long)host_cpu_metrics.entries.load(std::memory_order_relaxed),
            (unsigned long long)host_cpu_metrics.native_ns.load(std::memory_order_relaxed));
}
