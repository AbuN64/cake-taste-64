// Guest virtual time from the logical work count (RUNTIME.md "Virtual guest time"), and cooperative interrupt
// delivery at guest function entries.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "recomp.h"
#include "ultramodern/ultramodern.hpp"
#include "cpu_budget.h"
#include "cpu_profile.h"
#include "cpu_native.h"

void dequeue_external_messages(uint8_t *rdram);
uint8_t *n64host_rdram;

namespace {
thread_local HostCpuBudget budget;

void service_interrupts() {
    if (!budget.interrupts_enabled) return;
    dequeue_external_messages(n64host_rdram);
    ultramodern::check_running_queue(n64host_rdram);
}
}

// Whether an entry of the recompiled function `name` is guest work: the original game code both sides compile, never
// the SDK range or a function the port binds to native code. N64Recomp suffixes a name that collides with the host's
// C library with _recomp (__udivdi3_recomp); every other recompiled function is in symbols.toml, so an unknown name
// is a broken build.
extern "C" int n64host_charged(const char *name) {
    auto find = [](const char *key, size_t n) {
        size_t lo = 0, hi = sizeof host_functions / sizeof host_functions[0];
        while (lo < hi) {
            size_t mid = (lo + hi) / 2;
            int order = std::strncmp(host_functions[mid].name, key, n);
            if (order == 0 && host_functions[mid].name[n]) order = 1;
            if (order == 0) return int(host_functions[mid].charged);
            if (order < 0) lo = mid + 1; else hi = mid;
        }
        return -1;
    };
    size_t n = std::strlen(name);
    int charged = find(name, n);
    if (charged < 0 && n > 7 && std::strcmp(name + n - 7, "_recomp") == 0) charged = find(name, n - 7);
    if (charged >= 0) return charged;
    std::fprintf(stderr, "n64host: recompiled function %s is not in symbols.toml\n", name);
    std::_Exit(2);
}

// A guest service point: a function entry (charged when it is guest work), a spin loop's 1024th pass or an interrupt
// restore (never charged). Each delivers due interrupts.
extern "C" void n64host_cpu_tick(int charged) {
    if (!n64host_rdram || !ultramodern::this_thread()) return;
    if (charged) ultramodern::vclock_advance(budget.charge_entry(HOST_GUEST_NS_PER_ENTRY));
    service_interrupts();
}

extern "C" void __wrap___osDisableInt_recomp(uint8_t *, recomp_context *ctx) {
    ctx->r2 = budget.disable_interrupts();
}

extern "C" void __wrap___osRestoreInt_recomp(uint8_t *, recomp_context *ctx) {
    budget.restore_interrupts(uint32_t(ctx->r4));
    if (budget.interrupts_enabled) n64host_cpu_tick(0);
}
