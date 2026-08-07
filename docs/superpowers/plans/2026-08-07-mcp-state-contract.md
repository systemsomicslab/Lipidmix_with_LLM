# MCP 状態契約の汎用化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 汎用 MCP クライアント（Use-LLLM）からドメイン特化サーバ（ms-data-parser）固有の知識を完全に除去し、`missing_state` エンベロープと MCP 標準 annotations の2つだけを契約として、状態喪失の検知と復旧を成立させる。

**Architecture:** サーバは状態不足を「本文に載せた JSON エンベロープ」で伝え、自分のツール特性を MCP 標準 `ToolAnnotations` で宣言する。クライアントはエンベロープを読み、`required_tools` に名指しされたツールを**セッション履歴に記録済みの引数のまま**再実行して復旧し、本命を1回だけリトライする。復旧できなければ `message` を LLM へ渡す（Claude Desktop と同じ振る舞い）。承認要否も annotations 単独で決める。

**Tech Stack:** Python 3.14 / `mcp` 1.27（サーバ, `mcp.server.fastmcp.FastMCP`）/ `mcp` 1.28（クライアント）/ pytest / unittest / ruff

**設計書:** `docs/superpowers/specs/2026-08-07-mcp-state-contract-design.md`

## Global Constraints

- 対象リポジトリは2つ。**サーバ = `C:\Users\yuu18\Lipidmix_with_LLM`**、**クライアント = `C:\Users\yuu18\Use-LLLM`**（別 git リポジトリ。別々にコミットする）。
- ブランチは既存の未マージブランチ上で続ける。サーバ = `fix/parser-state-isolation`、クライアント = `fix/tool-catalog-completeness`。本作業はどちらもその上に積む。
- テスト実行コマンドはリポジトリごとに違う。サーバ = `python -m pytest tests/ -q`（システム Python）。クライアント = `.venv/Scripts/python.exe -m pytest tests/ -q`。
- クライアントは ruff 管理下。変更後に `.venv/Scripts/python.exe -m ruff check src tests` と `ruff format` を通す。**既存の未整形ファイル `src/use_lllm/core/mcp_state_policy.py` と `tests/test_mcp_state_policy.py` は本計画で削除するので触らなくてよい。**
- サーバ側リポジトリに ruff は入っていない。`python -m compileall -q` で構文だけ確認する。
- **クライアントに ms-data-parser のツール名を書いてはいけない。** テストのフィクスチャは例外（契約の検証に実名が要る）。
- annotations の判定は必ず `is True` / `is False` で行う。`ToolDescription.annotations` は `model_dump(by_alias=True)` の結果で、未設定フィールドが `None` として入るため、真偽値の truthiness 判定は誤る。
- `required_tools` は **OR の代替候補**。AND チェーンではない。
- コミットメッセージ末尾に `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` を付ける。push はしない。
- 既知の別件: `tests/test_server_registration.py::test_tool_count_is_stable` は本計画の着手前から失敗している（`39` を期待、実際は40）。Task 6 で構造的に直す。

## 実行順序の制約

サーバ（Task 1-6）を先に完了させてから、クライアント（Task 7-10）に着手する。逆順にするとクライアントが annotations 単独判定になった時点で、まだ annotations を持たない ms-data-parser の全ツールが UNKNOWN（＝毎回承認待ち）に落ち、ユーザーの実環境が使えなくなる。

## File Structure

**サーバ（Lipidmix_with_LLM）**

| ファイル | 責務 |
| --- | --- |
| `mcp_errors.py`（新規） | エラーエンベロープの生成のみ。stdlib だけに依存する leaf |
| `tools_arf.py` / `tools_pai2.py` / `tools_reports.py`（変更） | 状態不足13箇所を `mcp_errors.missing_state()` へ置換 |
| 全 `tools_*.py`（変更） | `@mcp.tool()` に `ToolAnnotations` を付与 |
| `docs/output_format/core.md`（変更） | 契約の定義を追加 |
| `tests/test_mcp_errors.py`（新規） | エンベロープ生成と13箇所の適用を検証 |
| `tests/test_tool_annotations.py`（新規） | 全40ツールの annotations を固定 |

**クライアント（Use-LLLM）**

| ファイル | 責務 |
| --- | --- |
| `src/use_lllm/core/tool_result_contract.py`（新規） | エンベロープの解釈のみ。純関数 |
| `src/use_lllm/core/policy.py`（変更） | annotations 単独の安全クラス判定へ |
| `src/use_lllm/core/mcp_registry.py`（変更） | `is_replay_safe()` を追加 |
| `src/use_lllm/core/mcp_client.py`（変更） | enforcement をツール発見後へ移動 |
| `src/use_lllm/general/agent_loop.py`（変更） | 事前リプレイを事後リカバリへ置換 |
| `src/use_lllm/core/mcp_state_policy.py`（**削除**） | ─ |
| `tests/test_tool_result_contract.py`（新規） | エンベロープ解釈の全経路 |
| `tests/test_approval_snapshot.py`（新規） | 承認挙動の回帰スナップショット |
| `tests/test_state_recovery.py`（新規） | リカバリの4経路 |
| `tests/test_mcp_state_policy.py`（**削除**） | ─ |

---

### Task 1: 承認挙動のスナップショットを先に固定する（クライアント）

承認経路を触る前に、現在の分類を凍結する。これが「一気に全部やる」の安全弁。この時点では実装を変えないので、テストは**現行実装に対して green** になる。

**Files:**
- Create: `C:\Users\yuu18\Use-LLLM\tests\test_approval_snapshot.py`

**Interfaces:**
- Consumes: `use_lllm.core.policy.classify_tool`, `ToolSafety`
- Produces: `CURRENT_CLASSIFICATION`（40ツール名 → ToolSafety の dict）。Task 7 がこの表を再利用する。

- [ ] **Step 1: スナップショットテストを書く**

```python
"""承認挙動の回帰スナップショット。

ms-data-parser の40ツールについて、現在の分類を凍結する。annotations 単独判定へ
移行しても、ここに書かれた分類が再現されなければならない。意図した差分は
EXPECTED_CHANGES に明記し、それ以外の変化はすべて回帰として落とす。

このフィクスチャに ms-data-parser の実名が並ぶのは意図的。契約が実サーバに対して
成立することを検証するのがこのテストの目的であり、製品コード側に実名は持たない。
"""

from __future__ import annotations

import unittest

from use_lllm.core.policy import ToolSafety, classify_tool

READ_ONLY = ToolSafety.READ_ONLY
LOCAL_WRITE = ToolSafety.LOCAL_WRITE
EXTERNAL = ToolSafety.EXTERNAL_NETWORK
MUTATION = ToolSafety.KNOWLEDGE_MUTATION

CURRENT_CLASSIFICATION: dict[str, ToolSafety] = {
    "arf_list_tags": READ_ONLY,
    "arf_list_classes": READ_ONLY,
    "arf_list_sample_roles": READ_ONLY,
    "arf_exclude": READ_ONLY,
    "arf_preprocess": READ_ONLY,
    "arf_pca_preprocessed": READ_ONLY,
    "arf_parser": READ_ONLY,
    "arf_differential": READ_ONLY,
    "arf_plot_volcano": READ_ONLY,
    "arf2_parser": READ_ONLY,
    "arf2_annotate_identities": READ_ONLY,
    "list_data_files": READ_ONLY,
    "load_dataset": READ_ONLY,
    "dcl_parser": READ_ONLY,
    "dcl_find_msms": READ_ONLY,
    "eic_parser": READ_ONLY,
    "eic_plot_chromatograms": READ_ONLY,
    "eic_plot_compounds": READ_ONLY,
    "eic_rank_by_max_intensity": READ_ONLY,
    "eic_search_by_mz_range": READ_ONLY,
    "eic_search_by_rt_range": READ_ONLY,
    "log_search": READ_ONLY,
    "knowledge_coverage": READ_ONLY,
    "pai2_parser": READ_ONLY,
    "pai2_inspect_peak": READ_ONLY,
    "verify_peak_annotation": READ_ONLY,
    "read_report": READ_ONLY,
    "list_reports": READ_ONLY,
    "sample_search": READ_ONLY,
    "record_objective": LOCAL_WRITE,
    "update_objective": LOCAL_WRITE,
    "write_report": LOCAL_WRITE,
    "save_pca_figure": LOCAL_WRITE,
    "save_volcano_figure": LOCAL_WRITE,
    "save_eic_figure": LOCAL_WRITE,
    "paper_search": EXTERNAL,
    "ingest_stage": MUTATION,
    "ingest_review_queue": MUTATION,
    "ingest_promote": MUTATION,
    "ingest_reject": MUTATION,
}

# annotations 単独判定へ移行したときに意図的に変わるもの（設計書 §6）。
# ingest_review_queue は build_inbox_index() を返すだけの純粋な読み取りで、
# KNOWLEDGE_MUTATION に入っているのは名前リストの分類ミス。ユーザ承認済みの緩和。
EXPECTED_CHANGES: dict[str, ToolSafety] = {
    "ingest_review_queue": READ_ONLY,
}

# 監査ラベルだけ変わるもの。decide_server_tool では LOCAL_WRITE も
# KNOWLEDGE_MUTATION も等しく承認必須なので、実行可否は変わらない。
EXPECTED_LABEL_CHANGES: dict[str, ToolSafety] = {
    "ingest_stage": LOCAL_WRITE,
}


def expected_after_migration() -> dict[str, ToolSafety]:
    """移行後に期待される分類表。"""
    result = dict(CURRENT_CLASSIFICATION)
    result.update(EXPECTED_CHANGES)
    result.update(EXPECTED_LABEL_CHANGES)
    return result


class CurrentClassificationSnapshotTests(unittest.TestCase):
    def test_snapshot_covers_every_ms_data_parser_tool(self) -> None:
        self.assertEqual(len(CURRENT_CLASSIFICATION), 40)

    def test_snapshot_matches_the_name_based_classifier(self) -> None:
        for name, expected in CURRENT_CLASSIFICATION.items():
            self.assertEqual(classify_tool(name), expected, name)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを走らせて green を確認する**

Run: `cd /c/Users/yuu18/Use-LLLM && .venv/Scripts/python.exe -m pytest tests/test_approval_snapshot.py -q`
Expected: PASS（2 passed）。ここで落ちるなら、スナップショットの写し間違いなので `policy.py` の4つの frozenset と突き合わせて直す。

- [ ] **Step 3: 整形と lint**

Run: `.venv/Scripts/python.exe -m ruff format tests/test_approval_snapshot.py && .venv/Scripts/python.exe -m ruff check tests/test_approval_snapshot.py`
Expected: `All checks passed!`

- [ ] **Step 4: コミット**

```bash
cd /c/Users/yuu18/Use-LLLM
git add tests/test_approval_snapshot.py
git commit -m "test(policy): 承認挙動のスナップショットを移行前に固定する

