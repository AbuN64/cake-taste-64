import subprocess
import tempfile
import unittest
from pathlib import Path


class InitAdapterTests(unittest.TestCase):
    def test_expansion_pak_switch_sets_the_guest_memory_size_only_when_off(self):
        host = Path(__file__).resolve().parents[1] / "hosts/n64recomp"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "recomp.h").write_text(
                "#include <stdint.h>\n"
                "typedef struct {int64_t r2;} recomp_context;\n"
                "#define MEM_W(o,r) (*(uint32_t*)(rdram+(((r)+(o))-0xffffffff80000000ull)))\n"
            )
            (root / "boot_profile.h").write_text("#define HOST_OS_CLOCK_RATE 0x80000008u\n#define HOST_COUNT_HZ 1u\n")
            (root / "mock.c").write_text(r"""
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdlib.h>
#include "recomp.h"
void __real_osInitialize_recomp(uint8_t *rdram, recomp_context *ctx) {
 (void)ctx; MEM_W(0,(int64_t)(int32_t)0x80000318u)=0x800000;
}
void __real___osInitialize_common_recomp(uint8_t *rdram, recomp_context *ctx) { __real_osInitialize_recomp(rdram,ctx); }
void __real_osGetMemSize_recomp(uint8_t *rdram, recomp_context *ctx) { (void)rdram; ctx->r2=0x800000; }
void __wrap_osInitialize_recomp(uint8_t *,recomp_context *);
void __wrap___osInitialize_common_recomp(uint8_t *,recomp_context *);
void __wrap_osGetMemSize_recomp(uint8_t *,recomp_context *);
static uint32_t ram[0x400];
static uint32_t run(const char *pak, uint32_t *size_word) {
 if (pak) setenv("N64R_PAK",pak,1); else unsetenv("N64R_PAK");
 recomp_context ctx={0};
 __wrap_osInitialize_recomp((uint8_t*)ram,&ctx);
 *size_word=ram[0x318/4];
 __wrap_osGetMemSize_recomp((uint8_t*)ram,&ctx);
 return (uint32_t)ctx.r2;
}
int main(int argc, char **argv) {
 uint32_t word;
 if (argc > 1) { run(argv[1],&word); return 0; }  /* a bad value must end the process */
 assert(run(NULL,&word)==0x800000 && word==0x800000);   /* unset: the runtime's own 8 MB, untouched */
 assert(run("on",&word)==0x800000 && word==0x800000);
 assert(run("off",&word)==0x400000 && word==0x400000);
}
""")
            binary = root / "test"
            subprocess.run(["cc","-std=c17","-Wall","-Wextra","-Werror","-I",str(root),
                            str(host / "init_adapter.c"),str(root / "mock.c"),"-o",str(binary)],
                           check=True,capture_output=True,text=True)
            subprocess.run([str(binary)],check=True,capture_output=True)
            bad = subprocess.run([str(binary), "maybe"], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("N64R_PAK must be on or off", bad.stderr)
    def test_both_initializers_publish_u64_clock_after_runtime_call(self):
        host = Path(__file__).resolve().parents[1] / "hosts/n64recomp"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "recomp.h").write_text(
                "#include <stdint.h>\n"
                "typedef struct {int marker; int64_t r2;} recomp_context;\n"
                "#define MEM_W(o,r) (*(uint32_t*)(rdram+(((r)+(o))-0xffffffff80000000ull)))\n"
            )
            (root / "boot_profile.h").write_text(
                "#define HOST_OS_CLOCK_RATE 0x80000008u\n"
                "#define HOST_COUNT_HZ 46875000u\n"
            )
            (root / "mock.c").write_text(r"""
#include <assert.h>
#include "recomp.h"
static int calls;
void __real_osGetMemSize_recomp(uint8_t *rdram, recomp_context *ctx) { (void)rdram; (void)ctx; }
void __real_osInitialize_recomp(uint8_t *rdram, recomp_context *ctx) {
 assert(ctx->marker==31); calls++;
 MEM_W(0,(int64_t)(int32_t)0x80000008u)=0x12345678; MEM_W(4,(int64_t)(int32_t)0x80000008u)=0x87654321;
}
void __real___osInitialize_common_recomp(uint8_t *rdram, recomp_context *ctx) {
 __real_osInitialize_recomp(rdram,ctx);
}
void __wrap_osInitialize_recomp(uint8_t *,recomp_context *);
void __wrap___osInitialize_common_recomp(uint8_t *,recomp_context *);
int main(void) {
 uint32_t words[6]={7,11,0xffffffff,0xffffffff,13,17};
 recomp_context ctx={31,0};
 void (*init[3])(uint8_t*,recomp_context*)={__wrap_osInitialize_recomp,
     __wrap___osInitialize_common_recomp,__wrap_osInitialize_recomp};
 for(int i=0;i<3;i++) {
  init[i]((uint8_t*)words,&ctx);
  assert(words[2]==0 && words[3]==46875000);
  assert(words[0]==7 && words[1]==11 && words[4]==13 && words[5]==17);
  assert(ctx.marker==31 && calls==i+1);
 }
}
""")
            binary=root / "test"
            subprocess.run(["cc","-std=c17","-Wall","-Wextra","-Werror","-I",str(root),
                            str(host / "init_adapter.c"),str(root / "mock.c"),"-o",str(binary)],
                           check=True,capture_output=True,text=True)
            subprocess.run([str(binary)],check=True,capture_output=True)
