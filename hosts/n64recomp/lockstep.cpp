// Lockstep recorder for the oracle host: a test instrument only, inert unless N64R_LOCKSTEP_DIR is set.
// It writes the oracle half of a lockstep comparison against a native port (the port's own tool compares):
//   lockstep.jsonl  one JSON object per line:
//     meta  side, ram, page, bytes ("canonical-be"), hash
//     hit   the first entry of a function: n (point number), fn, thr, vi, sp, pages = [[page, hash], ...] for
//           every 4 KiB page whose hash changed since the previous point (all pages at the first point)
//     call  one of the first N64R_LOCKSTEP_CALLS (8) calls of a function: i, a = a0..a3, f12, f14 (raw 64-bit), sp
//     ret   that call's return: r = v0, v1, f0 (raw 64-bit)
//     watch the word at N64R_LOCKSTEP_WATCH changed between two hooks: at (enter|ret), fn, old, new
//   pages.bin       each distinct page once: u32 page, u32 0, u64 hash (little-endian), then its 4096 bytes
// Pages are hashed and stored in canonical byte order (big-endian, as the cartridge-era CPU saw them): RDRAM keeps
// each 32-bit word in host order (byte accesses use address ^ 3), so every word is byte-swapped back first.
// The hash is FNV-1a over the little-endian 64-bit words of those bytes.
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

extern "C" uint64_t n64host_golden_frame();

namespace {
constexpr uint32_t PAGE = 4096;
struct Fn { unsigned calls = 0; bool hit = false; };
struct Frame { const char *name; uint32_t sp; int call; };
std::mutex lock;
int log_fd = -1, store_fd = -1;
uint32_t ram_bytes = 0x800000, max_calls = 8, points = 0, watch_phys = 0, watch_value = 0;
bool watching = false;
unsigned watch_lines = 0;
// Leaked on purpose: guest threads still call in while main returns and static destructors run.
std::unordered_map<const char *, Fn> &fns = *new std::unordered_map<const char *, Fn>;
std::vector<uint64_t> &last = *new std::vector<uint64_t>;
std::vector<uint8_t> &shadow = *new std::vector<uint8_t>;
std::unordered_set<uint64_t> &stored = *new std::unordered_set<uint64_t>;
thread_local std::vector<Frame> frames;

long tid() { return (long)syscall(SYS_gettid); }
void out(int fd, const void *data, size_t size) {
    const char *p = static_cast<const char *>(data);
    while (size) {
        ssize_t n = write(fd, p, size);
        if (n <= 0) return;
        p += n;
        size -= (size_t)n;
    }
}
void out_line(const std::string &line) { out(log_fd, line.data(), line.size()); }
std::string hex32(uint32_t v) { char b[16]; std::snprintf(b, sizeof b, "\"%08x\"", v); return b; }
std::string hex64(uint64_t v) { char b[24]; std::snprintf(b, sizeof b, "\"%016llx\"", (unsigned long long)v); return b; }
uint32_t word(const uint8_t *rdram, uint32_t phys) { uint32_t w; std::memcpy(&w, rdram + phys, 4); return w; }

int open_files() {
    const char *dir = std::getenv("N64R_LOCKSTEP_DIR");
    if (!dir || !*dir) return 0;
    if (const char *r = std::getenv("N64R_LOCKSTEP_RAM")) ram_bytes = (uint32_t)std::strtoul(r, nullptr, 0) & ~(PAGE - 1);
    if (const char *c = std::getenv("N64R_LOCKSTEP_CALLS")) max_calls = (uint32_t)std::strtoul(c, nullptr, 10);
    if (const char *w = std::getenv("N64R_LOCKSTEP_WATCH")) {
        char *end;
        unsigned long a = std::strtoul(w, &end, 16);
        watching = !*end && !(a & 3) && (a & 0x1FFFFFFFu) + 4 <= ram_bytes;
        watch_phys = (uint32_t)(a & 0x1FFFFFFFu);
    }
    mkdir(dir, 0777);
    std::string d(dir);
    log_fd = open((d + "/lockstep.jsonl").c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_APPEND | O_CLOEXEC, 0666);
    store_fd = open((d + "/pages.bin").c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_APPEND | O_CLOEXEC, 0666);
    if (log_fd < 0 || store_fd < 0 || !ram_bytes) {
        std::fprintf(stderr, "n64host: lockstep off (cannot write %s)\n", dir);
        return 0;
    }
    last.assign(ram_bytes / PAGE, 0);
    shadow.assign(ram_bytes, 0);
    char meta[256];
    std::snprintf(meta, sizeof meta, "{\"k\":\"meta\",\"side\":\"oracle\",\"ram\":%u,\"page\":%u,\"bytes\":\"canonical-be\","
                  "\"hash\":\"fnv1a64-le64\",\"calls\":%u,\"watch\":\"%08x\"}\n", ram_bytes, PAGE, max_calls,
                  watching ? 0x80000000u + watch_phys : 0u);
    out(log_fd, meta, std::strlen(meta));
    return 1;
}

// One point (lock held): hash every page in canonical order, list and store the changed ones.
void point(const char *name, const uint8_t *rdram, uint32_t sp) {
    static uint8_t page[PAGE];
    std::string line = "{\"k\":\"hit\",\"n\":" + std::to_string(points++) + ",\"fn\":\"" + name + "\",\"thr\":" +
                       std::to_string(tid()) + ",\"vi\":" + std::to_string(n64host_golden_frame()) + ",\"sp\":" +
                       hex32(sp) + ",\"pages\":[";
    const char *sep = "";
    for (uint32_t pg = 0; pg < ram_bytes / PAGE; pg++) {
        // Only pages whose raw bytes changed since the last point are re-hashed: a point costs about one memcmp
        // of RDRAM, so the guest loses no retraces to the recorder.
        if (points > 1 && std::memcmp(shadow.data() + pg * PAGE, rdram + pg * PAGE, PAGE) == 0) continue;
        std::memcpy(shadow.data() + pg * PAGE, rdram + pg * PAGE, PAGE);
        uint64_t h = 0xcbf29ce484222325ull;
        for (uint32_t i = 0; i < PAGE; i += 4) {
            uint32_t w = __builtin_bswap32(word(shadow.data(), pg * PAGE + i));
            std::memcpy(page + i, &w, 4);
        }
        for (uint32_t i = 0; i < PAGE; i += 8) {
            uint64_t w;
            std::memcpy(&w, page + i, 8);
            h = (h ^ w) * 0x100000001b3ull;
        }
        if (points > 1 && last[pg] == h) continue;
        last[pg] = h;
        if (stored.insert(h).second) {
            uint32_t head[4] = {pg, 0, (uint32_t)h, (uint32_t)(h >> 32)};
            out(store_fd, head, sizeof head);
            out(store_fd, page, PAGE);
        }
        line += sep;
        line += "[" + std::to_string(pg) + "," + hex64(h) + "]";
        sep = ",";
    }
    out_line(line + "]}\n");
}

void watch(const char *name, const uint8_t *rdram, const char *at) {
    uint32_t now = word(rdram, watch_phys);
    if (now == watch_value || watch_lines >= 20000) return;
    out_line(std::string("{\"k\":\"watch\",\"at\":\"") + at + "\",\"fn\":\"" + name + "\",\"thr\":" +
             std::to_string(tid()) + ",\"old\":" + hex32(watch_value) + ",\"new\":" + hex32(now) + "}\n");
    watch_value = now;
    watch_lines++;
}
}  // namespace

