// Raw SI DMA owns a 64-byte PIF exchange; both directions complete through OS_EVENT_SI.
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include "host_profile.h"
#include "librecomp/game.hpp"
#include "ultramodern/ultramodern.hpp"
#include "ultramodern/input.hpp"
#include "addrmap.h"

ultramodern::input::callbacks_t host_input_callbacks();
void host_si_schedule(void (*)(), uint64_t);
void convert_to_n64_range(float, float, int8_t&, int8_t&);
extern "C" bool n64host_golden_pad(unsigned channel, uint16_t *buttons, int8_t *x, int8_t *y);
namespace {
std::mutex si_mutex;
std::array<uint8_t, 64> request{}, pif{};
bool have_request = false, busy = false;
uint8_t *pending_ram = nullptr;
uintptr_t pending_physical = 0;
uint32_t pending_direction = 0;
[[noreturn]] void fail(const char *why) {
    std::fprintf(stderr, "n64host: raw SI DMA: %s\n", why);
    std::fputs("n64host: PIF request=", stderr);
    for (auto byte : request) std::fprintf(stderr, "%02x", byte);
    std::fputc("\n"[0], stderr);
    std::_Exit(4);
}
uint8_t pak_crc(const uint8_t *bytes) {
    // Remainder of the 256 data bits followed by eight zero bits, polynomial 0x185.
    uint8_t remainder = 0;
    for (unsigned i = 0; i < 32; ++i) {
        remainder ^= bytes[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            remainder = uint8_t((remainder << 1) ^ ((remainder & 0x80) ? 0x85 : 0));
    }
    return remainder;
}
// The Controller Pak card N64R_CPAK names (a 32 KiB image, as the port's --cpak); unset: nothing in the slot.
// Packets: read [2, addr_hi, addr_lo] -> 32 bytes + CRC; write [3, addr_hi, addr_lo, 32 bytes] -> CRC. The low five
// bits of the address field are its CRC; blocks from 0x400 are the accessory area, which a memory card answers
// with zeros and ignores. A bad address CRC is answered with an inverted data CRC, a failed transfer to the SDK.
std::array<uint8_t, 0x8000> card{};
const char *card_path = nullptr;
void card_open() {
    static bool done = false;
    if (done) return;
    done = true;
    card_path = std::getenv("N64R_CPAK");
    if (!card_path || !*card_path) { card_path = nullptr; return; }
    FILE *file = std::fopen(card_path, "rb");
    if (!file || std::fread(card.data(), 1, card.size(), file) != card.size() || std::fgetc(file) != EOF)
        fail("N64R_CPAK is not a 32 KiB image");
    std::fclose(file);
}
uint8_t address_crc(unsigned block) {
    unsigned reg = 0;
    for (int bit = 10; bit >= -5; --bit) {
        unsigned in = bit >= 0 ? (block >> bit) & 1u : 0u, top = (reg >> 4) & 1u;
        reg = ((reg << 1) | in) & 0x1fu;
        if (top) reg ^= 0x15u;
    }
    return uint8_t(reg);
}
void card_packet(uint8_t *command, uint8_t *reply, bool write) {
    unsigned field = (command[1] << 8) | command[2], block = field >> 5;
    bool good = address_crc(block) == (field & 0x1f);
    if (write) {
        if (good && block < card.size() / 32) {
            std::memcpy(card.data() + block * 32, command + 3, 32);
            FILE *file = std::fopen(card_path, "r+b");
            good = file && std::fseek(file, long(block * 32), SEEK_SET) == 0 &&
                   std::fwrite(card.data() + block * 32, 1, 32, file) == 32;
            if (file) good = std::fclose(file) == 0 && good;
        }
        reply[0] = uint8_t(pak_crc(command + 3) ^ (good ? 0 : 0xff));
    } else {
        std::memset(reply, 0, 32);
        if (good && block < card.size() / 32) std::memcpy(reply, card.data() + block * 32, 32);
        reply[32] = uint8_t(pak_crc(reply) ^ (good ? 0 : 0xff));
    }
}
unsigned execute() {
    card_open();
    pif = request;
    if (pif[63] != 1) fail("unsupported PIF control byte");
    auto input = host_input_callbacks();
    unsigned pos = 0, channel = 0, active = 0;
    while (pos < 63) {
        unsigned tx = pif[pos];
        if (tx == 0xfe) break;
        if (tx == 0xff) { ++pos; continue; }
        if (tx == 0) { ++pos; ++channel; continue; }
        // Older SDK packet structs can leave one alignment byte before END.
        if (pos + 1 < 63 && pif[pos + 1] == 0xfe) break;
        if (tx > 63 || pos + 2 >= 63) fail("invalid PIF transmit length");
        unsigned rx = pif[pos + 1] & 63;
        if (!rx || pos + 2 + tx + rx > 63) fail("invalid PIF receive length");
        pif[pos + 1] = rx;
        auto device = input.get_connected_device_info(channel);
        const bool connected = device.connected_device == ultramodern::input::Device::Controller;
        active += connected;
        unsigned command = pif[pos + 2], result = pos + 2 + tx;
        if (!connected) {
            pif[pos + 1] |= 0x80; // no response from this channel
        } else if (command == 0 || command == 0xff) {
            if (tx != 1 || rx != 3) fail("invalid controller status packet");
            pif[result] = 5; pif[result + 1] = 0;
            pif[result + 2] = card_path || device.connected_pak != ultramodern::input::Pak::None ? 1 : 2;
        } else if (command == 1) {
            if (tx != 1 || rx != 4) fail("invalid controller read packet");
            uint16_t buttons = 0; float x = 0, y = 0; int8_t sx = 0, sy = 0;
            if (!n64host_golden_pad(channel, &buttons, &sx, &sy)) {
                if (input.poll_input) input.poll_input();
                if (!input.get_input(channel, &buttons, &x, &y)) pif[pos + 1] |= 0x80;
                convert_to_n64_range(x, y, sx, sy);
            }
            pif[result] = buttons >> 8; pif[result + 1] = buttons;
            pif[result + 2] = uint8_t(sx); pif[result + 3] = uint8_t(sy);
        } else if (command == 2 && card_path) {
            if (tx != 3 || rx != 33) fail("invalid accessory read packet");
            card_packet(&pif[pos + 2], &pif[result], false);
        } else if (command == 3 && card_path) {
            if (tx != 35 || rx != 1) fail("invalid accessory write packet");
            card_packet(&pif[pos + 2], &pif[result], true);
        } else if ((command == 2 || command == 3) && device.connected_pak == ultramodern::input::Pak::None) {
            // A connected controller answers even when its accessory is absent.
            // The inverted data CRC triggers the SDK's status-query/error path.
            if (command == 2) {
                if (tx != 3 || rx != 33) fail("invalid accessory read packet");
                pif[result + 32] = uint8_t(~pak_crc(pif.data() + result));
            } else {
                if (tx != 35 || rx != 1) fail("invalid accessory write packet");
                pif[result] = uint8_t(~pak_crc(pif.data() + pos + 5));
            }
        } else {
            fail("unsupported PIF device command");
        }
        pos += 2 + tx + rx;
        ++channel;
    }
    pif[63] = 0;
    return active;
}
void complete() {
    {
        std::lock_guard lock(si_mutex);
        if (pending_direction == 1) {
            for (unsigned i = 0; i < 64; ++i)
                request[i] = pending_ram[pending_physical + (i ^ 3)];
            have_request = true;
        } else {
            for (unsigned i = 0; i < 64; ++i)
                pending_ram[pending_physical + (i ^ 3)] = pif[i];
        }
        busy = false;
    }
    ultramodern::send_si_message();
}
}
extern "C" void __osSiDeviceBusy_recomp(uint8_t*, recomp_context *ctx) {
    std::lock_guard lock(si_mutex);
    ctx->r2 = busy;
}
extern "C" void __osSiRawStartDma_recomp(uint8_t *rdram, recomp_context *ctx) {
    const uint32_t direction = uint32_t(ctx->r4), address = uint32_t(ctx->r5);
    const uintptr_t physical = uintptr_t(n64host_xlate_slow(rdram, address)) - uintptr_t(rdram);
    if (direction > 1 || (physical & 7) || physical > HOST_RDRAM_SIZE - 64) fail("invalid transfer");
    uint64_t duration_ns = 64000; // 6000 CPU cycles at 93.75 MHz for the write DMA.
    {
        std::lock_guard lock(si_mutex);
        if (busy) { ctx->r2 = -1; return; }
        busy = true;
        pending_ram = rdram;
        pending_physical = physical;
        pending_direction = direction;
        if (direction == 0) {
            if (!have_request) fail("read without a PIF request");
            const unsigned active = execute();
            duration_ns = 256000 + 320000 * active; // PIF processing and active Joybus channels.
        }
    }
    ctx->r2 = 0;
    host_si_schedule(complete, duration_ns);
}
