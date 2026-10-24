// Behaviour harness: many calls of one function of a bounded closure, each from a clean copy of one RDRAM
// image, with an event record per call. No boot, threads, interrupts or renderer; the cases file carries every
// game-specific fact (registers, memory patches, seeded ranges).
//   behave_harness RDRAM_IN CASES OUT_DIR     writes OUT_DIR/events.txt (exit 0, 2 bad input)
// Cases grammar, numbers in hex, one directive per line:
//   seed LO HI            memory the probe knows the content of (before the first case: for every case)
//   case NAME             start a case; entry ADDR, reg rN|fN|status VALUE, mem ADDR HEXBYTES (guest order),
//                         stubv NAME VALUE (the v0 a stub of that name returns in this case; default 0),
//                         scall NAME V0 V1 [ADDR:VALUE...] (the next recorded call of a stub of that name: the Nth
//                         `scall` of a name answers the Nth call of that stub with V0/V1 and writes the words first;
//                         calls past the recorded ones answer as without; recorded in the real run, see capture)
//   go                    run the case
//   lenient 1             an access to unmapped space reads zero and drops its stores, a call outside the closure
//                         is a stub named func_ADDR (as a direct call to it is) (both are recorded as unseeded reads / stub events)
//   timeout MS | budget N  before the first case: the wall clock and the call/stub/store events one case may write
//                         (default 20000; a case over it ends fatal event-budget)
// Events per case: call/stub NAME a0 a1 a2 a3 s0 s1 s2 s3 f12 f14, st ADDR WIDTH VALUE, rd ADDR WIDTH,
// uns ADDR WIDTH (read outside the seeded ranges), net ADDR VALUE (every word that differs from the image
// after the case), ret V0 V1 F0 F2, saved REG..., end STATUS [DETAIL].
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/time.h>
#include "recomp.h"
#include "behave.h"
#include "fpu.h"
#include "host_profile.h"

extern const probe_func probe_funcs[];
void n64host_addrmap_init(void);
int32_t *section_addresses;

static FILE *out;
static uint8_t *rdram;
static uint8_t *base;
static size_t image_size;
static sigjmp_buf stop;
static char stop_what[64];
static uint32_t stop_a, stop_b;

typedef struct { char name[64]; uint32_t value; } stub_result;
static stub_result stub_results[256];
static int nstub_results;

typedef struct { char name[64]; uint32_t v0, v1; uint32_t first, count; } scall_row;   // words in scall_words
static scall_row scalls[8192];
static int nscalls;
static uint32_t scall_words[1 << 20][2];
static uint32_t nscall_words;
static struct { char name[64]; uint32_t used; } scall_used[256];
static int nscall_used;

typedef struct { uint32_t lo, hi; } range;
static range seeded[4096];
static int nseeded, ncommon;

static struct { int on; uint32_t va; int w; uint8_t *p; uint32_t before; } pend;
// Open-addressing sets of 64-bit keys (0 is the empty slot; every key carries a set bit).
#define SLOTS 65536
typedef struct { uint64_t slot[SLOTS]; int count, overflow; } keyset;
static keyset reads, written;
static uint64_t calls_seen;
#define DEFAULT_EVENT_BUDGET 20000
static int lenient;                                    // `lenient 1`: see behave_ptr and get_function
static uint8_t scratch[16];
static char indirect_name[32];
static uint64_t event_budget = DEFAULT_EVENT_BUDGET;   // `budget N` in the cases file replaces it
static uint64_t events_written;          // call/stub/store lines written in this case
static uint32_t stack_lo, stack_hi;      // `stack LO HI`: stores in this window are the dead frame, counted at 16x the budget
static uint64_t stack_events;          // stores in the stack window written in this case
static volatile sig_atomic_t printing;   // inside an event write: the alarm waits until it is whole
static volatile sig_atomic_t alarm_late;
static uint32_t last_va;
static int keyset_add(keyset *s, uint64_t key) {
    if (s->count >= SLOTS / 2) { s->overflow = 1; return 0; }
    uint64_t i = (key * 0x9E3779B97F4A7C15ull) >> 48;
    for (;; i = (i + 1) % SLOTS) {
        if (s->slot[i] == key) return 0;
        if (!s->slot[i]) { s->slot[i] = key; ++s->count; return 1; }
    }
}
static int keyset_has(const keyset *s, uint64_t key) {
    uint64_t i = (key * 0x9E3779B97F4A7C15ull) >> 48;
    for (;; i = (i + 1) % SLOTS) {
        if (s->slot[i] == key) return 1;
        if (!s->slot[i]) return 0;
    }
}

