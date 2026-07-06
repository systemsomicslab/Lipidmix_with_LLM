import asyncio
import unittest

import server
import phase_router as pr


class TestPhaseCoverage(unittest.TestCase):
    def setUp(self):
        tools = asyncio.run(server.mcp.list_tools())
        self.tool_names = {t.name for t in tools}

    def test_all_phase_tools_exist(self):
        for phase, names in pr.PHASES.items():
            for n in names:
                self.assertIn(n, self.tool_names, f"{phase}:{n} が server のツールに無い")

    def test_partition_is_exact(self):
        assigned = [n for names in pr.PHASES.values() for n in names]
        self.assertEqual(len(assigned), len(set(assigned)), "フェーズ間でツールが重複")
        self.assertEqual(set(assigned), self.tool_names,
                         "PHASES は全ツールを過不足なく分割していない")

    def test_core_tools_exist(self):
        for n in pr.CORE_TOOLS:
            self.assertIn(n, self.tool_names)

    def test_analysis_phases_exclude_entry(self):
        self.assertNotIn("ENTRY", pr.ANALYSIS_PHASES)
        self.assertEqual(set(pr.ANALYSIS_PHASES), set(pr.PHASES) - {"ENTRY"})


class TestParsePhase(unittest.TestCase):
    cands = ["ARF", "PAI2", "EIC", "LITERATURE"]

    def test_exact_match(self):
        self.assertEqual(pr._parse_phase("PAI2", self.cands), "PAI2")

    def test_whitespace_and_case(self):
        self.assertEqual(pr._parse_phase("  pai2\n", self.cands), "PAI2")

    def test_embedded_name(self):
        self.assertEqual(
            pr._parse_phase("このクエリのフェーズは LITERATURE です", self.cands),
            "LITERATURE",
        )

    def test_unknown_returns_none(self):
        self.assertIsNone(pr._parse_phase("わからない", self.cands))

    def test_descriptions_cover_analysis_phases(self):
        self.assertEqual(set(pr.PHASE_DESCRIPTIONS), set(pr.ANALYSIS_PHASES))


class TestRoute(unittest.TestCase):
    @staticmethod
    def _fake(ret, spy=None):
        def fn(query, candidates):
            if spy is not None:
                spy.append((query, candidates))
            return ret
        return fn

    def test_gate_entry_when_not_loaded(self):
        spy = []
        st = pr.RouterState(dataset_loaded=False)
        res = pr.route("PAI2ファイルを解析して", st, self._fake("PAI2", spy))
        self.assertEqual(res.phase, "ENTRY")
        self.assertEqual(res.reason, "gate")
        self.assertEqual(spy, [])  # LLM を呼ばない

    def test_keyword_pai2_short_circuits(self):
        spy = []
        st = pr.RouterState(dataset_loaded=True)
        res = pr.route("C:/data/x.pai2 を解析して", st, self._fake("EIC", spy))
        self.assertEqual(res.phase, "PAI2")
        self.assertEqual(res.reason, "keyword")
        self.assertEqual(spy, [])  # キーワードヒット時は LLM を呼ばない

    def test_keyword_eic_and_literature(self):
        st = pr.RouterState(dataset_loaded=True)
        r1 = pr.route("m/z 700〜720 のEICピークを検索して", st, self._fake("ARF"))
        self.assertEqual(r1.phase, "EIC")
        r2 = pr.route("Europe PMC で文献を検索して", st, self._fake("ARF"))
        self.assertEqual(r2.phase, "LITERATURE")

    def test_llm_used_when_no_keyword(self):
        st = pr.RouterState(dataset_loaded=True)
        res = pr.route("保留中ノートのレビューキューを見せて", st, self._fake("LITERATURE"))
        self.assertEqual(res.phase, "LITERATURE")
        self.assertEqual(res.reason, "llm")

    def test_fallback_to_last_phase(self):
        st = pr.RouterState(dataset_loaded=True, last_phase="ARF")
        res = pr.route("なにか曖昧な要求", st, self._fake("NONSENSE"))
        self.assertEqual(res.phase, "ARF")
        self.assertEqual(res.reason, "fallback")

    def test_fallback_entry_when_no_last_phase(self):
        st = pr.RouterState(dataset_loaded=True, last_phase=None)
        res = pr.route("なにか曖昧な要求", st, self._fake("NONSENSE"))
        self.assertEqual(res.phase, "ENTRY")
        self.assertEqual(res.reason, "fallback")

    def test_core_tools_exposed_and_dedup(self):
        st = pr.RouterState(dataset_loaded=True)
        res = pr.route("群間の差次的解析を実行して", st, self._fake("ARF"))
        for c in pr.CORE_TOOLS:
            self.assertIn(c, res.tool_names)
        self.assertEqual(len(res.tool_names), len(set(res.tool_names)))
        # ARF フェーズのツールも含む
        self.assertIn("arf_differential", res.tool_names)

    def test_entry_has_no_duplicate_core(self):
        st = pr.RouterState(dataset_loaded=False)
        res = pr.route("フォルダを読み込んで", st, self._fake(""))
        self.assertEqual(len(res.tool_names), len(set(res.tool_names)))
