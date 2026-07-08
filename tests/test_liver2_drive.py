import unittest
from unittest import mock

import liver2_drive


class TestDrive(unittest.TestCase):
    def test_runs_steps_and_formats_output(self):
        with mock.patch.object(liver2_drive.ac, "execute_tool",
                               side_effect=["LOADED", "PCA_OUT"]) as ex, \
             mock.patch.object(liver2_drive.session_state, "session", None), \
             mock.patch.object(liver2_drive.server, "AnalysisSession",
                               return_value=object()):
            out = liver2_drive.drive("D", [
                {"name": "load_dataset", "args": {"directory": "D"}},
                {"name": "arf_re_pca", "args": {"top_features": 10}}])
        self.assertIn("=== load_dataset", out)
        self.assertIn("LOADED", out)
        self.assertIn("=== arf_re_pca", out)
        self.assertIn("PCA_OUT", out)
        self.assertEqual(ex.call_count, 2)


if __name__ == "__main__":
    unittest.main()
