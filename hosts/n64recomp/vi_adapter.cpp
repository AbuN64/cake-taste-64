// The pinned runtime leaves osCreateViManager empty. A VI mode must exist before
// its next-state message queue can become current and deliver the first retrace.
#include <mutex>
#include "recomp.h"
#include "ultramodern/ultramodern.hpp"
#include "boot_profile.h"
extern "C" void __wrap_osCreateViManager_recomp(uint8_t *rdram, recomp_context *) {
    static std::once_flag initialized;
    std::call_once(initialized, [rdram] {
        osViSetMode(rdram, (int32_t)HOST_INITIAL_VI_MODE);
        osViBlack(1);
    });
}
