import subprocess
import tempfile
import unittest
from pathlib import Path


class MicrocodeGuardTests(unittest.TestCase):
    def test_unsupported_microcode_exits_with_addresses_before_interpretation(self):
        host = Path(__file__).resolve().parents[1] / "hosts/n64recomp"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mock.cpp"
            source.write_text('''#include "microcode_guard.h"
int main(int argc,char**) {
 n64host_require_microcode(argc==1,0x80001000,0x80002000);
 std::puts("interpreter reached");
}
''')
            binary = root / "test"
            subprocess.run(["c++", "-std=c++17", "-I", str(host), str(source), "-o", str(binary)],
                           check=True, capture_output=True)
            unsupported = subprocess.run([str(binary), "unknown"], capture_output=True, text=True)
            self.assertEqual(unsupported.returncode, 4)
            self.assertEqual(unsupported.stdout, "")
            self.assertIn("text=0x80001000 data=0x80002000", unsupported.stderr)
            supported = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(supported.returncode, 0)
            self.assertEqual(supported.stdout, "interpreter reached\n")


if __name__ == "__main__":
    unittest.main()
