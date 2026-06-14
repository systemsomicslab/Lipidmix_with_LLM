"""データフォルダ入口ツール load_dataset と arf 自動選択の検証。

実バイナリ(.arf2/.arf)が無くても通る範囲を確認する:
- PeakProperties.arf を DriftSpots.arf より優先すること
- load_dataset が未登録/欠損を握りつぶさず警告で返すこと
- load_dataset がツールとして登録されていること
"""

import asyncio
import os
import unittest
from pathlib import Path
import tempfile

import server


class ResolveArfPreferenceTests(unittest.TestCase):
    def setUp(self):
        self._orig_data_dir = server.DATA_DIR

    def tearDown(self):
        server.DATA_DIR = self._orig_data_dir

    def _touch(self, directory: Path, name: str) -> None:
        (directory / name).write_bytes(b"")  # 中身は不要（パス解決のみ検証）

    def test_prefers_peakproperties_over_driftspots(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._touch(directory, "Sample_DriftSpots.arf")
            self._touch(directory, "Sample_PeakProperties.arf")
            server.DATA_DIR = directory
            resolved = server.resolve_arf_file_path()
            self.assertIsNotNone(resolved)
            self.assertTrue(resolved.lower().endswith("peakproperties.arf"))

    def test_falls_back_when_no_peakproperties(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._touch(directory, "Sample_DriftSpots.arf")
            server.DATA_DIR = directory
            resolved = server.resolve_arf_file_path()
            self.assertTrue(resolved.lower().endswith("driftspots.arf"))

    def test_explicit_path_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._touch(directory, "Sample_DriftSpots.arf")
            explicit = str(directory / "Sample_DriftSpots.arf")
            self.assertEqual(server.resolve_arf_file_path(explicit), explicit)


class LoadDatasetTests(unittest.TestCase):
    def setUp(self):
        self._orig_data_dir = server.DATA_DIR

    def tearDown(self):
        server.DATA_DIR = self._orig_data_dir

    def test_registered_as_tool(self):
        tools = asyncio.run(server.mcp.list_tools())
        names = {t.name for t in tools}
        self.assertIn("load_dataset", names)

    def test_missing_directory_returns_error(self):
        out = server.load_dataset(directory=str(Path(tempfile.gettempdir()) / "no_such_dir_xyz"))
        self.assertTrue(any("存在しません" in str(x) for x in out))

    def test_empty_directory_warns_for_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = server.load_dataset(directory=tmp)
            joined = "\n".join(str(x) for x in out)
            self.assertIn(".arf2 ファイルが見つかりませんでした", joined)
            self.assertIn(".arf", joined)
            # 既定探索先が指定フォルダに更新される
            self.assertEqual(str(server.DATA_DIR), tmp)


class PickLatestDuplicateTests(unittest.TestCase):
    """同種ファイルが重複（旧版/新版）する場合に最新版を選ぶこと。"""

    def setUp(self):
        self._orig_data_dir = server.DATA_DIR

    def tearDown(self):
        server.DATA_DIR = self._orig_data_dir

    def _touch(self, directory: Path, name: str, mtime: float | None = None) -> Path:
        path = directory / name
        path.write_bytes(b"")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_arf2_picks_latest_embedded_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._touch(directory, "AlignmentResult_2026_05_15_10_13_35.arf2")
            self._touch(directory, "AlignmentResult_2026_06_01_09_00_00.arf2")
            server.DATA_DIR = directory
            resolved = server.resolve_arf2_file_path()
            self.assertTrue(resolved.endswith("2026_06_01_09_00_00.arf2"))

    def test_arf_embedded_timestamp_beats_mtime(self):
        # 旧タイムスタンプ版に新しい mtime を与えても、埋め込みタイムスタンプが優先される
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._touch(
                directory,
                "AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf",
                mtime=10_000_000_000,  # わざと新しい mtime
            )
            self._touch(
                directory,
                "AlignmentResult_2026_06_01_09_00_00_PeakProperties.arf",
                mtime=1_000_000_000,  # わざと古い mtime
            )
            server.DATA_DIR = directory
            resolved = server.resolve_arf_file_path()
            self.assertTrue(resolved.endswith("2026_06_01_09_00_00_PeakProperties.arf"))

    def test_falls_back_to_mtime_without_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._touch(directory, "older.aef", mtime=1_000_000_000)
            self._touch(directory, "newer.aef", mtime=2_000_000_000)
            server.DATA_DIR = directory
            resolved = server.resolve_eicaef_file_path()
            self.assertTrue(resolved.endswith("newer.aef"))


if __name__ == "__main__":
    unittest.main()