static uint32_t rd(const uint8_t *p, int w) { return w == 1 ? *p : w == 2 ? *(const uint16_t *)p : *(const uint32_t *)p; }

static int seeded_at(uint32_t va);
static void stop_case(const char *what, uint32_t a, uint32_t b);
// A case that spins on calls or stores would write gigabytes of events: it ends once it has written enough.
static void count_event(void) { if (++events_written > event_budget) stop_case("event-budget", 0, 0); }
static void end_printing(void) { printing = 0; if (alarm_late) { alarm_late = 0; siglongjmp(stop, 2); } }
static void mark_written(uint32_t va, int w) {
    for (uint32_t a = va & ~3u; a < va + (uint32_t)w; a += 4) keyset_add(&written, ((uint64_t)a << 1) | 1);
}
static int is_written(uint32_t va) { return keyset_has(&written, (((uint64_t)(va & ~3u)) << 1) | 1); }

static void settle(void) {
    if (!pend.on) return;
    pend.on = 0;
    last_va = pend.va;
    uint32_t now = rd(pend.p, pend.w);
    if (now != pend.before) {
        if (pend.va >= stack_lo && pend.va < stack_hi) { if (++stack_events > 16 * event_budget) stop_case("event-budget", 0, 0); }
        else count_event();
        printing = 1;
        fprintf(out, "st %08X %d %X\n", pend.va, pend.w, now);
        end_printing();
        mark_written(pend.va, pend.w);
        return;
    }
    int unseeded = !seeded_at(pend.va) && !is_written(pend.va);
    keyset_add(&reads, (((uint64_t)pend.va << 4) | ((uint64_t)unseeded << 3) | (uint64_t)pend.w) << 1 | 1);
}

static void stop_case(const char *what, uint32_t a, uint32_t b) {
    snprintf(stop_what, sizeof stop_what, "%s", what);
    stop_a = a; stop_b = b;
    siglongjmp(stop, 1);
}

uint8_t *behave_ptr(uint8_t *mem, uint64_t va64, int width) {
    settle();
    uint32_t va = (uint32_t)va64;
    uint32_t phys = va & 0x1FFFFFFFu;
    if (va >= 0x80000000u) {
        if (phys >= 0x03F00000u) stop_case("mmio", va, (uint32_t)width);
    } else {
        int mapped = 0;
        for (unsigned i = 0; i < HOST_TLB_COUNT; ++i)
            if (va - host_tlb[i][0] < host_tlb[i][2]) { mapped = 1; break; }
        if (!mapped && lenient) {
            keyset_add(&reads, (((uint64_t)va << 4) | 8u | (uint64_t)width) << 1 | 1);
            memset(scratch, 0, sizeof scratch);
            last_va = va;
            pend.on = 0;
            return scratch;
        }
        if (!mapped) stop_case("unmapped", va, (uint32_t)width);
    }
    uint32_t xlate = va ^ (width == 1 ? 3u : width == 2 ? 2u : 0u);
    uint8_t *p = n64host_ptr(mem, xlate);
    pend.on = 1; pend.va = va; pend.w = width; pend.p = p; pend.before = rd(p, width);
    return p;
}

