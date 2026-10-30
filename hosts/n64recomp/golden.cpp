// Golden recorder for the oracle host: a test instrument only. Everything is written when
// N64R_GOLDEN_DIR is set and is a no-op otherwise. It knows nothing about any game.
//   N64R_GOLDEN_DIR   output directory (events.jsonl, dl-*.bin, acmd-all.bin, pcm-all.s16, trace-*.tsv)
//   N64R_SCRIPT       lines "<vi> key <Name>", "<vi> checkpoint <name>", "<vi> quit" (vi = retrace count)
//   N64R_SHOT_CMD     shell template run at a checkpoint; "%s" is replaced by the PNG path
//   N64R_PADS         controllers the guest sees (1..4, default 1): a movie line holds that many triples
//   N64R_TAS_PLAY     raw controller movie, one "BUTTONS X Y" line per guest controller poll (hex buttons), the
//                     same file a native port replays (works without N64R_GOLDEN_DIR); N64R_TAS_SHOT_EVERY=N
//                     checkpoints "poll-<n>" every N polls (with the golden dir) and writes the picture there
//                     (view.cpp, N64R_TAS_SHOT_DIR), N64R_TAS_EXIT_AFTER=N quits N polls after the movie ends,
//                     N64R_TAS_TRACE=FILE writes "poll vi" per guest controller poll (poll/VI parity with the port)
//   N64R_TAS_STATE    a state spec (tas_state.h): N64R_TAS_STATE_HASH=FILE writes "<poll> <symbol>:<hash> ..." per
//                     poll, N64R_TAS_STATE_AT=P1,P2 with N64R_TAS_STATE_DIR=DIR writes state-<poll>.bin there: the
//                     port's own N64C_TAS_STATE trace, read from the word-swizzled RDRAM (TS_ORACLE), so
//                     `decomp tas play --oracle` diffs the two sides' game state per poll
#include <SDL.h>
#include <array>
#include <atomic>
#include <deque>
#include <memory>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#include "ultramodern/renderer_context.hpp"
#define TS_ORACLE 1
#include "tas_state.h"
#include "render_gate.h"

extern uint8_t *n64host_rdram;
void n64host_script_key(const char *name);
extern "C" void n64host_view_shot(uint64_t poll);
extern "C" void n64host_view_final();

// The guest controller poll the host is in (the port's `polls`): an exported plain global, so the debugger's dprintf
// that traces an event reads it and prints `EV|FUNC@<poll>|..`, the port's form. Set at the TS_CHECKPOINT (tas_state.h).
extern "C" volatile unsigned long long n64host_tas_poll = 0;

