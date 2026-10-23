// Function-replay recorder for the oracle host: a test instrument only, inert unless N64R_REPLAY_DIR is set.
// It records a few real calls of every function the run reaches, so a native port can run the same function on the
// same inputs in isolation and compare (decomp behave --replay): the arguments, every guest memory location the call
// (and its callees) read before writing it, with the value and access width, and what the call stored and returned.
//
// replay.jsonl, one line per sampled call (numbers are decimal; an address is the KSEG0 form 0x80000000 | physical):
//   {"fn", "i" (call number of the function, 0-based), "frame" (VI retrace count), "thr", "depth" (recordings it ran
//    inside), "sp" (entry stack pointer), "a": [a0..a3], "f12", "f14" (raw 64-bit FPR), "stack": [8 words at sp+16],
//    "in": [[addr, width, value], ...], "out": [[addr, width, value], ...] (last value of each location the call
//    stored, widths 1/2/4/8 as the generated code accessed them), "ret": [v0, v1], "f0", "events"}
// or {"fn", "i", "skip": "<why>"} when the call cannot be replayed: another thread ran meanwhile (the call's memory
// was not its own), it touched memory outside RDRAM or used an unaligned merge, read one location at two widths, or
// made more than N64R_REPLAY_EVENTS accesses.
//   N64R_REPLAY_DIR     output directory (created)
//   N64R_REPLAY_CALLS   sampled calls kept per function (default 3)
//   N64R_REPLAY_GAP     VI retraces between two samples of one function (default 25)
//   N64R_REPLAY_EVENTS  memory accesses one call may make (default 200000)
//   N64R_REPLAY_RAM     RDRAM bytes (default 0x800000)
// A call is sampled at its first entry and then at entries whose arguments differ from every earlier sample's, GAP
// retraces apart. Recordings nest (a callee of a sampled call can be sampled), at most 6 deep per thread.
//
// How loads and stores are told apart: the generated code is `MEM_W(off, reg) = v` for a store and a plain use for a
// load, and a macro cannot see which. The accessor (addrmap.h) hands out the pointer and remembers it as the pending
// access; the next accessor call, entry or return settles it: memory that changed was a store of the new value, memory
// that did not was a load. A store of the value already there is therefore a load: an input equal to what it wrote.
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <sys/syscall.h>
#include <unistd.h>
extern "C" uint64_t n64host_golden_frame();
#include "addrmap.h"

