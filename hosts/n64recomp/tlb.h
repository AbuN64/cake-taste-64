#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void n64host_tlb_init(void);
void n64host_tlb_map(uint32_t index, uint32_t mask, uint32_t va, uint32_t even, uint32_t odd);
void n64host_tlb_unmap(uint32_t index);
uint32_t n64host_tlb_probe(uint32_t va);
uint32_t n64host_guest_translate(uint8_t *ram, uint32_t va);
void n64host_tlb_fault(uint8_t *ram, uint32_t va);
void n64host_fault_event(uint32_t queue, uint32_t message);
#ifdef __cplusplus
}
#endif
