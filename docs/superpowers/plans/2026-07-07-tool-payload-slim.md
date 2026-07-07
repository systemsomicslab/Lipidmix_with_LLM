# 高価値ツール戻り値の構造化スリム化（C）実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PCA・文献ツールの戻り値 payload をスリム化し、`_truncate(8000)` 後もモデルが結論と解釈の実体（PCA loadings／全候補 title＋抄録冒頭）を必ず見られるようにする。

**Architecture:** 差次スリム（`arf_differential`）で確立した per-tool パターンを踏襲する。冗長なプロット用配列を解釈 payload から外し、全量は session に残して図ツール（`save_pca_figure`）が参照する。`agent_core._truncate(8000)` は backstop として不変。

**Tech Stack:** Python 3, unittest, fastmcp（既存）。新規依存なし。

## Global Constraints

- 後方互換: 既存ツール名・既定挙動・図ツール（`save_pca_figure`）は不変。図データ供給源 `session.last_pca_plot` / `_remember_arf_pca_plot` / `arf_reader.run_pca` は変更しない。
- 抄録上限 = **500字**（超過時は末尾に `…（截断）` を付す）。
- `agent_core._truncate(text, 8000)` は変更しない（backstop）。
- 差次・QC・identity の payload は変更しない（既に 8000字内）。
- テストは `tests/` 配下の unittest スタイル（`python -m pytest` で実行可）。
- 実行は `PYTHONPATH=<proj> .venv-1/Scripts/python.exe`。

---

### Task 1: PCA payload スリム化（散布図点列を外し要約＋loadings 保全）

**Files:**
- Modify: `tool_helpers.py:144-172`（`_format_pca_plot_block`）
- Modify: `docs/output_format.md:491-503`（§8.1 スコアプロット用JSON の記述同期）
- Test: `tests/test_report_tools.py`（`PcaPlotHelperTests` クラスに追加）

**Interfaces:**
- Consumes: なし（既存ヘルパーの内部変更）。
- Produces: `_format_pca_plot_block(pca_result, sample_names, title, intro, groups=None) -> str` — 署名不変。戻り文字列は散布図 per-sample 配列を**含まず**、`intro` ＋ タイトル行 ＋ `PC1 (NN.NN%) × PC2 (NN.NN%)` 行 ＋ 群別サンプル数（または総数）行 ＋ `save_pca_figure` note 行から成る。呼び出し3箇所（`tools_arf.py:184` / `:308` / `:458`）は無変更。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_report_tools.py` の `PcaPlotHelperTests` クラス（`test_remember_arf_pca_plot_builds_session_state` の直後）に追加する:

```python
    def test_format_pca_plot_block_omits_points_keeps_summary(self):
        import tool_helpers
        block = tool_helpers._format_pca_plot_block(
            {"components": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]],
             "explained_variance_ratio": [0.24, 0.15]},
            ["s1", "s2", "s3", "s4"],
            title="PCA Score Plot (x.arf)",
            intro="\n#### 📊 PCA スコア要約\n",
            groups={"s1": "A", "s2": "A", "s3": "B", "s4": "B"},
        )
        # 散布図の per-sample 点列は非同梱
        self.assertNotIn('"pc1"', block)
        self.assertNotIn('"sample"', block)
        self.assertNotIn("```json", block)
        # 結論・群別サンプル数・図示 note は残る
        self.assertIn("24.00%", block)
        self.assertIn("15.00%", block)
        self.assertIn("A=2", block)
        self.assertIn("B=2", block)
        self.assertIn("save_pca_figure", block)

    def test_format_pca_plot_block_no_groups_shows_total(self):
        import tool_helpers
        block = tool_helpers._format_pca_plot_block(
            {"components": [[1.0, 2.0], [3.0, 4.0]],
             "explained_variance_ratio": [0.5, 0.3]},
            ["s1", "s2"],
            title="T",
            intro="\n#### PCA\n",
        )
        self.assertIn("サンプル数: 2", block)
        self.assertNotIn('"pc1"', block)
```

- [ ] **Step 2: テストを走らせ失敗を確認**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_report_tools.py -k format_pca_plot_block -v`
Expected: FAIL（現行は `"pc1"` / ```json``` を含むため `assertNotIn` が失敗）

- [ ] **Step 3: `_format_pca_plot_block` を実装**

`tool_helpers.py:144-172` の関数本体を以下へ置換する（署名は不変）:

