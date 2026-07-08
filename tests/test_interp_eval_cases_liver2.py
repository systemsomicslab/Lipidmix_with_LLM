import unittest

import interp_eval as ie
import phase_router
from interp_eval_cases_liver2 import LIVER2_CASES

ALLOWED = {n for names in phase_router.PHASES.values() for n in names}


class TestLiver2Cases(unittest.TestCase):
    def test_structure_valid_and_covers_all_phases(self):
        self.assertEqual(ie.validate_cases(LIVER2_CASES, ALLOWED), [])

    def test_has_neg_and_pos_and_unique_ids(self):
        modes = {c.mode for c in LIVER2_CASES}
        self.assertEqual(modes, {"NEG", "POS"})
        ids = [c.id for c in LIVER2_CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_pipeline_step_tool_is_allowed(self):
        for c in LIVER2_CASES:
            for step in c.pipeline:
                self.assertIn(step.name, ALLOWED, f"{c.id}:{step.name}")


if __name__ == "__main__":
    unittest.main()