static void args_line(const char *kind, const char *name, uint8_t *mem, recomp_context *ctx) {
    uint32_t sp = (uint32_t)ctx->r29;
    uint32_t s[4] = {0, 0, 0, 0};
    if (sp >= 0x80000000u && (sp & 0x1FFFFFFFu) < 0x03F00000u)
        for (int i = 0; i < 4; ++i) s[i] = *(uint32_t *)n64host_ptr(mem, sp + 16 + 4 * i);
    count_event();
    printing = 1;
    fprintf(out, "%s %s %X %X %X %X %X %X %X %X %X %X\n", kind, name, (uint32_t)ctx->r4, (uint32_t)ctx->r5,
            (uint32_t)ctx->r6, (uint32_t)ctx->r7, s[0], s[1], s[2], s[3], ctx->f12.u32l, ctx->f14.u32l);
    end_printing();
}
// A function whose last word falls into the next symbol (a hoisted prologue word the decomp splits off) tail-calls
// that entry: the entry is the same function running on, so it writes no call event.
static int continuing;
void behave_fallthrough(void) { continuing = 1; }
void behave_enter(const char *name, uint8_t *mem, recomp_context *ctx) {
    settle();
    if (continuing) { continuing = 0; return; }
    ++calls_seen;
    args_line("call", name, mem, ctx);
}
// A stub with no given answer returns zero, until a case has called it TICK_AFTER times: a body that spins on
// a clock or a status the stub stands for would never leave, so from then on each call answers one step further
// (v1, the low word of a 64-bit result, by 0x100000; v0 by one). Both bodies of a comparison see the same answers.
#define TICK_AFTER 16
static struct { char name[64]; unsigned long long calls; } stub_calls[256];
static int nstub_calls;
void behave_stub(const char *name, uint8_t *mem, recomp_context *ctx) {
    settle();
    args_line("stub", name, mem, ctx);
    ctx->r2 = 0;
    ctx->r3 = 0;
    int given = 0;
    for (int i = 0; i < nstub_results; ++i)
        if (!strcmp(stub_results[i].name, name)) { ctx->r2 = (gpr)(int64_t)(int32_t)stub_results[i].value; given = 1; }
    if (given) return;
    if (nscalls) {  // the recorded real run of the entry case: the Nth call to this name answers as it did
        int u = -1;
        for (int i = 0; i < nscall_used; ++i) if (!strcmp(scall_used[i].name, name)) u = i;
        if (u < 0 && nscall_used < 256) { u = nscall_used++; snprintf(scall_used[u].name, sizeof scall_used[u].name, "%s", name); scall_used[u].used = 0; }
        uint32_t want = u >= 0 ? scall_used[u].used++ : 0xFFFFFFFFu, seen = 0;
        for (int i = 0; u >= 0 && i < nscalls; ++i)
            if (!strcmp(scalls[i].name, name) && seen++ == want) {
                for (uint32_t w = 0; w < scalls[i].count; ++w)
                    *(uint32_t *)n64host_ptr(mem, scall_words[scalls[i].first + w][0]) = scall_words[scalls[i].first + w][1];
                ctx->r2 = (gpr)(int64_t)(int32_t)scalls[i].v0;
                ctx->r3 = (gpr)(int64_t)(int32_t)scalls[i].v1;
                return;
            }
    }
    int slot = -1;
    for (int i = 0; i < nstub_calls; ++i) if (!strcmp(stub_calls[i].name, name)) slot = i;
    if (slot < 0 && nstub_calls < 256) {
        slot = nstub_calls++;
        snprintf(stub_calls[slot].name, sizeof stub_calls[slot].name, "%s", name);
        stub_calls[slot].calls = 0;
    }
    if (slot >= 0 && ++stub_calls[slot].calls > TICK_AFTER) {
        unsigned long long step = stub_calls[slot].calls - TICK_AFTER;
        ctx->r2 = (gpr)(int64_t)(int32_t)step;
        ctx->r3 = (gpr)(int64_t)(int32_t)(step * 0x100000ull);
    }
}

static uint32_t indirect_target;
static void indirect_stub(uint8_t *mem, recomp_context *ctx) {
    snprintf(indirect_name, sizeof indirect_name, "func_%08X", indirect_target);
    behave_stub(indirect_name, mem, ctx);
}
recomp_func_t *get_function(int32_t vram) {
    indirect_target = (uint32_t)vram;
    for (const probe_func *p = probe_funcs; p->fn; ++p)
        if (p->vram == (uint32_t)vram) return p->fn;
    if (lenient) return indirect_stub;
    stop_case("indirect-call-outside-closure", (uint32_t)vram, 0);
    return 0;
}
void switch_error(const char *func, uint32_t vram, uint32_t jtbl) { (void)func; stop_case("switch-error", vram, jtbl); }
void do_break(uint32_t vram) { stop_case("break", vram, 0); }
void recomp_syscall_handler(uint8_t *mem, recomp_context *ctx, int32_t vram) { (void)mem; (void)ctx; stop_case("syscall", (uint32_t)vram, 0); }
void pause_self(uint8_t *mem) { (void)mem; stop_case("pause-self", 0, 0); }
gpr cop0_status_read(recomp_context *ctx) { return ctx->status_reg; }
void cop0_status_write(recomp_context *ctx, gpr value) {
    ctx->status_reg = (uint32_t)value;
    ctx->mips3_float_mode = (value & 0x04000000u) != 0;
    ctx->f_odd = ctx->mips3_float_mode ? &ctx->f1.u32l : &ctx->f0.u32h;
}

