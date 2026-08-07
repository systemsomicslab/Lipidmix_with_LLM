# 全プロットのクライアント描画統一と PNG 明示要求化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** volcano に構造化ペイロード経路を新設し、EIC オーバーレイを webUI で描画できるようにして、PNG 生成をユーザー明示要求時のみに限定する。

**Architecture:** MCP サーバ（`C:\Users\yuu18\Lipidmix_with_LLM`）は描画中立な JSON を返すだけにし、webUI クライアント（`C:\Users\yuu18\Use-LLLM`）が Plotly で描画する。既存の `eic_plot.py` ＋ `eic-plot.js` の対を volcano にも複製する形（`volcano_plot.py` ＋ `volcano-plot.js`）。PNG 生成は既存 `save_*_figure` に隔離したまま、docstring と案内文で「明示要求時のみ」を徹底する。

**Tech Stack:** Python 3.11 / FastMCP 3.3.0 / unittest（サーバ側）、素の JavaScript UMD / Plotly / node:assert（クライアント側）

## Global Constraints

- 設計書: `docs/superpowers/specs/2026-08-07-client-side-plot-rendering-design.md`
- volcano スキーマ名は `lipidmix.volcano.v1`、`plot_type` は `"scatter"`（厳密一致）
- EIC スキーマ名は既存の `lipidmix.eic.v1` と `lipidmix.eic.multi.v1`（変更しない）
- `up`/`down` の点は絶対に間引かない。間引くのは `ns` のみ
- 間引きは決定的（乱数を使わない）。同一入力なら常に同一結果
- 新規ツールは read-only。ファイルを一切書かない
- volcano の配色は `save_volcano_figure` の matplotlib 配色と一致させる: `up`=`#c0392b`、`down`=`#2471a3`、`ns`=`#95a5a6`
- サーバ側テスト実行: `cd C:\Users\yuu18\Lipidmix_with_LLM; python -m unittest <module> -v`
- クライアント側テスト実行: `cd C:\Users\yuu18\Use-LLLM; node tests/<name>.cjs`（pytest には未接続。README.md:94-95 の流儀）
- Lipidmix_with_LLM の作業ブランチ: `feat/client-side-plot-rendering`（既に作成済み）
- Use-LLLM には**既に未コミットの変更が存在する**（`app.js` / `api.py` / `endpoints.py` 他）。それらには一切触らず、自分が作成・変更したファイルだけを `git add` すること

---

## File Structure

サーバ側（Lipidmix_with_LLM）

| ファイル | 責務 |
| --- | --- |
| `volcano_plot.py`（新規） | `lipidmix.volcano.v1` ペイロードの組み立てと `ns` 間引き。MCP 非依存の純ロジック。`eic_plot.py` と同じ位置づけ |
| `tools_arf.py`（変更） | `arf_plot_volcano` ツール追加、`last_differential` にしきい値と群サイズを保存、`volcano_note` の案内先変更 |
| `tools_reports.py`（変更） | `save_pca_figure` / `save_volcano_figure` の docstring を「明示要求時のみ」に統一 |
| `arf_reader.py`（変更） | CLI の無条件 PNG 生成を除去 |
| `tests/test_volcano_plot.py`（新規） | `volcano_plot` の純ロジックと `arf_plot_volcano` のテスト |
| `tests/test_server_registration.py`（変更） | 期待ツール一覧に `arf_plot_volcano` を追加 |
| `tests/test_differential_tools.py`（変更） | `volcano_note` と `last_differential` の新キーを検証 |

クライアント側（Use-LLLM）

| ファイル | 責務 |
| --- | --- |
| `src/use_lllm/general/static/volcano-plot.js`（新規） | `lipidmix.volcano.v1` の探索・Plotly トレース化・しきい値破線 |
| `src/use_lllm/general/static/eic-plot.js`（変更） | `lipidmix.eic.multi.v1` の受理と multi 用 hover・注釈 |
| `src/use_lllm/general/static/app.js`（変更） | `appendVolcanoPlot` 追加、`appendEicPlot` に注釈と凡例切替を追加 |
| `src/use_lllm/general/static/index.html`（変更） | `volcano-plot.js` の script タグ追加 |
| `src/use_lllm/core/policy.py`（変更） | ツール名を実サーバへ同期 |
| `src/use_lllm/core/mcp_state_policy.py`（変更） | state 規則をツール名変更と volcano に追随 |
| `tests/test_general_volcano_plot.cjs`（新規） | volcano ヘルパのテスト |
| `tests/test_general_eic_plot.cjs`（変更） | multi スキーマのテスト追加 |
| `tests/test_policy_server.py` / `tests/test_mcp_state_policy.py`（変更） | 同期後のツール名で分類とルールを検証 |

---

## Task 1: volcano_plot.py — 構造化ペイロードの純ロジック

**Files:**
- Create: `C:\Users\yuu18\Lipidmix_with_LLM\volcano_plot.py`
- Test: `C:\Users\yuu18\Lipidmix_with_LLM\tests\test_volcano_plot.py`

**Interfaces:**
- Consumes: `session.last_differential` 相当の dict（キー: `kind` / `a` / `b` / `n_a` / `n_b` / `q_threshold` / `log2fc_threshold` / `volcano`）。`volcano` の各要素は `differential.volcano_data` の出力形＝`{"feature": str, "log2fc": float|None, "neg_log10_p": float, "sig": "up"|"down"|"ns"}`
- Produces: `VOLCANO_PLOT_SCHEMA: str`、`VolcanoPlotPayload`（TypedDict）、`build_volcano_plot_payload(last_differential, *, max_points=3000, title=None) -> VolcanoPlotPayload`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_volcano_plot.py` を新規作成:

```python
"""volcano プロットの構造化ペイロード（lipidmix.volcano.v1）のテスト。

間引きは決定的で、up/down は絶対に切らないことを固定する。ns の削減件数が
selection と caveats の両方に出ることも検証する（「間引かれた点＝有意でない」と
誤読されないための表示根拠）。
"""
import math
import unittest

import volcano_plot


def _point(feature, log2fc, neg_log10_p, sig):
    return {"feature": feature, "log2fc": log2fc,
            "neg_log10_p": neg_log10_p, "sig": sig}


def _differential(volcano, **overrides):
    base = {"kind": "two_group", "a": "24M", "b": "9w", "n_a": 6, "n_b": 5,
            "q_threshold": 0.05, "log2fc_threshold": 1.0, "volcano": volcano}
    base.update(overrides)
    return base


class TestBuildVolcanoPlotPayload(unittest.TestCase):
    def test_payload_shape_and_metadata(self):
        payload = volcano_plot.build_volcano_plot_payload(_differential([
            _point("PC 34:1", 1.8, 3.4, "up"),
            _point("PE 36:2", -2.1, 4.0, "down"),
            _point("TG 52:3", 0.2, 0.5, "ns"),
        ]))
        self.assertEqual(payload["plot_schema"], "lipidmix.volcano.v1")
        self.assertEqual(payload["plot_type"], "scatter")
        self.assertEqual(payload["title"], "Volcano (24M vs 9w)")
        self.assertEqual(payload["comparison"],
                         {"group_a": "24M", "group_b": "9w", "n_a": 6, "n_b": 5})
        self.assertEqual(payload["axes"]["x"]["label"], "log2 fold change")
        self.assertEqual(payload["axes"]["y"]["label"], "-log10 p")
        self.assertEqual(payload["thresholds"], {"q": 0.05, "log2fc": 1.0})
        self.assertEqual(payload["render_hints"]["mode"], "markers")
        self.assertEqual(payload["render_hints"]["color_by"], "sig")
        self.assertEqual(payload["render_hints"]["guides"]["x"], [-1.0, 1.0])
        self.assertAlmostEqual(payload["render_hints"]["guides"]["y"][0], 1.3010, places=3)
        self.assertEqual(len(payload["points"]), 3)
        self.assertEqual(payload["selection"]["total"], 3)
        self.assertEqual(payload["selection"]["plotted"], 3)

    def test_custom_title_overrides_default(self):
        payload = volcano_plot.build_volcano_plot_payload(
            _differential([_point("PC 34:1", 1.8, 3.4, "up")]), title="肝 24M vs 9w",
        )
        self.assertEqual(payload["title"], "肝 24M vs 9w")

    def test_significant_points_are_never_thinned(self):
        volcano = [_point(f"sig-{i}", 2.0, 5.0, "up") for i in range(40)]
        volcano += [_point(f"sig-d-{i}", -2.0, 5.0, "down") for i in range(40)]
        volcano += [_point(f"ns-{i}", 0.1, 0.2, "ns") for i in range(500)]
        payload = volcano_plot.build_volcano_plot_payload(
            _differential(volcano), max_points=100,
        )
        kept = [p for p in payload["points"] if p["sig"] in ("up", "down")]
        self.assertEqual(len(kept), 80)
        self.assertEqual(payload["selection"]["significant_total"], 80)
        self.assertEqual(payload["selection"]["significant_plotted"], 80)
        self.assertEqual(payload["selection"]["ns_plotted"], 20)
        self.assertEqual(payload["selection"]["plotted"], 100)

    def test_ns_subsampling_is_deterministic(self):
        volcano = [_point(f"ns-{i}", 0.1, 0.2, "ns") for i in range(1000)]
        first = volcano_plot.build_volcano_plot_payload(
            _differential(volcano), max_points=50)
        second = volcano_plot.build_volcano_plot_payload(
            _differential(volcano), max_points=50)
        self.assertEqual([p["feature"] for p in first["points"]],
                         [p["feature"] for p in second["points"]])
        self.assertEqual(len(first["points"]), 50)

    def test_ns_thinning_is_reported_in_caveats(self):
        volcano = [_point(f"ns-{i}", 0.1, 0.2, "ns") for i in range(300)]
        payload = volcano_plot.build_volcano_plot_payload(
            _differential(volcano), max_points=50)
        self.assertTrue(any("間引" in note for note in payload["caveats"]))

    def test_nonfinite_points_are_dropped_and_explained(self):
        payload = volcano_plot.build_volcano_plot_payload(_differential([
            _point("ok", 1.8, 3.4, "up"),
            _point("no-fc", None, 3.4, "ns"),
            _point("nan-fc", math.nan, 3.4, "ns"),
            _point("nan-p", 1.0, math.nan, "ns"),
        ]))
        self.assertEqual([p["feature"] for p in payload["points"]], ["ok"])
        self.assertEqual(payload["selection"]["dropped_nonfinite"], 3)
        self.assertEqual(payload["selection"]["total"], 4)
        self.assertTrue(any("有限値でない" in note for note in payload["caveats"]))

    def test_significant_overflow_drops_all_ns_but_keeps_significant(self):
        volcano = [_point(f"sig-{i}", 2.0, 5.0, "up") for i in range(120)]
        volcano += [_point(f"ns-{i}", 0.1, 0.2, "ns") for i in range(30)]
        payload = volcano_plot.build_volcano_plot_payload(
            _differential(volcano), max_points=100)
        self.assertEqual(payload["selection"]["significant_plotted"], 120)
        self.assertEqual(payload["selection"]["ns_plotted"], 0)
        self.assertEqual(payload["selection"]["ns_total"], 30)
        self.assertEqual(payload["selection"]["plotted"], 120)
        self.assertTrue(any("全て省略" in note for note in payload["caveats"]))

    def test_q_and_p_axis_mismatch_is_always_caveated(self):
        payload = volcano_plot.build_volcano_plot_payload(
            _differential([_point("PC 34:1", 1.8, 3.4, "up")]))
        self.assertTrue(any("-log10(q" in note for note in payload["caveats"]))

    def test_missing_thresholds_fall_back_to_defaults(self):
        last = {"kind": "two_group", "a": "A", "b": "B",
                "volcano": [_point("PC 34:1", 1.8, 3.4, "up")]}
        payload = volcano_plot.build_volcano_plot_payload(last)
        self.assertEqual(payload["thresholds"], {"q": 0.05, "log2fc": 1.0})
        self.assertIsNone(payload["comparison"]["n_a"])

    def test_rejects_invalid_max_points(self):
        last = _differential([_point("PC 34:1", 1.8, 3.4, "up")])
        for bad in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                volcano_plot.build_volcano_plot_payload(last, max_points=bad)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; python -m unittest tests.test_volcano_plot -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'volcano_plot'`

- [ ] **Step 3: volcano_plot.py を実装**

`volcano_plot.py` を新規作成:

```python
"""Client-neutral volcano plot payload built from a two-group differential result.

`eic_plot.py` と同じ役割で、MCP にも matplotlib にも依存しない。`differential.py`
が作った点列を、クライアントがそのまま描ける形へ整形するだけの純ロジック層。

点数は特徴量数ぶん（数千〜数万件）になりうるため `ns` 点だけを決定的に間引く。
`up`/`down` を切らないのは、有意点を落とすと図の意味が壊れるため。
"""
from __future__ import annotations

