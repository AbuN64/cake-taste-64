"""Validate architectural boot TLB entries and the original faulted-thread symbol."""
import json
import sys
from pathlib import Path


def render(facts):
    boot = facts["boot"]
    fault = int(boot["faulted_thread"], 0)
    if not 0x80000000 <= fault <= 0x80000000 + boot["rdram_size"] - 4 or fault & 3:
        raise ValueError("faulted_thread must be an aligned RDRAM word")
    if boot["tlb_map_abi"] != "entrylo_flags":
        raise ValueError("unqualified TLB mapping ABI")
    rows = []
    indices = set()
    for item in boot["tlb"]:
        i, size = item["index"], item["page_size"]
        if i in indices or not 0 <= i < 32 or size not in (4096, 16384, 65536, 262144, 1048576, 4194304, 16777216):
            raise ValueError("invalid boot TLB index or page size")
        indices.add(i)
        if item["size"] not in (size, size * 2):
            raise ValueError("boot TLB extent must cover one or two pages")
        odd = item["paddr"] + size if item["size"] == size * 2 else 0xffffffff
        rows.append("{" + ",".join(str(n) + "u" for n in (i, size * 2 - 8192, item["vaddr"], item["paddr"], odd)) + "}")
    return ("#pragma once\n#include <stdint.h>\n"
            f"#define HOST_FAULTED_THREAD {fault}u\n#define HOST_BOOT_TLB_COUNT {len(rows)}\n"
            "static const uint32_t host_boot_tlb[][5] = {" + ",".join([*rows, "{0,0,0,0,0}"]) + "};\n")


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(render(json.loads(Path(sys.argv[1]).read_text())))
