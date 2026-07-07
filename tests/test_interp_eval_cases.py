# tests/test_interp_eval_cases.py
import unittest

import interp_eval as ie
import interp_eval_cases as cases
import agent_core as ac


class TestCasesWellFormed(unittest.TestCase):
    def test_validate_passes(self):
        errs = ie.validate_cases(cases.CASES, ac._ALLOWED_TOOLS)
        self.assertEqual(errs, [], msg=f"validate errors: {errs}")

    def test_covers_five_phases_two_modes(self):
        combos = {(c.phase_label, c.mode) for c in cases.CASES}
        expected = {(p, m) for p in ie.PHASE_LABELS for m in ie.MODES}
        self.assertEqual(combos, expected)


if __name__ == "__main__":
    unittest.main()
