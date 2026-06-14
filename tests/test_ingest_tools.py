"""文献探索ツール群の登録と knowledge_coverage のスモーク検証。"""

import asyncio
import unittest

import server


class IngestToolRegistrationTests(unittest.TestCase):
    def test_new_tools_registered(self):
        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        for expected in (
            "knowledge_coverage",
            "paper_search",
            "ingest_stage",
            "ingest_review_queue",
            "ingest_promote",
            "ingest_reject",
        ):
            self.assertIn(expected, names)

    def test_inbox_resource_registered(self):
        uris = {str(r.uri) for r in asyncio.run(server.mcp.list_resources())}
        self.assertIn("lipidmix://knowledge/inbox", uris)


class KnowledgeCoverageSmokeTests(unittest.TestCase):
    def test_coverage_on_sample_objective(self):
        # サンプル objective（frontmatter の analysis_id で解決）
        out = server.knowledge_coverage("2026-06-13-neg-lipidome-trt-vs-ctrl")
        # 3つの小問が状態付きで返る
        self.assertIn("どの脂質クラスに群間差", out)
        self.assertIn("エーテル脂質", out)
        self.assertTrue(any(state in out for state in ("COVERED", "WEAK", "GAP")))

    def test_missing_objective(self):
        out = server.knowledge_coverage("no-such-analysis-id")
        self.assertIn("見つかりません", out)


if __name__ == "__main__":
    unittest.main()
