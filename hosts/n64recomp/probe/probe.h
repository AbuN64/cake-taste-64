#pragma once
#include "addrmap.h"
#include "memwatch.h"
// libultra internals the pinned runtime does not implement (see libultra_gaps.c), plus refused functions.
#include "refused_decls.h"
void osMapTLB_recomp(uint8_t* rdram, recomp_context* ctx);
void __osProbeTLB_recomp(uint8_t* rdram, recomp_context* ctx);
void __osSiDeviceBusy_recomp(uint8_t* rdram, recomp_context* ctx);
void osUnmapTLB_recomp(uint8_t* rdram, recomp_context* ctx);
void __osAiDeviceBusy_recomp(uint8_t* rdram, recomp_context* ctx);
void __osDpDeviceBusy_recomp(uint8_t* rdram, recomp_context* ctx);
void __osSpDeviceBusy_recomp(uint8_t* rdram, recomp_context* ctx);
void __osInsertTimer_recomp(uint8_t* rdram, recomp_context* ctx);
void __osSiRawStartDma_recomp(uint8_t* rdram, recomp_context* ctx);
void osUnmapTLBAll_recomp(uint8_t* rdram, recomp_context* ctx);

// Raw PI is supplied by the host adapter; the pinned runtime omits its declaration.
void osPiRawStartDma_recomp(uint8_t*, recomp_context*);
