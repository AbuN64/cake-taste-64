import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class CpuMetricsTests(unittest.TestCase):
    def test_entries_cpu_deltas_and_wrapping_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "metrics.cpp"
            source.write_text(r'''#include "cpu_metrics.h"
#include <cassert>
int main() {
 host_cpu_metrics.entry(); host_cpu_metrics.entry();
 host_cpu_metrics.work(-1, 100); // A new thread has no elapsed work yet.
 host_cpu_metrics.work(100, 100); host_cpu_metrics.work(100, 90);
 host_cpu_metrics.work(100, 300);
 assert(host_cpu_metrics.entries == 2 && host_cpu_metrics.native_ns == 200);
 host_cpu_vi(0xfffffff0u);
 for (uint32_t i = 1; i < 60; ++i) host_cpu_vi(0xfffffff0u + i * 781250u);
}
''')
            binary = root / "metrics"
            subprocess.run(["c++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                            "-I", str(HOST), str(source), str(HOST / "cpu_metrics.cpp"),
                            "-o", str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)], text=True, capture_output=True, check=True)
            self.assertIn("vi=1 count_ticks=0 entries=2 native_ns=200", result.stderr)
            self.assertIn("vi=60 count_ticks=46093750 entries=2 native_ns=200", result.stderr)
