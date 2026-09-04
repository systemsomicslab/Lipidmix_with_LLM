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

async def _registry_sizes():
    """ツール / リソース / リソーステンプレートの実登録数。"""
    return (
        len(await server.mcp.list_tools()),
        len(await server.mcp.list_resources()),
        len(await server.mcp.list_resource_templates()),
    )


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


# CLAUDE.md 冒頭の規模表記: `ツール 51・リソース 4・リソーステンプレート 3`
CLAUDE_SCALE_RE = re.compile(
    r"ツール\s*(\d+)\s*・\s*リソース\s*(\d+)\s*・\s*リソーステンプレート\s*(\d+)"
)

# README.md / CLAUDE.md に置いてはならない数量表現。
# どれも「正準が別文書にあり、そちらは実装と突き合わせ済み」か「実行すれば分かる」数で、
# 写しだけが腐る。並列で複数エージェントが走ると特に早く腐り、しかも全員が同じ
# 古い数字を読むため被害が揃う。書かせないことで根本から絶つ。
FORBIDDEN_COUNTS = (
    (re.compile(r"\d+\s*ツール"),
     "ツール数の正準は USAGE.md と docs/workflow/index.md（どちらも実登録と突き合わせ済み）"),
    (re.compile(r"\d+\s*文書"),
     "workflow の文書数の正準は docs/workflow/index.md"),
    (re.compile(r"\d+\s*件"),
     "テスト件数の正準は pytest の出力"),
)


class TestClaudeMdScaleMatchesRegistry(unittest.TestCase):
    """CLAUDE.md 冒頭の規模表記だけは残す。全エージェントが最初に読む要約であり、
    ここは削るより実登録に縛るほうが価値が高い。"""

    def test_scale_line_matches_registry(self):
        text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        found = CLAUDE_SCALE_RE.search(text)
        self.assertIsNotNone(
            found, "CLAUDE.md に「ツール N・リソース N・リソーステンプレート N」が見つからない"
        )
        tools, resources, templates = (int(g) for g in found.groups())
        actual = asyncio.run(_registry_sizes())
        self.assertEqual(
            (tools, resources, templates), actual,
            "CLAUDE.md の規模表記が実登録（ツール/リソース/テンプレート）と一致しない",
        )


class TestMapDocsCarryNoDuplicatedCounts(unittest.TestCase):
    """README.md / CLAUDE.md は「地図」であって数え上げの置き場ではない。

    README では既に「数値は書かない」方針を採っている。同じ方針を CLAUDE.md へ
    広げ、方針そのものをここで固定する。例外は上の規模表記のみ（実登録に縛ってある）。
    """

    MAP_DOCS = ("README.md", "CLAUDE.md")

    def test_regexes_are_alive(self):
        # 正規表現が壊れて何も検出しなくなった場合、上の検査は黙って緑になる
        samples = ("全 51 ツール", "9 文書", "全 766 件")
        for pattern, _ in FORBIDDEN_COUNTS:
            with self.subTest(pattern=pattern.pattern):
                self.assertTrue(
                    any(pattern.search(s) for s in samples),
                    f"検出できないパターン: {pattern.pattern}",
                )

    def test_no_forbidden_counts(self):
        for name in self.MAP_DOCS:
            doc = REPO_ROOT / name
            if not doc.is_file():
                continue
            for line_no, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
                for pattern, reason in FORBIDDEN_COUNTS:
                    hit = pattern.search(line)
                    if hit is None:
                        continue
                    with self.subTest(doc=name, line=line_no):
                        self.fail(
                            f"{name}:{line_no} に数量表現「{hit.group(0)}」がある。{reason}"
                        )


if __name__ == "__main__":
    unittest.main()
