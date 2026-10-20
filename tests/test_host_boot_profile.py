import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "host_boot_profile", Path(__file__).resolve().parents[1] / "hosts/n64recomp/boot_profile.py",
)
boot_profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot_profile)


class BootProfileTests(unittest.TestCase):
    def facts(self):
        return {
            "header_entrypoint": "0x80000400", "entrypoint": "0x80200400",
            "boot": {"rdram_size": 0x800000, "vi_mode": "0x80000800",
                     "registers": {"status": "0x24000000"}, "os_clock_rate": "0x80001000"},
            "cpu": {"timing": {"count_hz": 46875000}},
            "sections": [{"vram": 0x80200400, "rom": 0x1000}, {"vram": 0x80400400, "rom": 0x140000}],
        }

    def test_rom_entry_is_distinct_from_analysis_link_address(self):
        rendered = boot_profile.render(self.facts())
        self.assertIn("HOST_ROM_ENTRY_ADDR 2147484672u", rendered)
        self.assertIn("HOST_INITIAL_STATUS 603979776u", rendered)
        self.assertIn("HOST_INITIAL_VI_MODE 2147485696u", rendered)
        self.assertIn("{2149581824u,4096u}", rendered)
        self.assertIn("{2151678976u,1310720u}", rendered)

    def test_clock_global_and_runtime_rate_are_explicit(self):
        rendered = boot_profile.render(self.facts())
        self.assertIn("HOST_OS_CLOCK_RATE 2147487744u", rendered)
        self.assertIn("HOST_COUNT_HZ 46875000u", rendered)
        for address in (True, -1, 0x1000, 0x80001004, 0x80800000):
            facts = self.facts()
            facts["boot"]["os_clock_rate"] = address
            with self.assertRaises(ValueError):
                boot_profile.render(facts)
        for rate in (True, 0, 62500000):
            facts = self.facts()
            facts["cpu"]["timing"]["count_hz"] = rate
            with self.assertRaises(ValueError):
                boot_profile.render(facts)
        facts = self.facts()
        del facts["boot"]["os_clock_rate"]
        with self.assertRaises(KeyError):
            boot_profile.render(facts)

    def test_invalid_dma_destination_fails_before_build(self):
        for entry in (0x00700000, 0x80700400, -1, 0x100000000):
            facts = self.facts()
            facts["header_entrypoint"] = entry
            with self.assertRaises(ValueError):
                boot_profile.render(facts)

    def test_duplicate_and_missing_section_mapping_fail(self):
        for sections in ([], [{"vram": 1, "rom": 4}, {"vram": 1, "rom": 8}]):
            facts = self.facts()
            facts["sections"] = sections
            with self.assertRaises(ValueError):
                boot_profile.render(facts)


if __name__ == "__main__":
    unittest.main()
