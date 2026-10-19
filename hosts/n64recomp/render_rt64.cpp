// RT64 RendererContext. Generic: knows nothing about any game.
#include <SDL.h>
#include <cstring>
#include <cstdio>
#include <chrono>
#include "host.h"
#include "microcode_guard.h"
#include "ultramodern/renderer_context.hpp"
#include "hle/rt64_application.h"
#include "hle/rt64_state.h"
#include "hle/rt64_interpreter.h"

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
        auto r = app_->setup(SDL_GetThreadID(nullptr));
        setup_result = static_cast<SetupResult>(r);
        if (r != RT64::Application::SetupResult::Success) app_.reset();
    }
    bool valid() override { return app_ != nullptr; }
    bool update_config(const ultramodern::renderer::GraphicsConfig &, const ultramodern::renderer::GraphicsConfig &) override { return true; }
    void enable_instant_present() override {}
    void send_dl(const OSTask *task) override {
        n64host_golden_gfx(task->t.ucode, task->t.ucode_data, task->t.data_ptr, task->t.data_size);
        app_->state->rsp->reset();
        app_->interpreter->loadUCodeGBI(task->t.ucode & 0x3FFFFFF, task->t.ucode_data & 0x3FFFFFF, true);
        n64host_require_microcode(app_->interpreter->hleGBI != nullptr &&
            app_->interpreter->hleGBI->ucode != RT64::GBIUCode::Unknown,
            task->t.ucode, task->t.ucode_data);
        app_->processDisplayLists(app_->core.RDRAM, task->t.data_ptr & 0x3FFFFFF, 0, true);
        ++tasks_;
        if (tasks_ == 1 || tasks_ % 600 == 0)
            std::fprintf(stderr, "n64host: graphics tasks=%llu elapsed_ms=%lld dl=0x%08X bytes=%u\n",
                (unsigned long long)tasks_, elapsed_ms(), task->t.data_ptr, task->t.data_size);
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
    uint32_t get_display_framerate() const override { return 60; }
    float get_resolution_scale() const override { return 1.0f; }
private:
    uint64_t tasks_ = 0, screens_ = 0;
    const std::chrono::steady_clock::time_point started_ = std::chrono::steady_clock::now();
    long long elapsed_ms() const {
        return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - started_).count();
    }
    uint32_t dummy_ = 0;
    std::unique_ptr<RT64::Application> app_;
};

std::unique_ptr<RendererContext> create(uint8_t *rdram, WindowHandle w, bool dev) {
    return std::make_unique<RT64Context>(rdram, w, dev);
}
std::string api_name(ultramodern::renderer::GraphicsApi) { return "Vulkan"; }
}

ultramodern::renderer::callbacks_t host_renderer_callbacks() { return {create, api_name}; }
