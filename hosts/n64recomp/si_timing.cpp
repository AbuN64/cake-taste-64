// SI completion is a device event, never an inline guest SDK return.
#include <chrono>
#include <cstdint>
#include <condition_variable>
#include <mutex>
#include <thread>

namespace {
class Completion {
    std::mutex mutex;
    std::condition_variable wake;
    void (*pending)() = nullptr;
    std::chrono::steady_clock::time_point deadline;
    bool stopping = false;
    std::thread worker;
public:
    Completion() : worker([this] {
        std::unique_lock lock(mutex);
        for (;;) {
            wake.wait(lock, [this] { return stopping || pending; });
            if (stopping) return;
            if (wake.wait_until(lock, deadline, [this] { return stopping; })) return;
            auto callback = pending;
            pending = nullptr;
            lock.unlock();
            callback();
            lock.lock();
        }
    }) {}
    ~Completion() {
        { std::lock_guard lock(mutex); stopping = true; }
        wake.notify_one();
        worker.join();
    }
    void submit(void (*callback)(), uint64_t duration_ns) {
        std::lock_guard lock(mutex);
        deadline = std::chrono::steady_clock::now() + std::chrono::nanoseconds(duration_ns);
        pending = callback;
        wake.notify_one();
    }
};
}
void host_si_schedule(void (*callback)(), uint64_t duration_ns) {
    static Completion completion;
    completion.submit(callback, duration_ns);
}
