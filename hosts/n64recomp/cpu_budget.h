#pragma once
#include <cstdint>

// Guest time is a logical work count: a fixed number of guest nanoseconds per charged function entry, the
// sub-nanosecond fraction carried. It never reads a host clock, so guest time is the same on every host and load.
struct HostCpuBudget {
    double remainder_ns = 0;
    bool interrupts_enabled = true;

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
