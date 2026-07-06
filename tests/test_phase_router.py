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