annotations 単独判定へ移行する前に、ms-data-parser 40ツールの現行分類を
凍結する。意図した差分は EXPECTED_CHANGES / EXPECTED_LABEL_CHANGES に
明記し、それ以外の変化は回帰として落とす。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: サーバに `mcp_errors.py` を作る

**Files:**
- Create: `C:\Users\yuu18\Lipidmix_with_LLM\mcp_errors.py`
- Test: `C:\Users\yuu18\Lipidmix_with_LLM\tests\test_mcp_errors.py`

**Interfaces:**
- Produces: `mcp_errors.missing_state(state: str, required_tools: list[str], message: str) -> str`、定数 `mcp_errors.MISSING_STATE = "missing_state"`。Task 3 が全面的に使う。

- [ ] **Step 1: 失敗するテストを書く**

```python
"""クライアント非依存のエラーエンベロープ。"""
import json
import unittest

import mcp_errors


class MissingStateEnvelopeTests(unittest.TestCase):
    def test_envelope_has_the_contracted_shape(self):
        raw = mcp_errors.missing_state(
            "preprocessed_matrix",
            ["arf_preprocess"],
            "前処理後の行列がありません。先に arf_preprocess を実行してください。",
        )
        payload = json.loads(raw)
        self.assertEqual(payload["error"]["code"], "missing_state")
        self.assertEqual(payload["error"]["state"], "preprocessed_matrix")
        self.assertEqual(payload["error"]["required_tools"], ["arf_preprocess"])
        self.assertIn("arf_preprocess", payload["error"]["message"])

    def test_message_survives_verbatim(self):
        """既存の日本語文面をそのまま運ぶ（Claude Desktop での見え方を変えない）。"""
        message = "先に arf_parser を実行してARFデータとタグファイルを読み込んでください。"
        payload = json.loads(mcp_errors.missing_state("arf_dataset", ["arf_parser"], message))
        self.assertEqual(payload["error"]["message"], message)

    def test_japanese_is_not_escaped(self):
        raw = mcp_errors.missing_state("arf_dataset", ["arf_parser"], "先に arf_parser を")
        self.assertIn("先に", raw)

    def test_alternatives_are_preserved_in_order(self):
        """required_tools は OR の代替候補。順序が意味を持つ。"""
        payload = json.loads(
            mcp_errors.missing_state(
                "eic_plot", ["eic_plot_chromatograms", "eic_plot_compounds"], "先に"
            )
        )
        self.assertEqual(
            payload["error"]["required_tools"],
            ["eic_plot_chromatograms", "eic_plot_compounds"],
        )

    def test_empty_required_tools_is_rejected(self):
        """復旧の手掛かりが無いエンベロープは契約違反。無言で出さない。"""
        with self.assertRaises(ValueError):
            mcp_errors.missing_state("arf_dataset", [], "先に arf_parser を")

    def test_empty_state_is_rejected(self):
        with self.assertRaises(ValueError):
            mcp_errors.missing_state("", ["arf_parser"], "先に arf_parser を")

    def test_empty_message_is_rejected(self):
        with self.assertRaises(ValueError):
            mcp_errors.missing_state("arf_dataset", ["arf_parser"], "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `cd /c/Users/yuu18/Lipidmix_with_LLM && python -m pytest tests/test_mcp_errors.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mcp_errors'`）

- [ ] **Step 3: 実装する**

`mcp_errors.py`:

```python
"""クライアント非依存のエラーエンベロープ。

MCP クライアントが「どのツールを先に呼べばよいか」を、サーバ固有の日本語文面を
解釈せずに機械的に判断できるようにする。汎用クライアント（Use-LLLM 等）は
本文を JSON として読み、`error.code` が `missing_state` なら
`error.required_tools` を再実行して状態を復元できる。

なぜ本文の JSON なのか: 使用中の MCP SDK では `isError=true` を作る経路
（lowlevel/server.py の _make_error_result）がテキストのみを返して
structuredContent を捨てる。さらに FastMCP は戻り値アノテーションから
outputSchema を導出するため、`-> str` のツールが失敗時だけ dict を返すこともできない。
機械可読なエラーはテキストに載せるしかない。

このモジュールは依存グラフの leaf（stdlib のみ）。tools_* / server を import しない。
"""
import json

MISSING_STATE = "missing_state"


def missing_state(state: str, required_tools: list[str], message: str) -> str:
    """前提となるセッション状態が無いことを、契約どおりのエンベロープで返す。

    引数:
        state: 欠けている状態の識別子（例 "preprocessed_matrix"）。クライアントは
            これを不透明な文字列として扱い、解釈しない。開示とログのためにある。
        required_tools: その状態を作れるツール名の**代替候補（OR）**。サーバ名は
            含めない（クライアント側の名前空間はクライアントが決める）。先頭ほど優先。
        message: LLM と人間が読む説明。既存の日本語文面をそのまま渡すこと。
            エンベロープを解釈しないクライアントではこれだけが見える。

    3つとも欠かせない。復旧の手掛かりが無いエンベロープを無言で出すと、
    クライアントは「状態不足だが何もできない」状態に陥り、原因究明を誤らせる。
    """
    if not state:
        raise ValueError("state は必須です（欠けている状態の識別子）。")
    if not required_tools:
        raise ValueError("required_tools は必須です（状態を作れるツールの候補）。")
    if not message:
        raise ValueError("message は必須です（LLM と人間が読む説明）。")
    return json.dumps(
        {
            "error": {
                "code": MISSING_STATE,
                "state": state,
                "required_tools": list(required_tools),
                "message": message,
            }
        },
        ensure_ascii=False,
        indent=2,
    )
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_mcp_errors.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: コミット**

```bash
cd /c/Users/yuu18/Lipidmix_with_LLM
git add mcp_errors.py tests/test_mcp_errors.py
git commit -m "feat(mcp): 状態不足を伝えるクライアント非依存のエンベロープを追加

汎用 MCP クライアントが、サーバ固有の日本語文面を解釈せずに
「どのツールを先に呼べばよいか」を判断できるようにする。
SDK 制約（isError=true に structuredContent を載せられない／1ツールが
成功時 Markdown・失敗時 構造化を返せない）から、本文の JSON とする。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 状態不足13箇所をエンベロープへ置換する（サーバ）

**Files:**
- Modify: `tools_arf.py`（8箇所: 64, 82, 107, 181, 260, 358, 766, 934 行付近）
- Modify: `tools_pai2.py`（2箇所: 124, 149 行付近）
- Modify: `tools_reports.py`（3箇所: 124, 163, 204 行付近）
- Test: `tests/test_missing_state_contract.py`（新規）

**Interfaces:**
- Consumes: `mcp_errors.missing_state`（Task 2）
- Produces: 13ツールが失敗時に `missing_state` エンベロープを返すという事実。Task 9 のクライアント側リカバリがこれに依存する。

置換表（設計書 §4.2）:

| ツール | `state` | `required_tools` |
| --- | --- | --- |
| `arf_list_tags` | `arf_dataset` | `["arf_parser"]` |
| `arf_list_classes` | `arf_dataset` | `["arf_parser"]` |
| `arf_list_sample_roles` | `arf_dataset` | `["arf_parser"]` |
| `arf_exclude` | `arf_dataset` | `["arf_parser"]` |
| `arf_preprocess` | `arf_dataset` | `["arf_parser"]` |
| `arf_pca_preprocessed` | `preprocessed_matrix` | `["arf_preprocess"]` |
| `arf_differential` | `preprocessed_matrix` | `["arf_preprocess"]` |
| `arf_plot_volcano` | `differential_result` | `["arf_differential"]` |
| `pai2_inspect_peak` | `pai2_dataset` | `["pai2_parser"]` |
| `verify_peak_annotation` | `pai2_dataset` | `["pai2_parser"]` |
| `save_pca_figure` | `pca_result` | `["arf_parser"]` |
| `save_volcano_figure` | `differential_result` | `["arf_differential"]` |
| `save_eic_figure` | `eic_plot` | `["eic_plot_chromatograms", "eic_plot_compounds"]` |

- [ ] **Step 1: 失敗するテストを書く**

