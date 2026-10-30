#pragma once
#include <atomic>
#include <cstdint>

// Which graphics frames the oracle draws. A TAS movie replays at turbo with the deterministic clock, so the picture
// never decides game state, and the guest reads RDRAM, never RT64's render targets: a frame is worth drawing only
// when a picture of it is read. Software Vulkan draws and presents one frame for ~8 ms of CPU (pp-1p-match: ~900 of
// ~1000 CPU seconds of the original, all of them on frames nobody read).
// With `lead` 0 (the default) every frame is drawn. With `lead` K and a picture point every `every` polls, the frames
// submitted in the K polls before each picture point, and before the exit, are drawn; the others are skipped whole
// (`every` 0: no picture points, only the exit's).
// `poll` is the poll the guest is in when the graphics thread takes the frame (a task submitted after poll Q is drawn
// when a picture at poll P > Q is wanted and P - Q <= lead).
struct RenderGate {
    uint64_t lead = 0, every = 0;
    std::atomic<uint64_t> exit_poll{UINT64_MAX};  // set once the movie has ended: the poll the host quits at
    bool wanted(uint64_t poll) const {
        if (!lead) return true;
        return (every && every - poll % every <= lead) || exit_poll.load(std::memory_order_relaxed) - poll <= lead;
    }
};