import math
from typing import TypedDict

VOLCANO_PLOT_SCHEMA = "lipidmix.volcano.v1"

DEFAULT_Q_THRESHOLD = 0.05
DEFAULT_LOG2FC_THRESHOLD = 1.0
_SIGNIFICANT = ("up", "down")


class PlotAxis(TypedDict):
    label: str
    unit: str | None
    scale: str


class PlotAxes(TypedDict):
    x: PlotAxis
    y: PlotAxis


class VolcanoComparison(TypedDict):
    group_a: str
    group_b: str
    n_a: int | None
    n_b: int | None


class VolcanoThresholds(TypedDict):
    q: float
    log2fc: float


class VolcanoPoint(TypedDict):
    feature: str
    log2fc: float
    neg_log10_p: float
    sig: str


class VolcanoSelection(TypedDict):
    total: int
    plotted: int
    significant_total: int
    significant_plotted: int
    ns_total: int
    ns_plotted: int
    max_points: int
    dropped_nonfinite: int


class VolcanoGuides(TypedDict):
    x: list[float]
    y: list[float]


class VolcanoRenderHints(TypedDict):
    mode: str
    color_by: str
    show_legend: bool
    guides: VolcanoGuides
    hover_fields: list[str]


class VolcanoPlotPayload(TypedDict):
    plot_schema: str
    plot_type: str
    title: str
    comparison: VolcanoComparison
    axes: PlotAxes
    thresholds: VolcanoThresholds
    points: list[VolcanoPoint]
    selection: VolcanoSelection
    render_hints: VolcanoRenderHints
    caveats: list[str]


def _is_drawable(point: dict) -> bool:
    """log2fc と -log10 p の両方が有限なら描画できる。"""
    values = (point.get("log2fc"), point.get("neg_log10_p"))
    return all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in values
    )