namespace {
constexpr size_t SLOTS = 1u << 16, MAX_ACTIVE = 6;
struct Slot { std::atomic<const char *> key{nullptr}; std::atomic<uint32_t> calls{0}, started{0}, kept{0}; };
struct Sample { uint32_t a[4]; uint64_t frame; };
struct In { uint32_t addr; uint8_t width; uint64_t value; };
struct Store { uint32_t addr; uint8_t width; uint64_t value; };
struct Rec {
    const char *name;
    uint32_t sp, call;
    uint64_t frame, epoch;
    long thread;
    uint32_t a[4], stack[8];
    uint64_t f12, f14;
    std::vector<In> in;
    std::vector<Store> stores;
    std::unordered_set<uint32_t> written;       // bytes the call stored
    std::unordered_set<uint64_t> seen;          // (addr << 4 | width) of recorded inputs
    uint64_t events = 0;
    const char *skip = nullptr;
};
std::mutex lock;
FILE *out;
uint32_t max_calls = 3, gap = 25, ram_bytes = 0x800000;
uint64_t max_events = 200000;
Slot *table;
std::unordered_map<const char *, std::vector<Sample>> &samples = *new std::unordered_map<const char *, std::vector<Sample>>;
std::atomic<uint64_t> epoch{0};
std::atomic<long> last_thread{0};
thread_local std::vector<std::unique_ptr<Rec>> active;
struct Pending { bool on = false; uint32_t addr = 0; int width = 0; const uint8_t *p = nullptr; uint64_t before = 0; };
thread_local Pending pending;

long tid() { return (long)syscall(SYS_gettid); }
uint64_t raw(const uint8_t *p, int width) {
    switch (width) {
    case 1: return *p;
    case 2: return *reinterpret_cast<const uint16_t *>(p);
    case 4: return *reinterpret_cast<const uint32_t *>(p);
    default: return 0;
    }
}
uint32_t word(const uint8_t *rdram, uint32_t va) {
    return *reinterpret_cast<const uint32_t *>(rdram + (va & 0x1FFFFFFFu));
}

Slot &find(const char *name) {
    size_t i = (reinterpret_cast<uintptr_t>(name) >> 3) * 0x9E3779B1u;
    for (;; ++i) {
        Slot &s = table[i % SLOTS];
        const char *k = s.key.load(std::memory_order_acquire);
        if (k == name) return s;
        if (!k) {
            const char *expect = nullptr;
            if (s.key.compare_exchange_strong(expect, name) || expect == name) return s;
        }
    }
}

void taint(Rec &r, const char *why) { if (!r.skip) r.skip = why; }

void note_load(Rec &r, uint32_t addr, int width, uint64_t value) {
    if (r.skip) return;
    for (int i = 0; i < width; ++i)
        if (r.written.count(addr + i)) return;           // the call's own data: not an input
    if (!r.seen.insert(((uint64_t)addr << 4) | (uint64_t)width).second) return;
    r.in.push_back({addr, (uint8_t)width, value});
}
void note_store(Rec &r, uint32_t addr, int width, uint64_t value) {
    if (r.skip) return;
    for (int i = 0; i < width; ++i) r.written.insert(addr + i);
    r.stores.push_back({addr, (uint8_t)width, value});
}

void emit_skip(const Rec &r) {
    std::string line = "{\"fn\":\"" + std::string(r.name) + "\",\"i\":" + std::to_string(r.call) + ",\"skip\":\"" +
                       r.skip + "\"}\n";
    std::lock_guard<std::mutex> g(lock);
    std::fputs(line.c_str(), out);
    std::fflush(out);
}

// A call that can no longer be replayed stops costing anything: it is written off and leaves the active list.
void reap() {
    for (size_t i = active.size(); i-- > 0;)
        if (active[i]->skip) {
            emit_skip(*active[i]);
            active.erase(active.begin() + (long)i);
        }
    n64host_rec_active = (int)active.size();
}

void settle() {
    Pending p = pending;
    if (!p.on) return;
    pending.on = false;
    uint64_t now = raw(p.p, p.width);
    const bool store = now != p.before;
    for (auto &r : active) {
        if (++r->events > max_events) taint(*r, "events");
        if (store) note_store(*r, p.addr, p.width, now); else note_load(*r, p.addr, p.width, now);
    }
    reap();
}

void json_pairs(std::string &line, const char *key, const std::vector<std::array<uint64_t, 3>> &rows) {
    line += std::string(",\"") + key + "\":[";
    for (size_t i = 0; i < rows.size(); ++i) {
        char b[96];
        std::snprintf(b, sizeof b, "%s[%llu,%llu,%llu]", i ? "," : "", (unsigned long long)rows[i][0],
                      (unsigned long long)rows[i][1], (unsigned long long)rows[i][2]);
        line += b;
    }
    line += "]";
}

// Two inputs over the same bytes at different shapes cannot both be laid out natively.
bool mixed(const std::vector<In> &in) {
    std::vector<In> v(in);
    std::sort(v.begin(), v.end(), [](const In &a, const In &b) { return a.addr < b.addr; });
    uint32_t end = 0;
    const In *prev = nullptr;
    for (const In &x : v) {
        if (prev && x.addr < end && !(x.addr == prev->addr && x.width == prev->width)) return true;
        if (x.addr + x.width > end) { end = x.addr + x.width; prev = &x; }
    }
    return false;
}

void finish(Rec &r, uint32_t v0, uint32_t v1, uint64_t f0) {
    std::string line = "{\"fn\":\"" + std::string(r.name) + "\",\"i\":" + std::to_string(r.call);
    if (!r.skip && r.epoch != epoch.load()) r.skip = "interleaved";
    if (!r.skip && n64host_golden_frame() != r.frame) r.skip = "frames";
    if (!r.skip && mixed(r.in)) r.skip = "mixed-width";
    if (r.skip) {
        line += std::string(",\"skip\":\"") + r.skip + "\"}\n";
    } else {
        // The last store of each location; one a later store of another shape partly covered is not comparable.
        std::unordered_map<uint32_t, size_t> owner;
        for (size_t j = 0; j < r.stores.size(); ++j)
            for (int b = 0; b < r.stores[j].width; ++b) owner[r.stores[j].addr + b] = j;
        std::vector<std::array<uint64_t, 3>> outs, ins;
        for (size_t j = r.stores.size(); j-- > 0;) {
            const Store &s = r.stores[j];
            bool whole = true;
            for (int b = 0; b < s.width; ++b) whole = whole && owner[s.addr + b] == j;
            if (whole) outs.push_back({s.addr, s.width, s.value});
        }
        std::sort(outs.begin(), outs.end());
        for (const In &x : r.in) ins.push_back({x.addr, x.width, x.value});
        std::sort(ins.begin(), ins.end());
        char head[512];
        std::snprintf(head, sizeof head,
                      ",\"frame\":%llu,\"thr\":%ld,\"depth\":%zu,\"sp\":%u,\"a\":[%u,%u,%u,%u],\"f12\":%llu,\"f14\":%llu,"
                      "\"stack\":[%u,%u,%u,%u,%u,%u,%u,%u],\"ret\":[%u,%u],\"f0\":%llu,\"events\":%llu",
                      (unsigned long long)r.frame, r.thread, active.size(), r.sp, r.a[0], r.a[1], r.a[2], r.a[3],
                      (unsigned long long)r.f12, (unsigned long long)r.f14, r.stack[0], r.stack[1], r.stack[2],
                      r.stack[3], r.stack[4], r.stack[5], r.stack[6], r.stack[7], v0, v1, (unsigned long long)f0,
                      (unsigned long long)r.events);
        line += head;
        json_pairs(line, "in", ins);
        json_pairs(line, "out", outs);
        line += "}\n";
    }
    std::lock_guard<std::mutex> g(lock);
    std::fputs(line.c_str(), out);
    std::fflush(out);
}

int open_files() {
    const char *dir = std::getenv("N64R_REPLAY_DIR");
    if (!dir || !*dir) return 0;
    if (const char *v = std::getenv("N64R_REPLAY_CALLS")) max_calls = (uint32_t)std::strtoul(v, nullptr, 10);
    if (const char *v = std::getenv("N64R_REPLAY_GAP")) gap = (uint32_t)std::strtoul(v, nullptr, 10);
    if (const char *v = std::getenv("N64R_REPLAY_EVENTS")) max_events = std::strtoull(v, nullptr, 10);
    if (const char *v = std::getenv("N64R_REPLAY_RAM")) ram_bytes = (uint32_t)std::strtoul(v, nullptr, 0);
    std::string d(dir);
    std::string cmd = "mkdir -p '" + d + "'";
    if (std::system(cmd.c_str()) != 0) return 0;
    out = std::fopen((d + "/replay.jsonl").c_str(), "w");
    if (!out) {
        std::fprintf(stderr, "n64host: replay off (cannot write %s)\n", dir);
        return 0;
    }
    table = new Slot[SLOTS];
    return 1;
}
}  // namespace

