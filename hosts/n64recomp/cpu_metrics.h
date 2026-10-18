#pragma once
#include <atomic>
#include <cstdint>

// Counters describe the recompiled workload, not emulated R4300 cycles.
// VI snapshots are approximate by at most the work concurrently being published.
struct HostCpuMetrics {
    std::atomic<uint64_t> entries{0}, native_ns{0};
    void entry() { entries.fetch_add(1, std::memory_order_relaxed); }
    void work(int64_t previous_ns, int64_t current_ns) {
        if (previous_ns >= 0 && current_ns > previous_ns)
            native_ns.fetch_add(uint64_t(current_ns - previous_ns), std::memory_order_relaxed);
    }
};
extern HostCpuMetrics host_cpu_metrics;
void host_cpu_vi(uint32_t count);