def _subsample(points: list[dict], quota: int) -> list[dict]:
    """先頭から等間隔に最大 `quota` 件を抽出する（決定的・乱数なし）。

    `(index * total) // quota` は `total >= quota` のとき厳密に単調増加するため、
    重複なくちょうど `quota` 件を返す。x/y の分布形を保ったまま点数だけ落とす。
    """
    if quota <= 0 or not points:
        return []
    total = len(points)
    if total <= quota:
        return list(points)
    return [points[(index * total) // quota] for index in range(quota)]


def build_volcano_plot_payload(
    last_differential: dict,
    *,
    max_points: int = 3000,
    title: str | None = None,
) -> VolcanoPlotPayload:
    """`session.last_differential` から描画中立な volcano ペイロードを組み立てる。"""
    if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points < 1:
        raise ValueError("max_points must be a positive integer")

    raw = list(last_differential.get("volcano") or [])
    q_threshold = float(last_differential.get("q_threshold") or DEFAULT_Q_THRESHOLD)
    log2fc_threshold = float(
        last_differential.get("log2fc_threshold") or DEFAULT_LOG2FC_THRESHOLD
    )
    group_a = str(last_differential.get("a") or "")
    group_b = str(last_differential.get("b") or "")

    caveats: list[str] = []
    drawable = [point for point in raw if _is_drawable(point)]
    dropped_nonfinite = len(raw) - len(drawable)
    if dropped_nonfinite:
        caveats.append(
            f"log2fc または p が有限値でない {dropped_nonfinite} 件は描画対象から外しました"
            "（分散0・欠損・片群のみ検出などで検定不能。有意でないという意味ではありません）。"
        )

    significant = [p for p in drawable if p.get("sig") in _SIGNIFICANT]
    ns = [p for p in drawable if p.get("sig") not in _SIGNIFICANT]
    quota = max_points - len(significant)
    if quota <= 0:
        ns_plotted: list[dict] = []
        if ns:
            caveats.append(
                f"有意点 {len(significant)} 件だけで max_points={max_points} に達したため、"
                f"ns 点 {len(ns)} 件は全て省略しました（有意点は間引いていません）。"
            )
    else:
        ns_plotted = _subsample(ns, quota)
        if len(ns_plotted) < len(ns):
            caveats.append(
                f"ns 点は {len(ns)} 件から {len(ns_plotted)} 件へ等間隔で間引きました"
                "（up/down は全件保持）。件数の判断には selection を参照してください。"
            )

    caveats.append(
        "横破線は -log10(q しきい値) の目安であり、各点の y は -log10(p) です"
        "（q と p は別量なので破線位置と有意判定は厳密には一致しません）。"
    )

    points: list[VolcanoPoint] = [
        {
            "feature": str(point.get("feature") or ""),
            "log2fc": float(point["log2fc"]),
            "neg_log10_p": float(point["neg_log10_p"]),
            "sig": str(point.get("sig") or "ns"),
        }
        for point in significant + ns_plotted
    ]

    guide_y = -math.log10(q_threshold) if q_threshold > 0 else 0.0
    return {
        "plot_schema": VOLCANO_PLOT_SCHEMA,
        "plot_type": "scatter",
        "title": title or f"Volcano ({group_a} vs {group_b})",
        "comparison": {
            "group_a": group_a,
            "group_b": group_b,
            "n_a": last_differential.get("n_a"),
            "n_b": last_differential.get("n_b"),
        },
        "axes": {
            "x": {"label": "log2 fold change", "unit": None, "scale": "linear"},
            "y": {"label": "-log10 p", "unit": None, "scale": "linear"},
        },
        "thresholds": {"q": q_threshold, "log2fc": log2fc_threshold},
        "points": points,
        "selection": {
            "total": len(raw),
            "plotted": len(points),
            "significant_total": len(significant),
            "significant_plotted": len(significant),
            "ns_total": len(ns),
            "ns_plotted": len(ns_plotted),
            "max_points": max_points,
            "dropped_nonfinite": dropped_nonfinite,
        },
        "render_hints": {
            "mode": "markers",
            "color_by": "sig",
            "show_legend": True,
            "guides": {
                "x": [-log2fc_threshold, log2fc_threshold],
                "y": [round(guide_y, 4)],
            },
            "hover_fields": ["feature", "log2fc", "neg_log10_p", "sig"],
        },
        "caveats": caveats,
    }
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; python -m unittest tests.test_volcano_plot -v`
Expected: PASS（10 tests）

- [ ] **Step 5: コミット**

```bash
git add volcano_plot.py tests/test_volcano_plot.py
git commit -m "feat(volcano): 描画中立な lipidmix.volcano.v1 ペイロードを追加

up/down は全件保持し ns だけを等間隔で間引く。間引き件数と非有限値の
除外件数は selection と caveats の両方に出す（間引かれた点を「有意でない」
と誤読させないため）。"
```

---

## Task 2: arf_plot_volcano ツールと PNG の明示要求化

**Files:**
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\tools_arf.py`（import 部、`__all__`、`arf_differential` の `last_differential` 保存と `volcano_note`、末尾に新ツール）
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\tools_reports.py:113-116`, `148-156`（docstring のみ）
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\tests\test_server_registration.py`
- Test: `C:\Users\yuu18\Lipidmix_with_LLM\tests\test_volcano_plot.py`（Task 1 のファイルにクラス追加）

**Interfaces:**
- Consumes: Task 1 の `volcano_plot.build_volcano_plot_payload` と `volcano_plot.VolcanoPlotPayload`
- Produces: MCP ツール `arf_plot_volcano(max_points: int = 3000, title: str | None = None) -> VolcanoPlotPayload`。`session.last_differential` に新キー `n_a` / `n_b` / `q_threshold` / `log2fc_threshold` が入る

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_volcano_plot.py` の `if __name__` の直前に追記:

```python
class TestArfPlotVolcanoTool(unittest.TestCase):
    """ツール層: セッション状態からペイロードを作り、PNG は書かないこと。"""

    def setUp(self):
        import server
        import session_state
        self.server = server
        self.session_state = session_state
        self._saved = session_state.session
        session_state.session = server.AnalysisSession()

    def tearDown(self):
        self.session_state.session = self._saved

    def test_builds_payload_from_session_differential(self):
        self.session_state.session.last_differential = {
            "kind": "two_group", "a": "24M", "b": "9w", "n_a": 6, "n_b": 5,
            "q_threshold": 0.05, "log2fc_threshold": 1.0,
            "volcano": [_point("PC 34:1", 1.8, 3.4, "up"),
                        _point("TG 52:3", 0.2, 0.5, "ns")],
        }
        payload = self.server.arf_plot_volcano()
        self.assertEqual(payload["plot_schema"], "lipidmix.volcano.v1")
        self.assertEqual(payload["comparison"]["group_a"], "24M")
        self.assertEqual(len(payload["points"]), 2)

    def test_max_points_and_title_are_forwarded(self):
        self.session_state.session.last_differential = {
            "kind": "two_group", "a": "A", "b": "B",
            "volcano": [_point(f"ns-{i}", 0.1, 0.2, "ns") for i in range(100)],
        }
        payload = self.server.arf_plot_volcano(max_points=10, title="custom")
        self.assertEqual(payload["title"], "custom")
        self.assertEqual(payload["selection"]["plotted"], 10)

    def test_raises_when_no_differential_result(self):
        with self.assertRaises(ValueError) as ctx:
            self.server.arf_plot_volcano()
        self.assertIn("arf_differential", str(ctx.exception))

    def test_raises_for_non_two_group_result(self):
        self.session_state.session.last_differential = {
            "kind": "anova", "volcano": [_point("PC 34:1", 1.8, 3.4, "up")]}
        with self.assertRaises(ValueError) as ctx:
            self.server.arf_plot_volcano()
        self.assertIn("arf_differential", str(ctx.exception))

    def test_writes_no_png(self):
        import tempfile
        from pathlib import Path
        self.session_state.session.last_differential = {
            "kind": "two_group", "a": "A", "b": "B",
            "volcano": [_point("PC 34:1", 1.8, 3.4, "up")]}
        with tempfile.TemporaryDirectory() as tmp:
            import os
            saved = os.environ.get("LIPIDMIX_REPORTS_DIR")
            os.environ["LIPIDMIX_REPORTS_DIR"] = str(Path(tmp) / "reports")
            try:
                self.server.arf_plot_volcano()
                self.assertEqual(list(Path(tmp).rglob("*.png")), [])
            finally:
                if saved is None:
                    os.environ.pop("LIPIDMIX_REPORTS_DIR", None)
                else:
                    os.environ["LIPIDMIX_REPORTS_DIR"] = saved

    def test_fastmcp_exposes_output_schema(self):
        import asyncio
        tools = asyncio.run(self.server.mcp.list_tools())
        tool = next(item for item in tools if item.name == "arf_plot_volcano")
        self.assertIsNotNone(tool.outputSchema)
        self.assertIn("plot_schema", tool.outputSchema.get("properties", {}))
```

`tests/test_server_registration.py` の `EXPECTED_TOOLS` に1行追加（`"arf_pca_preprocessed",` の次）:

```python
    "arf_plot_volcano",
```

`tests/test_differential_tools.py` の `TestArfDifferential` にメソッド追加:

```python
    def test_volcano_note_points_to_structured_plot_tool(self):
        # 既存の test_two_group_* と同じ 3+3 サンプルの下地を使う（群内 n>=2 を満たし、
        # 「n 不足」caveat 経路に入らない構成）。
        session_state.session.feature_matrix = np.array([
            [10.0, 5.0], [11.0, 5.1], [9.5, 4.9],
            [50.0, 5.0], [52.0, 5.2], [48.0, 4.8],
        ])
        names = ["a1", "a2", "a3", "b1", "b2", "b3"]
        session_state.session.pp_sample_names = names
        session_state.session.pp_feature_names = ["f0", "f1"]
        session_state.session.preprocessing_recipe = {"normalize": "median"}
        session_state.session.sample_meta = {
            n: {"group": ("A" if n.startswith("a") else "B")} for n in names
        }
        out = json.loads(server.arf_differential(group_a="A", group_b="B"))
        self.assertIn("arf_plot_volcano", out["volcano_note"])
        self.assertNotIn("save_volcano_figure で図示", out["volcano_note"])
        self.assertNotIn("volcano", out)
        last = session_state.session.last_differential
        self.assertEqual(last["q_threshold"], 0.05)
        self.assertEqual(last["log2fc_threshold"], 1.0)
        self.assertEqual(last["n_a"], 3)
        self.assertEqual(last["n_b"], 3)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; python -m unittest tests.test_volcano_plot tests.test_server_registration tests.test_differential_tools -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'arf_plot_volcano'` と登録スナップショット不一致

- [ ] **Step 3: 実装する**

`tools_arf.py` の `import differential` の次の行に追加:

```python
import volcano_plot
```

`tools_arf.py` の import ブロック末尾（`from tool_helpers import (...)` の後）に追加:

```python
from volcano_plot import VolcanoPlotPayload
```

`tools_arf.py` の `__all__` の `"arf_differential",` の直後に追加:

```python
    "arf_plot_volcano",
```

`tools_arf.py:883-895` を差し替える。変更前:

```python
        session_state.session.last_differential = {"kind": "two_group", "a": group_a, "b": group_b,
                                     "results": results, "volcano": volcano}
        # 全量 volcano（~特徴数）は上の last_differential に保持し save_volcano_figure から
        # 使う。payload には載せない——先頭の summary が巨大 volcano 配列＋文脈切り詰めで
        # 埋没し、解釈モデルが有意件数を読めず「全て ns」と誤読する退行を避けるため。
        payload = {"status": "success", "kind": "two_group",
                   "group_a": group_a, "group_b": group_b,
                   "resolved_class_ids": resolved,
                   "resolved_samples": resolved_samples,
                   "n_a": n_a, "n_b": n_b,
                   "summary": summary, "caveats": caveats,
                   "volcano_note": "全特徴の volcano 点列は本要約に非同梱。"
                                   "save_volcano_figure で図示できます。"}
```

変更後:

```python
        session_state.session.last_differential = {"kind": "two_group", "a": group_a, "b": group_b,
                                     "n_a": n_a, "n_b": n_b,
                                     "q_threshold": q_threshold,
                                     "log2fc_threshold": log2fc_threshold,
                                     "results": results, "volcano": volcano}
        # 全量 volcano（~特徴数）は上の last_differential に保持し、arf_plot_volcano
        # （構造化点列）と save_volcano_figure（PNG）から使う。payload には載せない
        # ——先頭の summary が巨大 volcano 配列＋文脈切り詰めで埋没し、解釈モデルが
        # 有意件数を読めず「全て ns」と誤読する退行を避けるため。
        payload = {"status": "success", "kind": "two_group",
                   "group_a": group_a, "group_b": group_b,
                   "resolved_class_ids": resolved,
                   "resolved_samples": resolved_samples,
                   "n_a": n_a, "n_b": n_b,
                   "summary": summary, "caveats": caveats,
                   "volcano_note": "全特徴の volcano 点列は本要約に非同梱。"
                                   "arf_plot_volcano で構造化した点列を取得し、"
                                   "クライアント側で散布図を描画してください。"
                                   "PNG が必要だとユーザーが明示した場合のみ "
                                   "save_volcano_figure を実行します。"}
```

`tools_arf.py` の末尾に新ツールを追加:

```python
@mcp.tool()
def arf_plot_volcano(
    max_points: int = 3000, title: str | None = None,
) -> VolcanoPlotPayload:
    """Return client-neutral plot data for the latest two-group volcano.

    This read-only tool returns structured ``lipidmix.volcano.v1`` JSON only. It does
    not render an image and does not write files: Use-LLLM may render the points with
    Plotly, while Claude Desktop or another MCP client may use its own UI. Call
    ``save_volcano_figure`` only after the user explicitly requests PNG output.

    Run ``arf_differential`` (two-group) first. ``up`` and ``down`` points are always
    kept in full; only ``ns`` points are thinned to fit ``max_points``. Every count —
    total, plotted, thinned, and dropped-as-non-finite — is reported in ``selection``,
    so read it before concluding how many features moved.
    """
    last = getattr(session_state.session, "last_differential", None)
    if not last or last.get("kind") != "two_group" or not last.get("volcano"):
        raise ValueError(
            "先に arf_differential（2群比較）を実行してください"
            "（直近の2群差次的解析の volcano データがありません）。"
        )
    return volcano_plot.build_volcano_plot_payload(
        last, max_points=max_points, title=title,
    )
```

`tools_reports.py:113-118` の `save_pca_figure` docstring を差し替え。変更前:

```python
    """直近のセッションPCA結果からPNGを生成し reports/figures/ に保存する。

    arf_parser / arf_re_pca / pai2_parser 等でPCAを実行した後に呼ぶ。返り値の相対パスを
    write_report の本文に `![PCA](figures/<analysis_id>_pca.png)` として埋め込める。
    """
```

変更後:

```python
    """明示的なユーザー要求時だけ、直近のセッションPCA結果をPNGとして保存する。

    先に arf_parser / arf_pca_preprocessed / load_dataset 等でPCAを実行する。通常の
    対話描画ではこのツールを呼ばず、各MCPクライアントのUIへ描画を任せる（PCA座標は
    解析ツールの返り値に同梱されている）。返り値の相対パスは write_report の本文に
    `![PCA](figures/<analysis_id>_pca.png)` として埋め込める。
    """
```

`tools_reports.py:149-156` の `save_volcano_figure` docstring を差し替え。変更前:

```python
    """直近の差次的解析結果を volcano プロットとして
    reports/figures/<analysis_id>_volcano.png に保存し、相対パスを返す。

    先に arf_differential（2群比較）を実行して session_state.session.last_differential の
    volcano データを用意すること。返り値の相対パスは write_report の本文に
    `![volcano](figures/<analysis_id>_volcano.png)` として埋め込める。
    """
```

変更後:

```python
    """明示的なユーザー要求時だけ、直近の差次的解析を volcano プロットのPNGとして
    reports/figures/<analysis_id>_volcano.png に保存し、相対パスを返す。

    先に arf_differential（2群比較）を実行する。通常の対話描画ではこのツールを呼ばず、
    arf_plot_volcano で構造化した点列を返して各MCPクライアントのUIへ描画を任せる。
    なお本PNGは間引き前の全特徴を描く（arf_plot_volcano は ns 点を間引くことがある）。
    返り値の相対パスは write_report の本文に
    `![volcano](figures/<analysis_id>_volcano.png)` として埋め込める。
    """
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; python -m unittest tests.test_volcano_plot tests.test_server_registration tests.test_differential_tools tests.test_report_tools -v`
Expected: PASS

- [ ] **Step 5: 全サーバテストで退行がないことを確認**

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; python -m unittest discover -s tests -v`
Expected: PASS（既存の失敗が無いこと。失敗したら本タスクの変更が原因か切り分ける）

- [ ] **Step 6: コミット**

```bash
git add tools_arf.py tools_reports.py tests/test_volcano_plot.py tests/test_server_registration.py tests/test_differential_tools.py
git commit -m "feat(volcano): arf_plot_volcano を追加し PNG を明示要求時のみに固定

arf_differential の volcano_note が PNG を唯一の可視化手段として案内していた
のを、構造化点列ツール arf_plot_volcano へ向け直す。save_pca_figure /
save_volcano_figure の docstring も save_eic_figure と同じ「明示要求時のみ」
書式へ統一した。last_differential にしきい値と群サイズを保存し、ペイロード
組み立てが差次的解析を再計算しないようにした。"
```

---

## Task 3: CLI の無条件 PNG 生成を除去

**Files:**
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\arf_reader.py:875`, `881`, `935-940`, `985-987`

**Interfaces:**
- Consumes: なし
- Produces: なし（CLI の既定挙動のみ変更。`plot_pca` / `plot_pca_scores_by_sample` / `plot_peak_height_distribution` のシグネチャは不変）

- [ ] **Step 1: 現在の無条件生成を再現して確認**

Run:
```
cd C:\Users\yuu18\Lipidmix_with_LLM; python -c "import ast,sys; src=open('arf_reader.py',encoding='utf-8').read(); print('pca_plot.png' in src.split('# PCAプロットを表示')[1][:200])"
```
Expected: `True`（`--output-plot` 未指定でも `pca_plot.png` に落ちる既定値が残っている）

- [ ] **Step 2: `--output-plot` を任意化**

`arf_reader.py:875` を差し替え。変更前:

```python
    parser.add_argument("--output-plot", help="PCA プロットを画像ファイルとして保存するパス (デフォルト: pca_plot.png)")
```

変更後:

```python
    parser.add_argument("--output-plot", default=None, help="PCA プロットを PNG 保存するパス。未指定なら PNG を生成しない")
```

`arf_reader.py:881` を差し替え。変更前:

```python
    parser.add_argument("--output-dist-plot", type=str, default="peak_height_dist.png", help="分布プロットの保存先 (デフォルト: peak_height_dist.png)")
```

変更後:

```python
    parser.add_argument("--output-dist-plot", type=str, default=None, help="分布プロットの PNG 保存先。未指定なら PNG を生成しない")
```

- [ ] **Step 3: 呼び出し側を条件付きにする**

`arf_reader.py:985-987` を差し替え。変更前:

```python
            # PCAプロットを表示
            plot_file = args.output_plot or "pca_plot.png"
            plot_pca(pca_result, sample_names, args.props, file_path, plot_file)
```

変更後:

```python
            # PNG は明示要求時のみ。CLI が黙って画像ファイルを作らないようにする。
            if args.output_plot:
                plot_pca(pca_result, sample_names, args.props, file_path, args.output_plot)
            else:
                print("[INFO] --output-plot 未指定のため PCA プロット PNG は生成しません")
```

`arf_reader.py:935-940` を差し替え。変更前:

```python
            plot_peak_height_distribution(
                peak_df=peak_df, 
                filter_keyword=args.filter_name, 
                group_regex=regex_pattern,  # 追加
                output_file=args.output_dist_plot
            )
```

変更後:

```python
            # PNG は明示要求時のみ。CLI が黙って画像ファイルを作らないようにする。
            if args.output_dist_plot:
                plot_peak_height_distribution(
                    peak_df=peak_df,
                    filter_keyword=args.filter_name,
                    group_regex=regex_pattern,  # 追加
                    output_file=args.output_dist_plot
                )
            else:
                print("[INFO] --output-dist-plot 未指定のため分布プロット PNG は生成しません")
```

- [ ] **Step 4: 構文と CLI ヘルプを確認**

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; python arf_reader.py --help`
Expected: 終了コード 0。`--output-plot` の説明に「未指定なら PNG を生成しない」が出る

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; python -m unittest discover -s tests -v`
Expected: PASS（`arf_reader` を import するテストが壊れていないこと）

- [ ] **Step 5: コミット**

```bash
git add arf_reader.py
git commit -m "fix(cli): --output-plot 未指定時に PNG を無条件生成しない

--pca を付けるだけで pca_plot.png が、分布プロット経路では
peak_height_dist.png が黙って作られていた。どちらも出力パスを明示した
ときだけ書くようにした。関数側の default 値は直接呼び出しの利便性として残す。"
```

---

## Task 4: [Use-LLLM] eic-plot.js を multi スキーマ対応にする

**Files:**
- Modify: `C:\Users\yuu18\Use-LLLM\src\use_lllm\general\static\eic-plot.js:42-105`
- Modify: `C:\Users\yuu18\Use-LLLM\tests\test_general_eic_plot.cjs`（末尾に追記）
- Modify: `C:\Users\yuu18\Use-LLLM\src\use_lllm\general\static\app.js:577-612`

**Interfaces:**
- Consumes: MCP サーバの `lipidmix.eic.v1` と `lipidmix.eic.multi.v1`（`eic_plot.py` 定義）
- Produces: `GeneralEicPlot.findPlot(input) -> {schema, title, xLabel, yLabel, showAnnotations, series, raw} | null`、`GeneralEicPlot.traces(plot) -> Array`、`GeneralEicPlot.annotations(plot) -> Array`

- [ ] **Step 1: まず Use-LLLM に作業ブランチを作る**

```bash
cd /c/Users/yuu18/Use-LLLM
git checkout -b feat/client-side-plot-rendering
git status --short
```
Expected: 既存の未コミット変更（`app.js` / `api.py` 他）がそのまま残っていること。以降 `git add` は自分が触ったファイルのみを列挙する

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_general_eic_plot.cjs` の最終行は
`console.log("general EIC plot helper tests passed");` である。**その行の直前**に
以下を挿入する（最終行はそのまま残す。新しい `console.log` は追加しない）:

```javascript
// --- lipidmix.eic.multi.v1（複数物質×1サンプルのオーバーレイ） ---
const multiPayload = {
  plot_schema: "lipidmix.eic.multi.v1",
  plot_type: "line",
  title: "EIC overlay | 2 compounds | liver-01",
  axes: {
    x: { label: "RT", unit: "min", scale: "linear" },
    y: { label: "Intensity", unit: null, scale: "linear" },
  },
  sample: { file_id: 3, sample_name: "liver-01", class_id: "24M" },
  series: [
    {
      id: "spot-11", label: "PC 34:1", spot_id: 11, name: "PC 34:1",
      ontology: "PC", adduct: "[M+H]+", mz: 760.5851, rt: 6.42,
      x: [6.3, 6.42, 6.5], y: [100, 900, 120],
      peak_left: 6.3, peak_top: 6.42, peak_right: 6.5,
      max_intensity: 900, mean_intensity: 373, point_count: 3,
      annotation: { text: "PC 34:1 / 6.420", x: 6.42, y: 900 },
    },
    {
      id: "spot-12", label: "PE 36:2", spot_id: 12, name: "PE 36:2",
      ontology: "PE", adduct: "[M+H]+", mz: 744.5538, rt: 7.10,
      x: [7.0, 7.1, 7.2], y: [50, 400, 60],
      peak_left: 7.0, peak_top: 7.1, peak_right: 7.2,
      max_intensity: 400, mean_intensity: 170, point_count: 3,
      annotation: { text: "PE 36:2 / 7.100", x: 7.1, y: 400 },
    },
  ],
  render_hints: {
    mode: "lines", connect_points: true, show_legend: true,
    show_annotations: true, hover_fields: ["label", "spot_id"],
  },
  caveats: [],
};

const multi = helpers.findPlot(JSON.stringify(multiPayload));
assert.ok(multi, "multi スキーマが findPlot を通ること");
assert.equal(multi.schema, "multi");
assert.equal(multi.series.length, 2);
assert.equal(multi.xLabel, "RT (min)");
assert.equal(multi.series[0].spot_id, 11);

const multiTraces = helpers.traces(multi);
assert.equal(multiTraces.length, 2);
assert.equal(multiTraces[0].customdata[0].spot_id, 11);
assert.equal(multiTraces[0].customdata[0].ontology, "PC");
assert.equal(multiTraces[0].customdata[0].mz, 760.5851);
assert.equal(multiTraces[0].customdata[0].rt, 6.42);
assert.match(multiTraces[0].hovertemplate, /760\.5851/);
assert.match(multiTraces[0].hovertemplate, /PC/);

const multiAnnotations = helpers.annotations(multi);
assert.equal(multiAnnotations.length, 2);
assert.equal(multiAnnotations[0].text, "PC 34:1 / 6.420");
assert.equal(multiAnnotations[0].x, 6.42);
assert.equal(multiAnnotations[0].showarrow, false);

const multiOff = helpers.findPlot(JSON.stringify({
  ...multiPayload,
  render_hints: { ...multiPayload.render_hints, show_annotations: false },
}));
assert.equal(helpers.annotations(multiOff).length, 0);

// single スキーマは schema フラグが立ち、注釈を返さない
assert.equal(plot.schema, "single");
assert.equal(helpers.annotations(plot).length, 0);
assert.equal(traces[0].customdata[0].sample_name, undefined);

// 未知スキーマは拒否する
assert.equal(helpers.findPlot(JSON.stringify({
  ...multiPayload, plot_schema: "lipidmix.eic.v99",
})), null);
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `cd C:\Users\yuu18\Use-LLLM; node tests/test_general_eic_plot.cjs`
Expected: FAIL — `AssertionError: multi スキーマが findPlot を通ること`

- [ ] **Step 4: eic-plot.js を実装**

`eic-plot.js:42-55` の `normalizePlot` を差し替え。変更前:

```javascript
  function normalizePlot(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (value.plot_schema !== "lipidmix.eic.v1" || value.plot_type !== "line") return null;
    if (!Array.isArray(value.series)) return null;
    const series = value.series.map(normalizeSeries).filter(Boolean);
    if (!series.length || series.length !== value.series.length) return null;
    return {
      title: value.title == null || value.title === "" ? "Extracted ion chromatogram" : String(value.title),
      xLabel: axisLabel(value.axes?.x, "RT"),
      yLabel: axisLabel(value.axes?.y, "Intensity"),
      series,
      raw: value,
    };
  }
```

変更後:

```javascript
  // 1物質×複数サンプル（single）と複数物質×1サンプル（multi）の2スキーマを受ける。
  const SCHEMAS = { "lipidmix.eic.v1": "single", "lipidmix.eic.multi.v1": "multi" };

  function normalizePlot(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const schema = SCHEMAS[value.plot_schema];
    if (!schema || value.plot_type !== "line") return null;
    if (!Array.isArray(value.series)) return null;
    const series = value.series.map(normalizeSeries).filter(Boolean);
    if (!series.length || series.length !== value.series.length) return null;
    return {
      schema,
      title: value.title == null || value.title === "" ? "Extracted ion chromatogram" : String(value.title),
      xLabel: axisLabel(value.axes?.x, "RT"),
      yLabel: axisLabel(value.axes?.y, "Intensity"),
      showAnnotations: value.render_hints?.show_annotations === true,
      series,
      raw: value,
    };
  }
```

`eic-plot.js:85-105`（`traces` と `return`）を差し替え。変更前:

```javascript
  function traces(plot) {
    return plot.series.map((item) => ({
      type: item.x.length > 500 ? "scattergl" : "scatter",
      mode: "lines",
      name: item.label,
      x: item.x,
      y: item.y,
      customdata: item.x.map(() => ({
        file_id: item.file_id,
        sample_name: item.sample_name,
        class_id: item.class_id,
        peak_left: item.peak_left,
        peak_top: item.peak_top,
        peak_right: item.peak_right,
      })),
      hovertemplate: "%{fullData.name}<br>x %{x:.5g}<br>intensity %{y:.5g}<extra></extra>",
      line: { width: 1.5 },
    }));
  }

  return { findPlot, traces };
```

変更後:

```javascript
  function customdataFor(plot, item) {
    if (plot.schema === "multi") {
      return {
        spot_id: item.spot_id,
        name: item.name,
        ontology: item.ontology,
        adduct: item.adduct,
        mz: item.mz,
        rt: item.rt,
        peak_top: item.peak_top,
        max_intensity: item.max_intensity,
      };
    }
    return {
      file_id: item.file_id,
      sample_name: item.sample_name,
      class_id: item.class_id,
      peak_left: item.peak_left,
      peak_top: item.peak_top,
      peak_right: item.peak_right,
    };
  }

  // multi の物質メタは trace 内で一定なので、customdata 補間に頼らず
  // hovertemplate 文字列へ焼き込む（Plotly のバージョン差で崩れないため）。
  function hovertemplateFor(plot, item) {
    const tail = "x %{x:.5g}<br>intensity %{y:.5g}<extra></extra>";
    if (plot.schema !== "multi") return `%{fullData.name}<br>${tail}`;
    const mz = Number(item.mz);
    const rt = Number(item.rt);
    const meta = [
      item.ontology ? String(item.ontology) : null,
      Number.isFinite(mz) ? `m/z ${mz.toFixed(4)}` : null,
      Number.isFinite(rt) ? `RT ${rt.toFixed(2)}` : null,
    ].filter(Boolean).join(" \u00b7 ");
    return meta
      ? `%{fullData.name}<br>${meta}<br>${tail}`
      : `%{fullData.name}<br>${tail}`;
  }

  function traces(plot) {
    return plot.series.map((item) => ({
      type: item.x.length > 500 ? "scattergl" : "scatter",
      mode: "lines",
      name: item.label,
      x: item.x,
      y: item.y,
      customdata: item.x.map(() => customdataFor(plot, item)),
      hovertemplate: hovertemplateFor(plot, item),
      line: { width: 1.5 },
    }));
  }

  function annotations(plot) {
    if (plot.schema !== "multi" || !plot.showAnnotations) return [];
    return plot.series
      .filter((item) => item.annotation && Number.isFinite(Number(item.annotation.x)))
      .map((item) => ({
        text: String(item.annotation.text == null ? item.label : item.annotation.text),
        x: Number(item.annotation.x),
        y: Number(item.annotation.y),
        showarrow: false,
        textangle: -45,
        yshift: 10,
        xanchor: "left",
        font: { size: 9 },
      }));
  }

  return { findPlot, traces, annotations };
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd C:\Users\yuu18\Use-LLLM; node tests/test_general_eic_plot.cjs`
Expected: `eic-plot helpers: ok`

Run: `cd C:\Users\yuu18\Use-LLLM; node --check src/use_lllm/general/static/eic-plot.js`
Expected: 出力なし・終了コード 0

- [ ] **Step 6: app.js を注釈と凡例に対応させる**

`app.js:592-611` の `draw` を差し替え。変更前:

```javascript
  const draw = () => {
    Promise.resolve(Plotly.react(
      plot,
      window.GeneralEicPlot.traces(eic),
      {
        margin: { l: 68, r: 22, t: 18, b: 52 },
        xaxis: { title: eic.xLabel, zeroline: false, gridcolor: "#ebe8e1" },
        yaxis: { title: eic.yLabel, rangemode: "tozero", zeroline: false, gridcolor: "#ebe8e1" },
        legend: { orientation: "h", y: -0.22 },
        hovermode: "closest",
        paper_bgcolor: "transparent",
        plot_bgcolor: "#ffffff",
        font: { family: '"Segoe UI Variable", "Yu Gothic UI", sans-serif', color: "#3d3b36", size: 11 },
      },
      { responsive: true, displaylogo: false, scrollZoom: true },
    )).then(() => {
      scrollConversationToEnd();
    });
  };
```

変更後:

```javascript
  // 多系列（複数物質オーバーレイ）では横並び凡例が潰れるので右外側の縦並びにする。
  const manySeries = eic.series.length > 8;
  const draw = () => {
    Promise.resolve(Plotly.react(
      plot,
      window.GeneralEicPlot.traces(eic),
      {
        margin: { l: 68, r: manySeries ? 200 : 22, t: 18, b: 52 },
        xaxis: { title: eic.xLabel, zeroline: false, gridcolor: "#ebe8e1" },
        yaxis: { title: eic.yLabel, rangemode: "tozero", zeroline: false, gridcolor: "#ebe8e1" },
        legend: manySeries
          ? { orientation: "v", x: 1.02, xanchor: "left", y: 1, font: { size: 9 } }
          : { orientation: "h", y: -0.22 },
        annotations: window.GeneralEicPlot.annotations(eic),
        hovermode: "closest",
        paper_bgcolor: "transparent",
        plot_bgcolor: "#ffffff",
        font: { family: '"Segoe UI Variable", "Yu Gothic UI", sans-serif', color: "#3d3b36", size: 11 },
      },
      { responsive: true, displaylogo: false, scrollZoom: true },
    )).then(() => {
      scrollConversationToEnd();
    });
  };
```

- [ ] **Step 7: 構文を確認**

Run: `cd C:\Users\yuu18\Use-LLLM; node --check src/use_lllm/general/static/app.js`
Expected: 出力なし・終了コード 0

- [ ] **Step 8: コミット**

```bash
cd /c/Users/yuu18/Use-LLLM
git add src/use_lllm/general/static/eic-plot.js tests/test_general_eic_plot.cjs
git commit -m "feat(eic): lipidmix.eic.multi.v1 を Plotly で描画する

複数物質オーバーレイ（eic_plot_compounds）のペイロードがスキーマ判定で
弾かれ webUI に描画されず、PNG 保存に頼るしかなかった。single/multi の
両スキーマを受け、multi では物質メタを hover に出し apex 注釈を返す。"
```

`app.js` は既存の未コミット変更を含むため、このタスクではコミットしない。Task 5 でまとめてコミットする。

---

## Task 5: [Use-LLLM] volcano レンダラを新設し配線する

**Files:**
- Create: `C:\Users\yuu18\Use-LLLM\src\use_lllm\general\static\volcano-plot.js`
- Create: `C:\Users\yuu18\Use-LLLM\tests\test_general_volcano_plot.cjs`
- Modify: `C:\Users\yuu18\Use-LLLM\src\use_lllm\general\static\app.js`（`renderMessage` に1行、末尾に `appendVolcanoPlot`）
- Modify: `C:\Users\yuu18\Use-LLLM\src\use_lllm\general\static\index.html:196` 付近

**Interfaces:**
- Consumes: Task 1・2 が返す `lipidmix.volcano.v1`
- Produces: `GeneralVolcanoPlot.findPlot(input) -> {title, xLabel, yLabel, points, guides, selection, raw} | null`、`GeneralVolcanoPlot.traces(plot) -> Array`、`GeneralVolcanoPlot.shapes(plot) -> Array`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_general_volcano_plot.cjs` を新規作成:

```javascript
const assert = require("node:assert/strict");
const helpers = require("../src/use_lllm/general/static/volcano-plot.js");

const payload = {
  plot_schema: "lipidmix.volcano.v1",
  plot_type: "scatter",
  title: "Volcano (24M vs 9w)",
  comparison: { group_a: "24M", group_b: "9w", n_a: 6, n_b: 5 },
  axes: {
    x: { label: "log2 fold change", unit: null, scale: "linear" },
    y: { label: "-log10 p", unit: null, scale: "linear" },
  },
  thresholds: { q: 0.05, log2fc: 1.0 },
  points: [
    { feature: "PC 34:1", log2fc: 1.8, neg_log10_p: 3.4, sig: "up" },
    { feature: "PE 36:2", log2fc: -2.1, neg_log10_p: 4.0, sig: "down" },
    { feature: "TG 52:3", log2fc: 0.2, neg_log10_p: 0.5, sig: "ns" },
    { feature: "TG 54:4", log2fc: -0.1, neg_log10_p: 0.3, sig: "ns" },
  ],
  selection: {
    total: 4, plotted: 4, significant_total: 2, significant_plotted: 2,
    ns_total: 2, ns_plotted: 2, max_points: 3000, dropped_nonfinite: 0,
  },
  render_hints: {
    mode: "markers", color_by: "sig", show_legend: true,
    guides: { x: [-1.0, 1.0], y: [1.301] },
    hover_fields: ["feature", "log2fc", "neg_log10_p", "sig"],
  },
  caveats: [],
};

// --- findPlot: 生 JSON とフェンス JSON の両方 ---
const plot = helpers.findPlot(JSON.stringify(payload));
assert.ok(plot, "生 JSON から payload を見つけること");
assert.equal(plot.title, "Volcano (24M vs 9w)");
assert.equal(plot.xLabel, "log2 fold change");
assert.equal(plot.yLabel, "-log10 p");
assert.equal(plot.points.length, 4);
assert.equal(plot.selection.significant_total, 2);

const fenced = `volcano:\n\`\`\`json\n${JSON.stringify(payload)}\n\`\`\``;
assert.equal(helpers.findPlot(fenced).points.length, 4);

const nested = helpers.findPlot(JSON.stringify({ result: { data: payload } }));
assert.ok(nested, "入れ子から payload を見つけること");

// --- traces: sig ごとに3トレース、ns を下層に ---
const traces = helpers.traces(plot);
assert.equal(traces.length, 3);
assert.equal(traces[0].name, "ns (2)");
assert.equal(traces[1].name, "down (1)");
assert.equal(traces[2].name, "up (1)");
assert.equal(traces[0].marker.color, "#95a5a6");
assert.equal(traces[1].marker.color, "#2471a3");
assert.equal(traces[2].marker.color, "#c0392b");
assert.deepEqual(traces[2].x, [1.8]);
assert.deepEqual(traces[2].text, ["PC 34:1"]);
assert.equal(traces[2].mode, "markers");

// --- 空の sig グループはトレースを作らない ---
const upOnly = helpers.findPlot(JSON.stringify({
  ...payload,
  points: [{ feature: "PC 34:1", log2fc: 1.8, neg_log10_p: 3.4, sig: "up" }],
}));
assert.equal(helpers.traces(upOnly).length, 1);
assert.equal(helpers.traces(upOnly)[0].name, "up (1)");

// --- shapes: しきい値の破線 ---
const shapes = helpers.shapes(plot);
assert.equal(shapes.length, 3);
assert.equal(shapes.filter((item) => item.yref === "paper").length, 2);
assert.equal(shapes.filter((item) => item.xref === "paper").length, 1);
assert.equal(shapes[0].line.dash, "dash");

const noGuides = helpers.findPlot(JSON.stringify({
  ...payload, render_hints: { ...payload.render_hints, guides: undefined },
}));
assert.deepEqual(helpers.shapes(noGuides), []);

// --- 非有限値・不正スキーマ ---
assert.equal(helpers.findPlot(JSON.stringify({
  ...payload, plot_schema: "lipidmix.volcano.v99",
})), null);
assert.equal(helpers.findPlot(JSON.stringify({
  ...payload, plot_type: "line",
})), null);
assert.equal(helpers.findPlot(JSON.stringify({ ...payload, points: [] })), null);
assert.equal(helpers.findPlot("ただのテキスト応答です"), null);

const withBad = helpers.findPlot(JSON.stringify({
  ...payload,
  points: [
    { feature: "ok", log2fc: 1.8, neg_log10_p: 3.4, sig: "up" },
    { feature: "bad", log2fc: null, neg_log10_p: 3.4, sig: "ns" },
  ],
}));
assert.equal(withBad.points.length, 1, "描画できない点は落とすこと");

// --- 未知の sig は ns として扱う ---
const oddSig = helpers.findPlot(JSON.stringify({
  ...payload,
  points: [{ feature: "x", log2fc: 0.1, neg_log10_p: 0.2, sig: "maybe" }],
}));
assert.equal(oddSig.points[0].sig, "ns");

console.log("volcano-plot helpers: ok");
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd C:\Users\yuu18\Use-LLLM; node tests/test_general_volcano_plot.cjs`
Expected: FAIL — `Cannot find module '../src/use_lllm/general/static/volcano-plot.js'`

- [ ] **Step 3: volcano-plot.js を実装**

`src/use_lllm/general/static/volcano-plot.js` を新規作成:

```javascript
(function exposeVolcanoPlot(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.GeneralVolcanoPlot = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function createVolcanoPlotHelpers() {
  "use strict";

  const SCHEMA = "lipidmix.volcano.v1";

  // 配色は save_volcano_figure（matplotlib）と一致させ、PNG と画面で色が
  // 入れ替わらないようにする。ns を先に積んで有意点を上に描く。
  const GROUPS = [
    { sig: "ns", color: "#95a5a6", size: 4, opacity: 0.45 },
    { sig: "down", color: "#2471a3", size: 6, opacity: 0.85 },
    { sig: "up", color: "#c0392b", size: 6, opacity: 0.85 },
  ];

  function parseJsonCandidates(value) {
    if (typeof value !== "string") return [value];
    const candidates = [];
    try { candidates.push(JSON.parse(value)); } catch (_) { /* Markdown response */ }
    const fences = /```(?:json)?\s*([\s\S]*?)```/gi;
    let match;
    while ((match = fences.exec(value)) !== null) {
      try { candidates.push(JSON.parse(match[1].trim())); } catch (_) { /* Ignore invalid fences. */ }
    }
    return candidates;
  }

  function axisLabel(axis, fallback) {
    if (!axis || typeof axis !== "object") return fallback;
    const label = axis.label == null || axis.label === "" ? fallback : String(axis.label);
    return axis.unit == null || axis.unit === "" ? label : `${label} (${axis.unit})`;
  }

  function normalizePoint(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const x = Number(value.log2fc);
    const y = Number(value.neg_log10_p);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return {
      x,
      y,
      feature: value.feature == null || value.feature === "" ? "(unnamed)" : String(value.feature),
      sig: value.sig === "up" || value.sig === "down" ? value.sig : "ns",
    };
  }

  function normalizePlot(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (value.plot_schema !== SCHEMA || value.plot_type !== "scatter") return null;
    if (!Array.isArray(value.points)) return null;
    const points = value.points.map(normalizePoint).filter(Boolean);
    if (!points.length) return null;
    return {
      title: value.title == null || value.title === "" ? "Volcano plot" : String(value.title),
      xLabel: axisLabel(value.axes?.x, "log2 fold change"),
      yLabel: axisLabel(value.axes?.y, "-log10 p"),
      points,
      guides: value.render_hints?.guides || null,
      selection: value.selection || null,
      raw: value,
    };
  }

  function findPlotInValue(value, seen = new Set()) {
    if (typeof value === "string") {
      for (const candidate of parseJsonCandidates(value)) {
        if (candidate === value) continue;
        const nested = findPlotInValue(candidate, seen);
        if (nested) return nested;
      }
      return null;
    }
    if (!value || typeof value !== "object" || seen.has(value)) return null;
    seen.add(value);
    const direct = normalizePlot(value);
    if (direct) return direct;
    for (const child of Object.values(value)) {
      const nested = findPlotInValue(child, seen);
      if (nested) return nested;
    }
    return null;
  }

  function findPlot(input) {
    for (const candidate of parseJsonCandidates(input)) {
      const plot = findPlotInValue(candidate);
      if (plot) return plot;
    }
    return null;
  }

  function traces(plot) {
    return GROUPS.map((group) => {
      const rows = plot.points.filter((point) => point.sig === group.sig);
      if (!rows.length) return null;
      return {
        type: rows.length > 1000 ? "scattergl" : "scatter",
        mode: "markers",
        name: `${group.sig} (${rows.length})`,
        x: rows.map((row) => row.x),
        y: rows.map((row) => row.y),
        text: rows.map((row) => row.feature),
        hovertemplate: `%{text}<br>log2FC %{x:.3f}<br>-log10 p %{y:.3f}<extra>${group.sig}</extra>`,
        marker: {
          size: group.size,
          color: group.color,
          opacity: group.opacity,
          line: { width: 0 },
        },
      };
    }).filter(Boolean);
  }

  function shapes(plot) {
    const guides = plot.guides;
    if (!guides || typeof guides !== "object") return [];
    const line = { color: "#b3ada2", width: 1, dash: "dash" };
    const out = [];
    for (const value of Array.isArray(guides.x) ? guides.x : []) {
      const x = Number(value);
      if (!Number.isFinite(x)) continue;
      out.push({ type: "line", xref: "x", yref: "paper", x0: x, x1: x, y0: 0, y1: 1, line });
    }
    for (const value of Array.isArray(guides.y) ? guides.y : []) {
      const y = Number(value);
      if (!Number.isFinite(y)) continue;
      out.push({ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: y, y1: y, line });
    }
    return out;
  }

  return { findPlot, traces, shapes };
}));
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd C:\Users\yuu18\Use-LLLM; node tests/test_general_volcano_plot.cjs`
Expected: `volcano-plot helpers: ok`

Run: `cd C:\Users\yuu18\Use-LLLM; node --check src/use_lllm/general/static/volcano-plot.js`
Expected: 出力なし・終了コード 0

- [ ] **Step 5: app.js に配線する**

`app.js:526`（`appendEicPlot(bubble, item);`）の直後に1行追加:

```javascript
  appendVolcanoPlot(bubble, item);
```

`app.js` の `appendEicPlot` 関数の直後（`function renderApproval(event) {` の直前）に追加:

```javascript
function appendVolcanoPlot(bubble, item) {
  if (item.role !== "tool" || item.metadata?.is_error || !window.Plotly || !window.GeneralVolcanoPlot) return;
  const volcano = window.GeneralVolcanoPlot.findPlot(item.content);
  if (!volcano?.points?.length) return;

  bubble.classList.add("has-plot");
  const card = document.createElement("section"); card.className = "chat-plot-card";
  const heading = document.createElement("div"); heading.className = "chat-plot-heading";
  const title = document.createElement("strong"); title.textContent = volcano.title;
  const note = document.createElement("span");
  note.textContent = volcanoNote(volcano);
  heading.append(title, note);
  const plot = document.createElement("div"); plot.className = "chat-pca-plot";
  plot.setAttribute("aria-label", `${volcano.title} Plotly chart`);
  card.append(heading, plot); bubble.append(card);

  const draw = () => {
    Promise.resolve(Plotly.react(
      plot,
      window.GeneralVolcanoPlot.traces(volcano),
      {
        margin: { l: 58, r: 22, t: 18, b: 52 },
        xaxis: { title: volcano.xLabel, zeroline: false, gridcolor: "#ebe8e1" },
        yaxis: { title: volcano.yLabel, rangemode: "tozero", zeroline: false, gridcolor: "#ebe8e1" },
        shapes: window.GeneralVolcanoPlot.shapes(volcano),
        legend: { orientation: "h", y: -0.22 },
        hovermode: "closest",
        paper_bgcolor: "transparent",
        plot_bgcolor: "#ffffff",
        font: { family: '"Segoe UI Variable", "Yu Gothic UI", sans-serif', color: "#3d3b36", size: 11 },
      },
      { responsive: true, displaylogo: false, scrollZoom: true },
    )).then(() => {
      scrollConversationToEnd();
    });
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(draw); else draw();
}

// 間引きが起きたことを画面に出す。全点が描かれていると誤解させないため。
function volcanoNote(volcano) {
  const parts = [];
  const selection = volcano.selection;
  if (selection && Number(selection.total) > Number(selection.plotted)) {
    parts.push(`${selection.plotted} / ${selection.total} points（ns を間引き）`);
  } else {
    parts.push(`${volcano.points.length} points`);
  }
  if (selection && Number(selection.significant_total) >= 0) {
    parts.push(`有意 ${selection.significant_total} 件`);
  }
  if (selection && Number(selection.dropped_nonfinite) > 0) {
    parts.push(`検定不能 ${selection.dropped_nonfinite} 件を除外`);
  }
  parts.push("hover / zoom / legend filter");
  return parts.join(" \u00b7 ");
}
```

- [ ] **Step 6: index.html に script タグを追加**

`index.html:196`（`<script src="./static/eic-plot.js"></script>`）の直後に追加:

```html
  <script src="./static/volcano-plot.js"></script>
```

- [ ] **Step 7: 構文を確認**

Run: `cd C:\Users\yuu18\Use-LLLM; node --check src/use_lllm/general/static/app.js`
Expected: 出力なし・終了コード 0

Run: `cd C:\Users\yuu18\Use-LLLM; node tests/test_general_markdown.cjs; node tests/test_general_pca_plot.cjs; node tests/test_general_eic_plot.cjs; node tests/test_general_volcano_plot.cjs`
Expected: 全て ok

- [ ] **Step 8: コミット**

```bash
cd /c/Users/yuu18/Use-LLLM
git add src/use_lllm/general/static/volcano-plot.js tests/test_general_volcano_plot.cjs src/use_lllm/general/static/index.html src/use_lllm/general/static/app.js
git commit -m "feat(volcano): lipidmix.volcano.v1 を Plotly で描画する

sig ごとに3トレース（ns/down/up の順で積む）、しきい値を破線で示し、
hover に feature 名を出す。配色は save_volcano_figure の matplotlib と
一致させた。間引きが起きた場合は見出しに件数を出して、画面が全点でない
ことを隠さない。EIC 側の注釈と多系列凡例の対応も同じコミットに含む。"
```

`app.js` には他作業の未コミット変更が混在している可能性がある。`git add` の前に `git diff src/use_lllm/general/static/app.js` を読み、自分の変更（`appendVolcanoPlot` / `volcanoNote` / `appendEicPlot` の layout）以外が含まれる場合は `git add -p` で自分の hunk のみをステージする。

---

## Task 6: [Use-LLLM] ツール分類と state 規則をサーバへ同期する

**Files:**
- Modify: `C:\Users\yuu18\Use-LLLM\src\use_lllm\core\policy.py:19-59`
- Modify: `C:\Users\yuu18\Use-LLLM\src\use_lllm\core\mcp_state_policy.py:16-70`
- Modify: `C:\Users\yuu18\Use-LLLM\tests\test_policy_server.py`
- Modify: `C:\Users\yuu18\Use-LLLM\tests\test_mcp_state_policy.py`

**Interfaces:**
- Consumes: MCP サーバの実ツール名（`tests/test_server_registration.py` の `EXPECTED_TOOLS` が正準）
- Produces: `classify_tool` が現行 EIC ツールと `arf_plot_volcano` を `READ_ONLY` に分類する。`RULES` に `eic_plot_chromatograms` / `eic_plot_compounds` / `arf_plot_volcano` / `save_volcano_figure` が入る

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_policy_server.py` の末尾に追記:

```python
def test_current_eic_tools_are_read_only():
    from use_lllm.core.policy import ToolSafety, classify_tool

    for name in (
        "eic_parser",
        "eic_plot_chromatograms",
        "eic_plot_compounds",
        "eic_rank_by_max_intensity",
        "eic_search_by_mz_range",
        "eic_search_by_rt_range",
        "arf_plot_volcano",
        "dcl_parser",
        "dcl_find_msms",
        "sample_search",
        "pai2_inspect_peak",
    ):
        assert classify_tool(name) == ToolSafety.READ_ONLY, name


def test_removed_server_tools_are_no_longer_classified():
    from use_lllm.core.policy import ToolSafety, classify_tool

    for name in (
        "eicaef_parser",
        "eicaef_plot_chromatograms",
        "eicaef_top_peak_tops",
        "arf_re_pca",
        "pai2_get_top_metabolites",
        "pai2_inspect_metabolite_details",
        "pai2_update_analysis_filter",
    ):
        assert classify_tool(name) == ToolSafety.UNKNOWN, name


def test_figure_savers_stay_local_write():
    from use_lllm.core.policy import ToolSafety, classify_tool

    for name in ("save_pca_figure", "save_volcano_figure", "save_eic_figure"):
        assert classify_tool(name) == ToolSafety.LOCAL_WRITE, name
```

`tests/test_mcp_state_policy.py` の末尾に追記:

```python
def test_eic_plot_tools_provide_eic_plot_state():
    from use_lllm.core.mcp_state_policy import rule_for

    for name in (
        "ms-data-parser::eic_plot_chromatograms",
        "ms-data-parser::eic_plot_compounds",
    ):
        rule = rule_for(name)
        assert rule.provides == ("eic_plot",), name
        assert rule.replay_safe is True, name
    assert rule_for("ms-data-parser::eicaef_plot_chromatograms").provides == ()


def test_volcano_tools_require_differential_result():
    from use_lllm.core.mcp_state_policy import rule_for

    assert rule_for("ms-data-parser::arf_differential").provides == ("differential_result",)
    assert rule_for("ms-data-parser::arf_differential").replay_safe is False
    assert rule_for("ms-data-parser::arf_plot_volcano").requires == ("differential_result",)
    assert rule_for("ms-data-parser::save_volcano_figure").requires == ("differential_result",)


def test_removed_tool_rules_are_gone():
    from use_lllm.core.mcp_state_policy import rule_for

    assert rule_for("ms-data-parser::arf_re_pca").provides == ()


def test_missing_state_markers_match_current_server_messages():
    from use_lllm.core.mcp_state_policy import indicates_missing_state

    assert indicates_missing_state(
        "ms-data-parser::save_eic_figure",
        "先に eic_plot_chromatograms または eic_plot_compounds を実行してください",
    )
    assert indicates_missing_state(
        "ms-data-parser::arf_plot_volcano",
        "先に arf_differential（2群比較）を実行してください",
    )
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd C:\Users\yuu18\Use-LLLM; python -m pytest tests/test_policy_server.py tests/test_mcp_state_policy.py -v`
Expected: FAIL — `assert ToolSafety.UNKNOWN == ToolSafety.READ_ONLY` 等

- [ ] **Step 3: policy.py を同期する**

`policy.py:19-59` を差し替え。変更前:

```python
READ_ONLY_TOOLS = frozenset(
    {
        "list_reports",
        "read_report",
        "list_data_files",
        "load_dataset",
        "pai2_parser",
        "pai2_get_top_metabolites",
        "pai2_inspect_metabolite_details",
        "verify_peak_annotation",
        "arf_list_tags",
        "arf_list_classes",
        "arf_list_sample_roles",
        "arf_exclude",
        "arf_preprocess",
        "arf_pca_preprocessed",
        "arf_parser",
        "arf_re_pca",
        "arf_differential",
        "arf2_parser",
        "arf2_annotate_identities",
        "eicaef_parser",
        "eicaef_plot_chromatograms",
        "eicaef_top_peak_tops",
        "eicaef_search_by_mz_range",
        "eicaef_search_by_rt_range",
        "knowledge_coverage",
        "log_search",
    }
)
LOCAL_WRITE_TOOLS = frozenset(
    {
        "write_report",
        "save_pca_figure",
        "save_eic_figure",
        "save_volcano_figure",
        "record_objective",
        "update_objective",
        "pai2_update_analysis_filter",
    }
)
```

変更後:

```python
# ms-data-parser の実ツール名と一致させる（正準は同サーバの
# tests/test_server_registration.py の EXPECTED_TOOLS）。旧名が残ると
# classify_tool が UNKNOWN を返し、read-only 解析が毎回承認待ちで止まる。
READ_ONLY_TOOLS = frozenset(
    {
        "list_reports",
        "read_report",
        "list_data_files",
        "load_dataset",
        "log_search",
        "knowledge_coverage",
        "sample_search",
        "pai2_parser",
        "pai2_inspect_peak",
        "verify_peak_annotation",
        "dcl_parser",
        "dcl_find_msms",
        "arf_list_tags",
        "arf_list_classes",
        "arf_list_sample_roles",
        "arf_exclude",
        "arf_preprocess",
        "arf_pca_preprocessed",
        "arf_parser",
        "arf_differential",
        "arf_plot_volcano",
        "arf2_parser",
        "arf2_annotate_identities",
        "eic_parser",
        "eic_plot_chromatograms",
        "eic_plot_compounds",
        "eic_rank_by_max_intensity",
        "eic_search_by_mz_range",
        "eic_search_by_rt_range",
    }
)
LOCAL_WRITE_TOOLS = frozenset(
    {
        "write_report",
        "save_pca_figure",
        "save_eic_figure",
        "save_volcano_figure",
        "record_objective",
        "update_objective",
    }
)
```

`paper_search` は `EXTERNAL_NETWORK_TOOLS`（`policy.py:60`）に既に入っているため
`READ_ONLY_TOOLS` に加えてはいけない。加えると `network_mode` のゲートを迂回する。
`ingest_*` 4ツールも `KNOWLEDGE_MUTATION_TOOLS` のままにする。

- [ ] **Step 4: mcp_state_policy.py を同期する**

`mcp_state_policy.py:16-48` の `RULES` を差し替え。変更前の該当行:

```python
    "ms-data-parser::arf_re_pca": ToolStateRule(
        requires=("arf_dataset",), provides=("pca_result",), replay_safe=True
    ),
```
```python
    "ms-data-parser::arf_differential": ToolStateRule(requires=("arf_dataset",)),
    "ms-data-parser::save_pca_figure": ToolStateRule(requires=("pca_result",)),
    "ms-data-parser::eicaef_plot_chromatograms": ToolStateRule(
        provides=("eic_plot",), replay_safe=True
    ),
    "ms-data-parser::save_eic_figure": ToolStateRule(requires=("eic_plot",)),
```

変更後（`arf_re_pca` の3行を削除し、残りを以下に差し替え）:

```python
    # arf_differential は群指定引数に依存するため replay_safe にしない。無引数の
    # 再実行では同じ差次的結果を再現できず、別条件の図を「同じ結果」として
    # 保存・解釈してしまうため。
    "ms-data-parser::arf_differential": ToolStateRule(
        requires=("arf_dataset",), provides=("differential_result",)
    ),
    "ms-data-parser::arf_plot_volcano": ToolStateRule(
        requires=("differential_result",)
    ),
    "ms-data-parser::save_volcano_figure": ToolStateRule(
        requires=("differential_result",)
    ),
    "ms-data-parser::save_pca_figure": ToolStateRule(requires=("pca_result",)),
    "ms-data-parser::eic_plot_chromatograms": ToolStateRule(
        provides=("eic_plot",), replay_safe=True
    ),
    "ms-data-parser::eic_plot_compounds": ToolStateRule(
        provides=("eic_plot",), replay_safe=True
    ),
    "ms-data-parser::save_eic_figure": ToolStateRule(requires=("eic_plot",)),
```

`mcp_state_policy.py:59-70` の `indicates_missing_state` のマーカーを差し替え。変更前:

```python
    return any(
        marker in result_text
        for marker in (
            "先に eicaef_plot_chromatograms",
            "先に arf_parser",
            "PCA結果がありません",
            "データを読み込んでください",
        )
    )
```

変更後:

```python
    return any(
        marker in result_text
        for marker in (
            "先に eic_plot_chromatograms",
            "先に arf_parser",
            "先に arf_differential",
            "PCA結果がありません",
            "データを読み込んでください",
        )
    )
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd C:\Users\yuu18\Use-LLLM; python -m pytest tests/test_policy_server.py tests/test_mcp_state_policy.py -v`
Expected: PASS

- [ ] **Step 6: Use-LLLM 全テストで退行がないことを確認**

Run: `cd C:\Users\yuu18\Use-LLLM; python -m pytest -q`
Expected: 既存の失敗以外に新規失敗がないこと。失敗があれば本タスクの変更が原因か切り分ける

Run: `cd C:\Users\yuu18\Use-LLLM; python -m ruff check src tests`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
cd /c/Users/yuu18/Use-LLLM
git add src/use_lllm/core/policy.py src/use_lllm/core/mcp_state_policy.py tests/test_policy_server.py tests/test_mcp_state_policy.py
git commit -m "fix(policy): ツール分類と state 規則を ms-data-parser の実名へ同期

READ_ONLY_TOOLS が旧名 eicaef_* のままで、現行 EIC ツールと
arf_plot_volcano が UNKNOWN 扱い＝毎回承認待ちになり描画に到達できなかった。
撤去済みツール（arf_re_pca / pai2_get_top_metabolites 他）も削除した。
arf_differential は引数依存なので replay_safe にせず、volcano 系ツールが
requires する differential_result を provides するだけにした。"
```

---

## Task 7: ドキュメント更新

**Files:**
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\USAGE.md:105-107` 付近
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\README.md:14` 付近, `195-199` 付近
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\docs\output_format\arf.md:196` 付近
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\docs\output_format\pai2.md:73`
- Modify: `C:\Users\yuu18\Lipidmix_with_LLM\docs\HISTRY.md`

**Interfaces:**
- Consumes: Task 1〜6 の成果
- Produces: なし

- [ ] **Step 1: USAGE.md のツール表を更新**

`USAGE.md:105-107` の3行を差し替え。変更前:

```markdown
| `save_pca_figure` | 直近セッションの PCA 結果を PNG 化し `reports/figures/` に保存。 |
| `save_volcano_figure` | 直近の差次的解析(2群)を volcano プロット PNG として保存。 |
| `save_eic_figure` | ユーザーが明示的に保存を希望した場合だけ、直近のEICプロット情報を PNG 化し `reports/figures/<analysis_id>_eic.png` に保存。Use-LLLMではローカル書き込みとして承認が必要。 |
```

変更後:

```markdown
| `arf_plot_volcano` | 直近の2群差次的解析を `lipidmix.volcano.v1` の点列として返す（read-only・画像なし）。`up`/`down` は全件、`ns` は `max_points` まで等間隔で間引き、件数は `selection` に出る。 |
| `save_pca_figure` | ユーザーがPNGを明示的に希望した場合だけ、直近セッションの PCA 結果を `reports/figures/` に保存。通常の描画はクライアントUIに任せる。 |
| `save_volcano_figure` | ユーザーがPNGを明示的に希望した場合だけ、直近の差次的解析(2群)を volcano PNG として保存。通常の描画は `arf_plot_volcano` ＋クライアントUI。本PNGは間引き前の全特徴を描く。 |
| `save_eic_figure` | ユーザーが明示的に保存を希望した場合だけ、直近のEICプロット情報を PNG 化し `reports/figures/<analysis_id>_eic.png` に保存。Use-LLLMではローカル書き込みとして承認が必要。 |
```

- [ ] **Step 2: README.md を更新**

`README.md:14` の直後に1行追加:

```markdown
- `volcano_plot.py` - Builds the renderer-neutral `lipidmix.volcano.v1` scatter payload from the latest two-group differential result, keeping every `up`/`down` point and thinning only `ns` points.
```

`README.md:195-196` の2行を差し替え。変更前:

```markdown
- `save_pca_figure(analysis_id, title=None)` - Render the latest session PCA result to `reports/figures/<analysis_id>_pca.png` and return a relative path to embed in the report body as `![PCA](figures/<analysis_id>_pca.png)`.
- `save_volcano_figure(analysis_id, title=None)` - Render the latest two-group differential result (`arf_differential`) as a volcano plot to `reports/figures/<analysis_id>_volcano.png` and return a relative path to embed as `![volcano](figures/<analysis_id>_volcano.png)`.
```

変更後:

```markdown
- `arf_plot_volcano(max_points=3000, title=None)` - Return the latest two-group differential result as a renderer-neutral `lipidmix.volcano.v1` payload. Read-only, writes no file. `up`/`down` points are always complete; only `ns` points are thinned, and every count is reported in `selection`.
- `save_pca_figure(analysis_id, title=None)` - Only when the user explicitly requests PNG output, render the latest session PCA result to `reports/figures/<analysis_id>_pca.png` and return a relative path to embed as `![PCA](figures/<analysis_id>_pca.png)`.
- `save_volcano_figure(analysis_id, title=None)` - Only when the user explicitly requests PNG output, render the latest two-group differential result as a volcano plot to `reports/figures/<analysis_id>_volcano.png`. Unlike `arf_plot_volcano`, this draws every feature without thinning.
```

- [ ] **Step 3: docs/output_format/arf.md に volcano スキーマを追記**

`docs/output_format/arf.md` の volcano/differential 節（`:196` の段落の後）に追記:

```markdown
#### `arf_plot_volcano` の返り値（`lipidmix.volcano.v1`）

`arf_differential`（2群）の後に呼ぶ read-only ツール。画像は作らず、クライアントが
そのまま描ける散布図データを返す。

| フィールド | 意味 |
| --- | --- |
| `points[].feature` | 特徴量名（`differential` の `feature`） |
| `points[].log2fc` | log2 fold change（x 軸） |
| `points[].neg_log10_p` | `-log10(p)`（y 軸）。`q` ではなく **`p`** |
| `points[].sig` | `up` / `down` / `ns`。`q <= q_threshold` かつ `|log2fc| >= log2fc_threshold` で up/down |
| `thresholds` | 判定に使った `q` と `log2fc` のしきい値 |
| `render_hints.guides.x` | 縦破線の位置（`±log2fc_threshold`） |
| `render_hints.guides.y` | 横破線の位置（`-log10(q_threshold)`）。y 軸は `-log10(p)` なので目安であり有意判定と厳密には一致しない |
| `selection.total` | 元の点数（＝検定した特徴量数） |
| `selection.plotted` | 実際に返した点数 |
| `selection.significant_total` / `significant_plotted` | 有意点の総数と返した数。**常に一致する**（有意点は間引かない） |
| `selection.ns_total` / `ns_plotted` | `ns` 点の総数と返した数。間引きが起きるとここが乖離する |
| `selection.dropped_nonfinite` | `log2fc` か `p` が有限でなく描画対象外にした件数。**「有意でない」という意味ではない** |

`selection.plotted < selection.total` のとき、図は全点ではない。有意件数の判断は必ず
`selection.significant_total` を見ること（画面の点を数えてはいけない）。PNG が必要な
ときだけ `save_volcano_figure` を使う（こちらは間引き前の全特徴を描く）。
```

- [ ] **Step 4: docs/output_format/pai2.md の古い記述を削除**

`docs/output_format/pai2.md:73` の行を削除する。変更前:

```markdown
| `img_bytes` | PCA散布図のPNGバイト列。点=`ピーク`、色=`m/z` |
```

この行を削除する。PAI2 の PCA ツールは既に撤去済みで `img_bytes` を返す経路は存在しない。行の前後に PCA 前提の説明文が残っていれば併せて削除する。

- [ ] **Step 5: docs/HISTRY.md に開発ログを追記**

`docs/HISTRY.md` の先頭（最新エントリの位置。既存の並び順に従う）に追記:

```markdown
## 2026-08-07 全プロットのクライアント描画統一と PNG 明示要求化

- 問題: volcano と EIC オーバーレイが「構造化データを返しクライアントが描く」方針から
  外れていた。volcano はサーバ側に構造化経路が無く `volcano_note` が
  `save_volcano_figure`（PNG）を唯一の可視化手段として案内。EIC オーバーレイは
  webUI 側に `lipidmix.eic.multi.v1` のレンダラが無く描画されなかった。
- サーバ: `volcano_plot.py` を新設（`lipidmix.volcano.v1`。`up`/`down` は全件保持、
  `ns` のみ `(i*total)//quota` の等間隔で決定的に間引く）。ツール
  `arf_plot_volcano(max_points=3000, title=None)` を追加。`last_differential` に
  `n_a`/`n_b`/`q_threshold`/`log2fc_threshold` を保存し再計算を避けた。
- サーバ: `save_pca_figure` / `save_volcano_figure` の docstring を `save_eic_figure` と
  同じ「明示要求時のみ」書式へ統一。`arf_reader.py` CLI が `--output-plot` 未指定でも
  `pca_plot.png` を無条件生成していたのを止めた（分布プロットも同様）。
- webUI(Use-LLLM): `volcano-plot.js` を新設し `app.js` に `appendVolcanoPlot` を配線。
  `eic-plot.js` を single/multi の2スキーマ対応にし、multi では物質メタを hover に
  出して apex 注釈を返す。`policy.py` / `mcp_state_policy.py` の旧ツール名
  （`eicaef_*` / `arf_re_pca` 他）を実サーバ名へ同期した。
- 仮想PNG(inline base64 image)は表示段に採らない判断を設計書に記録。Plotly の
  インタラクティブ性と数値の可読性を失う代償が大きいため。
- 設計: `docs/superpowers/specs/2026-08-07-client-side-plot-rendering-design.md`
```

- [ ] **Step 6: 記述と実装の食い違いがないか確認**

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; grep -rn "img_bytes\|eicaef_" docs/ USAGE.md README.md`
Expected: `docs/superpowers/` 配下の過去の設計書・計画書のみに残る（履歴なので変更しない）。`USAGE.md` / `README.md` / `docs/output_format/` には残らない

- [ ] **Step 7: コミット**

```bash
cd /c/Users/yuu18/Lipidmix_with_LLM
git add USAGE.md README.md docs/output_format/arf.md docs/output_format/pai2.md docs/HISTRY.md
git commit -m "docs: arf_plot_volcano を追記し PNG の位置づけを明示要求のみに統一

output_format/arf.md に lipidmix.volcano.v1 のフィールド定義と、
selection.plotted < total のとき画面が全点でないという注意を追加。
pai2.md の img_bytes（撤去済み PAI2 PCA の記述）を削除。"
```

---

## Task 8: 実データ webUI 検証

**Files:**
- 変更なし（検証のみ）

**Interfaces:**
- Consumes: Task 1〜7 の全成果

- [ ] **Step 1: Use-LLLM を起動する**

Run: `cd C:\Users\yuu18\Use-LLLM; .\Start-WebUI.ps1`
（または `python -m use_lllm.cli`）
Expected: ローカル URL が表示され、`ms-data-parser` サーバに接続される

- [ ] **Step 2: データセットを読み込む**

チャットで `load_dataset` を実データフォルダに対して実行させる。
Expected: 群構成・脂質クラス・極性が返る

- [ ] **Step 3: PCA が Plotly で描画されることを確認**

Expected: `chat-plot-card` に散布図が出る。hover で sample 名と PC1/PC2 が出る。
`reports/figures/` に PNG が増えていないこと

- [ ] **Step 4: EIC 単一スポットが描画されることを確認**

`eic_search_by_mz_range` で spot_id を得て `eic_plot_chromatograms` を実行させる。
Expected: 複数サンプルの折れ線が出る。承認ダイアログで止まらない（Task 6 の効果）

- [ ] **Step 5: EIC 複数物質オーバーレイが描画されることを確認**

`eic_plot_compounds` を `ontologies=["PC"]` 等で実行させる。
Expected: 複数物質の折れ線が重なって出る。凡例が右側の縦並びになる。apex に物質名の
注釈が出る。PNG は増えない

- [ ] **Step 6: volcano が描画されることを確認**

`arf_preprocess` → `arf_differential(group_a=..., group_b=...)` → `arf_plot_volcano` を
実行させる。
Expected: up=赤 / down=青 / ns=灰 の散布図、しきい値の破線、hover に feature 名。
見出しに `<plotted> / <total> points` と有意件数が出る

- [ ] **Step 7: PNG が1枚も作られていないことを確認**

Run: `cd C:\Users\yuu18\Lipidmix_with_LLM; Get-ChildItem -Recurse -Filter *.png reports 2>$null | Select-Object FullName, LastWriteTime`
Expected: 本セッション中に作られた PNG が存在しない（既存の古い PNG のみ）

- [ ] **Step 8: 明示要求時だけ PNG が作られることを確認**

チャットで「いまの volcano を PNG で reports に保存して」と依頼する。
Expected: `save_volcano_figure` が承認要求を出し、承認後に
`reports/figures/<analysis_id>_volcano.png` が作られる

- [ ] **Step 9: 結果を記録する**

`C:\Users\yuu18\Use-LLLM\task.md` に検証結果を既存の書式で追記し、コミットする。

```bash
cd /c/Users/yuu18/Use-LLLM
git add task.md
git commit -m "docs(task): クライアント描画統一の webUI 検証結果を記録"
```

---

## 完了条件

- [ ] `cd C:\Users\yuu18\Lipidmix_with_LLM; python -m unittest discover -s tests` が PASS
- [ ] `cd C:\Users\yuu18\Use-LLLM; python -m pytest -q` に新規失敗なし
- [ ] `cd C:\Users\yuu18\Use-LLLM; python -m ruff check src tests` がエラーなし
- [ ] `node --check` が `app.js` / `eic-plot.js` / `volcano-plot.js` / `pca-plot.js` で通る
- [ ] `node tests/test_general_{markdown,pca_plot,eic_plot,volcano_plot}.cjs` が全て ok
- [ ] webUI で PCA / EIC 単一 / EIC オーバーレイ / volcano の4つが Plotly 描画される
- [ ] 上記の過程で PNG が生成されない。明示依頼時のみ生成される