extern "C" {
int n64host_replay_on = open_files();
__thread int n64host_rec_active = 0;
}

extern "C" void n64host_replay_entry(const char *name, const uint8_t *rdram, uint32_t a0, uint32_t a1, uint32_t a2,
                                     uint32_t a3, uint64_t f12, uint64_t f14, uint32_t sp) {
    settle();
    const long me = tid();
    if (last_thread.exchange(me) != me) epoch.fetch_add(1);
    Slot &s = find(name);
    const uint32_t call = s.calls.fetch_add(1);
    if (s.kept.load() >= max_calls || s.started.load() >= max_calls + 4 || active.size() >= MAX_ACTIVE) return;
    const uint64_t frame = n64host_golden_frame();
    {
        std::lock_guard<std::mutex> g(lock);
        auto &taken = samples[name];
        if (!taken.empty()) {
            if (frame < taken.back().frame + gap) return;
            for (const Sample &t : taken)
                if (t.a[0] == a0 && t.a[1] == a1 && t.a[2] == a2 && t.a[3] == a3) return;
        }
        taken.push_back({{a0, a1, a2, a3}, frame});
    }
    s.started.fetch_add(1);
    auto r = std::make_unique<Rec>();
    r->name = name; r->sp = sp; r->call = call; r->frame = frame; r->epoch = epoch.load(); r->thread = me;
    r->a[0] = a0; r->a[1] = a1; r->a[2] = a2; r->a[3] = a3; r->f12 = f12; r->f14 = f14;
    if ((sp & 0x1FFFFFFFu) + 48 <= ram_bytes)
        for (int i = 0; i < 8; ++i) r->stack[i] = word(rdram, sp + 16 + 4u * i);
    active.push_back(std::move(r));
    n64host_rec_active = (int)active.size();
}