namespace {
struct Step { uint64_t vi; std::string kind, arg; };
struct Entry { const char *name; uint64_t first_vi, count; unsigned thread; };
std::mutex lock;
std::atomic<uint64_t> vi_count{0};
FILE *events, *acmd_all, *pcm_all;
// Leaked on purpose: guest threads still call in while main returns and static destructors run.
std::string &dir = *new std::string, &shot_cmd = *new std::string;
std::vector<Step> &steps = *new std::vector<Step>;
size_t next_step = 0;
FILE *movie;  // N64R_TAS_PLAY
uint64_t polls, played, shot_every, exit_after = UINT64_MAX;
RenderGate render_gate;  // N64R_TAS_RENDER_LEAD (render_gate.h); written before the guest runs, then only exit_poll (atomic)
std::atomic<uint64_t> state_cpu_ns{0};
bool movie_done;
std::string &armed = *new std::string;   // checkpoint waiting for its first graphics task
uint64_t acmd_seq = 0, acmd_bytes = 0, pcm_samples = 0, dl_seq = 0;
unsigned pcm_rate = 0;
std::unordered_map<const char *, size_t> &seen = *new std::unordered_map<const char *, size_t>;
std::vector<Entry> &order = *new std::vector<Entry>;
// Requester tracking for PI DMAs: the last functions entered per thread, and the payload words of
// message sends not yet received. A DMA's requester is the sender of the message its thread last received.
constexpr size_t RING = 8;
struct Ring { std::array<const char *, RING> fn{}; size_t n = 0; };
struct Send {
    unsigned thread; uint32_t queue, msg;
    std::array<const char *, RING> fn; size_t nfn;
    std::shared_ptr<const Send> parent;  // the message the sending thread had last received: the chain upward
};
std::unordered_map<unsigned, Ring> &rings = *new std::unordered_map<unsigned, Ring>;
std::deque<std::shared_ptr<Send>> &sends = *new std::deque<std::shared_ptr<Send>>;
std::unordered_map<unsigned, std::shared_ptr<const Send>> &current = *new std::unordered_map<unsigned, std::shared_ptr<const Send>>;

// N64R_TAS_STATE: the per-poll game state trace, the same lines the port writes (tas_state.h)
struct ts_spec state;
struct ts_polls state_at;
FILE *state_hash;
const char *state_dir;
uint64_t *state_now;
uint32_t *state_prev;
int state_started;

void state_init() {
    const char *path = std::getenv("N64R_TAS_STATE");
    if (!path || !*path) return;
    if (ts_load(path, &state)) { std::fprintf(stderr, "n64host: cannot read state spec %s\n", path); std::_Exit(5); }
    const char *out = std::getenv("N64R_TAS_STATE_HASH");
    if (out && *out && !(state_hash = std::fopen(out, "w"))) {
        std::fprintf(stderr, "n64host: cannot write state hashes %s\n", out);
        std::_Exit(5);
    }
    state_dir = std::getenv("N64R_TAS_STATE_DIR");
    ts_polls_parse(std::getenv("N64R_TAS_STATE_AT"), &state_at);
    state_now = static_cast<uint64_t *>(std::calloc(state.nsymbols ? state.nsymbols : 1, sizeof *state_now));
    state_prev = static_cast<uint32_t *>(std::calloc(state.nsymbols ? state.nsymbols : 1, sizeof *state_prev));
}

uint64_t thread_cpu_ns() {
    timespec t;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
    return uint64_t(t.tv_sec) * 1000000000ull + uint64_t(t.tv_nsec);
}

void state_poll(uint64_t poll) {
    if (!state.nruns || !n64host_rdram) return;
    const uint64_t began = thread_cpu_ns();
    if (state_hash) {
        ts_hash(&state, n64host_rdram, state_now);
        ts_trace_line(state_hash, long(poll), &state, state_now, state_prev, &state_started);
    }
    if (state_dir && ts_wanted(&state_at, long(poll))) {
        std::string path = std::string(state_dir) + "/state-" + std::to_string(poll) + ".bin";
        FILE *f = std::fopen(path.c_str(), "wb");
        if (!f || ts_dump(&state, n64host_rdram, f) || std::fclose(f)) {
            std::fprintf(stderr, "n64host: state dump at poll %llu to %s failed\n", (unsigned long long)poll, path.c_str());
            std::_Exit(2);
        }
    }
    state_cpu_ns += thread_cpu_ns() - began;
}

bool on() { return events != nullptr; }
uint8_t *ram(uint32_t addr) { return n64host_rdram + (addr & 0x3FFFFFF); }

void write_trace(const std::string &tag) {
    std::string path = dir + "/trace-" + tag + ".tsv";
    FILE *f = std::fopen(path.c_str(), "w");
    if (!f) return;
    std::fprintf(f, "order\tsymbol\tfirst_vi\tfirst_thread\tcount\n");
    for (size_t i = 0; i < order.size(); ++i)
        std::fprintf(f, "%zu\t%s\t%llu\t%u\t%llu\n", i + 1, order[i].name, (unsigned long long)order[i].first_vi,
                     order[i].thread, (unsigned long long)order[i].count);
    std::fclose(f);
}

void checkpoint(const std::string &name) {
    auto *vi = ultramodern::renderer::get_vi_regs();
    std::fprintf(events,
        "{\"ev\":\"checkpoint\",\"name\":\"%s\",\"vi\":%llu,\"acmd_seq\":%llu,\"acmd_bytes\":%llu,"
        "\"pcm_samples\":%llu,\"pcm_rate\":%u,\"traced_functions\":%zu,"
        "\"vi_status\":%u,\"vi_origin\":%u,\"vi_width\":%u,\"vi_x_scale\":%u,\"vi_y_scale\":%u,"
        "\"vi_h_start\":%u,\"vi_v_start\":%u}\n",
        name.c_str(), (unsigned long long)vi_count.load(), (unsigned long long)acmd_seq,
        (unsigned long long)acmd_bytes, (unsigned long long)pcm_samples, pcm_rate, order.size(),
        vi->VI_STATUS_REG, vi->VI_ORIGIN_REG, vi->VI_WIDTH_REG, vi->VI_X_SCALE_REG, vi->VI_Y_SCALE_REG,
        vi->VI_H_START_REG, vi->VI_V_START_REG);
    std::fflush(events);
    write_trace(name);
    armed = name;
    if (!shot_cmd.empty()) {
        char png[1024], cmd[2048];
        std::snprintf(png, sizeof png, "%s/vi-%s.png", dir.c_str(), name.c_str());
        std::snprintf(cmd, sizeof cmd, shot_cmd.c_str(), png);
        int rc = std::system(cmd);
        std::fprintf(events, "{\"ev\":\"shot\",\"name\":\"%s\",\"rc\":%d}\n", name.c_str(), rc);
    }
}

void quit(uint64_t now) {
    n64host_view_final();
    if (state.nruns)
        std::fprintf(stderr, "n64host: state trace polls=%llu symbols=%zu cpu_ms=%llu\n", (unsigned long long)polls,
                     state.nsymbols, (unsigned long long)(state_cpu_ns.load() / 1000000));
    if (events) {
        std::fprintf(events, "{\"ev\":\"quit\",\"vi\":%llu}\n", (unsigned long long)now);
        std::fflush(events);
        write_trace("final");
        std::fflush(acmd_all); std::fflush(pcm_all);
    }
    // Everything is on disk. Runtime teardown races the running guest threads (a crash after the
    // goldens are written), so a scripted quit ends the process here with a clean status.
    std::fflush(stdout); std::fflush(stderr);
    std::_Exit(0);
}
}  // namespace