```python
"""状態不足13箇所が missing_state エンベロープを返すことの契約テスト。

message は既存の日本語文面を保持する（エンベロープを解釈しないクライアントでも
LLM が読む内容が変わらないこと）。
"""
import json
import unittest

import server
import session_state


def envelope(raw):
    """本文がエンベロープならその error 部を返す。違えば None。"""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict) and error.get("code") == "missing_state":
        return error
    return None


class MissingStateContractTests(unittest.TestCase):
    """空セッションで各ツールを呼び、エンベロープが返ることを確認する。"""

    def setUp(self):
        session_state.session = server.AnalysisSession()

    def assert_missing(self, raw, state, required_tools, message_contains):
        error = envelope(raw)
        self.assertIsNotNone(error, f"エンベロープでない: {raw[:200]}")
        self.assertEqual(error["state"], state)
        self.assertEqual(error["required_tools"], required_tools)
        self.assertIn(message_contains, error["message"])

    def test_arf_list_tags(self):
        self.assert_missing(
            server.arf_list_tags(), "arf_dataset", ["arf_parser"], "タグファイル"
        )

    def test_arf_list_classes(self):
        self.assert_missing(
            server.arf_list_classes(), "arf_dataset", ["arf_parser"], "ARFデータ"
        )

    def test_arf_list_sample_roles(self):
        self.assert_missing(
            server.arf_list_sample_roles(), "arf_dataset", ["arf_parser"], "arf_parser"
        )

    def test_arf_exclude(self):
        self.assert_missing(
            server.arf_exclude(exclude_samples=["x"]), "arf_dataset", ["arf_parser"], "arf_parser"
        )

    def test_arf_preprocess(self):
        self.assert_missing(
            server.arf_preprocess(), "arf_dataset", ["arf_parser"], "arf_parser"
        )

    def test_arf_pca_preprocessed(self):
        self.assert_missing(
            server.arf_pca_preprocessed(),
            "preprocessed_matrix",
            ["arf_preprocess"],
            "前処理後の行列がありません",
        )

    def test_arf_differential(self):
        self.assert_missing(
            server.arf_differential(group_a="a", group_b="b"),
            "preprocessed_matrix",
            ["arf_preprocess"],
            "arf_preprocess",
        )

    def test_arf_plot_volcano_returns_envelope_instead_of_raising(self):
        """例外だと isError=true になりエンベロープを載せられない（SDK 制約）。"""
        self.assert_missing(
            server.arf_plot_volcano(),
            "differential_result",
            ["arf_differential"],
            "arf_differential",
        )

    def test_pai2_inspect_peak(self):
        self.assert_missing(
            server.pai2_inspect_peak(peak_name="PC 34:1"),
            "pai2_dataset",
            ["pai2_parser"],
            "pai2_parser",
        )

    def test_verify_peak_annotation(self):
        self.assert_missing(
            server.verify_peak_annotation(peak_name="PC 34:1"),
            "pai2_dataset",
            ["pai2_parser"],
            "pai2_parser",
        )

    def test_save_pca_figure(self):
        self.assert_missing(
            server.save_pca_figure(analysis_id="x"), "pca_result", ["arf_parser"], "PCA"
        )

    def test_save_volcano_figure(self):
        self.assert_missing(
            server.save_volcano_figure(analysis_id="x"),
            "differential_result",
            ["arf_differential"],
            "arf_differential",
        )

    def test_save_eic_figure_offers_both_producers(self):
        """eic_plot は2つのツールのどちらでも作れる（OR の代替候補）。"""
        self.assert_missing(
            server.save_eic_figure(analysis_id="x"),
            "eic_plot",
            ["eic_plot_chromatograms", "eic_plot_compounds"],
            "eic_plot_chromatograms",
        )


class SampleSearchIsNotAMissingStateTests(unittest.TestCase):
    """sample_search は ARF 未ロードでも動くのが要件。エンベロープ化してはいけない。"""

    def setUp(self):
        session_state.session = server.AnalysisSession()

    def test_sample_search_failure_is_not_an_envelope(self):
        import tempfile

        with tempfile.TemporaryDirectory() as empty:
            raw = server.sample_search(directory=empty)
        self.assertIsNone(
            envelope(raw),
            "sample_search をエンベロープ化すると、ARF を必要としない検索のために "
            "arf_parser の自動リプレイが走ってしまう",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `python -m pytest tests/test_missing_state_contract.py -q`
Expected: FAIL。13件中12件は「エンベロープでない」、`arf_plot_volcano` は `ValueError` が送出されてエラーになる。

- [ ] **Step 3: `tools_arf.py` の8箇所を置換する**

先頭の import に追加:

```python
import mcp_errors
```

各置換（`ARF_DATASET_HINT` のような定数は作らない。`message` はツールごとに文面が違い、既存の日本語をそのまま運ぶのが契約）:

```python
# tools_arf.py:64 arf_list_tags
        return mcp_errors.missing_state(
            "arf_dataset", ["arf_parser"],
            "先に arf_parser を実行してARFデータとタグファイルを読み込んでください。")

# tools_arf.py:82 arf_list_classes
        return mcp_errors.missing_state(
            "arf_dataset", ["arf_parser"],
            "先に arf_parser を実行してARFデータを読み込んでください。")

# tools_arf.py:107 arf_list_sample_roles / :181 arf_exclude / :260 arf_preprocess
# （3箇所とも同じ本文。json.dumps({"status": "error", ...}) を置き換える）
        return mcp_errors.missing_state(
            "arf_dataset", ["arf_parser"],
            "先に arf_parser で ARF を読み込んでください。")

# tools_arf.py:358 arf_pca_preprocessed
        return mcp_errors.missing_state(
            "preprocessed_matrix", ["arf_preprocess"],
            "前処理後の行列がありません。先に arf_preprocess を実行してください。")

# tools_arf.py:766 arf_differential
        return mcp_errors.missing_state(
            "preprocessed_matrix", ["arf_preprocess"],
            "先に arf_preprocess を実行してください（前処理後行列が必要）。")
```

`arf_plot_volcano`（:931-936）は戻り値型が `VolcanoPlotPayload` なので扱いが違う。`raise ValueError` をやめると型が `VolcanoPlotPayload | str` になり FastMCP の outputSchema 導出が壊れる。**戻り値アノテーションを `-> VolcanoPlotPayload | str` にはせず、`-> str` へ変える**のが正しい。現状 `arf_plot_volcano` は構造化ペイロードを返しており、これは webUI の描画経路が使っている。

したがってここだけは例外を残し、**例外メッセージの本文をエンベロープにする**:

```python
        last = getattr(session_state.session.arf, "last_differential", None)
        if not last or last.get("kind") != "two_group" or not last.get("volcano"):
            # 戻り値型が VolcanoPlotPayload（構造化）なので、失敗時に str を返すと
            # outputSchema 導出が壊れる。例外の本文にエンベロープを載せる。
            # FastMCP が "Error executing tool <name>: " を前置するため、
            # クライアント側は本文中の JSON を切り出して読む必要がある。
            raise ValueError(mcp_errors.missing_state(
                "differential_result", ["arf_differential"],
                "先に arf_differential（2群比較）を実行してください"
                "（直近の2群差次的解析の volcano データがありません）。"))
```

**この決定は Task 8 のクライアント側パーサに要件を足す**: 本文全体が JSON でなくても、埋め込まれた JSON オブジェクトを取り出せること。

- [ ] **Step 4: `tools_pai2.py` の2箇所を置換する**

先頭に `import mcp_errors` を追加し、124 行と 149 行の `json.dumps({"status": "error", "message": "先に pai2_parser …"})` を置換:

```python
        return mcp_errors.missing_state(
            "pai2_dataset", ["pai2_parser"],
            "先に pai2_parser を実行してデータを読み込んでください。")
```

- [ ] **Step 5: `tools_reports.py` の3箇所を置換する**

先頭に `import mcp_errors` を追加:

```python
# :124 save_pca_figure
        return mcp_errors.missing_state(
            "pca_result", ["arf_parser"],
            "先に arf_parser / arf_re_pca / pai2_parser 等でPCAを実行してください"
            "（PCA結果がありません）。")

# :163 save_volcano_figure
        return mcp_errors.missing_state(
            "differential_result", ["arf_differential"],
            "[error] 直近の差次的解析（volcano データ）がありません。"
            "先に arf_differential を実行してください。")

# :204 save_eic_figure
        return mcp_errors.missing_state(
            "eic_plot", ["eic_plot_chromatograms", "eic_plot_compounds"],
            "先に eic_plot_chromatograms または eic_plot_compounds を実行してください"
            "（EICプロット情報がありません）。")
```

- [ ] **Step 6: 契約テストを通す**

Run: `python -m pytest tests/test_missing_state_contract.py -q`
Expected: PASS（14 passed）。`arf_plot_volcano` のテストは例外送出のままだと落ちるので、そのテストだけ次のように書き換える:

```python
    def test_arf_plot_volcano_puts_the_envelope_in_the_exception(self):
        """戻り値が構造化ペイロードなので、ここだけ例外の本文にエンベロープを載せる。"""
        with self.assertRaises(ValueError) as ctx:
            server.arf_plot_volcano()
        error = envelope(str(ctx.exception))
        self.assertIsNotNone(error)
        self.assertEqual(error["state"], "differential_result")
        self.assertEqual(error["required_tools"], ["arf_differential"])
