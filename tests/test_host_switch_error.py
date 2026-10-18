import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class SwitchErrorTests(unittest.TestCase):
    def test_failure_retains_guest_location_and_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "recomp.h").write_text(
                "#include <stdint.h>\ntypedef uint64_t gpr;\ntypedef struct {gpr r2;} recomp_context;\n"
            )
            (p / "main.c").write_text(
                "#include <stdint.h>\n"
                "void __wrap_switch_error(const char*,uint32_t,uint32_t);\n"
                'int main(void){__wrap_switch_error("f",0x80001234,0x80005678);}\n'
                ""
            )
            binary = p / "test"
            subprocess.run(
                ["cc", "-I", tmp, str(HOST / "libultra_gaps.c"), str(p / "main.c"), "-o", str(binary)],
                check=True,
                capture_output=True,
            )
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 4)
            self.assertIn("function=f pc=0x80001234 table=0x80005678", result.stderr)
