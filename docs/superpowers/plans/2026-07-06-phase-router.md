# phase-router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** クエリごとに解析フェーズを判定し、そのフェーズのツール群＋常時コアツールだけを LLM へ露出する純粋関数 `route()` を持つ `phase_router.py` を実装する。

**Architecture:** MCP サーバ（35ツール全露出）は非改変。router は将来の Python Agent 側の部品として独立モジュールに置く。判定はハイブリッド（状態ゲート → キーワード短絡 → LLM分類 → last_phase フォールバック）。LLM 呼び出しは注入（DI）で、既定は qwen3:14b(think OFF) の Ollama バックエンド。テスト時はフェイクを注入して Ollama 非依存で検証。

**Tech Stack:** Python 3.12、標準ライブラリ `unittest`（既存テストと同じ）、`httpx`（既存依存）、Ollama `/api/chat`。

## Global Constraints

- MCP サーバ（`server.py` 及び `tools_*.py`）に差分を出さない。router は読み取り専用に `server.mcp.list_tools()` を参照するのみ。
- `route` は純粋関数。`RouterState` の更新（`last_phase` の書き戻し）は呼び出し側の責務。
- LLM 依存は `classify_fn(query: str, candidate_phases: list[str]) -> str` の 1 点に限定（DI）。
- 分類器バックエンドは qwen3:14b、`options.temperature=0`、top-level `think=false` を固定。
- テストは既存慣習に合わせ `unittest.TestCase` で書く。実行は repo ルートから
  `.venv-1/Scripts/python.exe -m unittest tests.test_phase_router -v`。
- PHASES はツール名の**分割**（全35ツールを重複なく網羅）でなければならない。

---

### Task 1: フェーズ定数と分割整合テスト

router のデータ定義（PHASES / CORE_TOOLS / ANALYSIS_PHASES）を作り、それが
`server.mcp.list_tools()` の 35 ツールと厳密に一致（網羅かつ重複なし）することをテストで固定する。
これがツール追加/改名時のドリフト検出網になる。

**Files:**
- Create: `phase_router.py`
- Test: `tests/test_phase_router.py`

**Interfaces:**
- Consumes: `server.mcp.list_tools()`（async、`mcp.types.Tool` を返す。`.name` を使用）。
- Produces:
  - `PHASES: dict[str, list[str]]` — 8フェーズ（ENTRY/OBJECTIVE/ARF/ARF2/PAI2/EIC/LITERATURE/FIGURES）→ツール名。
  - `CORE_TOOLS: list[str]` = `["load_dataset", "list_data_files", "list_reports"]`。
  - `ANALYSIS_PHASES: list[str]` = ENTRY を除く 7 フェーズ名（順序保存）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_phase_router.py`:

```python
import asyncio
import unittest

import server
import phase_router as pr


class TestPhaseCoverage(unittest.TestCase):
    def setUp(self):
        tools = asyncio.run(server.mcp.list_tools())
        self.tool_names = {t.name for t in tools}

    def test_all_phase_tools_exist(self):
        for phase, names in pr.PHASES.items():
            for n in names:
                self.assertIn(n, self.tool_names, f"{phase}:{n} が server のツールに無い")

    def test_partition_is_exact(self):
        assigned = [n for names in pr.PHASES.values() for n in names]
        self.assertEqual(len(assigned), len(set(assigned)), "フェーズ間でツールが重複")
        self.assertEqual(set(assigned), self.tool_names,
                         "PHASES は全ツールを過不足なく分割していない")

    def test_core_tools_exist(self):
        for n in pr.CORE_TOOLS:
            self.assertIn(n, self.tool_names)

    def test_analysis_phases_exclude_entry(self):
        self.assertNotIn("ENTRY", pr.ANALYSIS_PHASES)
        self.assertEqual(set(pr.ANALYSIS_PHASES), set(pr.PHASES) - {"ENTRY"})
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_phase_router -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'phase_router'`）

- [ ] **Step 3: phase_router.py にデータ定義を実装**

`phase_router.py`:

```python
"""phase-router: クエリの解析フェーズを判定し、そのフェーズのツール＋常時コアツール
だけを LLM へ露出する。MCP サーバは非改変で、これは将来の Python Agent 側の部品。

「35ツールを一度に露出しない」が鉄則。judgement はハイブリッド（状態ゲート →
キーワード短絡 → LLM分類 → last_phase フォールバック）。LLM 依存は classify_fn の
1点のみ（DI）で、テストはフェイクを注入して Ollama 非依存で検証する。
"""