```

- [ ] **Step 7: 既存テストへの影響を確認する**

Run: `python -m pytest tests/ -q`
Expected: 既存テストのうち、エラー文面を部分文字列で見ているものが落ちる。落ちたテストは**エンベロープの `message` を見るように直す**（文面自体は変えていないので、`assertIn` の対象を `json.loads(raw)["error"]["message"]` へ移すだけ）。`test_tool_count_is_stable` は Task 6 まで落ちたままでよい。

- [ ] **Step 8: コミット**

```bash
git add tools_arf.py tools_pai2.py tools_reports.py tests/test_missing_state_contract.py tests/
git commit -m "feat(mcp): 状態不足13箇所を missing_state エンベロープへ置換

日本語の message はそのまま運ぶので、エンベロープを解釈しないクライアント
（Claude Desktop 等）での見え方は変わらない。arf_plot_volcano だけは
戻り値が構造化ペイロードで str を返せないため、例外の本文にエンベロープを載せる。

sample_search は対象外。ARF 未ロードでも動くのが要件で、エンベロープ化すると
ARF を必要としない検索のために arf_parser の自動リプレイが走ってしまう。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 全40ツールに `ToolAnnotations` を付与する（サーバ）

**Files:**
- Modify: `tools_arf.py`, `tools_arf2.py`, `tools_dataset.py`, `tools_dcl.py`, `tools_eic.py`, `tools_objective.py`, `tools_pai2.py`, `tools_reports.py`, `tools_samples.py`
- Test: `tests/test_tool_annotations.py`（新規）

**Interfaces:**
- Produces: 全40ツールが `annotations` を持つ。Task 7（承認判定）と Task 9（リプレイ安全性）がこれに依存する。

- [ ] **Step 1: 失敗するテストを書く**

```python
"""全ツールが MCP 標準 annotations を宣言していることの回帰テスト。

汎用 MCP クライアントは annotations だけを見て承認要否とリプレイ安全性を決める。
annotations の無いツールはクライアント側で UNKNOWN 扱いになり、毎回承認待ちで
止まる。新しいツールを足したらここにも足すこと。

readOnlyHint の解釈: 「サーバの外に副作用が無い」＝ファイルとネットワークを
変更しない。サーバ自身の解析セッション状態の更新は副作用に数えない
（セッション状態の依存は missing_state エンベロープで伝えるため、
annotations で二重に表現しない）。
"""
import asyncio
import unittest

import server

READ_ONLY = {"readOnlyHint": True}
EXTERNAL = {"readOnlyHint": True, "openWorldHint": True}
LOCAL_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
STAGE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True}

EXPECTED_ANNOTATIONS = {
    # --- ARF（多サンプル解析）。セッション状態は更新するが外部副作用は無い ---
    "arf_list_tags": READ_ONLY,
    "arf_list_classes": READ_ONLY,
    "arf_list_sample_roles": READ_ONLY,
    "arf_exclude": READ_ONLY,
    "arf_preprocess": READ_ONLY,
    "arf_pca_preprocessed": READ_ONLY,
    "arf_parser": READ_ONLY,
    "arf_differential": READ_ONLY,
    "arf_plot_volcano": READ_ONLY,
    "arf2_parser": READ_ONLY,
    "arf2_annotate_identities": READ_ONLY,
    # --- データセット入口 ---
    "list_data_files": READ_ONLY,
    "load_dataset": READ_ONLY,
    # --- DCL / PAI2 / EIC ---
    "dcl_parser": READ_ONLY,
    "dcl_find_msms": READ_ONLY,
    "pai2_parser": READ_ONLY,
    "pai2_inspect_peak": READ_ONLY,
    "verify_peak_annotation": READ_ONLY,
    "eic_parser": READ_ONLY,
    "eic_plot_chromatograms": READ_ONLY,
    "eic_plot_compounds": READ_ONLY,
    "eic_rank_by_max_intensity": READ_ONLY,
    "eic_search_by_mz_range": READ_ONLY,
    "eic_search_by_rt_range": READ_ONLY,
    # --- 読み取り系 ---
    "log_search": READ_ONLY,
    "knowledge_coverage": READ_ONLY,
    "read_report": READ_ONLY,
    "list_reports": READ_ONLY,
    "sample_search": READ_ONLY,
    "ingest_review_queue": READ_ONLY,
    # --- 外部ネットワーク ---
    "paper_search": EXTERNAL,
    # --- ローカル書き出し（同じ引数なら同じパスへ上書き＝idempotent） ---
    "record_objective": LOCAL_WRITE,
    "update_objective": LOCAL_WRITE,
    "write_report": LOCAL_WRITE,
    "save_pca_figure": LOCAL_WRITE,
    "save_volcano_figure": LOCAL_WRITE,
    "save_eic_figure": LOCAL_WRITE,
    # --- knowledge の変更 ---
    "ingest_stage": STAGE,
    "ingest_promote": DESTRUCTIVE,
    "ingest_reject": DESTRUCTIVE,
}


def actual_annotations():
    tools = asyncio.run(server.mcp.list_tools())
    out = {}
    for tool in tools:
        ann = tool.annotations
        out[tool.name] = (
            None
            if ann is None
            else ann.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
    return out


class ToolAnnotationTests(unittest.TestCase):
    def test_expectation_covers_every_registered_tool(self):
        self.assertEqual(set(actual_annotations()), set(EXPECTED_ANNOTATIONS))

    def test_every_tool_declares_annotations(self):
        missing = [name for name, ann in actual_annotations().items() if not ann]
        self.assertEqual(missing, [], f"annotations 未宣言のツール: {missing}")

    def test_annotations_match_the_expected_table(self):
        actual = actual_annotations()
        for name, expected in EXPECTED_ANNOTATIONS.items():
            self.assertEqual(actual[name], expected, name)

    def test_external_network_tool_also_declares_open_world(self):
        """openWorldHint が無いと汎用クライアントがネットワークゲートを迂回する。"""
        self.assertIs(actual_annotations()["paper_search"]["openWorldHint"], True)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `python -m pytest tests/test_tool_annotations.py -q`
Expected: FAIL（`annotations 未宣言のツール: [...]` に40件並ぶ）

- [ ] **Step 3: 各 `tools_*.py` に annotations を付ける**

各ファイル先頭の import に追加:

```python
from mcp.types import ToolAnnotations
```

`tools_arf.py` / `tools_arf2.py` / `tools_dcl.py` / `tools_eic.py` / `tools_pai2.py` / `tools_samples.py` は全ツールが read-only:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
```

`tools_dataset.py` は `load_dataset` のデコレータと、25 行の関数呼び出し形式の両方:

```python
list_data_files = mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(
    path_resolvers.list_data_files
)
```

`tools_reports.py`:

```python
# read_report / list_reports
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))

# write_report / save_pca_figure / save_volcano_figure / save_eic_figure
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True))
```

`tools_objective.py`:

```python
# log_search / knowledge_coverage / ingest_review_queue
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))

# record_objective / update_objective
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True))

# paper_search
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))

# ingest_stage
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False))

# ingest_promote / ingest_reject
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
```

`ingest_review_queue` には理由をコメントで残す:

```python
# _inbox の索引を組み立てて返すだけで、ファイルもネットワークも変更しない。
# 汎用クライアントの承認判定はこの宣言だけを見るため、正直に read-only とする。
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def ingest_review_queue() -> str:
```

- [ ] **Step 4: テストを通す**

Run: `python -m pytest tests/test_tool_annotations.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 全体テスト**

Run: `python -m pytest tests/ -q`
Expected: `test_tool_count_is_stable` のみ失敗（Task 6 で直す）

- [ ] **Step 6: コミット**

```bash
git add tools_*.py tests/test_tool_annotations.py
git commit -m "feat(mcp): 全40ツールに MCP 標準 ToolAnnotations を宣言する

汎用 MCP クライアントが承認要否とリプレイ安全性を、サーバ固有のツール名
リストではなく標準 annotations だけで判断できるようにする。

readOnlyHint は「サーバの外に副作用が無い」の意味で使う。サーバ自身の解析
セッション状態の更新は副作用に数えない（その依存は missing_state
エンベロープで伝えるため、annotations で二重に表現しない）。

ingest_review_queue は build_inbox_index() を返すだけの純粋な読み取りなので
正直に readOnlyHint=True とする。クライアント側の名前リストでは
KNOWLEDGE_MUTATION に分類されていたが、これは分類ミスだった。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: サーバ側ドキュメントに契約を書く

**Files:**
- Modify: `docs/output_format/core.md`

- [ ] **Step 1: 契約の節を追加する**

`core.md` の末尾に追加（既存の見出しレベルに合わせること。ファイル冒頭を読んで `##` か `###` かを確認する）:

```markdown
## 状態不足の伝え方（MCP クライアント向けの契約）

多くのツールは、先行するツールが作ったセッション状態に依存する。
`arf_pca_preprocessed` は `arf_preprocess` の行列を、`arf_plot_volcano` は
`arf_differential` の結果を必要とする。

その状態が無いとき、ツールは本文に次の JSON を返す。

```json
{"error": {"code":           "missing_state",
           "state":          "preprocessed_matrix",
           "required_tools": ["arf_preprocess"],
           "message":        "前処理後の行列がありません。先に arf_preprocess を実行してください。"}}