extern "C" {
int n64host_lockstep_on = open_files();
}

extern "C" void n64host_lockstep_entry(const char *name, const uint8_t *rdram, uint32_t a0, uint32_t a1, uint32_t a2,
                                       uint32_t a3, uint64_t f12, uint64_t f14, uint32_t sp) {
    int call;
    {
        std::lock_guard<std::mutex> g(lock);
        Fn &f = fns[name];
        call = f.calls < max_calls ? (int)f.calls : -1;
        f.calls++;
        if (!f.hit) {
            f.hit = true;
            point(name, rdram, sp);
        }
        if (call >= 0)
            out_line("{\"k\":\"call\",\"fn\":\"" + std::string(name) + "\",\"i\":" + std::to_string(call) + ",\"thr\":" +
                     std::to_string(tid()) + ",\"sp\":" + hex32(sp) + ",\"a\":[" + hex32(a0) + "," + hex32(a1) + "," +
                     hex32(a2) + "," + hex32(a3) + "],\"f12\":" + hex64(f12) + ",\"f14\":" + hex64(f14) + "}\n");
        if (watching) watch(name, rdram, "enter");
    }
    frames.push_back({name, sp, call});
}

// sp is the restored stack pointer, equal to the one at entry; frames left above it were tail calls or skipped.
extern "C" void n64host_lockstep_return(const char *name, const uint8_t *rdram, uint32_t v0, uint32_t v1, uint64_t f0,
                                        uint32_t sp) {
    while (!frames.empty() && frames.back().sp < sp) frames.pop_back();
    if (frames.empty() || frames.back().sp != sp || frames.back().name != name) return;
    const Frame fr = frames.back();
    frames.pop_back();
    std::lock_guard<std::mutex> g(lock);
    if (fr.call >= 0)
        out_line("{\"k\":\"ret\",\"fn\":\"" + std::string(name) + "\",\"i\":" + std::to_string(fr.call) + ",\"thr\":" +
                 std::to_string(tid()) + ",\"r\":[" + hex32(v0) + "," + hex32(v1) + "],\"f0\":" + hex64(f0) + "}\n");
    if (watching) watch(name, rdram, "ret");
}
