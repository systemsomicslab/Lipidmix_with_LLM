# tests/test_interp_eval_cases.py
import unittest

import interp_eval as ie
import interp_eval_cases as cases
import agent_core as ac


class TestCasesWellFormed(unittest.TestCase):
    def test_validate_passes(self):
        errs = ie.validate_cases(cases.CASES, ac._ALLOWED_TOOLS)
        self.assertEqual(errs, [], msg=f"validate errors: {errs}")

    def test_covers_all_five_phases(self):
        # POS が単一群 n=3 のため厳密な 5×2 直積は不成立。全5フェーズの被覆と
        # モード妥当性・件数10 を検証する（差次は NEG 2コントラストで2件）。
        self.assertEqual({c.phase_label for c in cases.CASES}, set(ie.PHASE_LABELS))
        self.assertEqual(len(cases.CASES), 10)
        self.assertTrue(all(c.mode in ie.MODES for c in cases.CASES))


if __name__ == "__main__":
    unittest.main()
