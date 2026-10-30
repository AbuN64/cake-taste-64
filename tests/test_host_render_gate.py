import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts" / "n64recomp"
# pp-1p-match on the rig: picture point every 25 polls, the host quits at poll 59,874 (59,574 movie polls + 300)
POLLS, EVERY = 59874, 25
DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include "render_gate.h"
int main(int argc, char **argv) {
    RenderGate g;
    g.lead = std::strtoull(argv[1], nullptr, 10);
    g.every = std::strtoull(argv[2], nullptr, 10);
    if (argc > 4) g.exit_poll = std::strtoull(argv[4], nullptr, 10);
    for (unsigned long long p = 0; p <= std::strtoull(argv[3], nullptr, 10); p++) if (g.wanted(p)) std::printf("%llu\n", p);
    return 0;
}
"""


class RenderGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cxx = shutil.which("g++") or shutil.which("c++")
        if not cxx:
            raise unittest.SkipTest("no C++ compiler")
        cls.dir = tempfile.TemporaryDirectory()
        src = Path(cls.dir.name) / "gate.cpp"
        src.write_text(DRIVER, encoding="utf-8")
        cls.exe = Path(cls.dir.name) / "gate"
        subprocess.run([cxx, "-std=c++17", f"-I{HOST}", str(src), "-o", str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def drawn(self, lead, every, last, exit_poll=None):
        args = [str(self.exe), str(lead), str(every), str(last)] + ([str(exit_poll)] if exit_poll is not None else [])
        out = subprocess.run(args, capture_output=True, text=True, check=True).stdout.split()
        return [int(p) for p in out]

    def test_lead_zero_draws_every_frame(self):
        self.assertEqual(self.drawn(0, 25, 100), list(range(101)))
        self.assertEqual(self.drawn(0, 0, 100), list(range(101)))

    def test_a_picture_point_draws_the_frames_of_the_polls_before_it(self):
        self.assertEqual(self.drawn(2, 25, 80), [23, 24, 48, 49, 73, 74])

    def test_no_picture_points_draws_only_before_the_exit(self):
        self.assertEqual(self.drawn(2, 0, 100, exit_poll=90), [88, 89, 90])
        self.assertEqual(self.drawn(2, 0, 100), [])

    def test_the_exit_window_ends_at_the_exit_poll(self):
        self.assertEqual(self.drawn(3, 1000, 100, exit_poll=60), [57, 58, 59, 60])

    def test_pp_1p_match_draws_a_twelfth_of_the_frames(self):
        drawn = self.drawn(2, EVERY, POLLS, exit_poll=POLLS)
        # 2,395 picture points of two polls each, plus poll 59,872 of the exit window (59,873 and 59,874 are in both)
        self.assertEqual(len(drawn), 2 * 2395 + 1)
        self.assertLess(len(drawn) / POLLS, 1 / 12)


if __name__ == "__main__":
    unittest.main()
