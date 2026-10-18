import subprocess
import tempfile
import unittest
from pathlib import Path


class ViAdapterTests(unittest.TestCase):
    def test_manager_sets_mode_and_black_once(self):
        host = Path(__file__).resolve().parents[1] / "hosts/n64recomp"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ultramodern").mkdir()
            (root / "recomp.h").write_text("#include <cstdint>\nstruct recomp_context {};\n")
            (root / "boot_profile.h").write_text("#define HOST_INITIAL_VI_MODE 0x80000800u\n")
            (root / "ultramodern/ultramodern.hpp").write_text(
                "void osViSetMode(uint8_t*,int32_t); void osViBlack(uint32_t);\n")
            (root / "mock.cpp").write_text("""
#include <cassert>
#include "recomp.h"
static int count=0;
void osViSetMode(uint8_t*,int32_t mode) {assert(count++==0);assert(mode==(int32_t)0x80000800u);}
void osViBlack(uint32_t black) {assert(count++==1);assert(black==1);}
extern "C" void __wrap_osCreateViManager_recomp(uint8_t*,recomp_context*);
int main() {
 uint8_t ram[4096];recomp_context ctx;
 __wrap_osCreateViManager_recomp(ram,&ctx);
 __wrap_osCreateViManager_recomp(ram,&ctx);
 assert(count==2);
}
""")
            binary = root / "test"
            subprocess.run(["c++", "-std=c++17", "-I", str(root), str(host / "vi_adapter.cpp"),
                            str(root / "mock.cpp"), "-o", str(binary)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