```

- `state` は欠けている状態の識別子。クライアントは**不透明な文字列として扱う**。
- `required_tools` はその状態を作れるツール名の**代替候補（OR）**。サーバ名は含めない。
  先頭ほど優先。
- `message` は LLM と人間が読む説明。エンベロープを解釈しないクライアントでは
  これだけが見える。

エンベロープを解釈するクライアントは、`required_tools` のいずれかを
**セッション履歴に記録済みの引数のまま**再実行して状態を復元できる。LLM に
再実行させると前処理の引数が変わって解析条件が黙って変わりうるため、
引数の同一性はクライアント側で担保することが望ましい。

### annotations の解釈

全ツールが MCP 標準の `ToolAnnotations` を宣言する。

- `readOnlyHint=true` は「**サーバの外に副作用が無い**」を意味する。ファイルと
  ネットワークを変更しないこと。**サーバ自身の解析セッション状態の更新は
  副作用に数えない** — その依存関係は上のエンベロープで伝えるため、annotations で
  二重に表現しない。
- `openWorldHint=true` は外部ネットワークへ出ることを示す（`paper_search` のみ）。
- リプレイ安全（同じ引数での再実行が安全）は `readOnlyHint=true` または
  `idempotentHint=true` で判断できる。
```

- [ ] **Step 2: リソースが読めることを確認する**

Run: `python -m pytest tests/test_output_format_sections.py -q`
Expected: PASS

- [ ] **Step 3: コミット**

```bash
git add docs/output_format/core.md
git commit -m "docs(output-format): missing_state 契約と annotations の解釈を定義

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `test_tool_count_is_stable` のドリフトを構造的に直す

本計画の着手前から失敗している別件（コミット `f981cf3` が `EXPECTED_TOOLS` に `arf_plot_volcano` を足した際にリテラル `39` を更新し忘れた）。同じ事実が2箇所に重複していることが原因なので、重複を消す。

**Files:**
- Modify: `tests/test_server_registration.py:76-77`

- [ ] **Step 1: 現状の失敗を確認する**

Run: `python -m pytest tests/test_server_registration.py::test_tool_count_is_stable -q`
Expected: FAIL（`assert 40 == 39`）

- [ ] **Step 2: リテラルの重複を消す**

```python
def test_tool_count_is_stable():
    # ツール数の正準は EXPECTED_TOOLS。ここに数値リテラルを置くと、
    # ツール追加時に EXPECTED_TOOLS だけ更新されて数値が取り残される
    # （f981cf3 で実際に起きた）。
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == len(EXPECTED_TOOLS)
```

- [ ] **Step 3: 全体テストが green になることを確認する**

Run: `python -m pytest tests/ -q`
Expected: 全件 PASS（失敗0）

- [ ] **Step 4: コミット**

```bash
git add tests/test_server_registration.py
git commit -m "fix(test): ツール数の重複した真実を消す

f981cf3 が EXPECTED_TOOLS に arf_plot_volcano を足した際、
test_tool_count_is_stable のリテラル 39 が取り残されて main が赤かった。
同じ事実を2箇所に持たない形へ変え、ドリフトを構造的に防ぐ。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: クライアントに `tool_result_contract.py` を作る

**Files:**
- Create: `C:\Users\yuu18\Use-LLLM\src\use_lllm\core\tool_result_contract.py`
- Test: `C:\Users\yuu18\Use-LLLM\tests\test_tool_result_contract.py`

**Interfaces:**
- Produces: `MissingState`（`state: str`, `required_tools: tuple[str, ...]`, `message: str`）と `read_missing_state(result_text: str) -> MissingState | None`。Task 9 と Task 10 が使う。

Task 3 Step 3 の決定により、**本文全体が JSON でなくても埋め込まれた JSON を取り出せる**必要がある（`arf_plot_volcano` は例外経由で `Error executing tool arf_plot_volcano: {...}` になる）。

- [ ] **Step 1: 失敗するテストを書く**

```python
"""ツール結果契約リーダーの回帰テスト。サーバ固有の知識をここに入れない。"""

from __future__ import annotations

import json
import unittest

from use_lllm.core.tool_result_contract import read_missing_state


def envelope(state="preprocessed_matrix", tools=("arf_preprocess",), message="先に実行"):
    return json.dumps(
        {
            "error": {
                "code": "missing_state",
                "state": state,
                "required_tools": list(tools),
                "message": message,
            }
        },
        ensure_ascii=False,
    )


class ReadMissingStateTests(unittest.TestCase):
    def test_reads_a_well_formed_envelope(self) -> None:
        found = read_missing_state(envelope())
        self.assertIsNotNone(found)
        self.assertEqual(found.state, "preprocessed_matrix")
        self.assertEqual(found.required_tools, ("arf_preprocess",))
        self.assertEqual(found.message, "先に実行")

    def test_preserves_alternative_order(self) -> None:
        found = read_missing_state(envelope(tools=("a", "b")))
        self.assertEqual(found.required_tools, ("a", "b"))

    def test_reads_an_envelope_embedded_in_surrounding_text(self) -> None:
        """FastMCP は例外を "Error executing tool <name>: " で前置する。"""
        raw = "Error executing tool arf_plot_volcano: " + envelope()
        found = read_missing_state(raw)
        self.assertIsNotNone(found)
        self.assertEqual(found.state, "preprocessed_matrix")

    def test_plain_text_is_not_an_envelope(self) -> None:
        self.assertIsNone(read_missing_state("前処理後の行列がありません。"))

    def test_unrelated_json_is_not_an_envelope(self) -> None:
        self.assertIsNone(read_missing_state('{"status": "success", "rows": []}'))

    def test_unknown_error_code_is_ignored(self) -> None:
        raw = json.dumps({"error": {"code": "rate_limited", "message": "後で"}})
        self.assertIsNone(read_missing_state(raw))

    def test_envelope_without_required_tools_is_rejected(self) -> None:
        """復旧の手掛かりが無いものは契約違反。エンベロープとして扱わない。"""
        raw = json.dumps(
            {"error": {"code": "missing_state", "state": "x", "message": "m",
                       "required_tools": []}}
        )
        self.assertIsNone(read_missing_state(raw))

    def test_envelope_with_non_string_tools_is_rejected(self) -> None:
        raw = json.dumps(
            {"error": {"code": "missing_state", "state": "x", "message": "m",
                       "required_tools": [1, 2]}}
        )
        self.assertIsNone(read_missing_state(raw))

    def test_empty_and_none_input_are_safe(self) -> None:
        self.assertIsNone(read_missing_state(""))
        self.assertIsNone(read_missing_state(None))

    def test_json_array_is_not_an_envelope(self) -> None:
        self.assertIsNone(read_missing_state("[1, 2, 3]"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `cd /c/Users/yuu18/Use-LLLM && .venv/Scripts/python.exe -m pytest tests/test_tool_result_contract.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

```python
"""MCP ツール結果の契約リーダー。

サーバが「前提となるセッション状態が無い」ことを機械可読に伝えてきたときに、
それを読み取るだけのモジュール。**特定のサーバ名もツール名も知らない。**
契約を実装しないサーバの結果は素通しになるので、後方互換は保たれる。

契約の定義は ms-data-parser の docs/output_format/core.md にある。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

MISSING_STATE_CODE = "missing_state"


@dataclass(frozen=True, slots=True)
class MissingState:
    """ツールが必要とする前提状態が無かったことの表明。

    state はサーバが決める不透明な識別子。クライアントは解釈も比較もせず、
    ログと開示にだけ使う（ここでドメイン語彙を解釈し始めると、汎用クライアントで
    なくなる）。required_tools は AND チェーンではなく **OR の代替候補**で、
    先頭ほど優先される。
    """

    state: str
    required_tools: tuple[str, ...]
    message: str


def _candidate_payloads(result_text: str):
    """本文そのものと、本文に埋め込まれた最初の JSON オブジェクトを順に試す。

    FastMCP は例外を "Error executing tool <name>: " で前置するため、本文全体が
    JSON にならないことがある。前置を落として読み直せるようにする。
    """
    yield result_text
    start = result_text.find("{")
    end = result_text.rfind("}")
    if 0 <= start < end:
        yield result_text[start : end + 1]


def read_missing_state(result_text: str | None) -> MissingState | None:
    """本文が missing_state エンベロープならそれを返す。違えば None。

    壊れた JSON、別の error code、必須フィールド欠落はすべて None。例外は投げない
    （契約を実装しないサーバの通常の出力が大量に流れてくる経路なので、
    ここで落ちると全ツール呼び出しが壊れる）。
    """
    if not result_text:
        return None
    for candidate in _candidate_payloads(result_text):
        found = _read_one(candidate)
        if found is not None:
            return found
    return None


def _read_one(candidate: str) -> MissingState | None:
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("code") != MISSING_STATE_CODE:
        return None
    state = error.get("state")
    tools = error.get("required_tools")
    message = error.get("message")
    if not isinstance(state, str) or not state:
        return None
    if not isinstance(message, str) or not message:
        return None
    if not isinstance(tools, list) or not tools:
        return None
    if not all(isinstance(item, str) and item for item in tools):
        return None
    return MissingState(state, tuple(tools), message)
```

- [ ] **Step 4: テストを通す**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tool_result_contract.py -q`
Expected: PASS（10 passed）

- [ ] **Step 5: 整形・lint・コミット**

```bash
.venv/Scripts/python.exe -m ruff format src/use_lllm/core/tool_result_contract.py tests/test_tool_result_contract.py
.venv/Scripts/python.exe -m ruff check src tests
git add src/use_lllm/core/tool_result_contract.py tests/test_tool_result_contract.py
git commit -m "feat(mcp): サーバ非依存の missing_state 契約リーダーを追加

