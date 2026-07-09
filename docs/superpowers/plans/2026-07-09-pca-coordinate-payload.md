# ARF PCA 座標を LLM に渡す Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ARF 系 PCA ツールの戻り値に per-sample 座標 JSON を同梱し、LLM が Claude Desktop 上で群色分け散布図を自描画できるようにする（PNG は従来どおり `save_pca_figure` によるユーザー要求時のみ）。

**Architecture:** 座標同梱は `tool_helpers._format_pca_plot_block` 一箇所に集約する。同ヘルパを呼ぶ 3 ツール（`arf_parser` / `arf_re_pca` / `arf_pca_preprocessed`）は自動的に座標同梱となる。加えて各ツールの連結順を `要約 → loadings → 座標JSON` に入れ替え、座標ブロックを payload 末尾へ置く（万一の下流截断で解釈の金脈=loadings ではなく散布図の点を失うようにする）。pai2 経路は一切変更しない。

**Tech Stack:** Python 3, unittest, 標準 `json`。既存の `arf_reader.run_pca`（`pca_result` に `components` と `explained_variance_ratio` を含む dict を返す）。

## Global Constraints

- pai2 経路（`tools_pai2.py` / `pai2_reader.perform_pca_summary` / `session_state.run_pca` / `save_pca_figure`）は変更しない。
- `_format_pca_plot_block` の署名（引数名・順序）は不変。
- 座標 JSON のキー: 点は `pc1`, `pc2`, `sample`, （群があれば）`group`。ラッパは `x_label`, `y_label`, `points`。
- 実運用 MCP パスに戻り値 truncate は無い（`server.py`/`mcp_core.py` で確認済み）。8000字 `_truncate` は archives のローカルLLM専用で本作業と無関係。
- 対応する設計: `docs/superpowers/specs/2026-07-09-pca-coordinate-payload-design.md`。

---

### Task 1: `_format_pca_plot_block` に座標 JSON を同梱する

**Files:**
- Modify: `tool_helpers.py`（先頭に `import json` を追加、関数 `_format_pca_plot_block`（現 143-171 行）を差し替え）
- Test: `tests/test_report_tools.py`（既存 2 テスト `test_format_pca_plot_block_omits_points_keeps_summary`（153-172 行）と `test_format_pca_plot_block_no_groups_shows_total`（174-184 行）を差し替え）

**Interfaces:**
- Consumes: `pca_result: dict`（`components: list[list[float]]`, `explained_variance_ratio: list[float]`）, `sample_names: list[str]`, `title: str`, `intro: str`, `groups: dict[str, str|None] | None`。
- Produces: `_format_pca_plot_block(...) -> str`。戻り値は intro＋要約行＋```json フェンス（`{"x_label","y_label","points":[{"pc1","pc2","sample"[,"group"]}...]}`）を含む Markdown 文字列。`_remember_arf_pca_plot` / `save_pca_figure` は不変。

- [ ] **Step 1: 既存 2 テストを新挙動に書き換える（失敗させる）**

`tests/test_report_tools.py` の `test_format_pca_plot_block_omits_points_keeps_summary`（153-172 行）を次で置換:

```python
    def test_format_pca_plot_block_includes_points_and_summary(self):
        import json as _json
        import tool_helpers
        block = tool_helpers._format_pca_plot_block(
            {"components": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]],
             "explained_variance_ratio": [0.24, 0.15]},
            ["s1", "s2", "s3", "s4"],
            title="PCA Score Plot (x.arf)",
            intro="\n#### 📊 PCA スコア要約\n",
            groups={"s1": "A", "s2": "A", "s3": "B", "s4": "B"},
        )
        # 座標点列が JSON フェンスで同梱される
        self.assertIn("```json", block)
        self.assertIn('"sample": "s1"', block)
        self.assertIn('"pc1"', block)
        self.assertIn('"pc2"', block)
        self.assertIn('"group": "A"', block)
        # 結論・群別サンプル数・図示 note も残る
        self.assertIn("24.00%", block)
        self.assertIn("15.00%", block)
        self.assertIn("A=2", block)
        self.assertIn("B=2", block)
        self.assertIn("save_pca_figure", block)
        # JSON は valid で points が 4 件、座標が一致
        payload = _json.loads(block.split("```json")[1].split("```")[0].strip())
        self.assertEqual(len(payload["points"]), 4)
        self.assertEqual(payload["points"][0]["pc1"], 1.0)
        self.assertEqual(payload["points"][0]["pc2"], 2.0)
