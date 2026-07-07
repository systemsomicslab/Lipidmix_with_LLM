# tests/test_interp_eval.py
import unittest

import interp_eval as ie


def _valid_cases():
    cases = []
    for phase in ie.PHASE_LABELS:
        for mode in ie.MODES:
            cases.append(ie.Case(
                id=f"{phase.lower()}_{mode.lower()}",
                phase_label=phase,
                mode=mode,
                query=f"{phase} {mode} を解釈して",
                pipeline=[ie.ToolStep("load_dataset", {"directory": "X"})],
            ))
    return cases


class TestValidateCases(unittest.TestCase):
    def setUp(self):
        self.allowed = {"load_dataset", "arf_re_pca"}

    def test_valid_set_has_no_errors(self):
        self.assertEqual(ie.validate_cases(_valid_cases(), self.allowed), [])

    def test_wrong_count_flagged(self):
        errs = ie.validate_cases(_valid_cases()[:-1], self.allowed)
        self.assertTrue(any("10" in e for e in errs))

    def test_duplicate_phase_mode_flagged(self):
        cases = _valid_cases()
        cases[1] = ie.Case(cases[1].id + "x", cases[0].phase_label, cases[0].mode,
                           "q", [ie.ToolStep("load_dataset", {})])
        errs = ie.validate_cases(cases, self.allowed)
        self.assertTrue(any("重複" in e for e in errs))

    def test_unknown_tool_flagged(self):
        cases = _valid_cases()
        cases[0].pipeline.append(ie.ToolStep("no_such_tool", {}))
        errs = ie.validate_cases(cases, self.allowed)
        self.assertTrue(any("no_such_tool" in e for e in errs))


class TestBuildInterpMessages(unittest.TestCase):
    def _frozen(self):
        return ie.FrozenCase("pca_neg", "PCA", "NEG", "PCAを解釈して",
                             "arf_re_pca", {"top_features": 10}, '{"pc1": 42.0}')

    def test_shape_and_roles(self):
        msgs = ie.build_interp_messages("SYS", self._frozen())
        self.assertEqual([m["role"] for m in msgs],
                         ["system", "user", "assistant", "tool"])
        self.assertEqual(msgs[0]["content"], "SYS")
        self.assertEqual(msgs[1]["content"], "PCAを解釈して")

    def test_tool_call_and_result_wired(self):
        msgs = ie.build_interp_messages("SYS", self._frozen())
        call = msgs[2]["tool_calls"][0]["function"]
        self.assertEqual(call["name"], "arf_re_pca")
        self.assertEqual(call["arguments"], {"top_features": 10})
        self.assertEqual(msgs[3]["content"], '{"pc1": 42.0}')
        self.assertEqual(msgs[3]["tool_name"], "arf_re_pca")


if __name__ == "__main__":
    unittest.main()