特定サーバのツール名も日本語文面も知らずに、状態不足とその復旧手段を
読み取れるようにする。契約を実装しないサーバの結果は素通しになるので
後方互換は保たれる。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: 承認判定を annotations 単独へ移す（クライアント）

**Files:**
- Modify: `src/use_lllm/core/policy.py`
- Modify: `src/use_lllm/core/mcp_client.py:16, 231-256, 258-286`
- Modify: `tests/test_approval_snapshot.py`（Task 1 で作った表を新判定に対して回す）
- Modify: `tests/test_policy_server.py`（名前ベースのテストを削除・置換）

**Interfaces:**
- Consumes: Task 1 の `CURRENT_CLASSIFICATION` / `expected_after_migration()`
- Produces: `policy.classify_server_tool(tool_name, *, annotations) -> ToolSafety`（`server_name` 引数を落とす）、`policy.is_replay_safe(annotations) -> bool`、`policy.enforce_tool(tool_name, *, annotations, approved, network_mode)`

- [ ] **Step 1: 移行後の期待を検証するテストを追加する**

`tests/test_approval_snapshot.py` に追記:

```python
from use_lllm.core.policy import classify_server_tool

# Task 4 でサーバが宣言する annotations と同じ表。片方だけ変わったら落ちる。
READ_ONLY_ANN = {"readOnlyHint": True}
EXTERNAL_ANN = {"readOnlyHint": True, "openWorldHint": True}
LOCAL_WRITE_ANN = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
STAGE_ANN = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
DESTRUCTIVE_ANN = {"readOnlyHint": False, "destructiveHint": True}

ANNOTATIONS: dict[str, dict[str, bool]] = {
    name: (
        EXTERNAL_ANN
        if name == "paper_search"
        else DESTRUCTIVE_ANN
        if name in {"ingest_promote", "ingest_reject"}
        else STAGE_ANN
        if name == "ingest_stage"
        else LOCAL_WRITE_ANN
        if CURRENT_CLASSIFICATION[name] is LOCAL_WRITE
        else READ_ONLY_ANN
    )
    for name in CURRENT_CLASSIFICATION
}


class AnnotationDrivenClassificationTests(unittest.TestCase):
    def test_reproduces_the_snapshot_except_for_expected_changes(self) -> None:
        expected = expected_after_migration()
        for name, annotations in ANNOTATIONS.items():
            self.assertEqual(
                classify_server_tool(name, annotations=annotations), expected[name], name
            )

    def test_only_one_tool_changes_permission(self) -> None:
        """権限が変わるのは ingest_review_queue の1件だけ（設計書 §6.1）。"""
        changed = {
            name
            for name in CURRENT_CLASSIFICATION
            if classify_server_tool(name, annotations=ANNOTATIONS[name])
            is not CURRENT_CLASSIFICATION[name]
        }
        self.assertEqual(changed, {"ingest_review_queue", "ingest_stage"})

    def test_open_world_wins_over_read_only(self) -> None:
        """判定順を誤ると paper_search が READ_ONLY に落ちて network ゲートを迂回する。"""
        self.assertEqual(
            classify_server_tool("paper_search", annotations=EXTERNAL_ANN),
            ToolSafety.EXTERNAL_NETWORK,
        )

    def test_a_server_without_annotations_stays_unknown(self) -> None:
        self.assertEqual(classify_server_tool("whatever", annotations=None), ToolSafety.UNKNOWN)

    def test_partial_annotations_do_not_guess(self) -> None:
        """未設定フィールドは None で入る。truthiness で判定すると誤る。"""
        self.assertEqual(
            classify_server_tool("x", annotations={"readOnlyHint": None}), ToolSafety.UNKNOWN
        )
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `.venv/Scripts/python.exe -m pytest tests/test_approval_snapshot.py -q`
Expected: FAIL（`classify_server_tool() takes ... positional arguments` — 現在は `server_name` が第1引数）

- [ ] **Step 3: `policy.py` を書き換える**

`READ_ONLY_TOOLS` / `LOCAL_WRITE_TOOLS` / `EXTERNAL_NETWORK_TOOLS` / `KNOWLEDGE_MUTATION_TOOLS` / `BUILTIN_PROFILE_SERVERS` / `classify_tool` / `decide_tool` を削除し、次で置き換える:

```python
def classify_server_tool(
    tool_name: str,
    *,
    annotations: Mapping[str, Any] | None = None,
) -> ToolSafety:
    """MCP 標準 annotations だけからツールの安全クラスを決める。

    サーバ固有のツール名リストは持たない。汎用 MCP クライアントとして、
    どのローカルサーバに対しても同じ規則で判定する。

    判定順が重要。openWorldHint を先に見ないと、外部を読むだけのツール
    （readOnlyHint も真になる）が READ_ONLY に落ちて network_mode のゲートを
    迂回する。

    未設定フィールドは model_dump(by_alias=True) の結果で None として入るため、
    必ず `is True` / `is False` で判定する。truthiness では None を False と
    区別できず、宣言していない項目を「宣言した」と誤読する。
    """
    if annotations is None:
        return ToolSafety.UNKNOWN
    if annotations.get("openWorldHint") is True:
        return ToolSafety.EXTERNAL_NETWORK
    if annotations.get("readOnlyHint") is True:
        return ToolSafety.READ_ONLY
    if annotations.get("destructiveHint") is True:
        return ToolSafety.KNOWLEDGE_MUTATION
    if annotations.get("readOnlyHint") is False:
        return ToolSafety.LOCAL_WRITE
    return ToolSafety.UNKNOWN


def is_replay_safe(annotations: Mapping[str, Any] | None) -> bool:
    """同じ引数での再実行が安全か。状態復旧のリプレイ可否に使う。

    MCP 仕様は idempotentHint を「readOnlyHint == false のときだけ意味を持つ」と
    定義するため、readOnlyHint との OR で拾う。
    """
    if annotations is None:
        return False
    return annotations.get("readOnlyHint") is True or annotations.get("idempotentHint") is True
```

`decide_server_tool` の `classify_server_tool` 呼び出しを新シグネチャへ合わせる（`server_name` は `qualified` の組み立てにだけ使う）。

`enforce_tool` を annotations ベースへ:

```python
def enforce_tool(
    tool_name: str,
    *,
    annotations: Mapping[str, Any] | None = None,
    approved: bool = False,
    network_mode: str = "offline",
    server_name: str = "mcp",
) -> ToolDecision:
    """許可されないツール呼び出しを ToolPolicyError で止める。

    annotations を渡せないほど早い段階では呼ばないこと（annotations=None は
    UNKNOWN＝承認必須になる）。
    """
    decision = decide_server_tool(
        server_name,
        tool_name,
        annotations=annotations,
        approved=approved,
        network_mode=network_mode,
    )
    if not decision.allowed:
        raise ToolPolicyError(decision.reason)
    return decision
```

- [ ] **Step 4: `mcp_client.py` の enforcement をツール発見後へ移す**

`call_tool`（231-256 行）の `enforce_tool(...)` を `try` の外から、`tools` を取得した直後へ移動:

```python
        timeout = timeout_seconds or max(self._config.startup_timeout_seconds, 300.0)
        try:
            async with asyncio.timeout(timeout):
                async with self.connect() as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in await list_all_tools(session)}
                    if tool_name not in tools:
                        raise MCPConnectionError(f"MCPがツールを公開していません: {tool_name}")
                    # 承認判定はサーバが宣言した annotations で行う。ツール一覧を
                    # 取ってからでないと annotations が読めないため、接続後に判定する。
                    enforce_tool(
                        tool_name,
                        annotations=tools[tool_name].annotations,
                        approved=approved,
                        network_mode=network_mode,
                    )
                    values = arguments or {}
                    self._validate_arguments(tools[tool_name], values)
                    result = await session.call_tool(tool_name, values)
                    return self._serialize_result(tool_name, result)
```

`call_sequence`（258-286 行）も同様に、ループ前の `enforce_tool` を消し、`tools` 取得後に全 call を検証してから実行する:

```python
                    tools = {tool.name: tool for tool in await list_all_tools(session)}
                    for call in calls:
                        if call.name not in tools:
                            raise MCPConnectionError(f"MCPがツールを公開していません: {call.name}")
                        enforce_tool(
                            call.name,
                            annotations=tools[call.name].annotations,
                            approved=call.approved,
                            network_mode=network_mode,
                        )
                    results: list[MCPToolResult] = []
                    for call in calls:
                        self._validate_arguments(tools[call.name], call.arguments)
                        raw = await session.call_tool(call.name, call.arguments)
                        ...
```

- [ ] **Step 5: `tests/test_policy_server.py` の名前ベーステストを置き換える**

`classify_tool` を import している行と、それを使うテスト（107-146 行付近）を削除する。同等の検証は `tests/test_approval_snapshot.py` が担う。`decide_server_tool` を使うテストは `server_name` 引数のままなので変更不要。

- [ ] **Step 6: テストを通す**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: `test_approval_snapshot.py` の新テストが PASS。`test_mcp_client.py` / `test_policy_server.py` / `test_foundation_integration.py` で `enforce_tool` のシグネチャ変更に伴う失敗が出たら、呼び出し側を annotations 付きへ直す。

- [ ] **Step 7: 整形・lint・コミット**

```bash
.venv/Scripts/python.exe -m ruff format src tests
.venv/Scripts/python.exe -m ruff check src tests
git add src/use_lllm/core/policy.py src/use_lllm/core/mcp_client.py tests/
git commit -m "refactor(policy): 承認判定を MCP 標準 annotations 単独へ移す

