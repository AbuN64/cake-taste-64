// RT64 RendererContext. Generic: knows nothing about any game.
#include <SDL.h>
#include <cstring>
#include <cstdio>
#include <chrono>
#include <mutex>
#include <vector>
#include "host.h"
#include "microcode_guard.h"
#include "ultramodern/renderer_context.hpp"
#include "hle/rt64_application.h"
#include "hle/rt64_state.h"
#include "hle/rt64_interpreter.h"
#include "hle/rt64_vi.h"
#include "render/rt64_native_target.h"
#include "render/rt64_render_worker.h"

extern "C" void n64host_golden_gfx(uint32_t, uint32_t, uint32_t, uint32_t);
namespace {
using namespace ultramodern::renderer;

class RT64Context final : public RendererContext {
public:
    RT64Context(uint8_t *rdram, WindowHandle window, bool developer_mode) {
        auto *vi = get_vi_regs();
        RT64::Application::Core core{};
        core.window = window;
        core.RDRAM = rdram;
        core.HEADER = nullptr; core.DMEM = nullptr; core.IMEM = nullptr;
        core.MI_INTR_REG = &dummy_;
        core.DPC_START_REG = core.DPC_END_REG = core.DPC_CURRENT_REG = core.DPC_STATUS_REG = &dummy_;
        core.DPC_CLOCK_REG = core.DPC_BUFBUSY_REG = core.DPC_PIPEBUSY_REG = core.DPC_TMEM_REG = &dummy_;
        core.VI_STATUS_REG = &vi->VI_STATUS_REG;
        core.VI_ORIGIN_REG = &vi->VI_ORIGIN_REG;
        core.VI_WIDTH_REG = &vi->VI_WIDTH_REG;
        core.VI_INTR_REG = &vi->VI_INTR_REG;
        core.VI_V_CURRENT_LINE_REG = &vi->VI_V_CURRENT_LINE_REG;
        core.VI_TIMING_REG = &vi->VI_TIMING_REG;
        core.VI_V_SYNC_REG = &vi->VI_V_SYNC_REG;
        core.VI_H_SYNC_REG = &vi->VI_H_SYNC_REG;
        core.VI_LEAP_REG = &vi->VI_LEAP_REG;
        core.VI_H_START_REG = &vi->VI_H_START_REG;
        core.VI_V_START_REG = &vi->VI_V_START_REG;
        core.VI_V_BURST_REG = &vi->VI_V_BURST_REG;
        core.VI_X_SCALE_REG = &vi->VI_X_SCALE_REG;
        core.VI_Y_SCALE_REG = &vi->VI_Y_SCALE_REG;
        core.checkInterrupts = [] {};

        RT64::ApplicationConfiguration cfg;
        cfg.useConfigurationFile = false;
        app_ = std::make_unique<RT64::Application>(core, cfg);
        app_->userConfig.developerMode = developer_mode;
        if (window == WindowHandle{}) {
            // Headless (no display server): RT64 presents to a VK_EXT_headless_surface swap chain
            // (patches/rt64-headless-surface.diff); render at the original resolution and rate, no window scaling.
            app_->userConfig.resolution = RT64::UserConfiguration::Resolution::Original;
            app_->userConfig.refreshRate = RT64::UserConfiguration::RefreshRate::Original;
            std::fprintf(stderr, "n64host: headless renderer (no window)\n");
        }
        auto r = app_->setup(SDL_GetThreadID(nullptr));
        setup_result = static_cast<SetupResult>(r);
        if (r != RT64::Application::SetupResult::Success) app_.reset();
    }
    bool valid() override { return app_ != nullptr; }
    bool update_config(const ultramodern::renderer::GraphicsConfig &, const ultramodern::renderer::GraphicsConfig &) override { return true; }
    void enable_instant_present() override {}
    void send_dl(const OSTask *task) override {
        const auto began = std::chrono::steady_clock::now();
        n64host_golden_gfx(task->t.ucode, task->t.ucode_data, task->t.data_ptr, task->t.data_size);
        app_->state->rsp->reset();
        app_->interpreter->loadUCodeGBI(task->t.ucode & 0x3FFFFFF, task->t.ucode_data & 0x3FFFFFF, true);
        n64host_require_microcode(app_->interpreter->hleGBI != nullptr &&
            app_->interpreter->hleGBI->ucode != RT64::GBIUCode::Unknown,
            task->t.ucode, task->t.ucode_data);
        app_->processDisplayLists(app_->core.RDRAM, task->t.data_ptr & 0x3FFFFFF, 0, true);
        ++tasks_;
        dl_ns_ += std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - began).count();
        if (tasks_ == 1 || tasks_ % 600 == 0)
            std::fprintf(stderr, "n64host: graphics tasks=%llu elapsed_ms=%lld dl=0x%08X bytes=%u mean_send_dl_us=%lld\n",
                (unsigned long long)tasks_, elapsed_ms(), task->t.data_ptr, task->t.data_size,
                (long long)(dl_ns_ / 1000 / (long long)tasks_));
    }
    void send_dummy_workload(uint32_t) override {}
    void update_screen() override {
        app_->updateScreen();
        ++screens_;
        if (screens_ == 1 || screens_ % 600 == 0)
            std::fprintf(stderr, "n64host: screen updates=%llu elapsed_ms=%lld graphics_tasks=%llu\n",
                (unsigned long long)screens_, elapsed_ms(), (unsigned long long)tasks_);
    }
    void shutdown() override { if (app_) app_->end(); }

