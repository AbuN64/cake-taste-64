/* A bounded call owns its FP environment. These are effects, not guest trap emulation. */
#ifndef BEHAVE_FPU_H
#define BEHAVE_FPU_H
#include <fenv.h>

static inline void behave_fpu_begin(fenv_t *saved) {
    fegetenv(saved);
    fesetenv(FE_DFL_ENV);
}

static inline unsigned behave_fpu_end(const fenv_t *saved) {
    int flags = fetestexcept(FE_ALL_EXCEPT);
    int round = fegetround();
    unsigned csr = round == FE_TOWARDZERO ? 1u : round == FE_UPWARD ? 2u : round == FE_DOWNWARD ? 3u : 0u;
    if (flags & FE_INEXACT) csr |= 1u << 2;
    if (flags & FE_UNDERFLOW) csr |= 1u << 3;
    if (flags & FE_OVERFLOW) csr |= 1u << 4;
    if (flags & FE_DIVBYZERO) csr |= 1u << 5;
    if (flags & FE_INVALID) csr |= 1u << 6;
    fesetenv(saved);
    return csr;
}
#endif
