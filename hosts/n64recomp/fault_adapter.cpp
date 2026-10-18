// Deliver an address fault to the original guest pager, then suspend until it resumes us.
#include <cstdio>
#include <cstdlib>
#include "librecomp/game.hpp"
#include "ultramodern/ultramodern.hpp"
#include "host_profile.h"
#include "tlb_profile.h"
#include "tlb.h"
// Runtime queue primitive schedules receivers but never switches the current thread.
bool do_send(uint8_t *, PTR(OSMesgQueue), OSMesg, bool, bool);
namespace {
uint32_t fault_queue, fault_message;
[[noreturn]] void fail(const char *why, uint32_t va) {
    std::fprintf(stderr, "n64host: guest fault %08x: %s\n", va, why); std::_Exit(4);
}
}
extern "C" void n64host_fault_event(uint32_t queue, uint32_t message) {
    fault_queue = queue; fault_message = message;
}
extern "C" void n64host_tlb_fault(uint8_t *rdram, uint32_t va) {
    uint8_t *ram = rdram;
    const auto self = ultramodern::this_thread();
    if (!self || !fault_queue) fail("no guest thread or OS_EVENT_FAULT receiver", va);
    const uint32_t phys = uint32_t(self) & 0x1fffffff;
    if (phys > HOST_RDRAM_SIZE - 0x128) fail("invalid OSThread", va);
    // The runtime preserves the beginning of OSThread for its host context;
    // original architectural Cause and BadVAddr slots remain available.
    MEM_W(0x120, self) = 8; // ExcCode2: TLB refill/load; address access is retried in C.
    MEM_W(0x124, self) = va;
    MEM_W(0, (int32_t)HOST_FAULTED_THREAD) = self;
    auto *thread = TO_PTR(OSThread, self);
    thread->flags = 2; // OS_FLAG_FAULT
    thread->state = OSThreadState::STOPPED;
    if (!do_send(ram, (PTR(OSMesgQueue))fault_queue, (OSMesg)fault_message, false, false))
        fail("OS_EVENT_FAULT queue full", va);
    static unsigned faults;
    if (faults++ < 8) std::fprintf(stderr, "n64host: TLB fault va=%08x thread=%08x\n", va, uint32_t(self));
    ultramodern::run_next_thread_and_wait(ram);
}