    // The colour image at physical address `phys` as the VI shows it (size and format from the VI registers),
    // after every graphics task submitted so far has rendered, scaled to w x h (0xAABBGGRR, top row first).
    // RT64's render target when it drew that image, else guest RDRAM (a CPU-drawn picture). 0 when blank.
    int read_frame(uint32_t phys, uint32_t *rgba, uint32_t w, uint32_t h) {
        if (!app_ || !rgba || !w || !h) return 0;
        const auto *regs = get_vi_regs();
        RT64::VI vi;
        vi.status.word = regs->VI_STATUS_REG; vi.origin = regs->VI_ORIGIN_REG; vi.width = regs->VI_WIDTH_REG;
        vi.intr = regs->VI_INTR_REG; vi.vCurrentLine = regs->VI_V_CURRENT_LINE_REG; vi.burst.word = regs->VI_TIMING_REG;
        vi.vSync = regs->VI_V_SYNC_REG; vi.hSync.word = regs->VI_H_SYNC_REG; vi.leap.word = regs->VI_LEAP_REG;
        vi.hRegion.word = regs->VI_H_START_REG; vi.vRegion.word = regs->VI_V_START_REG; vi.vBurst.word = regs->VI_V_BURST_REG;
        vi.xTransform.word = regs->VI_X_SCALE_REG; vi.yTransform.word = regs->VI_Y_SCALE_REG;
        const uint8_t siz = vi.fbSiz();
        if (!vi.visible() || siz < G_IM_SIZ_16b) return 0;
        const auto size = vi.fbSize();
        // Height: the rows the game drew into that colour image when the renderer has it (as the native port reads
        // its surfaces), else the VI's estimate.
        const uint32_t fw = size.x, bpp = siz == G_IM_SIZ_32b ? 4 : 2;
        uint32_t fh = size.y;
        if (!fw || !fh || fw > 1024 || fh > 1024) return 0;
        phys &= 0x3FFFFFF;
        std::vector<uint8_t> image;  // guest byte order (big-endian pixels)
        std::lock_guard<std::mutex> one(read_mutex_);
        app_->workloadQueue->waitForWorkloadId(app_->state->workloadId);
        bool rendered = false;
        {
            auto &shared = *app_->sharedQueueResources;
            std::scoped_lock<std::mutex> workload(shared.workloadMutex);
            std::scoped_lock<std::mutex> managers(shared.managerMutex);
            RT64::Framebuffer *fb = shared.framebufferManager.find(phys);
            if (fb != nullptr && fb->siz == siz && fb->width == fw && fb->height > 0 && fb->height <= 1024) {
                RT64::RenderTarget &target = shared.renderTargetManager.get(
                    RT64::RenderTargetKey(fb->addressStart, fb->width, fb->siz, RT64::Framebuffer::Type::Color), true);
                if (!target.isEmpty()) {
                    fh = fb->height;
                    image.resize(size_t(fw) * fh * bpp);
                    if (!worker_) worker_ = std::make_unique<RT64::RenderWorker>(app_->device.get(), "n64host frame read",
                                                                                 RT64::RenderCommandListType::DIRECT);
                    {
                        RT64::RenderWorkerExecution execution(worker_.get());
                        native_.copyToNative(worker_.get(), &target, fw, 0, fh, siz, G_IM_FMT_RGBA,
                                             3 /* no dither: pattern G_CD_DISABLE */, 0, app_->shaderLibrary.get());
                    }
                    native_.copyToRAM(0, fh, fw, siz, image.data());
                    native_.resetBufferHistory();
                    rendered = true;
                }
            }
        }
        if (fw != shown_w_ || fh != shown_h_ || rendered != shown_rendered_) {
            shown_w_ = fw; shown_h_ = fh; shown_rendered_ = rendered;
            std::fprintf(stderr, "n64host: frame %ux%u %u-bit from %s (VI y %u..%u scale 0x%X)\n", fw, fh, bpp * 8,
                         rendered ? "the renderer" : "RDRAM", vi.vRegion.vStart, vi.vRegion.vEnd, regs->VI_Y_SCALE_REG);
        }
        if (!rendered) {  // RDRAM stores each 32-bit word host-endian: guest byte a lives at a ^ 3.
            image.resize(size_t(fw) * fh * bpp);
            if (phys + image.size() > (8u << 20)) return 0;
            for (size_t i = 0; i < image.size(); ++i) image[i] = app_->core.RDRAM[(phys + i) ^ 3];
        }
        auto channel = [&](uint32_t x, uint32_t y, int c) -> float {
            const uint8_t *p = &image[(size_t(y) * fw + x) * bpp];
            if (bpp == 4) return p[c];
            const uint32_t v = (uint32_t(p[0]) << 8) | p[1];  // RGBA 5551
            if (c == 3) return float((v & 1) * 255u);
            const uint32_t q = (v >> (11 - 5 * c)) & 31;
            return float(q << 3 | q >> 2);
        };
        // Bilinear, the same mapping the native port uses for its pictures.
        for (uint32_t y = 0; y < h; y++) {
            const float sy = std::max(0.0f, (float(y) + 0.5f) * fh / h - 0.5f);
            const uint32_t y0 = std::min(uint32_t(sy), fh - 1), y1 = std::min(y0 + 1, fh - 1);
            const float ty = sy - float(y0);
            for (uint32_t x = 0; x < w; x++) {
                const float sx = std::max(0.0f, (float(x) + 0.5f) * fw / w - 0.5f);
                const uint32_t x0 = std::min(uint32_t(sx), fw - 1), x1 = std::min(x0 + 1, fw - 1);
                const float tx = sx - float(x0);
                uint32_t out = 0;
                for (int c = 0; c < 4; c++) {
                    const float top = channel(x0, y0, c) * (1 - tx) + channel(x1, y0, c) * tx;
                    const float bottom = channel(x0, y1, c) * (1 - tx) + channel(x1, y1, c) * tx;
                    out |= uint32_t(std::lround(top * (1 - ty) + bottom * ty)) << (8 * c);
                }
                rgba[size_t(y) * w + x] = out;
            }
        }
        return 1;
    }
    uint32_t get_display_framerate() const override { return 60; }
    float get_resolution_scale() const override { return 1.0f; }
private:
    uint64_t tasks_ = 0, screens_ = 0;
    long long dl_ns_ = 0;
    const std::chrono::steady_clock::time_point started_ = std::chrono::steady_clock::now();
    long long elapsed_ms() const {
        return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - started_).count();
    }
    uint32_t dummy_ = 0;
    std::unique_ptr<RT64::Application> app_;
    std::mutex read_mutex_;
    uint32_t shown_w_ = 0, shown_h_ = 0;
    bool shown_rendered_ = false;
    std::unique_ptr<RT64::RenderWorker> worker_;
    RT64::NativeTarget native_;
};
RT64Context *g_context;  // the one renderer, for frame reads from guest threads

std::unique_ptr<RendererContext> create(uint8_t *rdram, WindowHandle w, bool dev) {
    auto context = std::make_unique<RT64Context>(rdram, w, dev);
    if (context->valid()) g_context = context.get();
    return context;
}
std::string api_name(ultramodern::renderer::GraphicsApi) { return "Vulkan"; }
}

ultramodern::renderer::callbacks_t host_renderer_callbacks() { return {create, api_name}; }

extern "C" int n64host_frame_read(uint32_t phys, uint32_t *rgba, uint32_t w, uint32_t h) {
    return g_context ? g_context->read_frame(phys, rgba, w, h) : 0;
}
