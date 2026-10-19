// Golden recorder for the oracle host: a test instrument only. Everything is written when
// N64R_GOLDEN_DIR is set and is a no-op otherwise. It knows nothing about any game.
//   N64R_GOLDEN_DIR   output directory (events.jsonl, dl-*.bin, acmd-all.bin, pcm-all.s16, trace-*.tsv)
//   N64R_SCRIPT       lines "<vi> key <Name>", "<vi> checkpoint <name>", "<vi> quit" (vi = retrace count)
//   N64R_SHOT_CMD     shell template run at a checkpoint; "%s" is replaced by the PNG path
#include <SDL.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#include "ultramodern/renderer_context.hpp"

extern uint8_t *n64host_rdram;
void n64host_script_key(const char *name);

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
std::string &armed = *new std::string;   // checkpoint waiting for its first graphics task
uint64_t acmd_seq = 0, acmd_bytes = 0, pcm_samples = 0, dl_seq = 0;
unsigned pcm_rate = 0;
std::unordered_map<const char *, size_t> &seen = *new std::unordered_map<const char *, size_t>;
std::vector<Entry> &order = *new std::vector<Entry>;

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
}  // namespace

extern "C" void n64host_golden_init() {
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
            std::fprintf(events, "{\"ev\":\"quit\",\"vi\":%llu}\n", (unsigned long long)now);
            std::fflush(events);
            write_trace("final");
            std::fflush(acmd_all); std::fflush(pcm_all);
            // Everything is on disk. Runtime teardown races the running guest threads (a crash after the
            // goldens are written), so a scripted quit ends the process here with a clean status.
            std::fflush(stdout); std::fflush(stderr);
            std::_Exit(0);
        }
    }
}

extern "C" uint64_t n64host_golden_frame() { return vi_count.load(); }

extern "C" void n64host_golden_entry(const char *name, unsigned thread) {
    if (!on()) return;
    std::lock_guard g(lock);
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

extern "C" void n64host_golden_pi(uint32_t rom, uint32_t ram_addr, uint32_t length) {
    if (!on()) return;
    std::lock_guard g(lock);
    std::fprintf(events, "{\"ev\":\"pi\",\"vi\":%llu,\"rom\":%u,\"ram\":%u,\"len\":%u}\n",
        (unsigned long long)vi_count.load(), rom, ram_addr, length);
}

extern "C" void n64host_golden_finish() {
    if (!on()) return;
    std::lock_guard g(lock);
    write_trace("final");
    std::fflush(events); std::fflush(acmd_all); std::fflush(pcm_all);
}
