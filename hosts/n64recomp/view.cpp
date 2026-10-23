// Oracle pictures in the native port's frame protocol: a test instrument, off unless asked. Generic: knows
// nothing about any game. The picture is the colour image the VI shows (osViGetCurrentFramebuffer), read
// through the renderer (render_rt64.cpp n64host_frame_read) and scaled to 320x240 like the port's pictures.
//   N64R_VIEW=<path>     the latest picture as a 24-bit BMP, replaced whole (written beside it, then renamed) at most
//                        N64R_VIEW_FPS (1..120, default 15) times a second of wall time, and once more at a TAS exit.
//   N64R_TAS_SHOT_DIR    with N64R_TAS_SHOT_EVERY=N (golden.cpp): poll-<n>.ppm (binary PPM) at every Nth poll,
//                        never replacing a file; the guest thread waits for it, so the picture belongs to that poll.
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>
#include "ultramodern/ultramodern.hpp"

extern "C" int n64host_frame_read(uint32_t phys, uint32_t *rgba, uint32_t w, uint32_t h);
void n64host_cpu_idle_return();

namespace {
constexpr uint32_t WIDTH = 320, HEIGHT = 240;
std::mutex lock;
struct View {
    std::string path, tmp, shots;
    long long interval_ns = 0;
    std::chrono::steady_clock::time_point last{};
    bool written = false, failed = false, opened = false;
};
View &view = *new View;  // leaked on purpose: guest threads still call in while statics are destroyed

void put_le(unsigned char *p, uint32_t v, int bytes) {
    for (int i = 0; i < bytes; i++) p[i] = (unsigned char)(v >> (8 * i));
}

void open_view() {
    if (view.opened) return;
    view.opened = true;
    if (const char *s = std::getenv("N64R_TAS_SHOT_DIR")) view.shots = s;
    const char *path = std::getenv("N64R_VIEW");
    if (!path || !*path) return;
    unsigned long fps = 15;
    if (const char *f = std::getenv("N64R_VIEW_FPS"); f && *f) {
        char *end;
        fps = std::strtoul(f, &end, 10);
        if (*end || fps < 1 || fps > 120) {
            std::fprintf(stderr, "n64host: N64R_VIEW_FPS must be 1..120, got %s; using 15\n", f);
            fps = 15;
        }
    }
    // A little under the period, so a picture due on a retrace is not pushed to the next one by timer jitter.
    view.interval_ns = 1000000000ll / (long long)fps - 2000000ll;
    view.path = path;
    view.tmp = view.path + ".tmp";
}

bool read(std::vector<uint32_t> &pixels) {
    pixels.resize(size_t(WIDTH) * HEIGHT);
    const uint32_t fb = uint32_t(osViGetCurrentFramebuffer());
    const bool ok = fb && n64host_frame_read(fb, pixels.data(), WIDTH, HEIGHT);
    n64host_cpu_idle_return();  // host work on a guest thread is not guest time
    return ok;
}

bool write_view(const std::vector<uint32_t> &pixels) {
    const uint32_t row = (WIDTH * 3 + 3) & ~3u, size = 54 + row * HEIGHT;
    unsigned char head[54] = {'B', 'M'};
    put_le(head + 2, size, 4); put_le(head + 10, 54, 4); put_le(head + 14, 40, 4);
    put_le(head + 18, WIDTH, 4); put_le(head + 22, HEIGHT, 4); put_le(head + 26, 1, 2); put_le(head + 28, 24, 2);
    put_le(head + 34, row * HEIGHT, 4);
    FILE *out = std::fopen(view.tmp.c_str(), "wb");
    bool ok = out && std::fwrite(head, 1, sizeof head, out) == sizeof head;
    std::vector<unsigned char> line(row);
    for (uint32_t y = HEIGHT; ok && y-- > 0;) {  // bottom-up rows, BGR
        const uint32_t *src = &pixels[size_t(y) * WIDTH];
        for (uint32_t x = 0; x < WIDTH; x++) {
            line[3 * x] = (unsigned char)(src[x] >> 16); line[3 * x + 1] = (unsigned char)(src[x] >> 8);
            line[3 * x + 2] = (unsigned char)src[x];
        }
        ok = std::fwrite(line.data(), 1, row, out) == row;
    }
    if (out && std::fclose(out)) ok = false;
    return ok && std::rename(view.tmp.c_str(), view.path.c_str()) == 0;
}

void update_view(bool force) {
    open_view();
    if (view.path.empty() || view.failed) return;
    const auto now = std::chrono::steady_clock::now();
    if (!force && view.written &&
        std::chrono::duration_cast<std::chrono::nanoseconds>(now - view.last).count() < view.interval_ns) return;
    std::vector<uint32_t> pixels;
    if (!read(pixels)) return;  // nothing on screen yet: try again next interval
    view.last = now;
    view.written = true;
    if (!write_view(pixels)) {
        view.failed = true;  // one warning, never a stop: the viewer is not the run
        std::fprintf(stderr, "n64host: cannot write N64R_VIEW %s; view off\n", view.path.c_str());
    }
}
}  // namespace

// Every VI retrace (main.cpp vi_cb).
extern "C" void n64host_view_vi() {
    std::lock_guard g(lock);
    update_view(false);
}

// A TAS exit: the last picture, whatever the interval.
extern "C" void n64host_view_final() {
    std::lock_guard g(lock);
    update_view(true);
}

// The picture at controller poll `poll` (golden.cpp, N64R_TAS_SHOT_EVERY).
extern "C" void n64host_view_shot(uint64_t poll) {
    std::lock_guard g(lock);
    open_view();
    if (view.shots.empty()) return;
    std::vector<uint32_t> pixels;
    if (!read(pixels)) {
        std::fprintf(stderr, "n64host: no picture at poll %llu\n", (unsigned long long)poll);
        return;
    }
    char path[1024];
    std::snprintf(path, sizeof path, "%s/poll-%06llu.ppm", view.shots.c_str(), (unsigned long long)poll);
    FILE *out = std::fopen(path, "wbx");
    if (!out) { std::fprintf(stderr, "n64host: cannot write %s: %s\n", path, std::strerror(errno)); return; }
    bool ok = std::fprintf(out, "P6\n%u %u\n255\n", WIDTH, HEIGHT) > 0;
    for (size_t i = 0; ok && i < pixels.size(); i++) {
        const unsigned char rgb[3] = {(unsigned char)pixels[i], (unsigned char)(pixels[i] >> 8), (unsigned char)(pixels[i] >> 16)};
        ok = std::fwrite(rgb, 1, 3, out) == 3;
    }
    if (std::fclose(out)) ok = false;
    if (!ok) { std::remove(path); std::fprintf(stderr, "n64host: shot at poll %llu failed\n", (unsigned long long)poll); }
}
