#pragma once
// Upstream merge helpers were compiled before MEM_* was redirected. Translate
// their aligned block, then use the unchanged helper on a local KSEG0 window.
// A 4/8-byte aligned block cannot cross the smallest TLB page (4096 bytes).
static inline gpr n64host_lwl(uint8_t *rdram, gpr initial, gpr offset, gpr reg) {
    uint32_t address = (uint32_t)(offset + reg);
    if (__builtin_expect(n64host_rec_active, 0)) n64host_replay_taint("unaligned");
    uint8_t *block = n64host_ptr(rdram, address & ~3u);
    gpr local = (gpr)(int32_t)(0x80000000u | (address & 3u));
    return do_lwl(block, initial, 0, local);
}
static inline gpr n64host_lwr(uint8_t *rdram, gpr initial, gpr offset, gpr reg) {
    uint32_t address = (uint32_t)(offset + reg);
    if (__builtin_expect(n64host_rec_active, 0)) n64host_replay_taint("unaligned");
    uint8_t *block = n64host_ptr(rdram, address & ~3u);
    gpr local = (gpr)(int32_t)(0x80000000u | (address & 3u));
    return do_lwr(block, initial, 0, local);
}
static inline gpr n64host_ldl(uint8_t *rdram, gpr initial, gpr offset, gpr reg) {
    uint32_t address = (uint32_t)(offset + reg);
    if (__builtin_expect(n64host_rec_active, 0)) n64host_replay_taint("unaligned");
    uint8_t *block = n64host_ptr(rdram, address & ~7u);
    gpr local = (gpr)(int32_t)(0x80000000u | (address & 7u));
    return do_ldl(block, initial, 0, local);
}
static inline gpr n64host_ldr(uint8_t *rdram, gpr initial, gpr offset, gpr reg) {
    uint32_t address = (uint32_t)(offset + reg);
    if (__builtin_expect(n64host_rec_active, 0)) n64host_replay_taint("unaligned");
    uint8_t *block = n64host_ptr(rdram, address & ~7u);
    gpr local = (gpr)(int32_t)(0x80000000u | (address & 7u));
    return do_ldr(block, initial, 0, local);
}
static inline void n64host_swl(uint8_t *rdram, gpr offset, gpr reg, gpr value) {
    uint32_t address = (uint32_t)(offset + reg);
    if (__builtin_expect(n64host_rec_active, 0)) n64host_replay_taint("unaligned");
    uint8_t *block = n64host_ptr(rdram, address & ~3u);
    gpr local = (gpr)(int32_t)(0x80000000u | (address & 3u));
    do_swl(block, 0, local, value);
}
static inline void n64host_swr(uint8_t *rdram, gpr offset, gpr reg, gpr value) {
    uint32_t address = (uint32_t)(offset + reg);
    if (__builtin_expect(n64host_rec_active, 0)) n64host_replay_taint("unaligned");
    uint8_t *block = n64host_ptr(rdram, address & ~3u);
    gpr local = (gpr)(int32_t)(0x80000000u | (address & 3u));
    do_swr(block, 0, local, value);
}
static inline void n64host_sdl(uint8_t *rdram, gpr offset, gpr reg, gpr value) {
    uint32_t address = (uint32_t)(offset + reg);
    if (__builtin_expect(n64host_rec_active, 0)) n64host_replay_taint("unaligned");
    uint8_t *block = n64host_ptr(rdram, address & ~7u);
    gpr local = (gpr)(int32_t)(0x80000000u | (address & 7u));
    do_sdl(block, 0, local, value);
}
static inline void n64host_sdr(uint8_t *rdram, gpr offset, gpr reg, gpr value) {
    uint32_t address = (uint32_t)(offset + reg);
    if (__builtin_expect(n64host_rec_active, 0)) n64host_replay_taint("unaligned");
    uint8_t *block = n64host_ptr(rdram, address & ~7u);
    gpr local = (gpr)(int32_t)(0x80000000u | (address & 7u));
    do_sdr(block, 0, local, value);
}
#define do_lwl n64host_lwl
#define do_lwr n64host_lwr
#define do_ldl n64host_ldl
#define do_ldr n64host_ldr
#define do_swl n64host_swl
#define do_swr n64host_swr
#define do_sdl n64host_sdl
#define do_sdr n64host_sdr
