// COP0 Status adapter. The pinned runtime aborts when a guest mtc0 Status changes any bit but FR
// (interrupt enable/mask changes from libultra). Interrupts are delivered by ultramodern, not by
// the guest status bits, so keep the guest-visible value and let the runtime handle FR only.
#include "recomp.h"
void __real_cop0_status_write(recomp_context *ctx, gpr value);
void __wrap_cop0_status_write(recomp_context *ctx, gpr value) {
    const uint32_t fr = 0x04000000u;
    __real_cop0_status_write(ctx, (gpr)((ctx->status_reg & ~fr) | ((uint32_t)value & fr)));
    ctx->status_reg = (uint32_t)value;
}
