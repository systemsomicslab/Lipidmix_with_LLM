# 解釈品質評価ハーネス Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ローカル3モデル（qwen3:14b think OFF/ON, qwen2.5:7b）の科学的解釈品質を、凍結した tool 出力に対する盲検ペア比較で単離測定するハーネスを構築し、実行して「モデル選定＋エスカレーション集合」を得る。

**Architecture:** 純粋ロジック（ケース検証・解釈メッセージ構築・盲検ペアリング・集計）を `interp_eval.py` に集約し unittest で検証。ケース定義を `interp_eval_cases.py` にデータとして分離。I/O オーケストレーション（tool 実行での凍結・Ollama 生成・シート出力）を手動スクリプト `interp_eval_run.py` に置く（既存 `phase_router_eval.py` と同じ「Ollama 起動前提の手動回帰」パターン）。採点はこの Claude Code セッション（私）が生成された盲検シートを読んで行う。

**Tech Stack:** Python 3 / unittest / pytest / httpx / Ollama `/api/chat` / 既存 `agent_core.execute_tool`・`phase_router`・`session_state`・MS-DIAL ツール群。

## Global Constraints

- 実行は repo ルートから `.venv-1/Scripts/python.exe`（`PYTHONPATH` は repo ルート＝カレント）。テストは `.venv-1/Scripts/python.exe -m pytest tests/<file> -v`。
- テストは既存様式に合わせ `unittest.TestCase` で書く。tool を触るテストは `setUp` で `session_state.session = server.AnalysisSession()` を張り直す。
- ツール実行は必ず `agent_core.execute_tool(name, args)`（in-process・never-raises・戻り値は常に str）経由。tool を直呼びしない。
- LLM が「解釈のみ」を行うよう、生成時は **tools を渡さない**（`/api/chat` の `tools` を省く）。温度は 0。
- 出場者モデルキーは固定 3 種: `"qwen3_off"`(qwen3:14b, think False) / `"qwen3_on"`(qwen3:14b, think True) / `"qwen25_7b"`(qwen2.5:7b, think False)。
- 5軸キー固定: `AXES = ["hallucination", "accuracy", "completeness", "utility", "language"]`。①hallucination が最上位ゲート。
- フェーズラベル固定: `PHASE_LABELS = ["PCA", "DIFFERENTIAL", "QC", "IDENTITY", "LITERATURE"]`、モード固定: `MODES = ["NEG", "POS"]`。ケースは 5×2=10 件、(phase, mode) の組が過不足なく1件ずつ。
- データ源: `C:\Users\yuu18\datasets\2_lipidome_lcms\NEG` と `...\POS`。
- 出力物は `interp_eval_out/`（.gitignore 対象、大きい中間物はコミットしない）。最終所見のみ docs にコミット。

---

### Task 1: 純粋ライブラリの土台 — ケース schema と検証

**Files:**
- Create: `interp_eval.py`
- Test: `tests/test_interp_eval.py`

**Interfaces:**
- Produces:
  - 定数 `AXES: list[str]`, `PHASE_LABELS: list[str]`, `MODES: list[str]`, `MODEL_KEYS: list[str]`
  - `@dataclass ToolStep(name: str, args: dict)`
  - `@dataclass Case(id: str, phase_label: str, mode: str, query: str, pipeline: list[ToolStep])`
  - `@dataclass FrozenCase(id: str, phase_label: str, mode: str, query: str, tool_name: str, tool_args: dict, tool_output: str)`
  - `validate_cases(cases: list[Case], allowed_tools: set[str]) -> list[str]`（エラー文字列のリスト。空なら妥当）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interp_eval.py
import unittest

import interp_eval as ie


def _valid_cases():
    cases = []
    for phase in ie.PHASE_LABELS:
        for mode in ie.MODES:
            cases.append(ie.Case(
                id=f"{phase.lower()}_{mode.lower()}",
                phase_label=phase,
                mode=mode,
                query=f"{phase} {mode} を解釈して",
                pipeline=[ie.ToolStep("load_dataset", {"directory": "X"})],
            ))
    return cases


