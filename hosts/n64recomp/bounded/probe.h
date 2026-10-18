#pragma once
// Bounded probes reuse the host address map (KSEG0/KSEG1 -> RDRAM, profile TLB windows, device shadow)
// but none of the host's runtime gaps: every callee outside the closure is an explicit stub.
#include "addrmap.h"
