import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class TlbTests(unittest.TestCase):
    def test_declared_mapping_and_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "recomp.h").write_text(
                "#include <stdint.h>\ntypedef struct { uint64_t r4,r5,r6,r7,r29; } recomp_context;\n"
            )
            (p / "host_profile.h").write_text(
                "#define HOST_TLB_COUNT 1\nstatic const uint32_t host_tlb[][3]={{0x400000,0x700000,0x100000}};\n"
            )
            (p / "mock.c").write_text(r"""
#include "recomp.h"
#include <stdlib.h>
#include <string.h>
static uint32_t odd=0xffffffff;
uint8_t *n64host_xlate_slow(uint8_t *ram,uint32_t va) { return (uint8_t*)&odd; }
void osMapTLB_recomp(uint8_t*,recomp_context*);
int main(int argc,char **argv) {
 recomp_context c={0,0x1fe000,0x400000,0x700000,0x80000000};
 if (argc>1) switch(atoi(argv[1])) {
 case 1: c.r7=0x600000; break;
 case 2: c.r5=0x2000; break;
 case 3: c.r4=32; break;
 case 4: c.r6+=0x1000; break;
 case 5: odd=0x700000; break;
 }
 osMapTLB_recomp(0,&c);
}
""")
            binary = p / "test"
            subprocess.run(
                [
                    "cc",
                    "-I",
                    tmp,
                    "-I",
                    str(HOST / "probe"),
                    str(HOST / "tlb_adapter.c"),
                    str(p / "mock.c"),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)
            for case in range(1, 6):
                result = subprocess.run([str(binary), str(case)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertIn("declared profile", result.stderr)