extern "C" void n64host_golden_init() {
    // A movie replays with or without the recorder: the TAS oracle runs it for pictures alone.
    if (const char *m = std::getenv("N64R_TAS_PLAY")) {
        movie = std::fopen(m, "r");
        if (!movie) { std::fprintf(stderr, "n64host: cannot read movie %s\n", m); std::_Exit(5); }
        if (const char *e = std::getenv("N64R_TAS_SHOT_EVERY")) shot_every = std::strtoull(e, nullptr, 10);
        if (const char *e = std::getenv("N64R_TAS_EXIT_AFTER")) exit_after = std::strtoull(e, nullptr, 10);
        if (const char *e = std::getenv("N64R_TAS_RENDER_LEAD")) render_gate.lead = std::strtoull(e, nullptr, 10);
        render_gate.every = shot_every;
        state_init();
    }
    const char *d = std::getenv("N64R_GOLDEN_DIR");
    if (!d) return;
    dir = d;
    events = std::fopen((dir + "/events.jsonl").c_str(), "w");
    acmd_all = std::fopen((dir + "/acmd-all.bin").c_str(), "wb");
    pcm_all = std::fopen((dir + "/pcm-all.s16").c_str(), "wb");
    if (!events || !acmd_all || !pcm_all) { std::fprintf(stderr, "n64host: golden dir unusable\n"); std::_Exit(5); }
    if (const char *c = std::getenv("N64R_SHOT_CMD")) shot_cmd = c;
    if (const char *s = std::getenv("N64R_SCRIPT")) {
        std::ifstream in(s);
        std::string line;
        while (std::getline(in, line)) {
            std::istringstream is(line);
            Step st;
            if (is >> st.vi >> st.kind) { is >> st.arg; steps.push_back(st); }
        }
    }
    armed = "boot";  // the very first graphics task is the "boot" golden
}

// Called from the VI retrace callback.
extern "C" void n64host_golden_vi() {
    uint64_t now = ++vi_count;
    if (!on()) return;
    std::lock_guard g(lock);
    while (next_step < steps.size() && steps[next_step].vi <= now) {
        const Step st = steps[next_step++];
        if (st.kind == "key") {
            n64host_script_key(st.arg.c_str());
            std::fprintf(events, "{\"ev\":\"key\",\"name\":\"%s\",\"vi\":%llu}\n", st.arg.c_str(), (unsigned long long)now);
        } else if (st.kind == "checkpoint") {
            checkpoint(st.arg);
        } else if (st.kind == "quit") {
            quit(now);
        }
    }
}

// Whether the graphics frame the guest is submitting now is drawn (render_gate.h).
extern "C" bool n64host_render_wanted() { return render_gate.wanted(n64host_tas_poll); }

extern "C" uint64_t n64host_golden_frame() { return vi_count.load(); }

// N64R_PADS: controllers the guest sees (1..4, default 1); a movie line holds that many "BUTTONS X Y" triples.
extern "C" unsigned n64host_golden_pads() {
    static const unsigned pads = [] {
        const char *p = std::getenv("N64R_PADS");
        long n = p && *p ? std::strtol(p, nullptr, 10) : 1;
        if (n < 1 || n > 4) { std::fprintf(stderr, "n64host: N64R_PADS is 1..4, got %s\n", p); std::_Exit(5); }
        return unsigned(n);
    }();
    return pads;
}

