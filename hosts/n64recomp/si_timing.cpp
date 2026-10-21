// SI completion is a device event in guest time, never an inline guest SDK return.
#include <cstdint>
#include "ultramodern/ultramodern.hpp"

void host_si_schedule(void (*callback)(), uint64_t duration_ns) {
    ultramodern::vclock_schedule(ultramodern::vclock_now_ns() + duration_ns, callback);
}
