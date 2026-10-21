// Guest virtual time from charged CPU work, and cooperative interrupt delivery at guest function entries.
#include "recomp.h"
#include "ultramodern/ultramodern.hpp"
#include "cpu_budget.h"
#include "cpu_profile.h"
#include "cpu_metrics.h"

void dequeue_external_messages(uint8_t *rdram);
int64_t host_thread_cpu_ns();
uint8_t *n64host_rdram;

namespace {
thread_local HostCpuBudget budget;


void service_interrupts() {
    if (!budget.interrupts_enabled) return;
    dequeue_external_messages(n64host_rdram);
    ultramodern::check_running_queue(n64host_rdram);
}
}

// The idle guest thread ran device events while waiting; they are not guest work.
void n64host_cpu_idle_return() {
    budget.last_cpu_ns = host_thread_cpu_ns();
}

extern "C" void n64host_cpu_tick() {
    if (!n64host_rdram || !ultramodern::this_thread()) return;
    const auto cpu_ns = host_thread_cpu_ns();
    host_cpu_metrics.work(budget.last_cpu_ns, cpu_ns);
    // Guest time advances by the work just done; devices due in that span fire in time order.
    ultramodern::vclock_advance(budget.charge(cpu_ns, HOST_NATIVE_CPU_SCALE));
    service_interrupts();
    // Do not charge device events or scheduler work as another piece of guest work.
    budget.last_cpu_ns = host_thread_cpu_ns();
}

extern "C" void __wrap___osDisableInt_recomp(uint8_t *, recomp_context *ctx) {
    ctx->r2 = budget.disable_interrupts();
}

extern "C" void __wrap___osRestoreInt_recomp(uint8_t *, recomp_context *ctx) {
    budget.restore_interrupts(uint32_t(ctx->r4));
    if (budget.interrupts_enabled) n64host_cpu_tick();
}
