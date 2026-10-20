import subprocess
import tempfile
import unittest
from pathlib import Path


class InitAdapterTests(unittest.TestCase):
    def test_both_initializers_publish_u64_clock_after_runtime_call(self):
        host = Path(__file__).resolve().parents[1] / "hosts/n64recomp"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "recomp.h").write_text("#include <stdint.h>\ntypedef struct {int marker;} recomp_context;\n#define MEM_W(o,r) (*(uint32_t*)(rdram+(((r)+(o))-0xffffffff80000000ull)))\n")
            (root / "boot_profile.h").write_text("#define HOST_OS_CLOCK_RATE 0x80000008u\n#define HOST_COUNT_HZ 46875000u\n")
            (root / "mock.c").write_text(r"""
#include <assert.h>
#include "recomp.h"
static int calls;
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
 recomp_context ctx={31};
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
