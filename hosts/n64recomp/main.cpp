// Generic N64Recomp host. No game constants: entrypoint and ROM hash come from the
// generated host_profile.h; the ROM image path comes from N64R_ROM at run time.
#include <SDL.h>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <unistd.h>
#include <vector>
#include "host.h"
#include "host_profile.h"
#include "boot_profile.h"
#include "host_input_profile.h"
#include "librecomp/overlays.hpp"
#include "librecomp/rsp.hpp"
#include "xxh3.h"
#include "recomp_overlays.inl"

size_t n64host_rom_size;

// ---- probes -------------------------------------------------------------
extern "C" void n64host_cpu_tick(int charged);
extern uint8_t *n64host_rdram;
extern "C" void n64host_golden_init();
extern "C" void n64host_golden_vi();
extern "C" void n64host_golden_finish();
extern "C" uint64_t n64host_golden_frame();
extern "C" void n64host_golden_entry(const char *, unsigned);
extern "C" void n64host_golden_audio(uint32_t, uint32_t, uint32_t);
extern "C" void n64host_view_vi();
extern "C" void n64host_trace(const char *name, int charged) {
    n64host_cpu_tick(charged);
    n64host_golden_entry(name, (unsigned)SDL_GetThreadID(nullptr));
    static FILE *f = [] { const char *p = std::getenv("N64R_TRACE"); FILE *h = p ? std::fopen(p, "w") : nullptr; if (h) setvbuf(h, nullptr, _IOLBF, 0); return h; }();
    if (!f) return;
    static std::atomic<unsigned> frame{0};
    static std::mutex m;
    std::lock_guard<std::mutex> g(m);
    std::fprintf(f, "%u %u %s\n", (unsigned)n64host_golden_frame(), (unsigned)SDL_GetThreadID(nullptr), name);
}
extern "C" void n64host_addrmap_init(void);
extern "C" void n64host_memwatch(unsigned, unsigned, int) {}

// ---- callbacks ----------------------------------------------------------
RspExitReason rsp_audio(uint8_t* rdram, uint32_t ucode_addr);
namespace {
SDL_Window *g_window;
void *create_gfx() {
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO | SDL_INIT_GAMECONTROLLER) != 0) {
        std::fprintf(stderr, "n64host: SDL initialization failed: %s\n", SDL_GetError());
        std::exit(3);
    }
    return nullptr;
}
// SDL's offscreen and dummy video drivers have no display: RT64 then renders to a headless Vulkan surface and
// pictures come out as files (view.cpp). No X11 or Wayland server is involved.
bool headless() {
    const char *driver = SDL_GetCurrentVideoDriver();
    return driver && (std::strcmp(driver, "offscreen") == 0 || std::strcmp(driver, "dummy") == 0);
}
ultramodern::renderer::WindowHandle create_window(void *) {
    if (headless()) return {};
    g_window = SDL_CreateWindow("n64host", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, 960, 540,
                                SDL_WINDOW_RESIZABLE | SDL_WINDOW_VULKAN);
    if (!g_window) {
        std::fprintf(stderr, "n64host: window creation failed: %s\n", SDL_GetError());
        std::exit(3);
    }
    return g_window;
}
void update_gfx(void *) {
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        if (e.type == SDL_QUIT) ultramodern::quit();
        n64host_input_event(e);
    }
    n64host_update_input();
}
RspUcodeFunc *get_ucode(const OSTask *task) {
    // type 2 = audio (M_AUDTASK); gfx tasks go to the renderer, not the RSP interpreter
    if (task->t.type == 2) n64host_golden_audio(task->t.ucode, task->t.data_ptr, task->t.data_size);
    return task->t.type == 2 ? rsp_audio : nullptr;
}
void message_box(const char *msg) { std::fprintf(stderr, "n64host: %s\n", msg); }
std::string thread_name(const OSThread *) { return {}; }
void vi_cb() { n64host_golden_vi(); n64host_view_vi(); }
void gfx_init_cb() {}

