#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>

inline void n64host_require_microcode(bool supported, uint32_t text, uint32_t data) {
    if (supported) return;
    std::fprintf(stderr,
        "n64host: unsupported graphics microcode text=0x%08X data=0x%08X; no display list executed\n",
        text, data);
    // Avoid running global thread destructors on an unrecoverable renderer error.
    std::_Exit(4);
}