汎用 MCP クライアントから ms-data-parser のツール名リスト40件と
BUILTIN_PROFILE_SERVERS を撤去した。どのローカルサーバに対しても同じ規則で
判定する。判定順は openWorldHint を最優先にする（後回しにすると外部を読む
だけのツールが READ_ONLY に落ちて network ゲートを迂回する）。

権限が変わるのは ingest_review_queue の1件のみ（純粋な読み取りなので自動実行へ）。
ingest_stage は監査ラベルのみ変わり実行可否は不変。他サーバへの影響は
厳格化方向のみ。差分は tests/test_approval_snapshot.py で固定した。

MCPClient の enforcement は削除せず、ツール発見後に annotations を見る形へ
付け替えた（cli.py の単一サーバ経路が通るため）。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: 事前リプレイを事後リカバリへ置換する（クライアント）

**Files:**
- Delete: `src/use_lllm/core/mcp_state_policy.py`, `tests/test_mcp_state_policy.py`
- Modify: `src/use_lllm/core/mcp_registry.py`（`is_replay_safe` 追加）
- Modify: `src/use_lllm/general/agent_loop.py`
- Create: `tests/test_state_recovery.py`
- Modify: `tests/test_general_agent.py`（`test_reconnect_replays_parser_before_differential` を置換）

**Interfaces:**
- Consumes: `read_missing_state` / `MissingState`（Task 7）、`policy.is_replay_safe`（Task 8）
- Produces: `MCPRegistry.is_replay_safe(qualified_name) -> bool`、`GeneralAgentLoop._recover_missing_state(...) -> str | None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_state_recovery.py`:

```python
"""状態喪失からの事後リカバリ。サーバ固有知識を使わないことも併せて確認する。"""

from __future__ import annotations

import json
import unittest

from use_lllm.core.mcp_client import MCPToolResult
from use_lllm.core.policy import ToolDecision, ToolSafety
from use_lllm.core.sessions import SessionStore
from use_lllm.general.agent_loop import GeneralAgentLoop

from tests.test_general_agent import FakeOllama, response  # 既存のフェイクを再利用


def envelope(state, tools, message="先に実行してください"):
    return json.dumps(
        {"error": {"code": "missing_state", "state": state,
                   "required_tools": list(tools), "message": message}},
        ensure_ascii=False,
    )


class RecordingRegistry:
    """呼び出し順を記録し、指定回数だけ missing_state を返すフェイク。"""

    def __init__(self, *, replay_safe: set[str], fail_once: dict[str, str]):
        self._replay_safe = replay_safe
        self._fail_once = dict(fail_once)
        self.calls: list[tuple[str, dict]] = []

    def ollama_tools(self, query=None, *, limit=None, excluded=None):
        return []

    def decide(self, name, *, approved=False, network_mode="offline"):
        return ToolDecision(name, ToolSafety.READ_ONLY, True, False, "テスト")

    def is_replay_safe(self, name):
        return name in self._replay_safe

    async def call_tool(self, name, arguments=None, *, approved=False,
                        network_mode="offline", session_id=None):
        self.calls.append((name, dict(arguments or {})))
        body = self._fail_once.pop(name, None)
        text = body if body is not None else f"ok:{name}"
        return MCPToolResult(name, False, ({"type": "text", "text": text},), None)


class MissingStateRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(Path(self.tmp.name))
        self.session = self.store.create_session(surface="general")

    def loop(self, registry):
        ollama = FakeOllama([response("done")])
        return GeneralAgentLoop(ollama, self.store, registry)

    async def test_replays_the_named_tool_with_its_recorded_arguments(self) -> None:
        """LLM に再実行させると引数が変わりうる。記録済み引数の同一性を守る。"""
        registry = RecordingRegistry(
            replay_safe={"srv::prep"},
            fail_once={"srv::pca": envelope("matrix", ["prep"])},
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(
            sid, "srv::prep", {"normalize": "none", "impute": "half_min"},
            approved=True, network_mode="offline",
        )
        result = await loop._call_and_record(
            sid, "srv::pca", {}, approved=True, network_mode="offline"
        )
        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertFalse(result.is_error)
        self.assertIn(
            ("srv::prep", {"normalize": "none", "impute": "half_min"}),
            registry.calls[1:],
        )

    async def test_gives_up_when_the_producer_is_not_replay_safe(self) -> None:
        registry = RecordingRegistry(
            replay_safe=set(), fail_once={"srv::pca": envelope("matrix", ["prep"])}
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(sid, "srv::prep", {}, approved=True, network_mode="offline")
        result = await loop._call_and_record(sid, "srv::pca", {}, approved=True,
                                             network_mode="offline")
        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertTrue(result.is_error)

    async def test_gives_up_when_there_is_no_recorded_invocation(self) -> None:
        registry = RecordingRegistry(
            replay_safe={"srv::prep"}, fail_once={"srv::pca": envelope("matrix", ["prep"])}
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        result = await loop._call_and_record(sid, "srv::pca", {}, approved=True,
                                             network_mode="offline")
        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertTrue(result.is_error)

    async def test_picks_the_first_usable_alternative(self) -> None:
        """required_tools は OR。使える最初の候補を選ぶ。"""
        registry = RecordingRegistry(
            replay_safe={"srv::second"},
            fail_once={"srv::save": envelope("plot", ["first", "second"])},
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(sid, "srv::second", {"n": 1}, approved=True,
                                    network_mode="offline")
        result = await loop._call_and_record(sid, "srv::save", {}, approved=True,
                                             network_mode="offline")
        result = await loop._maybe_recover_and_retry(
            sid, "srv::save", {}, result, approved=True, network_mode="offline"
        )
        self.assertFalse(result.is_error)
        self.assertIn(("srv::second", {"n": 1}), registry.calls)

    async def test_retry_is_single_level(self) -> None:
        """復旧後もまた missing_state なら、復旧の復旧はしない。"""
        registry = RecordingRegistry(
            replay_safe={"srv::prep"},
            fail_once={},
        )
        registry._fail_once = {"srv::pca": envelope("matrix", ["prep"])}
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(sid, "srv::prep", {}, approved=True, network_mode="offline")
        result = await loop._call_and_record(sid, "srv::pca", {}, approved=True,
                                             network_mode="offline")
        await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertEqual(sum(1 for name, _ in registry.calls if name == "srv::prep"), 2)

    async def test_a_plain_error_is_left_alone(self) -> None:
        registry = RecordingRegistry(replay_safe=set(), fail_once={})
        loop = self.loop(registry)
        sid = self.session["id"]
        plain = MCPToolResult("srv::x", True, ({"type": "text", "text": "壊れた"},), None)
        result = await loop._maybe_recover_and_retry(
            sid, "srv::x", {}, plain, approved=True, network_mode="offline"
        )
        self.assertIs(result, plain)
        self.assertEqual(registry.calls, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `.venv/Scripts/python.exe -m pytest tests/test_state_recovery.py -q`
Expected: FAIL（`AttributeError: 'GeneralAgentLoop' object has no attribute '_maybe_recover_and_retry'`）。`FakeOllama` / `response` が `tests/test_general_agent.py` から import できるか確認し、できなければローカルに最小のフェイクを定義する。

- [ ] **Step 3: `MCPRegistry.is_replay_safe` を足す**

```python
    def is_replay_safe(self, qualified_name: str) -> bool:
        """サーバが宣言した annotations から、同じ引数での再実行が安全かを判定する。"""
        try:
            tool = self.get_tool(qualified_name)
        except MCPConnectionError:
            return False
        return policy_is_replay_safe(tool.description.annotations)
```

import は `from use_lllm.core.policy import ToolDecision, ToolPolicyError, decide_server_tool, is_replay_safe as policy_is_replay_safe`。

- [ ] **Step 4: `agent_loop.py` を書き換える**

import を差し替える。`from use_lllm.core.mcp_state_policy import (...)` を削除し:

```python
from use_lllm.core.tool_result_contract import MissingState, read_missing_state
```

`RegistryLike` プロトコルに追加:

```python
    def is_replay_safe(self, name: str) -> bool: ...
```

`_call_and_record` の `indicates_missing_state` 部分を置換:

```python
            if not result.is_error and read_missing_state(content) is not None:
                # 契約上のエラー。サーバは isError を立てられない（SDK 制約）ので
                # クライアント側で立て直し、監査と LLM に「失敗」として見せる。
                result = MCPToolResult(
                    result.tool_name, True, result.content, result.structured_content
                )