# フェーズ → そのフェーズで露出するツール名。全35ツールの厳密な分割
# （tests/test_phase_router.py の test_partition_is_exact が保証）。
PHASES: dict[str, list[str]] = {
    "ENTRY": ["load_dataset", "list_data_files", "list_reports", "read_report"],
    "OBJECTIVE": ["record_objective", "update_objective", "knowledge_coverage"],
    "ARF": [
        "arf_parser", "arf_re_pca", "arf_list_classes", "arf_list_tags",
        "arf_list_sample_roles", "arf_preprocess", "arf_pca_preprocessed",
        "arf_differential",
    ],
    "ARF2": ["arf2_parser", "arf2_annotate_identities"],
    "PAI2": [
        "pai2_parser", "pai2_get_top_metabolites",
        "pai2_inspect_metabolite_details", "pai2_update_analysis_filter",
    ],
    "EIC": [
        "eicaef_parser", "eicaef_search_by_mz_range",
        "eicaef_search_by_rt_range", "eicaef_top_peak_tops",
    ],
    "LITERATURE": [
        "paper_search", "ingest_stage", "ingest_review_queue",
        "ingest_promote", "ingest_reject", "log_search",
    ],
    "FIGURES": [
        "save_pca_figure", "save_volcano_figure", "write_report",
        "verify_peak_annotation",
    ],
}

# どのフェーズでも「入口へ戻る」操作は意味が通るため常時露出する（少数ゆえ氾濫に無害）。
CORE_TOOLS: list[str] = ["load_dataset", "list_data_files", "list_reports"]

# LLM 分類の候補集合（ENTRY は状態ゲートで扱うので除外）。
ANALYSIS_PHASES: list[str] = [p for p in PHASES if p != "ENTRY"]
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_phase_router -v`
Expected: PASS（4 テスト）。もし `test_partition_is_exact` が落ちたら、実ツール名との差分
（欠落/余剰/改名）を PHASES 側で修正する。

- [ ] **Step 5: コミット**

```bash
git add phase_router.py tests/test_phase_router.py
git commit -m "feat(router): フェーズ定数＋分割整合テスト（35ツール分割を固定）"
```

---

### Task 2: route 純粋関数（状態ゲート＋キーワード＋LLM＋フォールバック）

判定ロジック本体。`RouterState` / `RouteResult` データクラスと、キーワード短絡・
dedup ヘルパを実装し、フェイク classify_fn で全経路を検証する。

**Files:**
- Modify: `phase_router.py`
- Test: `tests/test_phase_router.py`

**Interfaces:**
- Consumes: Task 1 の `PHASES`, `CORE_TOOLS`, `ANALYSIS_PHASES`。
- Produces:
  - `@dataclass RouterState(dataset_loaded: bool, last_phase: str | None = None)`。
  - `@dataclass RouteResult(phase: str, tool_names: list[str], reason: str)`。
  - `_match_keyword(query: str) -> str | None`。
  - `_dedup(seq: list[str]) -> list[str]`（順序保存）。
  - `route(query: str, state: RouterState, classify_fn: Callable[[str, list[str]], str]) -> RouteResult`。
    `reason` は `"gate" | "keyword" | "llm" | "fallback"` のいずれか。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_phase_router.py` に追記:

```python
class TestRoute(unittest.TestCase):
    @staticmethod
    def _fake(ret, spy=None):
        def fn(query, candidates):
            if spy is not None:
                spy.append((query, candidates))
            return ret
        return fn

    def test_gate_entry_when_not_loaded(self):
        spy = []
        st = pr.RouterState(dataset_loaded=False)
        res = pr.route("PAI2ファイルを解析して", st, self._fake("PAI2", spy))
        self.assertEqual(res.phase, "ENTRY")
        self.assertEqual(res.reason, "gate")
        self.assertEqual(spy, [])  # LLM を呼ばない

    def test_keyword_pai2_short_circuits(self):
        spy = []
        st = pr.RouterState(dataset_loaded=True)
        res = pr.route("C:/data/x.pai2 を解析して", st, self._fake("EIC", spy))
        self.assertEqual(res.phase, "PAI2")
        self.assertEqual(res.reason, "keyword")
        self.assertEqual(spy, [])  # キーワードヒット時は LLM を呼ばない

    def test_keyword_eic_and_literature(self):
        st = pr.RouterState(dataset_loaded=True)
        r1 = pr.route("m/z 700〜720 のEICピークを検索して", st, self._fake("ARF"))
        self.assertEqual(r1.phase, "EIC")
        r2 = pr.route("Europe PMC で文献を検索して", st, self._fake("ARF"))
        self.assertEqual(r2.phase, "LITERATURE")

    def test_llm_used_when_no_keyword(self):
        st = pr.RouterState(dataset_loaded=True)
        res = pr.route("保留中ノートのレビューキューを見せて", st, self._fake("LITERATURE"))
        self.assertEqual(res.phase, "LITERATURE")
        self.assertEqual(res.reason, "llm")

    def test_fallback_to_last_phase(self):
        st = pr.RouterState(dataset_loaded=True, last_phase="ARF")
        res = pr.route("なにか曖昧な要求", st, self._fake("NONSENSE"))
        self.assertEqual(res.phase, "ARF")
        self.assertEqual(res.reason, "fallback")

    def test_fallback_entry_when_no_last_phase(self):
        st = pr.RouterState(dataset_loaded=True, last_phase=None)
        res = pr.route("なにか曖昧な要求", st, self._fake("NONSENSE"))
        self.assertEqual(res.phase, "ENTRY")
        self.assertEqual(res.reason, "fallback")

    def test_core_tools_exposed_and_dedup(self):
        st = pr.RouterState(dataset_loaded=True)
        res = pr.route("群間の差次的解析を実行して", st, self._fake("ARF"))
        for c in pr.CORE_TOOLS:
            self.assertIn(c, res.tool_names)
        self.assertEqual(len(res.tool_names), len(set(res.tool_names)))
        # ARF フェーズのツールも含む
        self.assertIn("arf_differential", res.tool_names)

    def test_entry_has_no_duplicate_core(self):
        st = pr.RouterState(dataset_loaded=False)
        res = pr.route("フォルダを読み込んで", st, self._fake(""))
        self.assertEqual(len(res.tool_names), len(set(res.tool_names)))
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_phase_router.TestRoute -v`
Expected: FAIL（`AttributeError: module 'phase_router' has no attribute 'route'` 等）

- [ ] **Step 3: route 実装を追加**

`phase_router.py` の冒頭 import を追加し、末尾にロジックを実装:

```python
import re
from dataclasses import dataclass
from typing import Callable
```

```python
# 高精度・低誤爆のトークンだけを短絡に使う（曖昧語は入れない）。
KEYWORD_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\.pai2\b|pai2", re.IGNORECASE), "PAI2"),
    (re.compile(r"m/?z\b|\bEIC\b|\.aef\b|\bAEF\b", re.IGNORECASE), "EIC"),
    (re.compile(r"Europe ?PMC|PMC|文献|論文|paper", re.IGNORECASE), "LITERATURE"),
]


@dataclass
class RouterState:
    """Agent が保持する軽量ビュー。route はこれを読むだけで書き換えない。"""
    dataset_loaded: bool
    last_phase: str | None = None


@dataclass
class RouteResult:
    phase: str
    tool_names: list[str]
    reason: str  # "gate" | "keyword" | "llm" | "fallback"


def _match_keyword(query: str) -> str | None:
    for pattern, phase in KEYWORD_RULES:
        if pattern.search(query):
            return phase
    return None


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def route(
    query: str,
    state: RouterState,
    classify_fn: Callable[[str, list[str]], str],
) -> RouteResult:
    """クエリを1フェーズへ振り分け、露出ツール名（フェーズ＋コア、dedup）を返す。

    1. 状態ゲート: dataset 未ロードなら必ず ENTRY（LLM を呼ばない）。
    2. キーワード短絡: 明示的トークンにヒットしたらそのフェーズ（LLM を呼ばない）。
    3. LLM分類: 曖昧な時だけ classify_fn に解析7フェーズから1つ選ばせる。
    4. フォールバック: 分類が既知フェーズ名でなければ last_phase（無ければ ENTRY）。
    """
    if not state.dataset_loaded:
        phase, reason = "ENTRY", "gate"
    else:
        kw = _match_keyword(query)
        if kw is not None:
            phase, reason = kw, "keyword"
        else:
            candidate = classify_fn(query, list(ANALYSIS_PHASES))
            if candidate in PHASES:
                phase, reason = candidate, "llm"
            else:
                phase = state.last_phase or "ENTRY"
                reason = "fallback"

    tool_names = _dedup(PHASES[phase] + CORE_TOOLS)
    return RouteResult(phase=phase, tool_names=tool_names, reason=reason)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_phase_router -v`
