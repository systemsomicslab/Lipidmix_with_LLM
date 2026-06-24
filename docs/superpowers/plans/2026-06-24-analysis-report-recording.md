# 解析・解釈レポート記録機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MCPサーバに、解析・解釈レポートを `reports/<analysis_id>.md` に上書き保存し、読み戻し・一覧・PCA図保存を行う4ツールを追加する。

**Architecture:** レポートは frontmatter＋自由記述本文の md。書き出しは既存 `knowledge_store.write_note()` を再利用。`server.py` に薄い純ロジックヘルパー（書き込み先解決・メタ組立・PCA図データ整形）＋MCPツールを足す。書き込み先は「解析フォルダ(`DATA_DIR`)配下 `reports/` → 不可なら `LIPIDMIX_REPORTS_DIR`（既定 `<project>/reports`）」の順にフォールバック。`save_pca_figure` をパーサ非依存にするため各PCA実行時に `session.last_pca_plot` を埋める。

**Tech Stack:** Python, FastMCP, matplotlib, 既存 `knowledge_store`、unittest。

参照スペック: `docs/superpowers/specs/2026-06-24-analysis-report-recording-design.md`

---

## File Structure

- Modify: `server.py` — ヘルパー群（`_report_dir_candidates` / `_dir_is_writable` / `_first_writable_dir` / `_resolve_report_dir` / `_build_report_meta` / `_pca_scatter_arrays` / `_remember_arf_pca_plot`）と4ツール（`write_report` / `read_report` / `list_reports` / `save_pca_figure`）、`AnalysisSession` への `last_pca_plot` 追加と各PCA経路への配線。
- Create: `tests/test_report_tools.py` — 上記ヘルパー/ツールのユニットテスト（`import server` して直接呼ぶ。`@mcp.tool()` は関数をそのまま返すため直呼び可能）。
- Modify: `README.md`, `docs/HISTRY.md`, `.env.example` — ドキュメント更新。

**テスト方針メモ:** `@mcp.tool()` で装飾された関数は既存コードでも直接呼ばれている（`load_dataset` が `arf_parser` を直呼び）。よってテストは `server.write_report(...)` のように直接呼べる。matplotlib はヘッドレス実行のためテスト先頭で `matplotlib.use("Agg")` を `import server` の前に設定する。

---

## Task 1: 書き込み先解決とメタ組立のヘルパー（純ロジック）

**Files:**
- Modify: `server.py`（`BASE_DIR` 定義の近く、`_state_dir` の後ろにヘルパーを追加）
- Test: `tests/test_report_tools.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_report_tools.py` を新規作成:

```python
import os
import unittest
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # import server が pyplot を読む前にヘッドレス指定

import server
import knowledge_store


class WriteLocationHelpers(unittest.TestCase):
    def test_dir_is_writable_true_for_new_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "reports"
            self.assertTrue(server._dir_is_writable(target))
            self.assertTrue(target.is_dir())

    def test_dir_is_writable_false_when_parent_is_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            self.assertFalse(server._dir_is_writable(blocker / "reports"))

    def test_first_writable_dir_skips_unwritable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            bad = blocker / "reports"
            good = Path(d) / "ok"
            self.assertEqual(server._first_writable_dir([bad, good]), good)

    def test_first_writable_dir_raises_when_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            with self.assertRaises(OSError):
                server._first_writable_dir([blocker / "a", blocker / "b"])

    def test_build_report_meta_shape(self):
        meta = server._build_report_meta("a-1", "DS", "draft", ["s1", "s2"])
        self.assertEqual(meta["type"], "report")
        self.assertEqual(meta["analysis_id"], "a-1")
        self.assertEqual(meta["dataset"], "DS")
        self.assertEqual(meta["status"], "draft")
        self.assertEqual(meta["knowledge_refs"], ["s1", "s2"])
        self.assertRegex(meta["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_build_report_meta_defaults_refs_to_empty_list(self):
        meta = server._build_report_meta("a-1", "DS", "draft", None)
        self.assertEqual(meta["knowledge_refs"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m unittest tests.test_report_tools -v`
Expected: FAIL（`AttributeError: module 'server' has no attribute '_dir_is_writable'` 等）

- [ ] **Step 3: ヘルパーを実装**

`server.py` の `_state_dir(...)` 定義の直後（`KNOWLEDGE_DIR = ...` の前）に追加:

```python
def _dir_is_writable(directory: Path) -> bool:
    """ディレクトリを作成し、プローブファイルの書き込み/削除で書き込み可否を判定する。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _first_writable_dir(candidates: list[Path]) -> Path:
    """候補を順に試し、最初に書き込み可能なディレクトリを返す。無ければ OSError。"""
    for cand in candidates:
        if _dir_is_writable(cand):
            return cand
    raise OSError(
        "レポートの書き込み先がありません: "
        + ", ".join(str(c) for c in candidates)
        + "（LIPIDMIX_REPORTS_DIR に書き込み可能なパスを設定してください）"
    )
```

そして `server.py` 冒頭の `from datetime import date` が無ければ import 群に追加（既存ツールは関数内で `from datetime import date` している。ここではモジュール先頭に追加してよい）。`_build_report_meta` を上記ヘルパーの後に追加:

```python
from datetime import date as _date


def _report_dir_candidates() -> list[Path]:
    """レポート書き込み先候補。解析フォルダ配下 reports/ を優先、次に退避先。"""
    override = os.environ.get("LIPIDMIX_REPORTS_DIR")
    fallback = Path(override).expanduser() if override else BASE_DIR / "reports"
    return [DATA_DIR / "reports", fallback]


def _resolve_report_dir() -> Path:
    """書き込み可能なレポートディレクトリを返す（解析フォルダ→退避先）。"""
    return _first_writable_dir(_report_dir_candidates())


def _build_report_meta(
    analysis_id: str, dataset: str, status: str, knowledge_refs: list[str] | None
) -> dict:
    """レポートの frontmatter メタを組み立てる。"""
    return {
        "type": "report",
        "analysis_id": analysis_id,
        "dataset": dataset,
        "date": _date.today().isoformat(),
        "status": status,
        "knowledge_refs": knowledge_refs or [],
    }
```

注: `_report_dir_candidates` / `_resolve_report_dir` は `DATA_DIR` グローバルを参照する。`DATA_DIR` は `_build_report_meta` より後の行（`from data_config import get_data_dir` / `DATA_DIR = get_data_dir()`）で定義されるため、これらの**関数定義**は `DATA_DIR` 定義より前でも問題ない（関数は呼び出し時に解決する）。ただし読みやすさのため、これらヘルパーは `DATA_DIR = get_data_dir()` の行より後ろに置いてもよい。本タスクでは `_dir_is_writable` / `_first_writable_dir` / `_build_report_meta` を `_state_dir` 直後に、`_report_dir_candidates` / `_resolve_report_dir` を `DATA_DIR = get_data_dir()` の直後に置くこと。

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m unittest tests.test_report_tools -v`
Expected: PASS（6件）

- [ ] **Step 5: コミット**

```bash
git add server.py tests/test_report_tools.py
git commit -m "feat: report write-location and metadata helpers"
```

---

## Task 2: write_report / read_report / list_reports ツール

**Files:**
- Modify: `server.py`（`ingest_reject` ツールの後ろ、`# --- ステート保持クラス ---` の前あたりにツールを追加）
- Test: `tests/test_report_tools.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_report_tools.py` にクラスを追加:

```python
class ReportToolTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved_data_dir = server.DATA_DIR
        server.DATA_DIR = self.tmp
        self._saved_env = os.environ.pop("LIPIDMIX_REPORTS_DIR", None)

    def tearDown(self):
        server.DATA_DIR = self._saved_data_dir
        if self._saved_env is not None:
            os.environ["LIPIDMIX_REPORTS_DIR"] = self._saved_env
        self._tmp.cleanup()

    def test_write_report_creates_file_with_frontmatter_and_body(self):
        msg = server.write_report("a-1", "NEG / DS", "## 目的\nグループ比較")
        path = self.tmp / "reports" / "a-1.md"
        self.assertTrue(path.is_file())
        self.assertIn("reports", msg)
        meta, body = knowledge_store.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(meta["type"], "report")
        self.assertEqual(meta["analysis_id"], "a-1")
        self.assertEqual(meta["status"], "draft")
        self.assertIn("## 目的", body)

    def test_write_report_overwrites_on_second_call(self):
        server.write_report("a-1", "DS", "## 目的\n古い本文")
        server.write_report("a-1", "DS", "## 目的\n新しい本文", status="final")
        path = self.tmp / "reports" / "a-1.md"
        meta, body = knowledge_store.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "final")
        self.assertIn("新しい本文", body)
        self.assertNotIn("古い本文", body)

    def test_read_report_round_trips(self):
        server.write_report("a-1", "DS", "## 結論\nXがYより高い")
        text = server.read_report("a-1")
        self.assertIn("## 結論", text)
        self.assertIn("XがYより高い", text)

    def test_read_report_missing_returns_guidance(self):
        text = server.read_report("does-not-exist")
        self.assertIn("見つかりません", text)

    def test_list_reports_shows_written_report(self):
        server.write_report("a-1", "DS", "## 目的\nx", status="final")
        listing = server.list_reports()
        self.assertIn("a-1", listing)
        self.assertIn("status=final", listing)

    def test_list_reports_empty(self):
        listing = server.list_reports()
        self.assertIn("まだありません", listing)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m unittest tests.test_report_tools.ReportToolTests -v`