// Called by the SI adapter for each controller read of every channel. Returns true when a movie supplies the pad, so
// a replay is keyed to the guest's own polls exactly as the port keys it: channel 0's read advances the movie by one
// line, the other channels of that poll read the same line.
extern "C" bool n64host_golden_pad(unsigned channel, uint16_t *buttons, int8_t *x, int8_t *y) {
    if (!movie) return false;
    static uint16_t held[4];
    static int8_t held_x[4], held_y[4];
    if (channel >= n64host_golden_pads()) return false;
    if (channel) {
        *buttons = held[channel]; *x = held_x[channel]; *y = held_y[channel];
        return true;
    }
    std::unique_lock g(lock);
    char line[256];
    for (unsigned i = 0; i < 4; i++) { held[i] = 0; held_x[i] = held_y[i] = 0; }
    while (!movie_done) {
        if (!std::fgets(line, sizeof line, movie)) {
            movie_done = true;
            if (exit_after != UINT64_MAX) render_gate.exit_poll = played + exit_after;
            if (events) std::fprintf(events, "{\"ev\":\"movie_end\",\"poll\":%llu}\n", (unsigned long long)played);
            break;
        }
        if (line[0] == '#' || line[0] == '\n') continue;
        const char *at = line;
        for (unsigned i = 0; i < n64host_golden_pads(); i++) {
            unsigned b = 0;
            int sx = 0, sy = 0, used = 0;
            if (std::sscanf(at, "%x %d %d%n", &b, &sx, &sy, &used) != 3) {
                std::fprintf(stderr, "n64host: bad movie line after poll %llu (%u pads): %s", (unsigned long long)played, n64host_golden_pads(), line);
                std::_Exit(5);
            }
            at += used;
            held[i] = uint16_t(b); held_x[i] = int8_t(sx); held_y[i] = int8_t(sy);
        }
        ++played;
        break;
    }
    *buttons = held[0]; *x = held_x[0]; *y = held_y[0];
    ++polls;
    if (const char *t = std::getenv("N64R_TAS_TRACE")) { static FILE *tf = std::fopen(t, "w"); if (tf) { std::fprintf(tf, "%llu %llu\n", (unsigned long long)polls, (unsigned long long)vi_count.load()); std::fflush(tf); } }
    n64host_tas_poll = polls;
    state_poll(polls);  // TS_CHECKPOINT, the same point the port samples (tas_state.h)
    const bool shot = shot_every && polls % shot_every == 0;
    if (shot && on()) checkpoint("poll-" + std::to_string(polls));
    const uint64_t poll = polls;
    if (movie_done && poll - played >= exit_after) quit(vi_count.load());
    g.unlock();
    if (shot) n64host_view_shot(poll);  // the guest waits: this poll's picture, whatever the host speed
    return true;
}

extern "C" void n64host_golden_entry(const char *name, unsigned thread) {
    if (!on()) return;
    std::lock_guard g(lock);
    Ring &r = rings[thread];
    r.fn[r.n++ % RING] = name;
    auto it = seen.find(name);
    if (it == seen.end()) {
        seen.emplace(name, order.size());
        order.push_back({name, vi_count.load(), 1, thread});
    } else {
        ++order[it->second].count;
    }
}

// First graphics task after boot and after every checkpoint: save the top-level display list.
extern "C" void n64host_golden_gfx(uint32_t ucode, uint32_t ucode_data, uint32_t data_ptr, uint32_t data_size) {
    if (!on()) return;
    std::lock_guard g(lock);
    ++dl_seq;
    if (armed.empty()) return;
    std::string name = armed;
    armed.clear();
    if (data_size > (1u << 20)) data_size = 1u << 20;
    std::string path = dir + "/dl-" + name + ".bin";
    if (FILE *f = std::fopen(path.c_str(), "wb")) { std::fwrite(ram(data_ptr), 1, data_size, f); std::fclose(f); }
    std::fprintf(events,
        "{\"ev\":\"gfx\",\"name\":\"%s\",\"vi\":%llu,\"task_seq\":%llu,\"ucode\":%u,\"ucode_data\":%u,"
        "\"data_ptr\":%u,\"data_size\":%u}\n", name.c_str(), (unsigned long long)vi_count.load(),
        (unsigned long long)dl_seq, ucode, ucode_data, data_ptr, data_size);
}

