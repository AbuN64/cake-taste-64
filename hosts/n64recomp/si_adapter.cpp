// Raw SI DMA owns a 64-byte PIF exchange; both directions complete through OS_EVENT_SI.
#include <array>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include "host_profile.h"
#include "librecomp/game.hpp"
#include "ultramodern/ultramodern.hpp"
#include "ultramodern/input.hpp"
#include "addrmap.h"

ultramodern::input::callbacks_t host_input_callbacks();
void convert_to_n64_range(float, float, int8_t&, int8_t&);
namespace {
std::mutex si_mutex;
std::array<uint8_t, 64> pif{};
bool have_request = false;
[[noreturn]] void fail(const char *why) {
    std::fprintf(stderr, "n64host: raw SI DMA: %s\n", why);
    std::exit(4);
}
void execute() {
    if (pif[63] != 1) fail("unsupported PIF control byte");
    auto input = host_input_callbacks();
    unsigned pos = 0, channel = 0;
    while (pos < 63) {
        unsigned tx = pif[pos];
        if (tx == 0xfe) break;
        if (tx == 0xff) { ++pos; continue; }
        if (tx == 0) { ++pos; ++channel; continue; }
        if (tx > 63 || pos + 2 >= 63) fail("invalid PIF transmit length");
        unsigned rx = pif[pos + 1] & 63;
        if (!rx || pos + 2 + tx + rx > 63) fail("invalid PIF receive length");
        pif[pos + 1] = rx;
        auto device = input.get_connected_device_info(channel);
        const bool connected = device.connected_device == ultramodern::input::Device::Controller;
        unsigned command = pif[pos + 2], result = pos + 2 + tx;
        if (!connected) {
            pif[pos + 1] |= 0x80; // no response from this channel
        } else if (command == 0 || command == 0xff) {
            if (tx != 1 || rx != 3) fail("invalid controller status packet");
            pif[result] = 5; pif[result + 1] = 0;
            pif[result + 2] = device.connected_pak != ultramodern::input::Pak::None;
        } else if (command == 1) {
            if (tx != 1 || rx != 4) fail("invalid controller read packet");
            uint16_t buttons = 0; float x = 0, y = 0; int8_t sx = 0, sy = 0;
            if (input.poll_input) input.poll_input();
            if (!input.get_input(channel, &buttons, &x, &y)) pif[pos + 1] |= 0x80;
            convert_to_n64_range(x, y, sx, sy);
            pif[result] = buttons >> 8; pif[result + 1] = buttons;
            pif[result + 2] = uint8_t(sx); pif[result + 3] = uint8_t(sy);
        } else if ((command == 2 || command == 3) && device.connected_pak == ultramodern::input::Pak::None) {
            pif[pos + 1] |= 0x80;
        } else {
            fail("unsupported PIF device command");
        }
        pos += 2 + tx + rx;
        ++channel;
    }
    pif[63] = 0;
}
}
extern "C" void __osSiRawStartDma_recomp(uint8_t *rdram, recomp_context *ctx) {
    const uint32_t direction = uint32_t(ctx->r4), address = uint32_t(ctx->r5);
    const uintptr_t physical = uintptr_t(n64host_xlate_slow(rdram, address)) - uintptr_t(rdram);
    if (direction > 1 || (physical & 7) || physical > HOST_RDRAM_SIZE - 64) fail("invalid transfer");
    {
        std::lock_guard lock(si_mutex);
        if (direction == 1) {
            for (unsigned i = 0; i < 64; ++i) pif[i] = rdram[physical + (i ^ 3)];
            execute();
            have_request = true;
        } else {
            if (!have_request) fail("read without a PIF request");
            for (unsigned i = 0; i < 64; ++i) rdram[physical + (i ^ 3)] = pif[i];
        }
    }
    ctx->r2 = 0;
    ultramodern::send_si_message();
}
