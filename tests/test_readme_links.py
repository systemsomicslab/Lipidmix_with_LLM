"""README を「要約＋ポインタ」に絞った構成を腐らせないための検証。

README から細部を追い出して他文書へ委譲した以上、(1) ポインタの指す先が実在し、
(2) 参照先の一覧が実装から取り残されていない、の 2 点が保たれないと
「参照しろ」と書いてあるだけの壊れた案内になる。ここが両方を縛る。
"""
import asyncio
import re
import unittest
from pathlib import Path

import server

REPO_ROOT = Path(__file__).resolve().parents[1]

# ルート直下の入口文書。ここから docs/ 以下へポインタが張られる。
ENTRY_DOCS = ("README.md", "USAGE.md", "DEPLOY.md", "CLAUDE.md")

# markdown のインラインリンク `[表示](リンク先)`
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# USAGE.md の見出し: `# USAGE — ms-data-parser MCP ツール一覧(全51ツール)`
USAGE_COUNT_RE = re.compile(r"全\s*(\d+)\s*ツール")
# 表の 1 列目に置かれたツール名: `| \`arf_parser\` | ... |`
USAGE_TOOL_RE = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|", re.MULTILINE)


def _local_targets(text):
    """外部 URL とページ内アンカーを除いた、リポジトリ内リンク先を返す。"""
    for raw in LINK_RE.findall(text):
        target = raw.split()[0].strip()  # `(path "title")` 形式の title を捨てる
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        yield raw, target.split("#", 1)[0]  # 末尾のアンカーを落とす


class TestEntryDocLinks(unittest.TestCase):
    def test_relative_links_resolve(self):
        checked = 0
        for name in ENTRY_DOCS:
            doc = REPO_ROOT / name
            if not doc.is_file():
                continue
            for raw, target in _local_targets(doc.read_text(encoding="utf-8")):
                if not target:
                    continue
                checked += 1
                with self.subTest(doc=name, link=raw):
                    self.assertTrue(
                        (doc.parent / target).exists(),
                        f"{name} のリンク先が存在しない: {raw}",
                    )
        # リンク記法を一切拾えていない＝正規表現が壊れた場合をここで落とす
        self.assertGreater(checked, 0, "入口文書からリンクを 1 つも検出できなかった")


class TestUsageMatchesRegistry(unittest.TestCase):
    """README はツール一覧を USAGE.md へ委譲する。委譲先が古いと案内が壊れる。"""

    @classmethod
    def setUpClass(cls):
        cls.usage = (REPO_ROOT / "USAGE.md").read_text(encoding="utf-8")
        cls.registered = {t.name for t in asyncio.run(server.mcp.list_tools())}

    def test_every_registered_tool_is_documented(self):
        documented = set(USAGE_TOOL_RE.findall(self.usage))
        missing = sorted(self.registered - documented)
        self.assertEqual(missing, [], f"USAGE.md に未記載のツール: {missing}")

    def test_no_documented_tool_has_been_removed(self):
        documented = set(USAGE_TOOL_RE.findall(self.usage))
        stale = sorted(documented - self.registered)
        self.assertEqual(stale, [], f"USAGE.md に残った実在しないツール: {stale}")

    def test_declared_count_matches_registry(self):
        found = USAGE_COUNT_RE.search(self.usage)
        self.assertIsNotNone(found, "USAGE.md の見出しに「全Nツール」が見つからない")
        self.assertEqual(
            int(found.group(1)), len(self.registered),
            "USAGE.md の見出しのツール数が実登録数と一致しない",
        )


if __name__ == "__main__":
    unittest.main()
