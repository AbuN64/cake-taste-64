import re
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "hosts" / "n64recomp"


class GoldenDocsTest(unittest.TestCase):
    def test_every_golden_env_var_is_documented(self):
        source = (HOST / "golden.cpp").read_text(encoding="utf-8")
        names = set(re.findall(r"getenv\(\"(N64R_[A-Z_]+)\"\)", source))
        self.assertEqual(names, {"N64R_GOLDEN_DIR", "N64R_SCRIPT", "N64R_SHOT_CMD"})
        docs = (HOST / "RUNTIME.md").read_text(encoding="utf-8")
        for name in names:
            self.assertIn(f"`{name}`", docs)

    def test_recorder_is_inert_without_the_directory(self):
        source = (HOST / "golden.cpp").read_text(encoding="utf-8")
        self.assertIn("if (!d) return;", source)
        self.assertIn("if (!on()) return;", source)


    def test_scripted_quit_flushes_then_exits_without_runtime_teardown(self):
        source = (HOST / "golden.cpp").read_text(encoding="utf-8")
        quit_step = source[source.index('st.kind == "quit"'):]
        self.assertLess(quit_step.index('write_trace("final")'), quit_step.index("std::_Exit(0)"))
        self.assertIn("without runtime\nteardown", (HOST / "RUNTIME.md").read_text(encoding="utf-8"))
if __name__ == "__main__":
    unittest.main()