Expected: FAIL（`AttributeError: module 'server' has no attribute 'write_report'`）

- [ ] **Step 3: ツールを実装**

`server.py` の `ingest_reject(...)` 関数の後（`# --- ステート保持クラス ---` コメントの直前）に追加:

```python
# --- 解析・解釈レポート（reports/<analysis_id>.md） ---
@mcp.tool()
def write_report(
    analysis_id: str,
    dataset: str,
    body: str,
    status: str = "draft",
    knowledge_refs: list[str] | None = None,
) -> str:
    """解析・解釈レポートを reports/<analysis_id>.md に上書き保存する（成果物＋記録）。

    body は frontmatter を含まない Markdown 本文。推奨セクション見出し:
    `## 目的` / `## 実施した解析` / `## 主要な所見` / `## 解釈` /
    `## 注意点・コンフリクト` / `## 結論`。所見が増えたら本文を作り直して再度呼ぶ
    （ファイルは毎回上書き）。引用した knowledge/playbook の slug を knowledge_refs に渡す。
    書き込み先は解析フォルダ配下 reports/、不可なら LIPIDMIX_REPORTS_DIR（既定 <project>/reports）。
    """
    slug = knowledge_store.make_slug(analysis_id)
    reports_dir = _resolve_report_dir()
    meta = _build_report_meta(analysis_id, dataset, status, knowledge_refs)
    path = knowledge_store.write_note(reports_dir, slug, meta, body)
    return f"レポートを保存: {path}（status={status}）。read_report('{analysis_id}') で読み戻せます。"


@mcp.tool()
def read_report(analysis_id: str) -> str:
    """過去レポートを読み戻す（解析フォルダ→退避先の順に探索）。セッション継続用。"""
    slug = knowledge_store.make_slug(analysis_id)
    for directory in _report_dir_candidates():
        path = directory / f"{slug}.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return f"レポートが見つかりません: {analysis_id}（write_report で作成してください）"


@mcp.tool()
def list_reports() -> str:
    """既存レポートの1行索引（analysis_id / date / status）を返す。"""
    lines = ["# レポート一覧"]
    seen: set[str] = set()
    for directory in _report_dir_candidates():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            meta, _body = knowledge_store.parse_frontmatter(path.read_text(encoding="utf-8"))
            if meta.get("type") != "report":
                continue
            aid = str(meta.get("analysis_id", path.stem))
            if aid in seen:
                continue
            seen.add(aid)
            lines.append(
                f"- {aid} | date={meta.get('date', '?')} | status={meta.get('status', '?')}"
            )
    if len(lines) == 1:
        lines.append("（レポートはまだありません）")
    return "\n".join(lines)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m unittest tests.test_report_tools.ReportToolTests -v`
Expected: PASS（6件）

- [ ] **Step 5: コミット**

```bash
git add server.py tests/test_report_tools.py
git commit -m "feat: add write_report / read_report / list_reports tools"
```

---

## Task 3: PCA図データの保持と整形ヘルパー

**Files:**
- Modify: `server.py`（`AnalysisSession.__init__` に `self.last_pca_plot = None` を追加。`_format_pca_plot_block` の近くに2ヘルパーを追加）
- Test: `tests/test_report_tools.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_report_tools.py` にクラスを追加:

```python
class PcaPlotHelperTests(unittest.TestCase):
    def test_pca_scatter_arrays_extracts_points(self):
        plot = {
            "title": "T",
            "x_label": "PC1 (50.00%)",
            "y_label": "PC2 (30.00%)",
            "points": [
                {"x": 1.0, "y": 2.0, "label": "s1"},
                {"x": -1.0, "y": 0.5, "label": "s2"},
            ],
        }
        xs, ys, labels, x_label, y_label, title = server._pca_scatter_arrays(plot)
        self.assertEqual(xs, [1.0, -1.0])
        self.assertEqual(ys, [2.0, 0.5])
        self.assertEqual(labels, ["s1", "s2"])
        self.assertEqual(x_label, "PC1 (50.00%)")
        self.assertEqual(title, "T")

    def test_remember_arf_pca_plot_builds_session_state(self):
        saved = server.session.last_pca_plot
        try:
            server._remember_arf_pca_plot(
                {"components": [[1.0, 2.0], [3.0, 4.0]],
                 "explained_variance_ratio": [0.5, 0.3]},
                ["s1", "s2"],
                "PCA Score Plot (x.arf)",
            )
            plot = server.session.last_pca_plot
            self.assertEqual(plot["title"], "PCA Score Plot (x.arf)")
            self.assertEqual(plot["points"][0], {"x": 1.0, "y": 2.0, "label": "s1"})
            self.assertIn("50.00%", plot["x_label"])
        finally:
            server.session.last_pca_plot = saved
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m unittest tests.test_report_tools.PcaPlotHelperTests -v`
Expected: FAIL（`AttributeError: module 'server' has no attribute '_pca_scatter_arrays'`）

- [ ] **Step 3: 実装**

`server.py` の `AnalysisSession.__init__` の末尾（`self.current_tag_directory = None` の後）に追加:

```python
        self.last_pca_plot = None  # 直近PCAの描画用データ（save_pca_figure が参照）
```

`_format_pca_plot_block(...)` 関数の直前に2ヘルパーを追加:

```python
def _pca_scatter_arrays(plot: dict):
    """session.last_pca_plot から散布図用の配列とラベルを取り出す（純ロジック）。"""
    points = plot.get("points", [])
    xs = [float(p["x"]) for p in points]
    ys = [float(p["y"]) for p in points]
    labels = [p.get("label") for p in points]
    return (
        xs, ys, labels,
        plot.get("x_label", "PC1"),
        plot.get("y_label", "PC2"),
        plot.get("title", "PCA"),
    )


def _remember_arf_pca_plot(pca_result: dict, sample_names: list[str], title: str) -> None:
    """ARF系PCAのサンプル別スコアを session.last_pca_plot に保存する。"""
    coords = pca_result.get("components", [])
    evr = pca_result["explained_variance_ratio"]
    points = []
    for i, name in enumerate(sample_names):
        if i < len(coords) and len(coords[i]) >= 2:
            points.append({"x": float(coords[i][0]), "y": float(coords[i][1]), "label": name})
    session.last_pca_plot = {
        "title": title,
        "x_label": f"PC1 ({evr[0] * 100:.2f}%)",
        "y_label": f"PC2 ({evr[1] * 100:.2f}%)",
        "points": points,
    }
```

注: `_remember_arf_pca_plot` は `session` グローバルを参照する。`session = AnalysisSession()` は `_format_pca_plot_block` より後の行で生成されるが、関数本体は呼び出し時に解決するため定義順は問題ない。

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m unittest tests.test_report_tools.PcaPlotHelperTests -v`
Expected: PASS（2件）

- [ ] **Step 5: コミット**

```bash
git add server.py tests/test_report_tools.py
git commit -m "feat: PCA plot data retention and scatter shaping helpers"
```

---

## Task 4: 各PCA経路で last_pca_plot を配線

**Files:**
- Modify: `server.py`（`arf_parser`、`arf_re_pca`、`AnalysisSession.run_pca`）

このタスクは実データ依存の配線で、ユニットテストは Task 3 の `_remember_arf_pca_plot` 直呼びでカバー済み。ここでは既存スイートが壊れないことを確認する。

- [ ] **Step 1: arf_parser に配線を追加**

`arf_parser` 内、`plot_instruction_text = _format_pca_plot_block(...)` の呼び出し直後に1行追加:

```python
        _remember_arf_pca_plot(
            pca_result, sample_names,
            title=f"PCA Score Plot ({Path(file_path).name})",
        )
```

- [ ] **Step 2: arf_re_pca に配線を追加**

`arf_re_pca` 内でも `_format_pca_plot_block(...)` を呼んでいる箇所（`sample_names` と `pca_result` が確定した後）の直後に同様に追加:

```python
        _remember_arf_pca_plot(
            pca_result, sample_names,
            title="PCA Score Plot (arf_re_pca)",
        )
```

（`arf_re_pca` 内の正確な変数名は実コードで確認すること。`_format_pca_plot_block(pca_result, sample_names, ...)` に渡している変数をそのまま使う。）

- [ ] **Step 3: AnalysisSession.run_pca に pai2 用の配線を追加**

`AnalysisSession.run_pca` の `self.last_pca_summary = summary` の直後に追加:

```python
        ev = summary.get("explained_variance", {}) if isinstance(summary, dict) else {}
        coords = pca_result
        points = []
        try:
            if getattr(coords, "shape", (0, 0))[1] >= 2:
                for i in range(len(coords)):
                    points.append({"x": float(coords[i][0]), "y": float(coords[i][1]), "label": None})
        except (IndexError, TypeError):
            points = []
        self.last_pca_plot = {
            "title": "PCA (pai2 peak-level)",
            "x_label": f"PC1 ({ev.get('PC1', '')})",
            "y_label": f"PC2 ({ev.get('PC2', '')})",
            "points": points,
        }
```

- [ ] **Step 4: 既存スイートが壊れないことを確認**

Run: `python -m unittest discover -s tests -t . -v`
Expected: PASS（既存テスト＋ここまでの新規テストすべて）

- [ ] **Step 5: コミット**

```bash
git add server.py
git commit -m "feat: populate session.last_pca_plot from arf and pai2 PCA paths"
```

---

## Task 5: save_pca_figure ツール

**Files:**
- Modify: `server.py`（Task 2 で追加したレポートツール群の後ろに追加）
- Test: `tests/test_report_tools.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_report_tools.py` にクラスを追加:

```python
class SavePcaFigureTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved_data_dir = server.DATA_DIR
        server.DATA_DIR = self.tmp
        self._saved_env = os.environ.pop("LIPIDMIX_REPORTS_DIR", None)
        self._saved_plot = server.session.last_pca_plot

    def tearDown(self):
        server.DATA_DIR = self._saved_data_dir
        server.session.last_pca_plot = self._saved_plot
        if self._saved_env is not None:
            os.environ["LIPIDMIX_REPORTS_DIR"] = self._saved_env
        self._tmp.cleanup()

    def test_save_pca_figure_writes_png_and_returns_relpath(self):
        server.session.last_pca_plot = {
            "title": "T", "x_label": "PC1", "y_label": "PC2",
            "points": [
                {"x": 1.0, "y": 2.0, "label": "s1"},
                {"x": -1.0, "y": 0.5, "label": "s2"},
            ],
        }
        msg = server.save_pca_figure("a-1")
        png = self.tmp / "reports" / "figures" / "a-1_pca.png"
        self.assertTrue(png.is_file())
        self.assertIn("figures/a-1_pca.png", msg)

    def test_save_pca_figure_guidance_when_no_plot(self):
        server.session.last_pca_plot = None
        msg = server.save_pca_figure("a-1")
        self.assertIn("PCA", msg)
        self.assertFalse((self.tmp / "reports" / "figures").exists())
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m unittest tests.test_report_tools.SavePcaFigureTests -v`
Expected: FAIL（`AttributeError: module 'server' has no attribute 'save_pca_figure'`）

- [ ] **Step 3: ツールを実装**

`server.py` の `list_reports()` ツールの後ろに追加:

```python
@mcp.tool()
def save_pca_figure(analysis_id: str, title: str | None = None) -> str:
    """直近のセッションPCA結果からPNGを生成し reports/figures/ に保存する。

    arf_parser / arf_re_pca / pai2_parser 等でPCAを実行した後に呼ぶ。返り値の相対パスを
    write_report の本文に `![PCA](figures/<analysis_id>_pca.png)` として埋め込める。
    """
    plot = getattr(session, "last_pca_plot", None)
    if not plot or not plot.get("points"):
        return "先に arf_parser / arf_re_pca / pai2_parser 等でPCAを実行してください（PCA結果がありません）。"

    slug = knowledge_store.make_slug(analysis_id)
    reports_dir = _resolve_report_dir()
    figures_dir = reports_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    xs, ys, labels, x_label, y_label, plot_title = _pca_scatter_arrays(plot)
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(xs, ys, alpha=0.6)
    for x, y, label in zip(xs, ys, labels):
        if label:
            ax.annotate(str(label), (x, y), fontsize=8)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title or plot_title)
    out_path = figures_dir / f"{slug}_pca.png"
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)

    rel = f"figures/{out_path.name}"
    return f"PCA図を保存: {out_path}\n本文に ![PCA]({rel}) で埋め込めます。"
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m unittest tests.test_report_tools.SavePcaFigureTests -v`
Expected: PASS（2件）

- [ ] **Step 5: コミット**

```bash
git add server.py tests/test_report_tools.py
git commit -m "feat: add save_pca_figure tool"
```

---

## Task 6: ドキュメント更新と最終確認

**Files:**
- Modify: `README.md`, `docs/HISTRY.md`, `.env.example`

- [ ] **Step 1: README にツールを追記**

`README.md` の MCP ツール一覧（`eicaef_search_by_rt_range` の項目の後、または Objective lifecycle 節の後ろ）に追加:

```markdown
Report recording (see `docs/HISTRY.md`):

- `write_report(analysis_id, dataset, body, status="draft", knowledge_refs=None)` - Overwrite the analysis/interpretation report at `<analysis-folder>/reports/<analysis_id>.md` (falls back to `LIPIDMIX_REPORTS_DIR`, default `<project>/reports`, when the data folder is read-only). Keyed by `analysis_id` to the objective record.
- `read_report(analysis_id)` - Read back a past report for session continuity.
- `list_reports()` - One-line index (analysis_id / date / status) of existing reports.
- `save_pca_figure(analysis_id, title=None)` - Render the latest session PCA result to `reports/figures/<analysis_id>_pca.png` and return a relative path to embed in the report.
```

- [ ] **Step 2: .env.example に環境変数を追記**

`.env.example` に1行追加（既存の `LIPIDMIX_*` 変数の並びに合わせる）:

```bash
# レポート/図の退避先（解析フォルダが書き込み不可のとき使用。未設定時は <project>/reports）
LIPIDMIX_REPORTS_DIR=
```

- [ ] **Step 3: HISTRY に設計記録を追記**

`docs/HISTRY.md` の先頭付近（最新エントリの位置、既存の日付見出し書式に合わせる）に追記:

```markdown
## 2026-06-24 解析・解釈レポート記録機能

- `reports/<analysis_id>.md`（解析フォルダ配下、不可なら `LIPIDMIX_REPORTS_DIR`）に frontmatter＋推奨セクション本文を上書き保存。objective レコードと同一 `analysis_id` で突合。
- ツール: `write_report` / `read_report` / `list_reports` / `save_pca_figure`。書き出しは `knowledge_store.write_note` を再利用。
- `save_pca_figure` のため各PCA経路（arf_parser / arf_re_pca / pai2 の run_pca）で `session.last_pca_plot` を保持。
- 設計: `docs/superpowers/specs/2026-06-24-analysis-report-recording-design.md`、計画: `docs/superpowers/plans/2026-06-24-analysis-report-recording.md`。
```

- [ ] **Step 4: 全テストを実行**

Run: `python -m unittest discover -s tests -t . -v`
Expected: PASS（既存＋新規 test_report_tools すべて。失敗ゼロ）

- [ ] **Step 5: コミット**

```bash
git add README.md docs/HISTRY.md .env.example
git commit -m "docs: document report recording tools and LIPIDMIX_REPORTS_DIR"
```

---

## Self-Review メモ（計画作成者による確認）

- **Spec coverage:** データモデル(Task1,2) / 書き込み先解決(Task1) / 上書き(Task2) / 4ツール(Task2,5) / last_pca_plot配線(Task3,4) / エラー処理（書き込み不可=Task1の例外、未作成=Task2の案内、PCA未実行=Task5の案内、make_slug安全化=Task2,5） / テスト(各Task) / ドキュメント(Task6) — すべて対応タスクあり。
- **型整合:** `session.last_pca_plot` の dict 形 `{title, x_label, y_label, points:[{x,y,label}]}` は Task3定義・Task4書込・Task5消費で一致。`_pca_scatter_arrays` / `_remember_arf_pca_plot` / `_build_report_meta` / `_resolve_report_dir` の名称は全タスクで一致。
- **スコープ外:** `[[link]]` 索引・セクション単位追記・PCA以外の図はスペック通り除外。
