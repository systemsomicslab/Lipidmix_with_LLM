import unittest

import interp_eval_liver2 as run


class TestBuildAgent(unittest.TestCase):
    def test_azure_agent_has_no_cloud_interp_fn(self):
        agent = run.build_agent("azure", schemas={})
        self.assertIsNone(agent.interp_fn)          # Azure 自身が最終解釈も書く
        self.assertEqual(agent.execute_fn.__name__, "execute_tool")

    def test_hybrid_agent_has_cloud_interp_fn(self):
        agent = run.build_agent("hybrid", schemas={})
        self.assertIsNotNone(agent.interp_fn)        # 高価値ターンで Azure へエスカレート

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            run.build_agent("nope", schemas={})


if __name__ == "__main__":
    unittest.main()