static void on_alarm(int sig) { (void)sig; if (printing) alarm_late = 1; else siglongjmp(stop, 2); }
// N64Recomp's generated NaN checks are asserts: a case that reaches one is untestable, not a dead harness.
static void on_abort(int sig) { (void)sig; siglongjmp(stop, 3); }
// A host trap (the generated C divides where the MIPS divide leaves its result undefined; a wild host pointer)
// ends the case, not the process: the other cases still run.
static void on_trap(int sig) { siglongjmp(stop, sig == SIGFPE ? 4 : 5); }
static gpr *gpr_slot(recomp_context *ctx, int i) { return &ctx->r0 + i; }
static fpr *fpr_slot(recomp_context *ctx, int i) { return &ctx->f0 + i; }

static int seeded_at(uint32_t va) {
    for (int i = 0; i < nseeded; ++i) if (va >= seeded[i].lo && va < seeded[i].hi) return 1;
    return 0;
}
static int cmp64(const void *a, const void *b) { uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b; return x < y ? -1 : x > y; }

typedef struct { char name[256]; uint32_t entry; char *ops; size_t len, cap; } casebuf;
static void add_op(casebuf *c, const char *line) {
    size_t n = strlen(line);
    if (c->len + n + 2 > c->cap) { c->cap = (c->len + n + 2) * 2; c->ops = realloc(c->ops, c->cap); }
    memcpy(c->ops + c->len, line, n);
    c->len += n;
    c->ops[c->len++] = '\n';
    c->ops[c->len] = 0;
}