```

同ファイルの `test_format_pca_plot_block_no_groups_shows_total`（174-184 行）を次で置換:

```python
    def test_format_pca_plot_block_no_groups_includes_points_without_group(self):
        import tool_helpers
        block = tool_helpers._format_pca_plot_block(
            {"components": [[1.0, 2.0], [3.0, 4.0]],
             "explained_variance_ratio": [0.5, 0.3]},
            ["s1", "s2"],
            title="T",
            intro="\n#### PCA\n",
        )
        self.assertIn("サンプル数: 2", block)
        self.assertIn('"pc1"', block)
        self.assertNotIn('"group"', block)
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/test_report_tools.py -k format_pca_plot_block -v`
Expected: FAIL（現行実装は座標を非同梱のため ```json / "pc1" が無く AssertionError）。

- [ ] **Step 3: `tool_helpers.py` 先頭に `import json` を追加**

`tool_helpers.py` の `from pathlib import Path`（8 行目）の直後に追加:

```python
import json
```

- [ ] **Step 4: `_format_pca_plot_block` を差し替える**

`tool_helpers.py` の現 `_format_pca_plot_block`（143-171 行、docstring・本体すべて）を次で置換:

```python
def _format_pca_plot_block(
    pca_result: dict,
    sample_names: list[str],
    title: str,
    intro: str,
    groups: dict[str, str | None] | None = None,
) -> str:
    """PCAスコアの要約ヘッダ＋群別サンプル数＋座標点列(JSON)を返す。

    LLM がこの座標から散布図を自描画できるよう per-sample の点列を ```json
    フェンスで同梱する。点列は session_state.session.last_pca_plot にも別途保存
    され（_remember_arf_pca_plot）、save_pca_figure がユーザー要求時の PNG 化に
    使う。呼び出し側はこのブロックを loadings の後（payload 末尾）に置くこと
    （万一の下流截断で loadings ではなく座標点を失うようにするため）。
    """
    groups = groups or {}
    evr = pca_result["explained_variance_ratio"]
    coords = pca_result.get("components", [])
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

    points = []
    for i, name in enumerate(sample_names):
        if i < len(coords) and len(coords[i]) >= 2:
            point = {
                "pc1": round(float(coords[i][0]), 4),
                "pc2": round(float(coords[i][1]), 4),
                "sample": name,
            }
            if groups.get(name) is not None:
                point["group"] = groups[name]
            points.append(point)
    plot_data = {
        "x_label": f"PC1 ({evr[0] * 100:.2f}%)",
        "y_label": f"PC2 ({evr[1] * 100:.2f}%)",
        "points": points,
    }
    lines.append(
        "- 上記座標から散布図を描画してください（group があれば群ごとに色分け・凡例付き）。"
        "PNG が必要なときのみ save_pca_figure を実行します。"
    )
    lines.append("```json")
    lines.append(json.dumps(plot_data, ensure_ascii=False))
    lines.append("```")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: テストを実行して成功を確認**

Run: `python -m pytest tests/test_report_tools.py -k format_pca_plot_block -v`
Expected: PASS（2 テスト）。

- [ ] **Step 6: コミット**

```bash
git add tool_helpers.py tests/test_report_tools.py
git commit -m "feat(pca): _format_pca_plot_block に座標JSONを同梱しLLM自描画を復活"
```

---

### Task 2: 3 ツールで座標ブロックを loadings の後（末尾）へ置く

**Files:**
- Modify: `tools_arf.py`（3 箇所の連結順を入れ替え: `arf_pca_preprocessed` 204 行 / `arf_parser` 347-348 行 / `arf_re_pca` 498-499 行）
- Test: `tests/test_server_class_filter.py`（順序検証テストを 2 件追加）

**Interfaces:**
- Consumes: Task 1 の `_format_pca_plot_block`（座標 JSON を含む文字列を返す）。`_format_pca_loadings_md`（loadings 節、ヘッダに「Loadings 寄与度分析」を含む）。
- Produces: 各 ARF ツール戻り値 `result[0]` は `要約 → loadings（"Loadings 寄与度分析"）→ 座標JSON（"```json"）` の順を持つ。

- [ ] **Step 1: 順序検証テストを追加（失敗させる）**

`tests/test_server_class_filter.py` の `test_arf_parser_group_levels_collapse`（128-135 行）の直後（135 行の後、136 行の空行の前）に追加:

```python
    def test_arf_parser_orders_loadings_before_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            arf_path = Path(tmp) / "test.arf"
            arf_path.touch()
            result = server.arf_parser(str(arf_path))
        text = result[0]
        self.assertIn("```json", text)
        self.assertIn('"pc1"', text)
        # loadings 節は座標 JSON より前（末尾截断で loadings を守る）
        self.assertLess(text.index("Loadings 寄与度分析"), text.index("```json"))

    def test_arf_re_pca_orders_loadings_before_coordinates(self):
        self.session.current_file_path = "test.arf"
        with patch.object(path_resolvers, "_filter_arf_spots", return_value=self.session.features):
            result = server.arf_re_pca()
        text = result[0]
        self.assertIn("```json", text)
        self.assertLess(text.index("Loadings 寄与度分析"), text.index("```json"))
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/test_server_class_filter.py -k orders_loadings_before_coordinates -v`
Expected: FAIL（現状は座標 JSON が loadings の前にあるため `assertLess` が False）。

- [ ] **Step 3: `arf_pca_preprocessed` の連結順を入れ替え**

`tools_arf.py` 204 行を置換:

```python
        f"{plot_block}{loadings_block}"
```

→

```python
        f"{loadings_block}{plot_block}"
```

- [ ] **Step 4: `arf_parser` の連結順を入れ替え**

`tools_arf.py` 347-348 行:

```python
            f"{plot_instruction_text}"  # ← ここにプロット用の指示とデータを追加
            f"{loadings_summary_text}"
```

→

```python
            f"{loadings_summary_text}"
            f"{plot_instruction_text}"  # ← 座標ブロックは末尾（loadings の後）へ
```

- [ ] **Step 5: `arf_re_pca` の連結順を入れ替え**

`tools_arf.py` 498-499 行:

```python
            f"{plot_instruction_text}"
            f"{loadings_summary_text}"
```

→

```python
            f"{loadings_summary_text}"
            f"{plot_instruction_text}"
```

- [ ] **Step 6: 追加テスト＋既存 ARF スイートを実行して成功を確認**

Run: `python -m pytest tests/test_server_class_filter.py tests/test_arf_multiblock.py -v`
Expected: PASS（新規 2 件を含め全件緑。既存の群ラベル assertion `control=1` 等は要約行に残るため不変）。

- [ ] **Step 7: コミット**

```bash
git add tools_arf.py tests/test_server_class_filter.py
git commit -m "feat(pca): ARF出力で座標JSONを loadings の後（末尾）へ配置"
```

---

### Task 3: 回帰確認（全スイート）

**Files:** なし（実行のみ）

- [ ] **Step 1: 全テストスイートを実行**

Run: `python -m pytest -q`
Expected: PASS（全件）。pai2 系テストは未変更につき不変。もし失敗があれば座標同梱で文字列 assertion が壊れた箇所を特定し、該当テストの期待値を新挙動へ更新してから再実行する。

- [ ] **Step 2: （回帰修正が発生した場合のみ）コミット**

```bash
git add -A
git commit -m "test(pca): 座標同梱に伴う回帰テスト期待値の更新"
```