extern "C" void n64host_replay_return(const char *name, const uint8_t *, uint32_t v0, uint32_t v1, uint64_t f0,
                                      uint32_t sp) {
    settle();
    if (active.empty()) return;
    const long me = tid();
    if (last_thread.exchange(me) != me) epoch.fetch_add(1);
    while (!active.empty() && active.back()->sp < sp) active.pop_back();      // tail-called or skipped frames
    n64host_rec_active = (int)active.size();
    if (active.empty() || active.back()->sp != sp || active.back()->name != name) return;
    std::unique_ptr<Rec> r = std::move(active.back());
    active.pop_back();
    n64host_rec_active = (int)active.size();
    Slot &s = find(name);
    finish(*r, v0, v1, f0);
    if (!r->skip) s.kept.fetch_add(1);
}

// A host pointer for the guest address, remembered as the pending access (see the header comment).
extern "C" uint8_t *n64host_replay_ptr(uint8_t *rdram, uint32_t va, int width) {
    settle();
    const uint32_t flip = width == 1 ? 3u : width == 2 ? 2u : 0u;
    const uint32_t phys = va & 0x1FFFFFFFu;
    if (va < 0x80000000u || va >= 0xC0000000u || phys + (uint32_t)width > ram_bytes) {
        for (auto &r : active) taint(*r, "non-ram");
        reap();
        return n64host_ptr(rdram, va ^ flip);
    }
    uint8_t *p = rdram + (phys ^ flip);
    pending = {true, 0x80000000u | phys, width, p, raw(p, width)};
    return p;
}

extern "C" uint64_t n64host_replay_ld(uint8_t *rdram, uint32_t va) {
    settle();
    const uint32_t phys = va & 0x1FFFFFFFu;
    if (va < 0x80000000u || va >= 0xC0000000u || phys + 8 > ram_bytes) {
        for (auto &r : active) taint(*r, "non-ram");
        reap();
        return ((uint64_t)*(uint32_t *)n64host_ptr(rdram, (uint64_t)va + 4)) |
               ((uint64_t)*(uint32_t *)n64host_ptr(rdram, va) << 32);
    }
    const uint64_t v = ((uint64_t)word(rdram, phys) << 32) | word(rdram, phys + 4);
    for (auto &r : active) {
        if (++r->events > max_events) taint(*r, "events");
        note_load(*r, 0x80000000u | phys, 8, v);
    }
    reap();
    return v;
}

extern "C" void n64host_replay_sd(uint8_t *rdram, uint32_t va, uint64_t value) {
    settle();
    const uint32_t phys = va & 0x1FFFFFFFu;
    if (va < 0x80000000u || va >= 0xC0000000u || phys + 8 > ram_bytes) {
        for (auto &r : active) taint(*r, "non-ram");
        reap();
        *(uint32_t *)n64host_ptr(rdram, (uint64_t)va + 4) = (uint32_t)value;
        *(uint32_t *)n64host_ptr(rdram, va) = (uint32_t)(value >> 32);
        return;
    }
    *reinterpret_cast<uint32_t *>(rdram + phys + 4) = (uint32_t)value;
    *reinterpret_cast<uint32_t *>(rdram + phys) = (uint32_t)(value >> 32);
    for (auto &r : active) {
        if (++r->events > max_events) taint(*r, "events");
        note_store(*r, 0x80000000u | phys, 8, value);
    }
    reap();
}

// An unaligned merge (lwl/lwr/swl/swr, ldl/...) works on a partial word: its inputs and outputs have no native shape.
extern "C" void n64host_replay_taint(const char *why) {
    settle();
    for (auto &r : active) taint(*r, why);
    reap();
}