```python
def _format_pca_plot_block(
    pca_result: dict,
    sample_names: list[str],
    title: str,
    intro: str,
    groups: dict[str, str | None] | None = None,
) -> str:
    """PCAスコアの要約ヘッダ＋群別サンプル数＋図示noteを返す（散布図点列は非同梱）。

    散布図の全点列は session_state.session.last_pca_plot に別途保存され
    （_remember_arf_pca_plot）、save_pca_figure が図示する。ここに点列を埋め込むと
    8000字切り詰めで末尾の loadings が消えるため、要約のみを返す（差次スリムと同型）。
    """
    groups = groups or {}
    evr = pca_result["explained_variance_ratio"]
    lines = [
        intro.rstrip("\n"),
        f"- {title}",
        f"- PC1 ({evr[0] * 100:.2f}%) × PC2 ({evr[1] * 100:.2f}%)",
    ]
    labeled = [groups[n] for n in sample_names if groups.get(n) is not None]
    if labeled:
        from collections import Counter
        counts = ", ".join(f"{g}={c}" for g, c in sorted(Counter(labeled).items()))
        lines.append(f"- 群別サンプル数: {counts}")
    else:
        lines.append(f"- サンプル数: {len(sample_names)}")
    lines.append("- 散布図の点列は本要約に非同梱。save_pca_figure で図示できます。")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: テストを走らせ通過を確認**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_report_tools.py -k format_pca_plot_block -v`
Expected: PASS（2件）

- [ ] **Step 5: `docs/output_format.md` §8.1 を同期**

`docs/output_format.md:491` の一文中「スコアプロット用JSON、Loading上位を含む」を「PCAスコア要約（群別サンプル数・図示note、点列は非同梱）、Loading上位を含む」に変更し、`docs/output_format.md:493-503` の「スコアプロット用JSON:」表ブロック全体を以下へ置換する:

```markdown
PCAスコア要約（散布図の点列は非同梱＝`save_pca_figure` で図示。全点列は
`session.last_pca_plot` に保持され図ツールが参照する）:

| キー/行 | 意味 |
|---|---|
| タイトル行 | 図タイトル |
| `PC1 (x%) × PC2 (y%)` | PC1/PC2 説明分散率 |
| 群別サンプル数 | 群ラベルがあれば `群=件数` を列挙、無ければ総サンプル数 |
| 図示note | `save_pca_figure` で散布図を生成する旨 |
```

- [ ] **Step 6: PCA 経路の回帰確認（図データが session に残ること）**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_report_tools.py -v`
Expected: PASS（既存 `SavePcaFigureTests` 含め全て緑。`save_pca_figure` は `session.last_pca_plot` から読むため無影響）

- [ ] **Step 7: コミット**

```bash
git add tool_helpers.py tests/test_report_tools.py docs/output_format.md
git commit -m "$(cat <<'EOF'
feat(payload): PCA payload から散布図点列を外し要約＋loadings 保全

_format_pca_plot_block が 60サンプル分の散布図JSON(session重複)を埋め込み、
8000字truncateで末尾の loadings(解釈の金脈)が消えていた。点列を外しヘッダ＋
群別サンプル数＋save_pca_figure note の要約へ。図データは last_pca_plot に保持。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 文献 payload の抄録上限化

**Files:**
- Modify: `tools_objective.py:157-188`（`paper_search`）
- Test: `tests/test_objective_tools.py`（新規テストクラス追加、先頭 import に `from unittest import mock` と `import paper_ingest` を追加）

**Interfaces:**
- Consumes: なし。
- Produces: `paper_search(query, max_results=10) -> str` — 署名不変。各候補の `- abstract:` 行は **500字上限**（超過時 `…（截断）` 付与）。全候補の `## {title}` 見出し・citation・注記は不変。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_objective_tools.py` の先頭 import 群に追加:

```python
from unittest import mock
import paper_ingest
```

ファイル末尾に新規クラスを追加:

```python
class PaperSearchSlimTests(unittest.TestCase):
    def test_paper_search_caps_abstract_and_keeps_titles(self):
        fake = [
            {"title": "T1", "abstract": "L" * 1200, "journal": "J",
             "year": 2024, "doi": "d1", "pmid": "1"},
            {"title": "T2", "abstract": "short abstract", "journal": "J",
             "year": 2024, "doi": "d2", "pmid": "2"},
        ]
        with mock.patch.object(paper_ingest, "search_europepmc", lambda q, n: fake), \
             mock.patch.object(paper_ingest, "check_retraction", lambda c: c), \
             mock.patch.object(paper_ingest, "deduplicate", lambda c, e: c):
            out = server.paper_search("q", max_results=5)
        # 全候補の title は残る（breadth 保持）
        self.assertIn("## T1", out)
        self.assertIn("## T2", out)
        # 長い抄録は上限＋截断マーカー、短い抄録はそのまま
        self.assertIn("…（截断）", out)
        self.assertIn("short abstract", out)
        for line in out.splitlines():
            if line.startswith("- abstract:"):
                self.assertLessEqual(
                    len(line), len("- abstract: ") + 500 + len("…（截断）"))
```

- [ ] **Step 2: テストを走らせ失敗を確認**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_objective_tools.py -k PaperSearchSlim -v`
Expected: FAIL（現行は全文1200字を出力し `…（截断）` が無く、abstract 行長も超過）

- [ ] **Step 3: `paper_search` を実装**

`tools_objective.py` のモジュール定数（`paper_search` 定義の直前）に追加:

```python
PAPER_ABSTRACT_CAP = 500  # payload 内の抄録上限。全文は ingest_stage 時に再提示される。
```

`tools_objective.py:186` の1行:

```python
        out.append(f"- abstract: {cand['abstract']}")
```

を以下へ置換:

