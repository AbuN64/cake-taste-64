#pragma once
#include <algorithm>
#include <cstdint>

// CPU-time accounting is independent of wall time spent blocked or preempted.
// A scale of N models N nanoseconds of guest work per native CPU nanosecond.
struct HostCpuBudget {
    int64_t last_cpu_ns = -1;
    double debt_ns = 0;
    bool interrupts_enabled = true;

    void charge(int64_t cpu_ns, double scale) {
        if (last_cpu_ns >= 0 && cpu_ns > last_cpu_ns)
            debt_ns += double(cpu_ns - last_cpu_ns) * (scale - 1.0);
        last_cpu_ns = cpu_ns;
    }
    int64_t slice(int64_t maximum_ns) const {
        return std::min(int64_t(debt_ns), maximum_ns);
    }
    void spent(int64_t elapsed_ns) {
        debt_ns = std::max(0.0, debt_ns - double(elapsed_ns));
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
