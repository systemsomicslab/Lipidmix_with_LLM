# tests/test_interp_eval.py
import unittest
from unittest import mock

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

    def test_missing_phase_flagged(self):
        # あるフェーズの全ケースを別フェーズへ付け替える → そのフェーズ未カバーを検出（件数は10維持）。
        cases = _valid_cases()
        for c in cases:
            if c.phase_label == ie.PHASE_LABELS[0]:
                c.phase_label = ie.PHASE_LABELS[1]
        errs = ie.validate_cases(cases, self.allowed)
        self.assertTrue(any("未カバーのフェーズ" in e and ie.PHASE_LABELS[0] in e for e in errs))

    def test_duplicate_id_flagged(self):
        cases = _valid_cases()
        cases[1].id = cases[0].id
        errs = ie.validate_cases(cases, self.allowed)
        self.assertTrue(any("ID 重複" in e for e in errs))

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


class TestBlindSheet(unittest.TestCase):
    def test_make_pairs_three_models(self):
        pairs = ie.make_pairs(["a", "b", "c"])
        self.assertEqual(pairs, [("a", "b"), ("a", "c"), ("b", "c")])

    def _frozen_list(self):
        return [ie.FrozenCase(f"c{i}", "PCA", "NEG", "q", "t", {}, "out")
                for i in range(2)]

    def _interp(self):
        d = {}
        for i in range(2):
            for m in ie.MODEL_KEYS:
                d[(f"c{i}", m)] = f"text-{i}-{m}"
        return d

    def test_sheet_is_blind_and_key_recovers_models(self):
        items, key = ie.build_blind_sheet(self._frozen_list(), self._interp(),
                                          ie.MODEL_KEYS, seed=7)
        # 2ケース × 3ペア = 6 項目
        self.assertEqual(len(items), 6)
        # 盲検: 項目にモデル名が出ない
        for it in items:
            self.assertNotIn("model", it)
            blob = it["a_text"] + it["b_text"]
            # a_text/b_text は該当ケースの2モデル出力のどちらか
            self.assertTrue(blob.startswith("text-") or "text-" in blob)
        # answer_key で A/B の実モデルを復元でき、pair_key の2モデルと一致
        for it in items:
            ak = key[f"{it['case_id']}::{it['pair_key']}"]
            self.assertEqual({ak["A"], ak["B"]}, set(it["pair_key"].split("__")))

    def test_deterministic_with_seed(self):
        a = ie.build_blind_sheet(self._frozen_list(), self._interp(), ie.MODEL_KEYS, 7)
        b = ie.build_blind_sheet(self._frozen_list(), self._interp(), ie.MODEL_KEYS, 7)
        self.assertEqual(a, b)


class TestAggregate(unittest.TestCase):
    def test_overall_and_axis_tallies(self):
        verdicts = [
            ie.PairVerdict("c0", "PCA", "NEG", "qwen25_7b__qwen3_off",
                           overall="qwen3_off",
                           axes={"hallucination": "qwen3_off", "accuracy": "tie",
                                 "completeness": "qwen3_off", "utility": "tie",
                                 "language": "qwen3_off"}),
            ie.PairVerdict("c0", "PCA", "NEG", "qwen3_off__qwen3_on",
                           overall="tie",
                           axes={a: "tie" for a in ie.AXES}),
        ]
        agg = ie.aggregate(verdicts, ie.MODEL_KEYS)
        self.assertEqual(agg["overall"]["qwen3_off"], {"win": 1, "loss": 0, "tie": 1})
        self.assertEqual(agg["overall"]["qwen25_7b"], {"win": 0, "loss": 1, "tie": 0})
        self.assertEqual(agg["by_axis"]["hallucination"]["qwen3_off"], 1)
        self.assertEqual(agg["by_phase_axis"]["PCA|completeness"]["qwen3_off"], 1)


class TestOllamaGenerate(unittest.TestCase):
    def test_posts_without_tools_and_returns_content(self):
        captured = {}

        class FakeResp:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": "解釈テキスト"}}

        def fake_post(url, json=None, timeout=None):
            captured["payload"] = json
            return FakeResp()

        with mock.patch("interp_eval.httpx.post", side_effect=fake_post):
            out = ie.ollama_generate([{"role": "user", "content": "x"}],
                                     model="qwen3:14b", think=True)
        self.assertEqual(out, "解釈テキスト")
        self.assertNotIn("tools", captured["payload"])
        self.assertEqual(captured["payload"]["model"], "qwen3:14b")
        self.assertTrue(captured["payload"]["think"])
        self.assertEqual(captured["payload"]["options"]["temperature"], 0)


class InterpSystemSharedTests(unittest.TestCase):
    def test_interp_system_verbatim(self):
        import interp_eval
        self.assertEqual(
            interp_eval.INTERP_SYSTEM,
            "あなたはMS-DIALリピドミクス解析アシスタントです。直前のツール結果だけを"
            "根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を"
            "創作しないこと。")

    def test_run_system_is_shared(self):
        import interp_eval
        import interp_eval_run
        self.assertEqual(interp_eval_run.SYSTEM, interp_eval.INTERP_SYSTEM)


if __name__ == "__main__":
    unittest.main()
