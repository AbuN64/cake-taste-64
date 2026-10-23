#pragma once
#include <cstdint>

// Guest CPU time is native thread CPU time scaled to the original CPU. It is independent of wall
// time spent blocked or preempted, so guest time never depends on host speed or load.
// A scale of N models N nanoseconds of guest work per native CPU nanosecond.
struct HostCpuBudget {
    int64_t last_cpu_ns = -1;
    double remainder_ns = 0;
    bool interrupts_enabled = true;

    // Returns the whole guest nanoseconds that elapsed since the previous reading.
    uint64_t charge(int64_t cpu_ns, double scale) {
        if (last_cpu_ns >= 0 && cpu_ns > last_cpu_ns)
            remainder_ns += double(cpu_ns - last_cpu_ns) * scale;
        last_cpu_ns = cpu_ns;
        const uint64_t whole = uint64_t(remainder_ns);
        remainder_ns -= double(whole);
        return whole;
    }
    // The deterministic clock: one workload unit of `ns` guest nanoseconds, the fraction carried.
    uint64_t charge_entry(double ns) {
        remainder_ns += ns;
        const uint64_t whole = uint64_t(remainder_ns);
        remainder_ns -= double(whole);
        return whole;
    }
    uint32_t disable_interrupts() {
        const uint32_t previous = interrupts_enabled;
        interrupts_enabled = false;
        return previous;
    }
    void restore_interrupts(uint32_t previous) {
        // The SDK restores by ORing the saved IE bit, including nested callers.
        interrupts_enabled = interrupts_enabled || (previous & 1);
    }
};
