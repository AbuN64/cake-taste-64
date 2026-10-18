import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class QueueTraceTests(unittest.TestCase):
    def test_forward_filter_snapshot_and_invalid_address(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "host_profile.h").write_text("#define HOST_RDRAM_SIZE 4096u\n")
            (root / "librecomp").mkdir()
            (root / "ultramodern").mkdir()
            (root / "librecomp/game.hpp").write_text(
                "#pragma once\n#include <cstdint>\n"
                "struct recomp_context {uint64_t r2,r4,r5,r6;};\n"
            )
            (root / "ultramodern/ultramodern.hpp").write_text(
                "#include <cstdint>\nnamespace ultramodern { int32_t this_thread(); }\n"
            )
            (root / "mock.cpp").write_text(r"""
#include <cassert>
#include <cstring>
#include "librecomp/game.hpp"
static unsigned calls;
namespace ultramodern { int32_t this_thread() { return 0x80001000; } }
extern "C" uint8_t *n64host_xlate_slow(uint8_t *r,uint32_t a) { return r+(a&4095); }
#define REAL(n) extern "C" void __real_##n##_recomp(uint8_t*,recomp_context *c) {++calls;c->r2=42;}
REAL(osRecvMesg) REAL(osSendMesg) REAL(osCreateMesgQueue)
REAL(osContStartQuery) REAL(osContStartReadData) REAL(osContGetQuery)
extern "C" void __wrap_osRecvMesg_recomp(uint8_t*,recomp_context*);
int main(int argc, char**) {
 uint8_t ram[4096]={};
 uint32_t words[]={0,0,1,0,8,0x80000200};
 memcpy(ram+256,words,sizeof(words));
 recomp_context c{7,argc>1 ? 0x80000ff0u : 0x80000100u,0,1};
 __wrap_osRecvMesg_recomp(ram,&c);
 assert(c.r2==42 && calls==1 && !memcmp(ram+256,words,sizeof(words)));
 c.r4=0x80000300;
 __wrap_osRecvMesg_recomp(ram,&c);
 assert(calls==2 && c.r2==42);
}
""")
            binary = root / "test"
            subprocess.run(
                ["c++", "-std=c++17", "-Wall", "-Wextra", "-I", str(root),
                 "-I", str(HOST / "probe"), str(HOST / "queue_trace.cpp"),
                 str(root / "mock.cpp"), "-o", str(binary)],
                check=True, capture_output=True,
            )
            env = {k: v for k, v in os.environ.items() if not k.startswith("N64R_QUEUE_")}
            subprocess.run([str(binary)], env=env, check=True, capture_output=True)
            path = root / "queue.jsonl"
            env.update(N64R_QUEUE_TRACE=str(path), N64R_QUEUE_ADDRESS="0x80000100")
            subprocess.run([str(binary)], env=env, check=True, capture_output=True)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r["phase"] for r in rows], ["enter", "return"])
            self.assertEqual([r["v0"] for r in rows], [7, 42])
            self.assertEqual(rows[0]["queue"], [0, 0, 1, 0, 8, 0x80000200])
            self.assertEqual(rows[0]["thread"], 0x80001000)
            self.assertGreater(len(rows[0]["stack"]), 1)
            self.assertEqual(rows[1]["stack"], [])
            for value in ("invalid", "0x80000101", "0x100000000", "0x80000ff0"):
                env["N64R_QUEUE_ADDRESS"] = value
                result = subprocess.run([str(binary)] + (["span"] if value == "0x80000ff0" else []), env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 4)
                self.assertIn("queue trace", result.stderr)