Expected: PASS（Task 1 の 4 ＋ Task 2 の 8 = 12 テスト）

- [ ] **Step 5: コミット**

```bash
git add phase_router.py tests/test_phase_router.py
git commit -m "feat(router): route純粋関数（状態ゲート＋キーワード＋LLM＋フォールバック）"
```

---

### Task 3: 既定 classify_fn（qwen3 think OFF・Ollama バックエンド）

実運用の LLM 分類器を実装する。パース部分（`_parse_phase`）を純粋関数に切り出して
Ollama 非依存でテストし、HTTP 呼び出し本体（`ollama_classify_fn`）はパース結果を返すだけにする。

**Files:**
- Modify: `phase_router.py`
- Test: `tests/test_phase_router.py`

**Interfaces:**
- Consumes: `ANALYSIS_PHASES`, `PHASES`。
- Produces:
  - `PHASE_DESCRIPTIONS: dict[str, str]` — 分類器プロンプト用の各フェーズ一行説明。
  - `_parse_phase(text: str, candidates: list[str]) -> str | None` — 応答から既知フェーズ名を抽出。
  - `ollama_classify_fn(query: str, candidate_phases: list[str]) -> str` — 既定 classify_fn。
    未パース時は `""` を返す（route 側でフォールバックへ）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_phase_router.py` に追記:

```python
class TestParsePhase(unittest.TestCase):
    cands = ["ARF", "PAI2", "EIC", "LITERATURE"]

    def test_exact_match(self):
        self.assertEqual(pr._parse_phase("PAI2", self.cands), "PAI2")

    def test_whitespace_and_case(self):
        self.assertEqual(pr._parse_phase("  pai2\n", self.cands), "PAI2")

    def test_embedded_name(self):
        self.assertEqual(
            pr._parse_phase("このクエリのフェーズは LITERATURE です", self.cands),
            "LITERATURE",
        )

    def test_unknown_returns_none(self):
        self.assertIsNone(pr._parse_phase("わからない", self.cands))

    def test_descriptions_cover_analysis_phases(self):
        self.assertEqual(set(pr.PHASE_DESCRIPTIONS), set(pr.ANALYSIS_PHASES))
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_phase_router.TestParsePhase -v`
Expected: FAIL（`AttributeError: ... has no attribute '_parse_phase'`）

- [ ] **Step 3: 分類器を実装**

`phase_router.py` の import に追加:

```python
import os
import httpx
```

末尾に追加:

```python
OLLAMA_URL = os.environ.get("LIPIDMIX_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
CLASSIFIER_MODEL = os.environ.get("LIPIDMIX_ROUTER_MODEL", "qwen3:14b")

# 分類器プロンプト用の各フェーズ一行説明（キーは ANALYSIS_PHASES と一致させる）。
PHASE_DESCRIPTIONS: dict[str, str] = {
    "OBJECTIVE": "解析目的の記録・更新、知識カバレッジ(GAP)の確認",
    "ARF": "ARFアライメント行列のPCA・前処理・差次的解析・クラス/タグ/ロール一覧",
    "ARF2": "ARF2オーバービューのパースと identity 注釈",
    "PAI2": "PAI2ピークレベルのパース・上位代謝物・詳細確認・フィルタ更新",
    "EIC": "EIC/AEFクロマトのパース・m/z/RT範囲検索・ピークトップ",
    "LITERATURE": "文献検索(Europe PMC)と知識ノートのステージ/レビュー/昇格/却下",
    "FIGURES": "PCA/ボルケーノ図の保存・レポート執筆・ピークアノテーション検証",
}


def _parse_phase(text: str, candidates: list[str]) -> str | None:
    """LLM 応答から既知フェーズ名を抽出する。完全一致 → 埋め込み一致の順。"""
    stripped = text.strip().upper()
    for c in candidates:
        if c.upper() == stripped:
            return c
    for c in candidates:
        if re.search(rf"\b{re.escape(c)}\b", text, re.IGNORECASE):
            return c
    return None


def ollama_classify_fn(query: str, candidate_phases: list[str]) -> str:
    """既定 classify_fn。qwen3:14b(think OFF, temp0) にフェーズを1つ選ばせる。

    パースできなければ空文字を返し、route 側のフォールバックに委ねる。
    """
    listing = "\n".join(
        f"- {p}: {PHASE_DESCRIPTIONS[p]}" for p in candidate_phases
    )
    system = (
        "あなたはMS-DIALリピドミクス解析のルータです。ユーザーのクエリが属する"
        "解析フェーズを、次の候補からちょうど1つ選び、フェーズ名のみを大文字で"
        "答えてください（説明は不要）。\n" + listing
    )
    payload = {
        "model": CLASSIFIER_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    content = resp.json()["message"].get("content") or ""
    return _parse_phase(content, candidate_phases) or ""
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_phase_router -v`
Expected: PASS（合計 17 テスト）

- [ ] **Step 5: コミット**

```bash
git add phase_router.py tests/test_phase_router.py
git commit -m "feat(router): 既定classify_fn（qwen3 think OFF）＋フェーズ抽出パーサ"
```

---

### Task 4: ライブ回帰ハーネス（router 経由の 12 ケース実測）

scratchpad の `fc_probe.py` を repo 内の回帰ハーネスへ発展させ、`route()` を通した
subset で実 qwen3:14b を叩き、12 ケース命中率を測る。Ollama 起動時のみ実行する手動スクリプト
（pytest 自動発見の対象外）。受け入れ基準 ≥ 11/12。

**Files:**
- Create: `phase_router_eval.py`（repo ルート、`if __name__ == "__main__"` 実行のみ）

**Interfaces:**
- Consumes: `phase_router.route` / `RouterState` / `ollama_classify_fn`、`server.mcp.list_tools()`。
- Produces: 実行時に命中表と `命中: N/12` を標準出力へ。

- [ ] **Step 1: ハーネスを実装**

`phase_router_eval.py`:

```python
"""phase-router ライブ回帰ハーネス（Ollama 起動が前提の手動スクリプト）。

各ケースを route() に通し、返った subset ツールだけを Ollama に渡して、期待ツールを
選べるかを実 qwen3:14b(think OFF) で測る。受け入れ基準 >= 11/12。

使い方（repo ルートから）:
    .venv-1/Scripts/python.exe phase_router_eval.py
"""
import asyncio
import json

import httpx

import server
import phase_router as pr

OLLAMA = pr.OLLAMA_URL
MODEL = pr.CLASSIFIER_MODEL

SYSTEM = (
    "あなたはMS-DIALリピドミクス解析アシスタントです。ユーザーの要求に対し、"
    "提供されたツールから最適なものを1つ呼び出してください。"
)

# (クエリ, 期待ツール, dataset_loaded 前提)
CASES = [
    ("C:/data/exp1 のフォルダのデータを読み込んで解析を始めて", "load_dataset", False),
    ("解析フォルダにある既存レポートの一覧を見せて", "list_reports", False),
    ("analysis_id=exp1 の知識カバレッジ（GAP小問）を確認して", "knowledge_coverage", True),
    ("ロード済みのARF行列に前処理（正規化・QC）を適用して", "arf_preprocess", True),
    ("前処理後の行列で群間の差次的解析を実行して", "arf_differential", True),
    ("いまのARFデータで使えるClass IDの一覧を見せて", "arf_list_classes", True),
    ("PAI2ファイル C:/data/x.pai2 を解析して", "pai2_parser", True),
    ("PAI2の上位代謝物を見せて", "pai2_get_top_metabolites", True),
    ("m/z 700〜720 の範囲でEICピークを検索して", "eicaef_search_by_mz_range", True),
    ("Europe PMC で 'macrophage LPS ceramide' を検索して", "paper_search", True),
    ("_inbox の保留中ノートのレビューキューを見せて", "ingest_review_queue", True),
    ("analysis_id=exp1 の直近PCA結果をPNG図として保存して", "save_pca_figure", True),
]


async def get_tool_schemas():
    tools = await server.mcp.list_tools()
    return {
        t.name: {
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "").strip(),
                "parameters": t.inputSchema,
            },
        }
        for t in tools
    }


