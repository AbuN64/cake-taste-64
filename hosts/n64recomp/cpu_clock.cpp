#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <thread>
#include <time.h>

int64_t host_thread_cpu_ns() {
    timespec value{};
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &value) != 0)
        throw std::runtime_error("cannot read guest thread CPU clock");
    return int64_t(value.tv_sec) * 1000000000 + value.tv_nsec;
}

int64_t host_cpu_wait_ns(int64_t duration_ns) {
    const auto before = std::chrono::steady_clock::now();
    std::this_thread::sleep_for(std::chrono::nanoseconds(duration_ns));
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now() - before).count();
}