class TestValidateCases(unittest.TestCase):
    def setUp(self):
        self.allowed = {"load_dataset", "arf_re_pca"}

    def test_valid_set_has_no_errors(self):
        self.assertEqual(ie.validate_cases(_valid_cases(), self.allowed), [])

    def test_wrong_count_flagged(self):
        errs = ie.validate_cases(_valid_cases()[:-1], self.allowed)
        self.assertTrue(any("10" in e for e in errs))

    def test_duplicate_phase_mode_flagged(self):
        cases = _valid_cases()
        cases[1] = ie.Case(cases[1].id + "x", cases[0].phase_label, cases[0].mode,
                           "q", [ie.ToolStep("load_dataset", {})])
        errs = ie.validate_cases(cases, self.allowed)
        self.assertTrue(any("重複" in e for e in errs))

    def test_unknown_tool_flagged(self):
        cases = _valid_cases()
        cases[0].pipeline.append(ie.ToolStep("no_such_tool", {}))
        errs = ie.validate_cases(cases, self.allowed)
        self.assertTrue(any("no_such_tool" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'interp_eval'`）

- [ ] **Step 3: Write minimal implementation**

```python
# interp_eval.py
"""解釈品質評価の純粋ロジック（Ollama 非依存・unittest で検証）。

I/O オーケストレーションは interp_eval_run.py、ケース定義は interp_eval_cases.py。
"""
from collections import Counter
from dataclasses import dataclass, field

AXES = ["hallucination", "accuracy", "completeness", "utility", "language"]
PHASE_LABELS = ["PCA", "DIFFERENTIAL", "QC", "IDENTITY", "LITERATURE"]
MODES = ["NEG", "POS"]
MODEL_KEYS = ["qwen3_off", "qwen3_on", "qwen25_7b"]


@dataclass
class ToolStep:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class Case:
    id: str
    phase_label: str
    mode: str
    query: str
    pipeline: list  # list[ToolStep]（順に実行、最後の出力を解釈対象に凍結）


@dataclass
class FrozenCase:
    id: str
    phase_label: str
    mode: str
    query: str
    tool_name: str
    tool_args: dict
    tool_output: str


def validate_cases(cases, allowed_tools):
    """ケース集合の構造を検証し、エラー文字列のリストを返す（空＝妥当）。"""
    errors = []
    if len(cases) != 10:
        errors.append(f"ケース数は10であるべき（現在 {len(cases)}）。")
    ids = [c.id for c in cases]
    for dup in [i for i, n in Counter(ids).items() if n > 1]:
        errors.append(f"ID 重複: {dup}")
    seen = set()
    for c in cases:
        if c.phase_label not in PHASE_LABELS:
            errors.append(f"未知の phase_label: {c.phase_label}（{c.id}）")
        if c.mode not in MODES:
            errors.append(f"未知の mode: {c.mode}（{c.id}）")
        key = (c.phase_label, c.mode)
        if key in seen:
            errors.append(f"(phase, mode) 重複: {key}")
        seen.add(key)
        if not c.pipeline:
            errors.append(f"pipeline が空: {c.id}")
        for step in c.pipeline:
            if step.name not in allowed_tools:
                errors.append(f"許可外ツール {step.name}（{c.id}）")
    return errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py -v`
Expected: PASS（4 tests）

- [ ] **Step 5: Commit**

```bash
git add interp_eval.py tests/test_interp_eval.py
git commit -m "feat(eval): 解釈品質ハーネスのケースschemaと検証"
```

---

### Task 2: 解釈メッセージ構築（tool 出力を凍結入力に）

**Files:**
- Modify: `interp_eval.py`
- Test: `tests/test_interp_eval.py`（クラス追加）

**Interfaces:**
- Consumes: `FrozenCase`（Task 1）
- Produces: `build_interp_messages(system_prompt: str, frozen: FrozenCase) -> list[dict]`
  （`[system, user(query), assistant(tool_calls=[1件]), tool(output)]` の4メッセージ。生成側は tools を渡さず、この続きに解釈テキストだけを出させる）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interp_eval.py に追記
class TestBuildInterpMessages(unittest.TestCase):
    def _frozen(self):
        return ie.FrozenCase("pca_neg", "PCA", "NEG", "PCAを解釈して",
                             "arf_re_pca", {"top_features": 10}, '{"pc1": 42.0}')

    def test_shape_and_roles(self):
        msgs = ie.build_interp_messages("SYS", self._frozen())
        self.assertEqual([m["role"] for m in msgs],
                         ["system", "user", "assistant", "tool"])
        self.assertEqual(msgs[0]["content"], "SYS")
        self.assertEqual(msgs[1]["content"], "PCAを解釈して")

    def test_tool_call_and_result_wired(self):
        msgs = ie.build_interp_messages("SYS", self._frozen())
        call = msgs[2]["tool_calls"][0]["function"]
        self.assertEqual(call["name"], "arf_re_pca")
        self.assertEqual(call["arguments"], {"top_features": 10})
        self.assertEqual(msgs[3]["content"], '{"pc1": 42.0}')
        self.assertEqual(msgs[3]["tool_name"], "arf_re_pca")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py::TestBuildInterpMessages -v`
Expected: FAIL（`AttributeError: module 'interp_eval' has no attribute 'build_interp_messages'`）

- [ ] **Step 3: Write minimal implementation**

```python
# interp_eval.py に追記
def build_interp_messages(system_prompt, frozen):
    """凍結ケースから『ツール結果まで済んだ会話』を組み、続きに解釈だけ出させる。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": frozen.query},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": frozen.tool_name, "arguments": frozen.tool_args}}]},
        {"role": "tool", "content": frozen.tool_output, "tool_name": frozen.tool_name},
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py -v`
Expected: PASS（6 tests）

- [ ] **Step 5: Commit**

```bash
git add interp_eval.py tests/test_interp_eval.py
git commit -m "feat(eval): 凍結tool出力から解釈メッセージを構築"
```

---

### Task 3: 盲検ペアリングと匿名化シート

**Files:**
- Modify: `interp_eval.py`
- Test: `tests/test_interp_eval.py`（クラス追加）

**Interfaces:**
- Consumes: `FrozenCase`, `MODEL_KEYS`
- Produces:
  - `make_pairs(model_keys: list[str]) -> list[tuple[str, str]]`（全非順序ペア、要素はソート済み。3モデル→3ペア）
  - `build_blind_sheet(frozen_cases: list[FrozenCase], interp: dict, model_keys: list[str], seed: int) -> tuple[list[dict], dict]`
    - `interp`: `{(case_id, model_key): text}`
    - 戻り: `(items, answer_key)`。`items` は各要素 `{"case_id","phase_label","mode","pair_key","a_text","b_text"}`（**モデル名を含まない**＝盲検）。`answer_key` は `{f"{case_id}::{pair_key}": {"A": model_key, "B": model_key}}`。
    - `pair_key` は `f"{m1}__{m2}"`（ソート済み）。A/B の割当は `seed` 由来の乱数で決定的にシャッフル。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interp_eval.py に追記
class TestBlindSheet(unittest.TestCase):
    def test_make_pairs_three_models(self):
        pairs = ie.make_pairs(["a", "b", "c"])
        self.assertEqual(pairs, [("a", "b"), ("a", "c"), ("b", "c")])

    def _frozen_list(self):
        return [ie.FrozenCase(f"c{i}", "PCA", "NEG", "q", "t", {}, "out")
                for i in range(2)]

    def _interp(self):
        d = {}
        for i in range(2):
            for m in ie.MODEL_KEYS:
                d[(f"c{i}", m)] = f"text-{i}-{m}"
        return d

    def test_sheet_is_blind_and_key_recovers_models(self):
        items, key = ie.build_blind_sheet(self._frozen_list(), self._interp(),
                                          ie.MODEL_KEYS, seed=7)
        # 2ケース × 3ペア = 6 項目
        self.assertEqual(len(items), 6)
        # 盲検: 項目にモデル名が出ない
        for it in items:
            self.assertNotIn("model", it)
            blob = it["a_text"] + it["b_text"]
            # a_text/b_text は該当ケースの2モデル出力のどちらか
            self.assertTrue(blob.startswith("text-") or "text-" in blob)
        # answer_key で A/B の実モデルを復元でき、pair_key の2モデルと一致
        for it in items:
            ak = key[f"{it['case_id']}::{it['pair_key']}"]
            self.assertEqual({ak["A"], ak["B"]}, set(it["pair_key"].split("__")))

    def test_deterministic_with_seed(self):
        a = ie.build_blind_sheet(self._frozen_list(), self._interp(), ie.MODEL_KEYS, 7)
        b = ie.build_blind_sheet(self._frozen_list(), self._interp(), ie.MODEL_KEYS, 7)
        self.assertEqual(a, b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py::TestBlindSheet -v`
Expected: FAIL（`AttributeError: ... 'make_pairs'`）

- [ ] **Step 3: Write minimal implementation**

```python
# interp_eval.py 冒頭の import に追記
import itertools
import random

# interp_eval.py に追記
def make_pairs(model_keys):
    """全非順序ペアをソート済みタプルで返す。"""
    return [tuple(sorted(p)) for p in itertools.combinations(sorted(model_keys), 2)]


def build_blind_sheet(frozen_cases, interp, model_keys, seed):
    """ケース×モデルペアの盲検比較シートと復元用 answer_key を作る。"""
    rng = random.Random(seed)
    items = []
    answer_key = {}
    for fc in frozen_cases:
        for m1, m2 in make_pairs(model_keys):
            pair_key = f"{m1}__{m2}"
            if rng.random() < 0.5:
                a_model, b_model = m1, m2
            else:
                a_model, b_model = m2, m1
            items.append({
                "case_id": fc.id,
                "phase_label": fc.phase_label,
                "mode": fc.mode,
                "pair_key": pair_key,
                "a_text": interp[(fc.id, a_model)],
                "b_text": interp[(fc.id, b_model)],
            })
            answer_key[f"{fc.id}::{pair_key}"] = {"A": a_model, "B": b_model}
    return items, answer_key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py -v`
Expected: PASS（9 tests）

- [ ] **Step 5: Commit**

```bash
git add interp_eval.py tests/test_interp_eval.py
git commit -m "feat(eval): 盲検ペアリングと匿名化シート生成"
```

---

### Task 4: 採点結果の集計（勝率・軸別・フェーズ×軸）

**Files:**
- Modify: `interp_eval.py`
- Test: `tests/test_interp_eval.py`（クラス追加）

**Interfaces:**
- Consumes: `AXES`, `MODEL_KEYS`
- Produces:
  - `@dataclass PairVerdict(case_id: str, phase_label: str, mode: str, pair_key: str, overall: str, axes: dict)`
    （`overall` と `axes[axis]` は勝ったモデルキー、または `"tie"`）
  - `aggregate(verdicts: list[PairVerdict], model_keys: list[str]) -> dict`
    - 戻り: `{"overall": {model: {"win","loss","tie"}}, "by_axis": {axis: {model: wins}}, "by_phase_axis": {f"{phase}|{axis}": {model: wins}}}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interp_eval.py に追記
class TestAggregate(unittest.TestCase):
    def test_overall_and_axis_tallies(self):
        verdicts = [
            ie.PairVerdict("c0", "PCA", "NEG", "qwen25_7b__qwen3_off",
                           overall="qwen3_off",
                           axes={"hallucination": "qwen3_off", "accuracy": "tie",
                                 "completeness": "qwen3_off", "utility": "tie",
                                 "language": "qwen3_off"}),
            ie.PairVerdict("c0", "PCA", "NEG", "qwen3_off__qwen3_on",
                           overall="tie",
                           axes={a: "tie" for a in ie.AXES}),
        ]
        agg = ie.aggregate(verdicts, ie.MODEL_KEYS)
        self.assertEqual(agg["overall"]["qwen3_off"], {"win": 1, "loss": 0, "tie": 1})
        self.assertEqual(agg["overall"]["qwen25_7b"], {"win": 0, "loss": 1, "tie": 0})
        self.assertEqual(agg["by_axis"]["hallucination"]["qwen3_off"], 1)
        self.assertEqual(agg["by_phase_axis"]["PCA|completeness"]["qwen3_off"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py::TestAggregate -v`
Expected: FAIL（`AttributeError: ... 'PairVerdict'`）

- [ ] **Step 3: Write minimal implementation**

```python
# interp_eval.py に追記
@dataclass
class PairVerdict:
    case_id: str
    phase_label: str
    mode: str
    pair_key: str
    overall: str      # モデルキー or "tie"
    axes: dict        # axis -> モデルキー or "tie"


def _blank_counts(model_keys):
    return {m: {"win": 0, "loss": 0, "tie": 0} for m in model_keys}


def aggregate(verdicts, model_keys):
    """盲検採点（PairVerdict 群）を集計する。"""
    overall = _blank_counts(model_keys)
    by_axis = {ax: {m: 0 for m in model_keys} for ax in AXES}
    by_phase_axis = {}
    for v in verdicts:
        m1, m2 = v.pair_key.split("__")
        if v.overall == "tie":
            overall[m1]["tie"] += 1
            overall[m2]["tie"] += 1
        else:
            loser = m2 if v.overall == m1 else m1
            overall[v.overall]["win"] += 1
            overall[loser]["loss"] += 1
        for ax in AXES:
            winner = v.axes.get(ax, "tie")
            if winner != "tie":
                by_axis[ax][winner] += 1
                key = f"{v.phase_label}|{ax}"
                slot = by_phase_axis.setdefault(key, {m: 0 for m in model_keys})
                slot[winner] += 1
    return {"overall": overall, "by_axis": by_axis, "by_phase_axis": by_phase_axis}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py -v`
Expected: PASS（10 tests）

- [ ] **Step 5: Commit**

```bash
git add interp_eval.py tests/test_interp_eval.py
git commit -m "feat(eval): 盲検採点の集計（勝率・軸別・フェーズ×軸）"
```

---

### Task 5: Ollama 生成ラッパ（モデル／think 可変・tools なし）

**Files:**
- Modify: `interp_eval.py`
- Test: `tests/test_interp_eval.py`（クラス追加）

**Interfaces:**
- Produces: `ollama_generate(messages: list, model: str, think: bool, url: str = phase_router.OLLAMA_URL, timeout: int = 300) -> str`
  （`/api/chat` に **tools を渡さず** 問い合わせ、`message.content` を返す。温度0）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interp_eval.py に追記
from unittest import mock

class TestOllamaGenerate(unittest.TestCase):
    def test_posts_without_tools_and_returns_content(self):
        captured = {}

        class FakeResp:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": "解釈テキスト"}}

        def fake_post(url, json=None, timeout=None):
            captured["payload"] = json
            return FakeResp()

        with mock.patch("interp_eval.httpx.post", side_effect=fake_post):
            out = ie.ollama_generate([{"role": "user", "content": "x"}],
                                     model="qwen3:14b", think=True)
        self.assertEqual(out, "解釈テキスト")
        self.assertNotIn("tools", captured["payload"])
        self.assertEqual(captured["payload"]["model"], "qwen3:14b")
        self.assertTrue(captured["payload"]["think"])
        self.assertEqual(captured["payload"]["options"]["temperature"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py::TestOllamaGenerate -v`
Expected: FAIL（`AttributeError: ... 'ollama_generate'` または `httpx` 未 import）

- [ ] **Step 3: Write minimal implementation**

```python
# interp_eval.py 冒頭の import に追記
import httpx

import phase_router

# interp_eval.py に追記
def ollama_generate(messages, model, think, url=None, timeout=300):
    """解釈のみ（tools なし）で Ollama に問い合わせ、content を返す。"""
    url = url or phase_router.OLLAMA_URL
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {"temperature": 0},
    }
    resp = httpx.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["message"].get("content") or ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py -v`
Expected: PASS（11 tests）

- [ ] **Step 5: Commit**

```bash
git add interp_eval.py tests/test_interp_eval.py
git commit -m "feat(eval): Ollama生成ラッパ（model/think可変・toolsなし）"
```

---

### Task 6: 具体10ケースの定義（実データからの discovery 込み）

**Files:**
- Create: `interp_eval_cases.py`
- Test: `tests/test_interp_eval_cases.py`

**Interfaces:**
- Consumes: `Case`, `ToolStep`, `validate_cases`, `agent_core`（allowed tools 源）
- Produces: `CASES: list[Case]`（10件）, `DIR_NEG: str`, `DIR_POS: str`

**discovery 手順（コード記述の前に実施）:** 差次的解析ケースの `group_a`/`group_b` は実データの群名に依存する。次を一度走らせて実際の群名を得る（NEG/POS 各々）:

```bash
.venv-1/Scripts/python.exe - <<'PY'
import server, session_state, agent_core as ac
for d in [r"C:\Users\yuu18\datasets\2_lipidome_lcms\NEG",
          r"C:\Users\yuu18\datasets\2_lipidome_lcms\POS"]:
    session_state.session = server.AnalysisSession()
    print("==== ", d)
    print(ac.execute_tool("load_dataset", {"directory": d})[:400])
    print("-- classes --")
    print(ac.execute_tool("arf_list_classes", {})[:800])
    print("-- roles --")
    print(ac.execute_tool("arf_list_sample_roles", {})[:800])
PY
```

出力の `class_counts` / `factors_by_position` / サンプル `group` から、対比させたい2群を選び、下の `group_a`/`group_b` を **実際の群名に置換** する（下記コードの値は雛形。discovery 結果で必ず上書きする）。同様に IDENTITY/LITERATURE で現データが動かない場合は、その行のツール/引数を discovery 結果に合わせて差し替える（例: `arf2_annotate_identities` が空なら `pai2_get_top_metabolites` 系へ）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interp_eval_cases.py
import unittest

import interp_eval as ie
import interp_eval_cases as cases
import agent_core as ac


class TestCasesWellFormed(unittest.TestCase):
    def test_validate_passes(self):
        errs = ie.validate_cases(cases.CASES, ac._ALLOWED_TOOLS)
        self.assertEqual(errs, [], msg=f"validate errors: {errs}")

    def test_covers_five_phases_two_modes(self):
        combos = {(c.phase_label, c.mode) for c in cases.CASES}
        expected = {(p, m) for p in ie.PHASE_LABELS for m in ie.MODES}
        self.assertEqual(combos, expected)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval_cases.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'interp_eval_cases'`）

- [ ] **Step 3: Write minimal implementation**

```python
# interp_eval_cases.py
"""解釈品質評価の具体10ケース（5フェーズ × NEG/POS）。

各 Case の pipeline は順に execute_tool され、最後のツール出力が解釈対象。
差次的解析の group_a/group_b は discovery 結果で実際の群名に置換すること。
"""
from interp_eval import Case, ToolStep

DIR_NEG = r"C:\Users\yuu18\datasets\2_lipidome_lcms\NEG"
DIR_POS = r"C:\Users\yuu18\datasets\2_lipidome_lcms\POS"


def _pca(mode, directory):
    return Case(
        id=f"pca_{mode.lower()}", phase_label="PCA", mode=mode,
        query="このPCA結果を解釈し、群分離の有無と生物学的な意味を述べて。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_re_pca", {"top_features": 10}),
        ],
    )


def _differential(mode, directory, group_a, group_b):
    return Case(
        id=f"differential_{mode.lower()}", phase_label="DIFFERENTIAL", mode=mode,
        query=f"{group_a} と {group_b} の差次的解析結果を解釈し、"
              "有意な脂質と注意点を述べて。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_preprocess", {"normalize": "median", "impute": "half_min"}),
            ToolStep("arf_differential", {"group_a": group_a, "group_b": group_b}),
        ],
    )


def _qc(mode, directory):
    return Case(
        id=f"qc_{mode.lower()}", phase_label="QC", mode=mode,
        query="この前処理/QCレポートを解釈し、データ品質の問題と対処を述べて。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_preprocess",
                     {"normalize": "median", "max_qc_rsd": 30, "impute": "half_min"}),
        ],
    )