recomp_func_t *find_func(uint32_t vram) {
    for (size_t s = 0; s < num_sections; ++s)
        for (size_t i = 0; i < section_table[s].num_funcs; ++i)
            if (section_table[s].ram_addr + section_table[s].funcs[i].offset == vram)
                return section_table[s].funcs[i].func;
    return nullptr;
}
// When the profile entrypoint was refused by the recompiler (a cop0/TLB boot stub), the profile
// names the stub's continuation and initial stack; its TLB mappings are the address map's table.
extern "C" int32_t n64host_func_vram(int32_t va);
void configure_context(uint8_t *, recomp_context *ctx) {
    cop0_status_write(ctx, HOST_INITIAL_STATUS);
}
void entry(uint8_t *rdram, recomp_context *ctx) {
    n64host_rdram = rdram;
    configure_context(rdram, ctx);
    // The runtime resolves thread entry points by guest address; a pointer into a mapped KUSEG window
    // names the same function as its KSEG0 label, so register those aliases with the runtime.
    for (size_t s = 0; s < num_sections; ++s)
        for (size_t i = 0; i < section_table[s].num_funcs; ++i) {
            int32_t v = (int32_t)(section_table[s].ram_addr + section_table[s].funcs[i].offset);
            recomp::overlays::add_loaded_function(v, section_table[s].funcs[i].func);
            int32_t alias = v & 0x7FFFFFFF;
            if (v < 0 && n64host_func_vram(alias) == v) recomp::overlays::add_loaded_function(alias, section_table[s].funcs[i].func);
        }
    // Functions the profile renames to runtime replacements stay out of N64Recomp's table; guest code that
    // calls one through a stored pointer needs its address registered.
    static const struct { uint32_t vram; recomp_func_t *func; } replaced[] = {
#if __has_include("replaced_funcs.inl")
#include "replaced_funcs.inl"
#else
        { 0u, nullptr }
#endif
    };
    for (const auto &r : replaced)
        if (r.func) recomp::overlays::add_loaded_function((int32_t)r.vram, r.func);
    recomp_func_t *f = find_func(HOST_ENTRY_ADDR);
    if (!f) {
        f = find_func(HOST_BOOT_MAIN);
        ctx->r29 = (gpr)(int32_t)HOST_BOOT_SP;
    }
    if (!f) { std::fprintf(stderr, "n64host: no function at entrypoint 0x%08X or boot main 0x%08X\n", HOST_ENTRY_ADDR, HOST_BOOT_MAIN); std::exit(4); }
    f(rdram, ctx);
}
}
int main(int argc, char **argv) {
    n64host_addrmap_init();
    n64host_golden_init();
    const char *rom_path = std::getenv("N64R_ROM");
    if (!rom_path) { std::fprintf(stderr, "set N64R_ROM to the original ROM image\n"); return 2; }
    std::ifstream in(rom_path, std::ios::binary);
    std::vector<uint8_t> rom((std::istreambuf_iterator<char>(in)), {});
    if (rom.empty()) { std::fprintf(stderr, "cannot read %s\n", rom_path); return 2; }

    // Generated section offsets belong to the synthetic analysis image; runtime DMA offsets
    // must refer to the original ROM. Match by link address, never by table position.
    for (size_t i = 0; i < num_sections; ++i) {
        bool found = false;
        for (const auto &mapping : host_rom_sections) {
            if (section_table[i].ram_addr != mapping[0]) continue;
            section_table[i].rom_addr = mapping[1];
            found = true;
            break;
        }
        if (!found) { std::fprintf(stderr, "n64host: missing original ROM section at 0x%08X\n", section_table[i].ram_addr); return 4; }
    }
    n64host_rom_size = rom.size();
    recomp::overlays::overlay_section_table_data_t sections{section_table, num_sections, num_sections};
    recomp::overlays::overlays_by_index_t by_index{overlay_sections_by_index, 1};
    recomp::overlays::register_overlays(sections, by_index);

    recomp::GameEntry g{};
    g.rom_hash = XXH3_64bits(rom.data(), rom.size());
    g.internal_name = "n64host";
    g.display_name = HOST_VERSION;
    g.game_id = u8"host";
    g.mod_game_id = "host";
    g.save_type = recomp::SaveType::AllowAll;
    g.is_enabled = true;
    g.entrypoint_address = (gpr)(int32_t)HOST_ROM_ENTRY_ADDR;
    g.thread_create_callback = configure_context;
    g.entrypoint = entry;
    if (!recomp::register_game(g)) { std::fprintf(stderr, "register_game failed\n"); return 3; }
    const char *cfgdir = std::getenv("N64R_CONFIG");
    recomp::register_config_path(cfgdir ? cfgdir : ".");
    // Install the configured original ROM where load_stored_rom expects it; hash is checked there.
    std::filesystem::path cdir = cfgdir ? cfgdir : ".";
    std::filesystem::create_directories(cdir);
    // Runs may share one config folder: replace the stored ROM only when it differs, and atomically.
    const auto stored = cdir / g.stored_filename();
    std::ifstream stored_in(stored, std::ios::binary);
    const std::vector<uint8_t> stored_rom((std::istreambuf_iterator<char>(stored_in)), {});
    if (stored_rom != rom) {
        auto partial = stored;
        partial += "." + std::to_string(getpid());
        std::filesystem::copy_file(rom_path, partial, std::filesystem::copy_options::overwrite_existing);
        std::filesystem::rename(partial, stored);
    }

    std::printf("%s\n", host_controls);
    std::fflush(stdout);
    std::ofstream controls(cdir / "controls.txt");
    controls << host_controls << "\n";
    controls.close();
    if (!controls) { std::fprintf(stderr, "n64host: cannot write controls.txt\n"); return 4; }

    n64host_input_init(); // Validate all profile bindings before runtime threads exist.
    recomp::rsp::callbacks_t rsp_cb{get_ucode};
    ultramodern::gfx_callbacks_t gfx_cb{create_gfx, create_window, update_gfx};
    ultramodern::events::callbacks_t ev_cb{vi_cb, gfx_init_cb};
    ultramodern::error_handling::callbacks_t err_cb{message_box};
    ultramodern::threads::callbacks_t thr_cb{thread_name};

    recomp::Configuration cfg{};
    cfg.argc = argc; cfg.argv = argv;
    cfg.project_version = recomp::Version(0, 1, 0);
    cfg.window_handle = nullptr;
    cfg.rsp_callbacks = rsp_cb;
    cfg.renderer_callbacks = host_renderer_callbacks();
    cfg.audio_callbacks = host_audio_callbacks();
    cfg.input_callbacks = host_input_callbacks();
    cfg.gfx_callbacks = gfx_cb;
    cfg.events_callbacks = ev_cb;
    cfg.error_handling_callbacks = err_cb;
    cfg.threads_callbacks = thr_cb;
    recomp::start_game(u8"host", "");
    recomp::start(cfg);   // runs the gfx loop until quit
    n64host_golden_finish();
    return 0;
}
