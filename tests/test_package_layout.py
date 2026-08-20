"""再編で静かに壊れる箇所を縛る特性テスト。

BASE_DIR 系は解決先を間違えても例外を出さず、空のディレクトリを新規作成して
黙って動く。既存テストは全部緑のまま蓄積ノートだけ見えなくなるため、
リポジトリルートを指し続けることをここで固定する。
"""
import unittest
from pathlib import Path

# Task 2 で `from lipidmix.core import data_config, mcp_core, tool_helpers` に差し替える
import data_config
import mcp_core
import tool_helpers

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestBaseDirResolution(unittest.TestCase):
    def test_base_dir_is_repo_root(self):
        self.assertEqual(mcp_core.BASE_DIR.resolve(), REPO_ROOT)

    def test_base_dir_has_repo_markers(self):
        # lipidmix/core/ を指してしまった場合をここで落とす
        for marker in ("docs", "playbook", "reference"):
            with self.subTest(marker=marker):
                self.assertTrue(
                    (mcp_core.BASE_DIR / marker).is_dir(),
                    f"{marker}/ が見つからない: BASE_DIR={mcp_core.BASE_DIR}",
                )
        self.assertTrue((mcp_core.BASE_DIR / "server.py").is_file())

    def test_output_format_doc_resolves(self):
        self.assertTrue(mcp_core.OUTPUT_FORMAT_DOC.is_file())

    def test_data_config_root_matches_mcp_core(self):
        # 循環回避のため両者は独立に root を計算する。一致することをここで縛る。
        self.assertEqual(
            data_config.DEFAULT_DATA_DIR.resolve(),
            (mcp_core.BASE_DIR / "data").resolve(),
        )


class TestStateDirsOutsidePackage(unittest.TestCase):
    def test_state_dirs_are_not_inside_lipidmix(self):
        package_dir = (REPO_ROOT / "lipidmix").resolve()
        for name in ("KNOWLEDGE_DIR", "PLAYBOOK_DIR", "ANALYSES_DIR"):
            with self.subTest(name=name):
                resolved = getattr(mcp_core, name).resolve()
                self.assertNotIn(
                    package_dir,
                    resolved.parents,
                    f"{name} がパッケージ内を指している: {resolved}",
                )


class TestReferenceTables(unittest.TestCase):
    """`lipid_identity.load_reference_tables` は CWD 相対で reference/ を読む。

    移動では壊れないが、テストを常にリポジトリルートから実行する前提を固定する。
    表が空でも例外にならないので、行数で縛る。
    """

    def test_identity_tables_load_rows(self):
        tables = tool_helpers._identity_tables()
        self.assertEqual(len(tables["lipidmaps"]), 16)
        self.assertEqual(len(tables["refmet"]), 16)
