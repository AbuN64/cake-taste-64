import subprocess
import tempfile
import unittest
from pathlib import Path

from cake_taste import TasteError
from cake_taste.repro.cases import parse_events

HOST = Path(__file__).resolve().parents[1] / "hosts/n64recomp/behave"


class FpuEffectsTests(unittest.TestCase):
    def test_effects_survive_parsing_and_unknown_is_not_clean(self):
        cases = parse_events("case divide\nfpu 23\nend ok\ncase failed\nfpu unknown\nend fatal\n")
        self.assertEqual(cases[0]["fpu"], {
            "rounding": 3, "flags": 32, "exceptions": ["divide-by-zero"],
            "guest_exception_enables": "unmodeled",
        })
        self.assertIsNone(cases[1]["fpu"])
        with self.assertRaises(TasteError):
            parse_events("case invalid\nfpu 400\nend ok\n")

    def test_native_effects_are_isolated_and_callers_environment_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "effects.c"
            source.write_text('''#include <assert.h>
#include "fpu.h"
int main(void) {
    fenv_t saved;
    volatile float one = 1.0f, zero = 0.0f, result;
    fesetround(FE_DOWNWARD);
    feraiseexcept(FE_OVERFLOW);
    behave_fpu_begin(&saved);
    assert(fegetround() == FE_TONEAREST);
    assert(fetestexcept(FE_ALL_EXCEPT) == 0);
    result = one / zero;
    fesetround(FE_UPWARD);
    unsigned effects = behave_fpu_end(&saved);
    assert(effects == ((1u << 5) | 2u));
    assert(fegetround() == FE_DOWNWARD);
    assert(fetestexcept(FE_OVERFLOW));
    behave_fpu_begin(&saved);
    assert(behave_fpu_end(&saved) == 0);
    behave_fpu_begin(&saved);
    result = zero / zero;
    assert(behave_fpu_end(&saved) == (1u << 6));
    (void)result;
    return 0;
}
''')
            binary = root / "effects"
            subprocess.run(
                ["cc", "-std=c11", "-O0", "-I", str(HOST), str(source), "-lm", "-o", str(binary)],
                check=True, capture_output=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)