static void run_case(casebuf *c, unsigned timeout_ms) {
    memcpy(rdram, base, image_size);
    recomp_context ctx;
    memset(&ctx, 0, sizeof ctx);
    ctx.f_odd = &ctx.f0.u32h;
    nseeded = ncommon; pend.on = 0; last_va = 0; nstub_results = 0; continuing = 0; nstub_calls = 0; nscalls = 0; nscall_words = 0; nscall_used = 0; events_written = 0; stack_events = 0; printing = 0; alarm_late = 0;
    memset(&reads, 0, sizeof reads); memset(&written, 0, sizeof written);
    fprintf(out, "case %s\n", c->name);
    char *line = c->ops, *next;
    while (line && *line) {
        next = strchr(line, '\n');
        if (next) *next++ = 0;
        char what[32];
        unsigned long long a, b;
        if (sscanf(line, "reg %31s %llx", what, &a) == 2) {
            int i;
            if (!strcmp(what, "status")) cop0_status_write(&ctx, (gpr)a);
            else if (what[0] == 'r' && sscanf(what + 1, "%d", &i) == 1 && i >= 0 && i < 32) *gpr_slot(&ctx, i) = (gpr)a;
            else if (what[0] == 'f' && sscanf(what + 1, "%d", &i) == 1 && i >= 0 && i < 32) fpr_slot(&ctx, i)->u64 = a;
        } else if (!strncmp(line, "stubv ", 6)) {
            char stub[64];
            if (sscanf(line + 6, "%63s %llx", stub, &a) == 2 && nstub_results < 256) {
                snprintf(stub_results[nstub_results].name, sizeof stub_results[0].name, "%s", stub);
                stub_results[nstub_results++].value = (uint32_t)a;
            }
        } else if (!strncmp(line, "scall ", 6)) {
            char row[64];
            unsigned long long v0, v1;
            int used = 0;
            if (sscanf(line + 6, "%63s %llx %llx%n", row, &v0, &v1, &used) == 3 && nscalls < 8192) {
                scall_row *r = &scalls[nscalls++];
                snprintf(r->name, sizeof r->name, "%s", row);
                r->v0 = (uint32_t)v0; r->v1 = (uint32_t)v1; r->first = nscall_words; r->count = 0;
                const char *rest = line + 6 + used;
                unsigned long long wa, wv;
                int step;
                while (nscall_words < (1u << 20) && sscanf(rest, " %llx:%llx%n", &wa, &wv, &step) == 2) {
                    scall_words[nscall_words][0] = (uint32_t)wa; scall_words[nscall_words++][1] = (uint32_t)wv;
                    ++r->count; rest += step;
                }
            }
        } else if (sscanf(line, "seed %llx %llx", &a, &b) == 2) {
            if (nseeded < 4096) { seeded[nseeded].lo = (uint32_t)a; seeded[nseeded++].hi = (uint32_t)b; }
        } else if (!strncmp(line, "mem ", 4)) {
            char *hex = strchr(line + 4, ' ');
            if (hex) {
                *hex++ = 0;
                uint32_t addr = (uint32_t)strtoull(line + 4, 0, 16);
                for (size_t i = 0; hex[2 * i] && hex[2 * i + 1]; ++i) {
                    char byte[3] = {hex[2 * i], hex[2 * i + 1], 0};
                    rdram[((addr & 0x1FFFFFFFu) + i) ^ 3u] = (uint8_t)strtoul(byte, 0, 16);
                }
            }
        }
        line = next;
    }
    gpr saved_in[32];
    for (int i = 0; i < 32; ++i) saved_in[i] = *gpr_slot(&ctx, i);
    uint64_t fsaved_in[32];
    for (int i = 0; i < 32; ++i) fsaved_in[i] = fpr_slot(&ctx, i)->u64;
    const char *status = "ok";
    char detail[160] = "";
    fenv_t saved_fpu;
    behave_fpu_begin(&saved_fpu);
    struct itimerval timer = {{0, 0}, {timeout_ms / 1000, (timeout_ms % 1000) * 1000}};
    signal(SIGALRM, on_alarm);
    signal(SIGABRT, on_abort);
    signal(SIGFPE, on_trap);
    signal(SIGSEGV, on_trap);
    signal(SIGBUS, on_trap);
    int jumped = sigsetjmp(stop, 1);
    if (!jumped) {
        recomp_func_t *fn = get_function((int32_t)c->entry);
        setitimer(ITIMER_REAL, &timer, 0);
        fn(rdram, &ctx);
        struct itimerval off = {{0, 0}, {0, 0}};
        setitimer(ITIMER_REAL, &off, 0);
    } else {
        struct itimerval off = {{0, 0}, {0, 0}};
        setitimer(ITIMER_REAL, &off, 0);
        pend.on = 0;
        if (jumped == 2) { status = "timeout"; snprintf(detail, sizeof detail, "no return within %u ms", timeout_ms); }
        else if (jumped == 3) { status = "fatal"; snprintf(detail, sizeof detail, "check failed (NaN operand) last %08X", last_va); }
        else if (jumped == 4) { status = "fatal"; snprintf(detail, sizeof detail, "integer divide trap last %08X", last_va); }
        else if (jumped == 5) { status = "fatal"; snprintf(detail, sizeof detail, "host memory trap last %08X", last_va); }
        else {
            status = !strcmp(stop_what, "mmio") ? "mmio" : !strcmp(stop_what, "unmapped") ? "unmapped" : "fatal";
            snprintf(detail, sizeof detail, "%s %08X %X last %08X", stop_what, stop_a, stop_b, last_va);
        }
    }
    unsigned fpu_effects = behave_fpu_end(&saved_fpu);
    if (!jumped) fprintf(out, "fpu %X\n", fpu_effects);
    else fputs("fpu unknown\n", out);
    settle();
    if (!jumped) {
        fprintf(out, "ret %X %X %llX %llX\n", (uint32_t)ctx.r2, (uint32_t)ctx.r3, (unsigned long long)ctx.f0.u64,
                (unsigned long long)ctx.f2.u64);
        fputs("saved", out);
        static const int callee[] = {16, 17, 18, 19, 20, 21, 22, 23, 29, 30};
        for (unsigned i = 0; i < sizeof callee / sizeof *callee; ++i)
            if (*gpr_slot(&ctx, callee[i]) != saved_in[callee[i]]) fprintf(out, " r%d", callee[i]);
        for (int i = 20; i < 32; i += 2)
            if (fpr_slot(&ctx, i)->u64 != fsaved_in[i]) fprintf(out, " f%d", i);
        fputc('\n', out);
    }
    static uint64_t sorted[SLOTS / 2];
    int n = 0;
    for (int i = 0; i < SLOTS; ++i) if (reads.slot[i]) sorted[n++] = reads.slot[i] >> 1;
    qsort(sorted, (size_t)n, sizeof *sorted, cmp64);
    for (int i = 0; i < n; ++i) {
        uint32_t va = (uint32_t)(sorted[i] >> 4);
        fprintf(out, "%s %08X %d\n", (sorted[i] & 8) ? "uns" : "rd", va, (int)(sorted[i] & 7));
    }
    if (reads.overflow || written.overflow) fputs("overflow reads\n", out);
    int netcount = 0;
    for (size_t off = 0; off + 8 <= image_size; off += 8) {
        if (*(const uint64_t *)(rdram + off) == *(const uint64_t *)(base + off)) continue;
        for (size_t w = off; w < off + 8; w += 4) {
            uint32_t now = *(const uint32_t *)(rdram + w);
            if (now == *(const uint32_t *)(base + w)) continue;
            if (netcount++ < 8192) fprintf(out, "net %08X %X\n", 0x80000000u | (uint32_t)w, now);
        }
    }
    if (netcount > 8192) fprintf(out, "overflow net %d\n", netcount);
    fprintf(out, "events %llu\n", (unsigned long long)events_written);
    fprintf(out, "end %s%s%s\n", status, *detail ? " " : "", detail);
}

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: behave_harness RDRAM_IN CASES OUT_DIR\n"); return 2; }
    char path[4096];
    snprintf(path, sizeof path, "%s/events.txt", argv[3]);
    out = fopen(path, "w");
    FILE *in = fopen(argv[1], "rb");
    FILE *cases = fopen(argv[2], "r");
    if (!out || !in || !cases) { perror("behave"); return 2; }
    fseek(in, 0, SEEK_END);
    long size = ftell(in);
    rewind(in);
    rdram = mmap(0, 0x20000000u, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE | MAP_NORESERVE, -1, 0);
    base = malloc((size_t)size);
    if (size <= 0 || size > 0x20000000L || rdram == MAP_FAILED || !base || fread(base, 1, (size_t)size, in) != (size_t)size) {
        perror("behave rdram"); return 2;
    }
    fclose(in);
    image_size = (size_t)size;
    n64host_addrmap_init();
    size_t cap = 1 << 22;
    char *text = malloc(cap);
    casebuf cur = {{0}, 0, 0, 0, 0};
    int open = 0;
    unsigned timeout_ms = 2000;
    while (fgets(text, (int)cap, cases)) {
        text[strcspn(text, "\r\n")] = 0;
        unsigned long long a, b;
        char name[256];
        if (sscanf(text, "timeout %llu", &a) == 1) timeout_ms = (unsigned)a;
        else if (sscanf(text, "budget %llu", &a) == 1 && a > 0) event_budget = a;
        else if (sscanf(text, "stack %llx %llx", &a, &b) == 2) { stack_lo = (uint32_t)a; stack_hi = (uint32_t)b; }
        else if (sscanf(text, "lenient %llu", &a) == 1) lenient = a != 0;
        else if (!open && sscanf(text, "seed %llx %llx", &a, &b) == 2 && nseeded < 4096) {
            seeded[nseeded].lo = (uint32_t)a; seeded[nseeded++].hi = (uint32_t)b; ncommon = nseeded;
        } else if (!strncmp(text, "case ", 5)) {
            snprintf(name, sizeof name, "%s", text + 5);
            free(cur.ops);
            memset(&cur, 0, sizeof cur);
            snprintf(cur.name, sizeof cur.name, "%s", name);
            open = 1;
        } else if (open && sscanf(text, "entry %llx", &a) == 1) cur.entry = (uint32_t)a;
        else if (open && !strcmp(text, "go")) { run_case(&cur, timeout_ms); open = 0; }
        else if (open) add_op(&cur, text);
    }
    fclose(out);
    return 0;
}
