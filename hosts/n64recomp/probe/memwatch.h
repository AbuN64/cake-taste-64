#pragma once
// P3 probe: ranges named in the file given by N64R_MEMWATCH ("lo hi" hex per line).
// Wrapping MEM_* needs a regenerate step; this header only exposes the hook the wrapper calls.
#ifdef __cplusplus
extern "C" {
#endif
void n64host_memwatch(unsigned addr, unsigned size, int is_write);
#ifdef __cplusplus
}
#endif