def _identity(mode, directory):
    return Case(
        id=f"identity_{mode.lower()}", phase_label="IDENTITY", mode=mode,
        query="この脂質同定結果を解釈し、確信度と過剰主張のリスクを述べて。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf2_annotate_identities", {"max_rows": 30}),
        ],
    )


def _literature(mode, query_text):
    return Case(
        id=f"literature_{mode.lower()}", phase_label="LITERATURE", mode=mode,
        query="この文献検索結果を解釈し、どの知見が解析仮説を支持するか述べて。",
        pipeline=[ToolStep("paper_search", {"query": query_text, "max_results": 10})],
    )


# discovery 結果で group_a/group_b を実際の群名に置換すること（下は雛形）。
CASES = [
    _pca("NEG", DIR_NEG),
    _pca("POS", DIR_POS),
    _differential("NEG", DIR_NEG, group_a="GROUP_A_NEG", group_b="GROUP_B_NEG"),
    _differential("POS", DIR_POS, group_a="GROUP_A_POS", group_b="GROUP_B_POS"),
    _qc("NEG", DIR_NEG),
    _qc("POS", DIR_POS),
    _identity("NEG", DIR_NEG),
    _identity("POS", DIR_POS),
    _literature("NEG", "liver lipidomics ceramide disease association"),
    _literature("POS", "hepatic phosphatidylcholine remodeling metabolism"),
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval_cases.py -v`
Expected: PASS（2 tests）。`test_validate_passes` が落ちる場合は許可外ツール名を discovery に合わせて修正。

- [ ] **Step 5: Commit**

```bash
git add interp_eval_cases.py tests/test_interp_eval_cases.py
git commit -m "feat(eval): 具体10ケース（5フェーズ×NEG/POS）定義"
```

---

### Task 7: オーケストレーション runner（freeze / generate / sheet / aggregate）

**Files:**
- Create: `interp_eval_run.py`
- Modify: `.gitignore`（`interp_eval_out/` を追加）

**Interfaces:**
- Consumes: `interp_eval`（全 API）, `interp_eval_cases.CASES`, `agent_core.execute_tool`, `server.AnalysisSession`
- Produces（CLI サブコマンド）:
  - `freeze` → 各ケースの pipeline を順に `execute_tool` し、最後の出力を `FrozenCase` として `interp_eval_out/frozen/{id}.json` に保存
  - `generate` → frozen × 3モデル で `ollama_generate`、`interp_eval_out/interp/{id}__{model_key}.txt` に保存
  - `sheet` → `build_blind_sheet` で `interp_eval_out/blind_sheet.md`（モデル名なし）＋ `answer_key.json` を出力
  - `aggregate` → `interp_eval_out/verdicts.json`（採点後に手で用意）を読み `summary.json` ＋ `summary.md` を出力

**注:** これは Ollama 起動＋実データ前提の手動スクリプト（`phase_router_eval.py` と同様、ユニットテスト対象外）。検証は Task 8 の実走で行う。

- [ ] **Step 1: `.gitignore` に出力ディレクトリを追加**

```
# 追記
interp_eval_out/
```

- [ ] **Step 2: runner を実装**

```python
# interp_eval_run.py
"""解釈品質評価の手動オーケストレータ（Ollama 起動＋実データ前提）。

使い方（repo ルートから）:
    .venv-1/Scripts/python.exe interp_eval_run.py freeze
    .venv-1/Scripts/python.exe interp_eval_run.py generate
    .venv-1/Scripts/python.exe interp_eval_run.py sheet
    # ↑ で出た blind_sheet.md を審判が採点し verdicts.json を用意してから:
    .venv-1/Scripts/python.exe interp_eval_run.py aggregate
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

import agent_core as ac
import interp_eval as ie
import server
import session_state
from interp_eval_cases import CASES

OUT = Path("interp_eval_out")
FROZEN = OUT / "frozen"
INTERP = OUT / "interp"
SYSTEM = ("あなたはMS-DIALリピドミクス解析アシスタントです。直前のツール結果だけを"
          "根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を"
          "創作しないこと。")

# (model_key, ollama model, think)
MODELS = [("qwen3_off", "qwen3:14b", False),
          ("qwen3_on", "qwen3:14b", True),
          ("qwen25_7b", "qwen2.5:7b", False)]


def do_freeze():
    FROZEN.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        session_state.session = server.AnalysisSession()
        out = ""
        for step in case.pipeline:
            out = ac.execute_tool(step.name, step.args)
        last = case.pipeline[-1]
        fc = ie.FrozenCase(case.id, case.phase_label, case.mode, case.query,
                           last.name, last.args, out)
        (FROZEN / f"{case.id}.json").write_text(
            json.dumps(asdict(fc), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"froze {case.id} ({len(out)} chars)")


def _load_frozen():
    out = []
    for case in CASES:
        d = json.loads((FROZEN / f"{case.id}.json").read_text(encoding="utf-8"))
        out.append(ie.FrozenCase(**d))
    return out


def do_generate():
    INTERP.mkdir(parents=True, exist_ok=True)
    for fc in _load_frozen():
        msgs = ie.build_interp_messages(SYSTEM, fc)
        for key, model, think in MODELS:
            text = ie.ollama_generate(msgs, model=model, think=think)
            (INTERP / f"{fc.id}__{key}.txt").write_text(text, encoding="utf-8")
            print(f"generated {fc.id} / {key} ({len(text)} chars)")


def do_sheet():
    frozen = _load_frozen()
    interp = {}
    for fc in frozen:
        for key, _, _ in MODELS:
            interp[(fc.id, key)] = (INTERP / f"{fc.id}__{key}.txt").read_text(encoding="utf-8")
    items, answer_key = ie.build_blind_sheet(frozen, interp, ie.MODEL_KEYS, seed=20260707)
    (OUT / "answer_key.json").write_text(
        json.dumps(answer_key, ensure_ascii=False, indent=2), encoding="utf-8")
    fc_by_id = {fc.id: fc for fc in frozen}
    lines = ["# 盲検比較シート（審判用・モデル名は伏せてある）", ""]
    for it in items:
        fc = fc_by_id[it["case_id"]]
        lines += [
            f"## {it['case_id']} / {it['phase_label']} / {it['mode']} / pair={it['pair_key']}",
            "", "### ツール結果（根拠）", "```", fc.tool_output[:4000], "```", "",
            "### 出力A", it["a_text"], "", "### 出力B", it["b_text"], "",
            "### 採点（5軸：hallucination/accuracy/completeness/utility/language、"
            "各 A|B|tie。overall も A|B|tie）", "",
        ]
    (OUT / "blind_sheet.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT/'blind_sheet.md'} ({len(items)} items)")


def do_aggregate():
    raw = json.loads((OUT / "verdicts.json").read_text(encoding="utf-8"))
    verdicts = [ie.PairVerdict(**v) for v in raw]
    agg = ie.aggregate(verdicts, ie.MODEL_KEYS)
    (OUT / "summary.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 解釈品質サマリ", "", "## 総合勝敗"]
    for m, c in agg["overall"].items():
        lines.append(f"- {m}: win={c['win']} loss={c['loss']} tie={c['tie']}")
    lines += ["", "## 軸別勝数"]
    for ax, d in agg["by_axis"].items():
        lines.append(f"- {ax}: " + ", ".join(f"{m}={n}" for m, n in d.items()))
    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT/'summary.md'}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"freeze": do_freeze, "generate": do_generate,
     "sheet": do_sheet, "aggregate": do_aggregate}.get(cmd, lambda: print(
        "usage: interp_eval_run.py freeze|generate|sheet|aggregate"))()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: import が壊れていないことだけ確認**

Run: `.venv-1/Scripts/python.exe -c "import interp_eval_run; print('ok')"`
Expected: `ok`（実行はしない＝Ollama/データ不要の import 健全性のみ）

- [ ] **Step 4: 全ユニットテストが緑のままか確認**

Run: `.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py tests/test_interp_eval_cases.py -v`
Expected: PASS（全12 tests）

- [ ] **Step 5: Commit**

```bash
git add interp_eval_run.py .gitignore
git commit -m "feat(eval): freeze/generate/sheet/aggregate オーケストレータ"
```

---

### Task 8: 実走・盲検採点・所見文書化（ステップ4結論＋ステップ5エスカレーション集合）

**Files:**
- Create: `docs/superpowers/notes/2026-07-07-interpretation-quality-findings.md`

**前提:** Ollama 起動、`qwen3:14b` と `qwen2.5:7b` が pull 済み、NEG/POS データが存在。

- [ ] **Step 1: 凍結を実行**

Run: `.venv-1/Scripts/python.exe interp_eval_run.py freeze`
Expected: `interp_eval_out/frozen/*.json` が10件。各 `tool_output` が空/エラーでないことを目視（差次ケースは群名が正しいか特に確認。エラーなら Task 6 の discovery に戻り group 名・ツールを修正して再 freeze）。

- [ ] **Step 2: 3モデルで解釈生成**

Run: `.venv-1/Scripts/python.exe interp_eval_run.py generate`
Expected: `interp_eval_out/interp/` に 10×3=30 ファイル。think ON は所要が長い点に留意。

- [ ] **Step 3: B補助（フル run_turn）で凍結入力の妥当性を裏取り**

PCA・DIFFERENTIAL・IDENTITY から2〜3ケースを選び、champion モデル（`qwen3:14b` think OFF）で実際の `Agent.run_turn` を回し、route が選ぶツールと freeze の最終ツールが一致するか、戻り値が乖離していないかを目視で確認する。

```bash
.venv-1/Scripts/python.exe - <<'PY'
import server, session_state, phase_router, agent_core as ac
import asyncio
# tool schemas
schemas = asyncio.run(server.mcp.list_tools())
tool_schemas = {t.name: {"type":"function","function":{"name":t.name,
    "description":(t.description or "").strip(),"parameters":t.inputSchema}} for t in schemas}
session_state.session = server.AnalysisSession()
agent = ac.Agent(tool_schemas=tool_schemas, chat_fn=ac.ollama_chat,
                 execute_fn=ac.execute_tool, classify_fn=phase_router.ollama_classify_fn)
state = phase_router.RouterState(dataset_loaded=False)
conv = []
print(agent.run_turn("C:/Users/yuu18/datasets/2_lipidome_lcms/NEG のデータを読み込んで", state, conv)[:300])
print("----")
print(agent.run_turn("このPCA結果を解釈して群分離を述べて", state, conv)[:1200])
PY
```

Expected: PCA 系ツールが選ばれ、解釈が生成される。凍結入力と大きく乖離していれば Task 6 のケース定義（ツール/引数）を実運用に寄せて修正し、freeze/generate をやり直す。

- [ ] **Step 4: 盲検シート生成**

Run: `.venv-1/Scripts/python.exe interp_eval_run.py sheet`
Expected: `interp_eval_out/blind_sheet.md`（30項目＝10ケース×3ペア）と `answer_key.json`。

- [ ] **Step 5: 審判（この Claude Code セッション）が盲検採点**

`blind_sheet.md` を読み、各項目で 5軸（hallucination/accuracy/completeness/utility/language）＋ overall を `A|B|tie` で判定する。**①hallucination を最上位ゲート**とし、ツール結果にない数値・主張を出した側は他軸が良くても overall を負けにする。判定を `answer_key.json` で実モデルに変換し、`interp_eval_out/verdicts.json` を次の形で書く:

```json
[{"case_id":"pca_neg","phase_label":"PCA","mode":"NEG","pair_key":"qwen25_7b__qwen3_off",
  "overall":"qwen3_off",
  "axes":{"hallucination":"qwen3_off","accuracy":"tie","completeness":"qwen3_off",
          "utility":"tie","language":"qwen3_off"}}]
```

（`overall`/各 axis の値は実モデルキー `qwen3_off`/`qwen3_on`/`qwen25_7b` または `"tie"`。全30項目分。）

- [ ] **Step 6: 集計**

Run: `.venv-1/Scripts/python.exe interp_eval_run.py aggregate`
Expected: `interp_eval_out/summary.json` と `summary.md`。総合勝敗・軸別勝数が出る。

- [ ] **Step 7: 人手校正（ユーザー）**

ユーザーに `blind_sheet.md` から十数項目を抜き取りで採点してもらい、私の verdicts と一致するか確認する。大きくズレる軸があれば所見に「審判の信頼性の限界」として明記する。

- [ ] **Step 8: 所見を文書化（ステップ4結論＋ステップ5）**

`docs/superpowers/notes/2026-07-07-interpretation-quality-findings.md` に記述:
- **ステップ4結論**: 3モデルの総合勝率・軸別（特に hallucination で 7B/14B が負ける頻度）。think ON が think OFF に対し解釈で有意に勝つか＝6倍コストの是非。7B がフォールバックとして許容できるか。
- **ステップ5（エスカレーション集合）**: `summary.json` の `by_phase_axis` を根拠に「最良ローカル（想定 qwen3:14b think OFF）でも hallucination/accuracy が破綻するフェーズ×軸」を列挙。これが将来クラウド腕を足したときの投げ先候補。
- **人手校正の結果**と審判信頼性の注記。

- [ ] **Step 9: Commit**

```bash
git add docs/superpowers/notes/2026-07-07-interpretation-quality-findings.md
git commit -m "docs(eval): 解釈品質の実測所見とエスカレーション集合（ステップ4+5）"
```

---

## Self-Review

**1. Spec coverage:**
- 出場者3モデル（§2）→ Task 5 の `MODELS`／Global Constraints。✓
- A案・解釈単離（§3）→ Task 2 `build_interp_messages` ＋ Task 7 `freeze`/`generate`（tools なし生成）。✓
- B案・フル裏取り（§3）→ Task 8 Step 3。✓
- 盲検ペア比較・審判＝私・人手校正（§4）→ Task 3・Task 8 Step 5/7。✓
- 5軸・hallucination 最上位ゲート（§4）→ Task 4 `AXES`／Task 8 Step 5 の判定規則。✓
- フェーズ×軸集計（§4）→ Task 4 `by_phase_axis`。✓
- 10ケース・NEG/POS・データ源（§5）→ Task 6 ＋ `test_covers_five_phases_two_modes`。✓
- 成果物: 凍結/生成/盲検シート（§6）→ Task 7。✓
- ステップ5＝エスカレーション集合の文書化（§7）→ Task 8 Step 8。✓
- 未解決（§8: 現データで動くフェーズ・凍結ターン数）→ Task 6 discovery ＋ Task 8 Step 1/3 の修正ループで吸収。✓

**2. Placeholder scan:** `interp_eval_cases.py` の `GROUP_A_NEG` 等は「discovery 結果で置換する雛形」で、置換手順（Task 6 discovery ブロック）と検証（Task 8 Step 1）を明示しているため未解決プレースホルダではない。他に TBD/TODO なし。

**3. Type consistency:** `FrozenCase`/`Case`/`ToolStep`/`PairVerdict` のフィールド名、`build_interp_messages`/`build_blind_sheet`/`aggregate`/`ollama_generate` のシグネチャ、モデルキー（`qwen3_off`/`qwen3_on`/`qwen25_7b`）、軸キー、`pair_key` の `m1__m2` 形式、`answer_key` の `case_id::pair_key` 形式は Task 1→8 で一貫。`interp` dict のキー `(case_id, model_key)` も Task 3 テストと Task 7 `do_sheet` で一致。