```python
        abstract = cand.get("abstract") or ""
        if len(abstract) > PAPER_ABSTRACT_CAP:
            abstract = abstract[:PAPER_ABSTRACT_CAP] + "…（截断）"
        out.append(f"- abstract: {abstract}")
```

- [ ] **Step 4: テストを走らせ通過を確認**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_objective_tools.py -k PaperSearchSlim -v`
Expected: PASS

- [ ] **Step 5: 回帰（objective テスト全体）**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_objective_tools.py -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add tools_objective.py tests/test_objective_tools.py
git commit -m "$(cat <<'EOF'
feat(payload): paper_search の抄録を500字上限化（全候補titleは保持）

10件×全文抄録で 14k字となり 8000字truncateで後半候補が埋没していた。
抄録を500字＋截断マーカーに上限化し、全候補の title＋citation を残す。
全文は ingest_stage 時にモデルが本文から再提示する運用は不変。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 実データ end-to-end 検証（全高価値ケース ≤ 8000字）＋全スイート

**Files:**
- Create: `tools/measure_payloads.py`（再利用可能な payload 監査ハーネス）
- 依存（実行時のみ）: `interp_eval_cases.py`（既存）、実データ `C:\Users\yuu18\datasets\2_lipidome_lcms\NEG` / `POS`。

**Interfaces:**
- Consumes: `agent_core.execute_tool` / `agent_core._truncate` / `interp_eval_cases.CASES`（既存）。
- Produces: `python tools/measure_payloads.py` が高価値10ケースの実 payload 長と truncate 後長・BURIED 判定を出力する監査スクリプト。

- [ ] **Step 1: 監査ハーネスを作成**

`tools/measure_payloads.py` を作成:

```python
"""高価値ツールの最終 payload 実サイズを測り、8000字 truncate に埋没するか判定する。

実行: PYTHONPATH=<proj> .venv-1/Scripts/python.exe tools/measure_payloads.py
実データ（interp_eval_cases の DIR_NEG / DIR_POS）が必要。
"""
import sys
from agent_core import execute_tool, _truncate
from interp_eval_cases import CASES

LIMIT = 8000


def main() -> int:
    buried = []
    for case in CASES:
        last_out = None
        for step in case.pipeline:
            last_out = execute_tool(step.name, step.args)
        n = len(last_out)
        after = len(_truncate(last_out))
        flag = "BURIED" if n > LIMIT else "ok"
        if n > LIMIT:
            buried.append(case.id)
        print(f"{case.id:20s} phase={case.phase_label:12s} "
              f"chars={n:7d} after_trunc={after:6d} {flag}")
    if buried:
        print(f"\nFAIL: {len(buried)} 件が 8000字を超過: {buried}")
        return 1
    print("\nOK: 全高価値ケースが 8000字以内（BURIED なし）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 監査ハーネスを走らせ全ケース ≤ 8000字を確認**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe tools/measure_payloads.py`
Expected: 全10ケースが `ok`、末尾に `OK: 全高価値ケースが 8000字以内（BURIED なし）`、終了コード 0。特に `pca_neg`（従来14,360→<8000）と `literature_pos`（従来14,024→<8000）が `ok` になること。

- [ ] **Step 3: 全テストスイートを走らせ回帰なしを確認**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/ -q`
Expected: 全件 PASS（従来 293 前後 ＋ 本プランの新規4件）。

- [ ] **Step 4: コミット**

```bash
git add tools/measure_payloads.py
git commit -m "$(cat <<'EOF'
test(payload): 高価値ツール payload の8000字監査ハーネスを追加

execute_tool の実 payload 長と _truncate 後長・BURIED を実データで測る監査
スクリプト。PCA/文献スリム化後、全10ケースが 8000字以内であることを確認。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## 検証（実装後、任意）

eval harness で `pca_neg` と `literature_neg` / `literature_pos` を freeze→generate→再採点し、loadings/全候補が可視化されて解釈が改善するか（特に PCA で「どの脂質が群分離を駆動するか」に言及できるか）を実測する。これは後続 A（クラウド解釈切替）で「クラウドがさらに多くの loadings/抄録を欲するか＝tiering の要否」を判断する土台になる。手順は `interp_eval` の freeze/generate/judge フロー（`docs/superpowers/notes/2026-07-07-interpretation-quality-findings.md` 参照）。

## Self-Review（プラン作成者による確認済み）

- **Spec coverage**: 変更1(PCA)=Task 1、変更2(文献)=Task 2、backstop 不変=Global Constraints、TDD テスト=各 Task Step 1、実測検証(`measure_payloads.py`)=Task 3、付随更新(`output_format.md`)=Task 1 Step 5。差次/QC/identity 非変更・tiered payload 非目標=Global Constraints/本プランに含めず。全カバー。
- **Placeholder scan**: 実コード・実コマンド・期待出力のみ。プレースホルダなし。
- **Type consistency**: `_format_pca_plot_block` 署名は全 Task で不変。`PAPER_ABSTRACT_CAP=500` と截断マーカー `…（截断）` は Task 2 内で一貫。`execute_tool`/`_truncate`/`CASES` は既存 API。
