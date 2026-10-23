import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts" / "n64recomp"
DRIVER = r"""
#include <cstdint>
#include <cstring>
#include <cstdio>
typedef uint64_t gpr;
extern "C" uint64_t n64host_golden_frame() { static uint64_t f = 0; return f; }
extern "C" uint8_t *n64host_xlate_slow(uint8_t *, uint32_t) { static uint8_t d[16]; return d; }
extern "C" int32_t n64host_func_vram(int32_t v) { return v; }
extern "C" int n64host_replay_on;
extern "C" void n64host_replay_entry(const char *, const uint8_t *, uint32_t, uint32_t, uint32_t, uint32_t, uint64_t,
                                     uint64_t, uint32_t);
extern "C" void n64host_replay_return(const char *, const uint8_t *, uint32_t, uint32_t, uint64_t, uint32_t);
#include "addrmap.h"
static uint8_t ram[0x800000] __attribute__((aligned(16)));
static const char f[] = "f", g[] = "g", h[] = "h";
static void put(uint32_t phys, uint32_t w) { std::memcpy(ram + phys, &w, 4); }
int main() {
    uint8_t *rdram = ram;
    if (!n64host_replay_on) return 3;
    put(0x1000, 0x11223344);  // logical bytes 11 22 33 44
    put(0x1010, 0x3f800000);
    n64host_replay_entry(f, ram, 7, 8, 9, 10, 0, 0, 0x80003f00);
    int32_t x = MEM_W(0, 0x80001000);
    uint8_t b = MEM_BU(1, 0x80001000);
    uint16_t hw = MEM_HU(2, 0x80001000);
    MEM_W(4, 0x80001000) = x + 1;
    int32_t again = MEM_W(4, 0x80001000);     // read after its own write: not an input
    MEM_B(0, 0x80002000) = (int8_t)b;
    n64host_replay_entry(g, ram, 1, 2, 3, 4, 0, 0, 0x80003e00);   // nested
    SD(0x0102030405060708ull, 0, 0x80002008);
    int32_t fl = MEM_W(0, 0x80001010);
    (void)fl;
    n64host_replay_return(g, ram, 3, 0, 0, 0x80003e00);
    MEM_W(0, 0x80003e00) = 99;                // the dead frame below the entry stack pointer
    n64host_replay_return(f, ram, (uint32_t)(again + (hw - hw)), 0, 0x3ff0000000000000ull, 0x80003f00);
    // h: reads outside RDRAM -> skipped
    n64host_replay_entry(h, ram, 0, 0, 0, 0, 0, 0, 0x80003f00);
    (void)MEM_W(0, 0x00100000);
    n64host_replay_return(h, ram, 0, 0, 0, 0x80003f00);
    return 0;
}
"""


class ReplayDocsTest(unittest.TestCase):
    def test_every_replay_env_var_is_documented(self):
        source = (HOST / "replay.cpp").read_text(encoding="utf-8")
        names = set(re.findall(r"getenv\(\"(N64R_[A-Z_]+)\"\)", source))
        self.assertEqual(names, {"N64R_REPLAY_DIR", "N64R_REPLAY_CALLS", "N64R_REPLAY_GAP", "N64R_REPLAY_EVENTS",
                                 "N64R_REPLAY_RAM"})
        docs = (HOST / "RUNTIME.md").read_text(encoding="utf-8")
        for name in names:
            self.assertIn(f"`{name}`", docs)

    def test_recorder_is_inert_and_wired(self):
        source = (HOST / "replay.cpp").read_text(encoding="utf-8")
        self.assertIn("if (!dir || !*dir) return 0;", source)
        trace = (HOST / "probe" / "trace.h").read_text(encoding="utf-8")
        self.assertIn("if (n64host_replay_on) n64host_replay_entry(", trace)
        self.assertIn("if (n64host_replay_on) n64host_replay_return(", trace)
        self.assertIn("replay.cpp", (HOST / "CMakeLists.txt").read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("g++"), "needs g++")
class ReplayRecorderTest(unittest.TestCase):
    def test_inputs_outputs_returns_and_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "driver.cpp").write_text(DRIVER)
            exe = path / "driver"
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Werror", "-I", str(HOST / "probe"),
                            str(HOST / "replay.cpp"), str(path / "driver.cpp"), "-o", str(exe), "-lpthread"],
                           check=True, capture_output=True, text=True)
            out = path / "rp"
            env = {"N64R_REPLAY_DIR": str(out), "N64R_REPLAY_GAP": "0"}
            result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = {r["fn"]: r for r in map(json.loads, (out / "replay.jsonl").read_text().splitlines())}
            # the word, the byte at +1 and the halfword at +2 of one location are three shapes over the same bytes
            self.assertEqual(rows["f"].get("skip"), "mixed-width")

    def test_one_shape_per_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            source = DRIVER.replace(
                "    uint8_t b = MEM_BU(1, 0x80001000);\n    uint16_t hw = MEM_HU(2, 0x80001000);\n",
                "    uint8_t b = MEM_BU(1, 0x80001020);\n    uint16_t hw = MEM_HU(2, 0x80001020);\n")
            (path / "driver.cpp").write_text(source)
            exe = path / "driver"
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Werror", "-I", str(HOST / "probe"),
                            str(HOST / "replay.cpp"), str(path / "driver.cpp"), "-o", str(exe), "-lpthread"],
                           check=True, capture_output=True, text=True)
            out = path / "rp"
            env = {"N64R_REPLAY_DIR": str(out), "N64R_REPLAY_GAP": "0"}
            result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = {r["fn"]: r for r in map(json.loads, (out / "replay.jsonl").read_text().splitlines())}
            f, g = rows["f"], rows["g"]
            self.assertNotIn("skip", f)
            base = 0x80000000
            # inputs: the word at 0x1000 (value as the guest saw it), the byte and halfword at 0x1020 (zero memory)
            self.assertIn([base + 0x1000, 4, 0x11223344], f["in"])
            self.assertIn([base + 0x1020 + 1, 1, 0], f["in"])
            self.assertIn([base + 0x1020 + 2, 2, 0], f["in"])
            # the call's own store is an output, not an input; the read after it is not an input
            self.assertIn([base + 0x1004, 4, 0x11223345], f["out"])
            self.assertNotIn(0x1004 + base, [r[0] for r in f["in"]])
            self.assertEqual((f["a"], f["sp"], f["ret"], f["f0"]),
                             ([7, 8, 9, 10], 0x80003F00, [0x11223345, 0], 0x3FF0000000000000))
            self.assertEqual(g["depth"], 1)
            self.assertIn([base + 0x2008, 8, 0x0102030405060708], g["out"])
            self.assertIn([base + 0x2008, 8, 0x0102030405060708], f["out"])   # recorded in the outer call too
            self.assertIn([base + 0x1010, 4, 0x3F800000], g["in"])
            self.assertIn([base + 0x3E00, 4, 99], f["out"])                    # filtered by the decomp side, not here
            self.assertEqual(rows["h"]["skip"], "non-ram")


if __name__ == "__main__":
    unittest.main()
