// Bounded probe harness: no boot, threads, interrupts or renderer. It loads a word-swapped RDRAM
// image, sets the registers named in regs.txt, calls one function of the selected closure, and
// writes RDRAM, registers and the ordered call trace. Everything game-specific comes from the inputs.
//   harness RDRAM_IN REGS_IN OUT_DIR   (exit 0 returned, 3 left the closure, 2 bad input)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "recomp.h"
#include "bounded.h"

extern const probe_func probe_funcs[];
void n64host_addrmap_init(void);
int32_t *section_addresses;
static FILE *trace_out;

void probe_trace(const char *name) { fprintf(trace_out, "call %s\n", name); }
void probe_stub(const char *name) { fprintf(trace_out, "stub %s\n", name); }

static void fatal(const char *what, uint32_t a, uint32_t b) {
    fprintf(trace_out, "fatal %s 0x%08X 0x%08X\n", what, a, b);
    fclose(trace_out);
    fprintf(stderr, "probe: %s 0x%08X 0x%08X\n", what, a, b);
    exit(3);
}

recomp_func_t *get_function(int32_t vram) {
    for (const probe_func *p = probe_funcs; p->fn; ++p)
        if (p->vram == (uint32_t)vram) return p->fn;
    fatal("indirect-call-outside-closure", (uint32_t)vram, 0);
    return 0;
}
void switch_error(const char *func, uint32_t vram, uint32_t jtbl) { (void)func; fatal("switch-error", vram, jtbl); }
void do_break(uint32_t vram) { fatal("break", vram, 0); }
void recomp_syscall_handler(uint8_t *rdram, recomp_context *ctx, int32_t vram) {
    (void)rdram; (void)ctx; fatal("syscall", (uint32_t)vram, 0);
}
void pause_self(uint8_t *rdram) { (void)rdram; fatal("pause-self", 0, 0); }

// Status.FR selects how odd single-precision registers alias (MIPS III FPU architecture).
gpr cop0_status_read(recomp_context *ctx) { return ctx->status_reg; }
void cop0_status_write(recomp_context *ctx, gpr value) {
    ctx->status_reg = (uint32_t)value;
    ctx->mips3_float_mode = (value & 0x04000000u) != 0;
    ctx->f_odd = ctx->mips3_float_mode ? &ctx->f1.u32l : &ctx->f0.u32h;
}

static gpr *gpr_slot(recomp_context *ctx, int i) { return &ctx->r0 + i; }
static fpr *fpr_slot(recomp_context *ctx, int i) { return &ctx->f0 + i; }

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: harness RDRAM_IN REGS_IN OUT_DIR\n"); return 2; }
    char path[4096];
    snprintf(path, sizeof path, "%s/trace.txt", argv[3]);
    trace_out = fopen(path, "w");
    FILE *in = fopen(argv[1], "rb");
    if (!trace_out || !in) { perror("probe"); return 2; }
    fseek(in, 0, SEEK_END);
    long size = ftell(in);
    rewind(in);
    // The generated accessors index KSEG0 directly; reserve the whole 512 MiB window so a stray
    // address past the image reads zero instead of faulting.
    uint8_t *rdram = mmap(0, 0x20000000u, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE | MAP_NORESERVE, -1, 0);
    if (size <= 0 || size > 0x20000000L || rdram == MAP_FAILED || fread(rdram, 1, (size_t)size, in) != (size_t)size) { perror("probe rdram"); return 2; }
    fclose(in);
    n64host_addrmap_init();

    recomp_context ctx;
    memset(&ctx, 0, sizeof ctx);
    ctx.f_odd = &ctx.f0.u32h;
    uint32_t entry = 0;
    FILE *regs = fopen(argv[2], "r");
    char key[32];
    unsigned long long value;
    if (!regs) { perror("probe regs"); return 2; }
    while (fscanf(regs, "%31s %llx", key, &value) == 2) {
        int i;
        if (!strcmp(key, "entry")) entry = (uint32_t)value;
        else if (!strcmp(key, "status")) cop0_status_write(&ctx, (gpr)value);
        else if (key[0] == 'r' && sscanf(key + 1, "%d", &i) == 1 && i >= 0 && i < 32) *gpr_slot(&ctx, i) = (gpr)value;
        else if (key[0] == 'f' && sscanf(key + 1, "%d", &i) == 1 && i >= 0 && i < 32) fpr_slot(&ctx, i)->u64 = value;
        else { fprintf(stderr, "probe: unknown register %s\n", key); return 2; }
    }
    fclose(regs);
    recomp_func_t *fn = get_function((int32_t)entry);
    fn(rdram, &ctx);
    fprintf(trace_out, "return\n");
    fclose(trace_out);

    snprintf(path, sizeof path, "%s/regs.txt", argv[3]);
    FILE *out = fopen(path, "w");
    for (int i = 0; i < 32; ++i) fprintf(out, "r%d 0x%016llx\n", i, (unsigned long long)*gpr_slot(&ctx, i));
    for (int i = 0; i < 32; ++i) fprintf(out, "f%d 0x%016llx\n", i, (unsigned long long)fpr_slot(&ctx, i)->u64);
    fprintf(out, "hi 0x%016llx\nlo 0x%016llx\n", (unsigned long long)ctx.hi, (unsigned long long)ctx.lo);
    fclose(out);
    snprintf(path, sizeof path, "%s/rdram.bin", argv[3]);
    out = fopen(path, "wb");
    if (!out || fwrite(rdram, 1, (size_t)size, out) != (size_t)size) { perror("probe out"); return 2; }
    fclose(out);
    return 0;
}
