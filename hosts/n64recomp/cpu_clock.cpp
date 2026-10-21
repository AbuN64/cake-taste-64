#include <cstdint>
#include <stdexcept>
#include <time.h>

int64_t host_thread_cpu_ns() {
    timespec value{};
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &value) != 0)
        throw std::runtime_error("cannot read guest thread CPU clock");
    return int64_t(value.tv_sec) * 1000000000 + value.tv_nsec;
}