```

`_restore_mcp_state` / `_mark_mcp_state_live` / `_ensure_registry_session` の生成番号まわりを削除し、次を追加:

```python
    def _replay_source(
        self, session_id: str, qualified_name: str
    ) -> dict[str, Any] | None:
        """本セッションで成功した、その名前の最後の呼び出しを返す。"""
        found = None
        for item in self.sessions.list_tool_invocations(session_id, include_replays=False):
            if (
                item.get("tool_name") == qualified_name
                and item.get("status") == "complete"
                and not item.get("is_error")
            ):
                found = item
        return found

    async def _recover_missing_state(
        self,
        session_id: str,
        qualified_name: str,
        missing: MissingState,
        *,
        network_mode: str,
    ) -> str | None:
        """required_tools の候補を順に試して状態を復元する。復元できたツール名を返す。

        required_tools は OR の代替候補。サーバが宣言した annotations で
        リプレイ安全性を判断し、引数はセッション履歴に記録されたものをそのまま使う。
        LLM に再実行させると引数が変わって解析条件が黙って変わりうるため、
        引数の同一性はここで担保する。
        """
        server_name = self._server_name(qualified_name)
        for bare in missing.required_tools:
            target = f"{server_name}::{bare}"
            if not self.registry.is_replay_safe(target):
                continue
            source = self._replay_source(session_id, target)
            if source is None:
                continue
            replayed = await self._call_and_record(
                session_id,
                target,
                dict(source["arguments"]),
                approved=True,
                network_mode=network_mode,
                replay_of_id=int(source["id"]),
                add_message=False,
            )
            if not replayed.is_error:
                return target
        return None

    async def _maybe_recover_and_retry(
        self,
        session_id: str,
        qualified_name: str,
        arguments: dict[str, Any],
        result: MCPToolResult,
        *,
        approved: bool,
        network_mode: str,
    ) -> MCPToolResult:
        """missing_state なら状態を復元して1回だけリトライする。

        リトライは1段のみ。リトライ後の結果はこの関数を通らないので、
        復旧の復旧は起きない。
        """
        if not result.is_error:
            return result
        missing = read_missing_state(self._tool_content(result))
        if missing is None:
            return result
        recovered = await self._recover_missing_state(
            session_id, qualified_name, missing, network_mode=network_mode
        )
        if recovered is None:
            self.sessions.append_event(
                session_id,
                "mcp_state_recovery_failed",
                {
                    "tool": qualified_name,
                    "state": missing.state,
                    "required_tools": list(missing.required_tools),
                },
                status="skipped",
            )
            return result
        self.sessions.append_event(
            session_id,
            "mcp_state_recovered",
            {"tool": qualified_name, "state": missing.state, "replayed": recovered},
        )
        self.sessions.add_message(
            session_id,
            "tool",
            f"[自動復旧] {recovered} を記録済みの引数で再実行して状態を復元しました。"
            f"直前のエラーは解消済みです。続けて {qualified_name} を再実行します。",
            {"tool_name": qualified_name, "recovery": True},
        )
        return await self._call_and_record(
            session_id, qualified_name, arguments, approved=approved, network_mode=network_mode
        )
```

`_drive_stream` / `_drive` / `resolve_approval` の3箇所で、`state_ready = await self._restore_mcp_state(...)` と `if not result.is_error and (state_ready or ...)` のブロックを削除し、`_call_and_record` の直後に次を挟む:

```python
            result = await self._maybe_recover_and_retry(
                session_id, qualified_name, arguments, result,
                approved=False, network_mode=network_mode,
            )
```

（`resolve_approval` では `approved=True`、変数名は `name` / `arguments`。）

- [ ] **Step 5: 古いモジュールとテストを削除する**

```bash
git rm src/use_lllm/core/mcp_state_policy.py tests/test_mcp_state_policy.py
```

- [ ] **Step 6: `test_general_agent.py` の再接続リプレイテストを置換する**

`test_reconnect_replays_parser_before_differential` を削除し、同等シナリオを新機構で書く（`tests/test_state_recovery.py::test_replays_the_named_tool_with_its_recorded_arguments` が既にこれをカバーしているので、`test_general_agent.py` 側は削除のみでよい）。`FakeRegistry` に `is_replay_safe` を足す:

```python
    def is_replay_safe(self, name):
        return True
```

- [ ] **Step 7: テストを通す**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全件 PASS

- [ ] **Step 8: 整形・lint・コミット**

```bash
.venv/Scripts/python.exe -m ruff format src tests
.venv/Scripts/python.exe -m ruff check src tests
git add -A
git commit -m "refactor(mcp): 事前リプレイを missing_state 駆動の事後リカバリへ置換

mcp_state_policy.py（ms-data-parser のツール名16件と日本語部分文字列5個）を
削除し、サーバが宣言した required_tools と annotations だけで復旧する。
これでクライアントから ms-data-parser の名前が完全に消えた。

接続世代によるゲートも削除した。mcp_generations はセットのみで解除経路が無く、
一度成功した後にプロセス内で状態が壊れるとリプレイが構造的に起動しなかった。
事後リカバリは観測された失敗から動くのでこの穴が無い。代償は再接続直後に
1回だけ無駄な失敗呼び出しが挟まることだが、自己修復する。

引数はセッション履歴に記録されたものをそのまま使う。LLM に再実行させると
前処理の引数が変わって解析条件が黙って変わりうるため。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: 実データで往復を検証する

**Files:**
- Create: スクラッチのみ（リポジトリにはコミットしない）

- [ ] **Step 1: サーバ側の往復をスクラッチで確認する**

`C:\Users\yuu18\AppData\Local\Temp\claude\C--Users-yuu18-Lipidmix-with-LLM\<session>\scratchpad\verify_contract.py`:

```python
import sys
sys.path.insert(0, r"C:\Users\yuu18\Lipidmix_with_LLM")
sys.path.insert(0, r"C:\Users\yuu18\Use-LLLM\src")

import server
import session_state
from use_lllm.core.tool_result_contract import read_missing_state

DATASET = r"C:\Users\yuu18\datasets\a_lipidome_landscape_of_aging_in_mice\rplc\kidney\neg"

session_state.session = server.AnalysisSession()

# 空セッションで PCA を呼ぶ → クライアントが復旧手段を読み取れること
found = read_missing_state(server.arf_pca_preprocessed())
assert found is not None, "エンベロープが読めない"
assert found.required_tools == ("arf_preprocess",), found.required_tools
print("missing_state ->", found.state, found.required_tools)

# 前提を満たしてから呼ぶ → 成功しエンベロープにならないこと
server.load_dataset(DATASET)
server.arf_preprocess(normalize="none", impute="half_min")
out = server.arf_pca_preprocessed()
assert read_missing_state(out) is None, "成功結果がエンベロープとして誤検知された"
print("recovered PCA ->", " ".join(out.split())[:80])
print("OK")
```

Run: `python <scratchpad>/verify_contract.py`
Expected: `OK` で終了。`missing_state -> preprocessed_matrix ('arf_preprocess',)` が出る。

- [ ] **Step 2: 両リポジトリの全テストを最終確認する**

```bash
cd /c/Users/yuu18/Lipidmix_with_LLM && python -m pytest tests/ -q
cd /c/Users/yuu18/Use-LLLM && .venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: どちらも失敗0

- [ ] **Step 3: クライアントに ms-data-parser の名前が残っていないことを確認する**

```bash
cd /c/Users/yuu18/Use-LLLM
grep -rn "arf_\|pai2_\|eic_\|ms-data-parser\|dcl_\|ingest_\|volcano" --include=*.py src/ | grep -v __pycache__
```
Expected: **1件も出ない。** 出たら製品コードに残ったサーバ固有知識なので取り除く（テストのフィクスチャは対象外なので `src/` に限定して検索する）。

- [ ] **Step 4: HISTRY と task.md を更新してコミット**

`docs/HISTRY.md` の先頭（`## 2026-08-07 パーサ別セッション状態の分離` の直前）へ節を追加し、`docs/task.md` の「T. パーサ別セッション状態の分離」にある「未着手の関連」を DONE に更新する。

```bash
cd /c/Users/yuu18/Lipidmix_with_LLM
git add docs/HISTRY.md docs/task.md
git commit -m "docs: MCP 状態契約の汎用化を記録

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-Review

**スペック網羅:**

| 設計書の節 | 対応タスク |
| --- | --- |
| §3.1 エンベロープ | Task 2（生成）, Task 7（解釈） |
| §3.2 annotations の解釈 | Task 4（宣言）, Task 8（判定）, Task 5（文書化） |
| §4.1 `mcp_errors.py` | Task 2 |
| §4.2 13箇所の置換 | Task 3 |
| §4.3 40ツールの annotations | Task 4 |
| §4.4 サーバ側ドキュメント | Task 5 |
| §5.1 `tool_result_contract.py` | Task 7 |
| §5.2 削除するもの | Task 8（policy）, Task 9（mcp_state_policy / 世代ゲート） |
| §5.2.1 `MCPClient` の付け替え | Task 8 Step 4 |
| §5.3 リカバリのアルゴリズム | Task 9 |
| §5.4 判定順 | Task 8 Step 1（テスト）, Step 3（実装） |
| §6 承認挙動の差分 | Task 1（先行固定）, Task 8（検証） |
| §7 エラー処理 | Task 7（解釈の全経路）, Task 9（復旧失敗の経路） |
| §8 テスト戦略 | 各タスクに分散 |
| §10 既知の別件 | Task 6 |

**型の一貫性:** `MissingState.required_tools` は `tuple[str, ...]`（Task 7 で定義、Task 9 で消費）。`mcp_errors.missing_state` の `required_tools` は `list[str]`（JSON 化するため）。`MCPRegistry.is_replay_safe` は `qualified_name`（`server::tool`）を取り、`policy.is_replay_safe` は annotations の Mapping を取る。名前が同じで引数が違うので、`mcp_registry.py` では `policy_is_replay_safe` として別名 import する（Task 9 Step 3 に明記済み）。

**設計との差分1件:** 設計書 §4.2 は `arf_plot_volcano` を「本文で返す形へ揃える」としていたが、このツールの戻り値は構造化ペイロード `VolcanoPlotPayload` で、`str` を返すと FastMCP の outputSchema 導出が壊れる。Task 3 Step 3 で**例外の本文にエンベロープを載せる**方式へ変更し、その結果として Task 7 のパーサに「前置テキストを落として埋め込み JSON を読む」要件を足した（Task 7 のテスト `test_reads_an_envelope_embedded_in_surrounding_text`）。