def call_ollama(tools, query):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": query},
        ],
        "tools": tools,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    r = httpx.post(OLLAMA, json=payload, timeout=300)
    r.raise_for_status()
    msg = r.json()["message"]
    calls = msg.get("tool_calls") or []
    return [c["function"]["name"] for c in calls]


def main():
    by_name = asyncio.run(get_tool_schemas())
    ok = 0
    last_phase = None
    for query, expected, loaded in CASES:
        state = pr.RouterState(dataset_loaded=loaded, last_phase=last_phase)
        rr = pr.route(query, state, pr.ollama_classify_fn)
        last_phase = rr.phase
        tools = [by_name[n] for n in rr.tool_names if n in by_name]
        got = call_ollama(tools, query)
        hit = expected in got
        ok += hit
        mark = "OK " if hit else "NG "
        print(f"{mark}[{rr.phase:10}|{rr.reason:8}|{len(tools):2}t] "
              f"exp={expected:26} got={got}")
    print("-" * 60)
    print(f"命中: {ok}/{len(CASES)}  （受け入れ基準 >= 11）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Ollama 起動を確認して実行**

Run（Ollama 起動済み前提、repo ルートから）:
`.venv-1/Scripts/python.exe phase_router_eval.py`
Expected: 各ケースが `OK`/`NG` と選ばれたフェーズ・reason・ツール数を表示し、末尾に `命中: N/12`。
`N >= 11` なら受け入れ基準クリア。ENTRY 2件は reason=gate、pai2/EIC/文献は reason=keyword、
残りは reason=llm になるはず。

- [ ] **Step 3: 結果が基準未満なら診断（>=11 なら Step 4 へ）**

`N < 11` の場合、NG 行の `phase`/`reason` を見て切り分ける:
- reason が期待フェーズと違う → 分類ミス。keyword 対象なら `KEYWORD_RULES` を、
  llm 対象なら `PHASE_DESCRIPTIONS` の該当行を明確化して再実行。
- reason は正しいがツール選択を外す → そのフェーズの subset 内の曖昧さ（spec §8 の
  ARF description 課題等）。ここでは記録に留め、フェーズ判定側は変更しない。
再実行: `.venv-1/Scripts/python.exe phase_router_eval.py`

- [ ] **Step 4: コミット**

```bash
git add phase_router_eval.py
git commit -m "test(router): ライブ回帰ハーネス（route経由12ケース、基準>=11/12）"
```

---

## Self-Review

**Spec coverage:**
- §3 PHASES 分割 → Task 1（＋分割整合テスト）。
- §4 route アルゴリズム（gate/keyword/llm/fallback＋core dedup）→ Task 2。
- §4.4 スティッキー last_phase → Task 2（`test_fallback_to_last_phase`）＋ Task 4（ループで書き戻し）。
- §5 既定分類器（qwen3 think OFF）→ Task 3。
- §6.1 ユニット（分割整合/状態ゲート/キーワード/LLM/フォールバック/dedup）→ Task 1・2・3。
- §6.2 ライブ回帰 → Task 4。
- §7 受け入れ基準（純粋関数・ユニット通過・≥11/12・サーバ無改変）→ 全タスク＋Global Constraints。

**Placeholder scan:** "TBD"/"TODO"/"適切に処理" 等なし。全コード実体を記載。

**Type consistency:** `classify_fn(query, candidate_phases) -> str`、`RouterState(dataset_loaded, last_phase)`、
`RouteResult(phase, tool_names, reason)`、`route(query, state, classify_fn)`、`_parse_phase(text, candidates)`、
`ollama_classify_fn(query, candidate_phases)` は Task 2〜4 で一貫。`OLLAMA_URL`/`CLASSIFIER_MODEL` は
Task 3 で定義し Task 4 で参照。

ギャップなし。
