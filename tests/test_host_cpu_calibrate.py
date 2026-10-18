import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp"


class CpuCalibrationTests(unittest.TestCase):
    def test_busiest_complete_window_and_explicit_assumption(self):
        with patch.object(sys, "path", [str(HOST), *sys.path]):
            calibrate = importlib.import_module("cpu_calibrate").calibrate
        log = b"""n64host: cpu_cadence vi=1 count_ticks=0 entries=0 native_ns=0
n64host: cpu_cadence vi=60 count_ticks=46093750 entries=90 native_ns=100000000
n64host: cpu_cadence vi=120 count_ticks=92968750 entries=190 native_ns=300000000
n64host: cpu_cadence vi=180 count_ticks=139843750 entries=290 native_ns=700000000
"""
        result = calibrate(log, {"count_hz": 46875000, "vi_hz": 60}, "measured-run")
        self.assertEqual(result["window_count"], 2)
        self.assertEqual(result["window"]["first_vi"], 120)
        self.assertEqual(result["derived_native_time_scale"], 2.5)
        self.assertEqual(result["observed_count_ticks_per_vi"], 781250)
        self.assertEqual(result["nominal_count_ticks_per_vi"], 781250)
        self.assertIn("full guest CPU budget", result["assumption"])
        self.assertEqual(result["source_run"], "measured-run")
        with self.assertRaisesRegex(ValueError, "no complete"):
            calibrate(b"", {"count_hz": 46875000, "vi_hz": 60}, "empty")