extern "C" void n64host_golden_audio(uint32_t ucode, uint32_t data_ptr, uint32_t data_size) {
    if (!on()) return;
    std::lock_guard g(lock);
    if (data_size > (1u << 20)) data_size = 1u << 20;
    std::fwrite(ram(data_ptr), 1, data_size, acmd_all);
    std::fprintf(events, "{\"ev\":\"acmd\",\"seq\":%llu,\"vi\":%llu,\"offset\":%llu,\"size\":%u,\"ucode\":%u,\"data_ptr\":%u}\n",
        (unsigned long long)acmd_seq, (unsigned long long)vi_count.load(), (unsigned long long)acmd_bytes,
        data_size, ucode, data_ptr);
    ++acmd_seq;
    acmd_bytes += data_size;
}

extern "C" void n64host_golden_pcm(const int16_t *s, size_t n) {
    if (!on()) return;
    std::lock_guard g(lock);
    std::fwrite(s, sizeof(int16_t), n, pcm_all);
    pcm_samples += n;
}

extern "C" void n64host_golden_rate(unsigned rate) {
    if (!on()) return;
    std::lock_guard g(lock);
    pcm_rate = rate;
    std::fprintf(events, "{\"ev\":\"rate\",\"hz\":%u,\"vi\":%llu,\"pcm_samples\":%llu}\n", rate,
        (unsigned long long)vi_count.load(), (unsigned long long)pcm_samples);
}

extern "C" void n64host_golden_send(uint8_t *, uint32_t queue, uint32_t msg) {
    if (!on()) return;
    std::lock_guard g(lock);
    auto s = std::make_shared<Send>();
    s->queue = queue;
    s->msg = msg;
    s->thread = (unsigned)SDL_GetThreadID(nullptr);
    const Ring &r = rings[s->thread];
    s->nfn = r.n < RING ? r.n : RING;
    for (size_t i = 0; i < s->nfn; ++i) s->fn[i] = r.fn[(r.n - s->nfn + i) % RING];
    auto cur = current.find(s->thread);
    if (cur != current.end()) s->parent = cur->second;
    sends.push_back(s);
    if (sends.size() > 1024) sends.pop_front();
}

// A message was delivered to this thread: pair it with the oldest unconsumed send of that value to
// that queue. The thread's next PI DMA is attributed to that sender, and through the message the sender
// itself had received, to its senders in turn.
extern "C" void n64host_golden_recv(uint8_t *, uint32_t queue, uint32_t out) {
    if (!on()) return;
    uint32_t msg;
    if ((out & 0xE0000000u) != 0x80000000u && (out & 0xE0000000u) != 0xA0000000u) return;
    std::memcpy(&msg, n64host_rdram + (out & 0x3FFFFFFu & ~3u), 4);
    std::lock_guard g(lock);
    for (auto it = sends.begin(); it != sends.end(); ++it)
        if ((*it)->queue == queue && (*it)->msg == msg) {
            current[(unsigned)SDL_GetThreadID(nullptr)] = *it;
            sends.erase(it);
            return;
        }
}

extern "C" void n64host_golden_pi(uint32_t rom, uint32_t ram_addr, uint32_t length) {
    if (!on()) return;
    std::lock_guard g(lock);
    const unsigned me = (unsigned)SDL_GetThreadID(nullptr);
    std::string chain;
    auto cur = current.find(me);
    int depth = 0;
    for (const Send *s = cur != current.end() ? cur->second.get() : nullptr; s && depth < 6; s = s->parent.get(), ++depth) {
        std::string fns;
        for (size_t i = 0; i < s->nfn; ++i) fns += std::string(i ? "," : "") + "\"" + s->fn[i] + "\"";
        chain += std::string(depth ? "," : "") + "{\"thread\":" + std::to_string(s->thread) + ",\"fns\":[" + fns + "]}";
    }
    std::fprintf(events,
        "{\"ev\":\"pi\",\"vi\":%llu,\"rom\":%u,\"ram\":%u,\"len\":%u,\"thread\":%u,\"req\":[%s]}\n",
        (unsigned long long)vi_count.load(), rom, ram_addr, length, me, chain.c_str());
}

extern "C" void n64host_golden_finish() {
    if (!events) return;
    std::lock_guard g(lock);
    write_trace("final");
    std::fflush(events); std::fflush(acmd_all); std::fflush(pcm_all);
}
