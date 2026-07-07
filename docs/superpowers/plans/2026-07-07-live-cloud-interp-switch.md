# ライブ agent_core クラウド解釈切替 実装プラン（A）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ライブの `agent_core.run_turn` を2段ターンにし、高価値ツールを実行したターンのみ蓄積会話からクラウド(Azure)で最終解釈を生成、それ以外・失敗時はローカルへ縮退する。

**Architecture:** 既存の DI パターン（chat_fn/execute_fn/classify_fn 注入）に `interp_fn`（messages→text の解釈専用腕）を1本追加。ローカルがツールループを回し、ループ終端で純述語 `should_escalate(executed)` が真なら `interp_fn` へ今ターンの証拠を渡してクラウド最終解釈を得る。creds/トグルの判定は `agent_repl` に閉じ、`agent_core` は env・Azure 非依存を保つ。

**Tech Stack:** Python 3, httpx（REST のみ・新規SDK依存なし）, 既存 `interp_eval.azure_generate` / `_openai_messages`, unittest（フェイク注入）。

## Global Constraints

- 新規 SDK 依存を入れない。`azure_generate` / `_openai_messages` / `ollama_generate` の実装は不変（そのまま再利用）。
- `INTERP_SYSTEM` は単一定義（`interp_eval.py`）を両所から import する（DRY）。文言は現行 `interp_eval_run.SYSTEM` を逐語: `"あなたはMS-DIALリピドミクス解析アシスタントです。直前のツール結果だけを根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を創作しないこと。"`。
- `agent_core` は env を読まない（creds/トグル判定は `agent_repl._build_interp_fn` のみ）。
- 自動テストで実 API を叩かない（フェイク／`monkeypatch`）。
- ツール tier 定数は spec どおり: `CLOUD_TIER_TOOLS = {"arf_re_pca", "arf_pca_preprocessed", "arf_preprocess", "paper_search"}`、`LOCAL_VETO_TOOLS = {"arf_differential"}`。
- `route` は `RouterState` を書き換えない不変条件を保つ（`last_arm` は `run_turn` が最終応答時に書く別フィールド）。
- テスト実行コマンド（Git Bash）: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest <file> -v`。

---

## File Structure

- `interp_eval.py`（変更）: `INTERP_SYSTEM` 定数を新設（解釈用 system プロンプトの単一の真実源）。
- `interp_eval_run.py`（変更）: 局所定義 `SYSTEM` を `interp_eval.INTERP_SYSTEM` 参照に置換（重複排除・挙動不変）。
- `agent_core.py`（変更）: tier 定数＋純述語 `should_escalate`、`Agent.interp_fn` フィールド、`run_turn` の2段ターン化。
- `phase_router.py`（変更）: `RouterState` に `last_arm` フィールド追加（観測用）。
- `agent_repl.py`（変更）: `.env` の `AZURE_` 取り込み、`_build_interp_fn`（creds/トグルゲート）、`Agent` へ注入、腕の可視化表示。
- `tests/test_interp_eval.py`（変更）: `INTERP_SYSTEM` 共有・逐語テスト。
- `tests/test_agent_core.py`（変更）: `should_escalate` 判定表＋`run_turn` の2段ターン挙動テスト。
- `tests/test_agent_repl.py`（新規）: `_build_interp_fn` の creds/トグルゲートテスト。

---

## Task 1: INTERP_SYSTEM 共有定数

**Files:**
- Modify: `interp_eval.py`（`AXES` 群の定数付近、ファイル冒頭の定数ブロック）
- Modify: `interp_eval_run.py:37-39`
- Test: `tests/test_interp_eval.py`

**Interfaces:**
- Produces: `interp_eval.INTERP_SYSTEM: str`（解釈用 system プロンプト。Task 3 の `run_turn` が import する）。`interp_eval_run.SYSTEM` は同一値を指す。

- [ ] **Step 1: Write the failing test**

`tests/test_interp_eval.py` の末尾に追記（既存 import に続けて新規クラスを足す）:

```python
class InterpSystemSharedTests(unittest.TestCase):
    def test_interp_system_verbatim(self):
        import interp_eval
        self.assertEqual(
            interp_eval.INTERP_SYSTEM,
            "あなたはMS-DIALリピドミクス解析アシスタントです。直前のツール結果だけを"
            "根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を"
            "創作しないこと。")

    def test_run_system_is_shared(self):
        import interp_eval
        import interp_eval_run
        self.assertEqual(interp_eval_run.SYSTEM, interp_eval.INTERP_SYSTEM)
```

（`tests/test_interp_eval.py` が `import unittest` を先頭に持たない場合は追加する。）

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py::InterpSystemSharedTests -v`
Expected: FAIL（`AttributeError: module 'interp_eval' has no attribute 'INTERP_SYSTEM'`）

- [ ] **Step 3: Add the constant to interp_eval.py**

`interp_eval.py` の冒頭定数ブロック（`MODEL_KEYS = [...]` の直後、L17 付近）に追記:

```python
INTERP_SYSTEM = (
    "あなたはMS-DIALリピドミクス解析アシスタントです。直前のツール結果だけを"
    "根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を"
    "創作しないこと。")
```

- [ ] **Step 4: Point interp_eval_run.SYSTEM at the shared constant**

`interp_eval_run.py:37-39` の

```python
SYSTEM = ("あなたはMS-DIALリピドミクス解析アシスタントです。直前のツール結果だけを"
          "根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を"
          "創作しないこと。")
```

を次へ置換（`import interp_eval as ie` は L29 に既存）:

```python
SYSTEM = ie.INTERP_SYSTEM
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_interp_eval.py -v`
Expected: PASS（新規2件を含む既存の interp_eval テストが緑）

- [ ] **Step 6: Commit**

```bash
git add interp_eval.py interp_eval_run.py tests/test_interp_eval.py
git commit -m "refactor(interp): INTERP_SYSTEM を共有定数化（ライブ切替の下地）"
```

---

## Task 2: should_escalate 純述語＋tier 定数

**Files:**
- Modify: `agent_core.py`（`_LOAD_SUCCESS_MARKER = ...`（L83）と `@dataclass class Agent`（L86）の間に定数＋関数を追加）
- Test: `tests/test_agent_core.py`

**Interfaces:**
- Produces:
  - `agent_core.CLOUD_TIER_TOOLS: set[str]`
  - `agent_core.LOCAL_VETO_TOOLS: set[str]`
  - `agent_core.should_escalate(executed: set[str]) -> bool` — Task 3 の `run_turn` が呼ぶ純述語。

- [ ] **Step 1: Write the failing test**

`tests/test_agent_core.py` に新規クラスを追加（`import agent_core as ac` は L10 に既存）:

```python
class TestShouldEscalate(unittest.TestCase):
    def test_pca_tool_escalates(self):
        self.assertTrue(ac.should_escalate({"load_dataset", "arf_re_pca"}))

    def test_pca_preprocessed_escalates(self):
        self.assertTrue(ac.should_escalate({"arf_pca_preprocessed"}))

    def test_qc_preprocess_escalates(self):
        self.assertTrue(ac.should_escalate({"load_dataset", "arf_preprocess"}))

    def test_literature_escalates(self):
        self.assertTrue(ac.should_escalate({"paper_search"}))

    def test_differential_vetoes_even_with_preprocess(self):
        # arf_preprocess は cloud-tier だが arf_differential 同居で local へ降格
        self.assertFalse(
            ac.should_escalate({"load_dataset", "arf_preprocess", "arf_differential"}))

    def test_pca_and_differential_together_vetoes(self):
        self.assertFalse(ac.should_escalate({"arf_re_pca", "arf_differential"}))

    def test_identity_stays_local(self):
        self.assertFalse(ac.should_escalate({"load_dataset", "arf2_annotate_identities"}))

    def test_no_cloud_tier_stays_local(self):
        self.assertFalse(ac.should_escalate({"load_dataset"}))

    def test_empty_set_stays_local(self):
        self.assertFalse(ac.should_escalate(set()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_agent_core.py::TestShouldEscalate -v`
Expected: FAIL（`AttributeError: module 'agent_core' has no attribute 'should_escalate'`）

- [ ] **Step 3: Add constants and predicate to agent_core.py**

`agent_core.py` の `_LOAD_SUCCESS_MARKER = "データセット読み込み"`（L83）の直後に追加:

```python
# クラウド送りが分界点(7-0)で優勢だった高価値ツール（PCA/QC/文献）。実行ツール集合が
# これに触れたターンの最終解釈をクラウドへエスカレートする。
CLOUD_TIER_TOOLS = {"arf_re_pca", "arf_pca_preprocessed", "arf_preprocess", "paper_search"}
# 差次は slim 化後ローカルで互角。QC と同じ arf_preprocess を伴走するため、
# arf_differential が走ったターンはクラウド送りを取り消す（local veto）。
LOCAL_VETO_TOOLS = {"arf_differential"}


def should_escalate(executed: set[str]) -> bool:
    """このターンで実行したツール集合が高価値(クラウド送り)かを判定する純述語。"""
    return bool(executed & CLOUD_TIER_TOOLS) and not (executed & LOCAL_VETO_TOOLS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_agent_core.py::TestShouldEscalate -v`
Expected: PASS（9件）

- [ ] **Step 5: Commit**

```bash
git add agent_core.py tests/test_agent_core.py
git commit -m "feat(agent): should_escalate 純述語＋ツール tier 定数"
```

---

## Task 3: run_turn 2段ターン化（interp_fn 注入・last_arm）

**Files:**
- Modify: `phase_router.py:59-63`（`RouterState` に `last_arm` 追加）
- Modify: `agent_core.py:8-16`（import 追加）, `agent_core.py:86-93`（`Agent` に `interp_fn` 追加）, `agent_core.py:95-121`（`run_turn` 改修）
- Test: `tests/test_agent_core.py`

**Interfaces:**
- Consumes: `agent_core.should_escalate`（Task 2）, `interp_eval.INTERP_SYSTEM`（Task 1）。
- Produces:
  - `agent_core.Agent.interp_fn: Callable | None`（既定 `None`。`(messages: list) -> str`）。
  - `phase_router.RouterState.last_arm: str | None`（`"local"` / `"cloud"`。Task 4 の REPL 表示が読む）。
  - `run_turn` の契約: 高価値ターンで `interp_fn` 成功時はクラウド text を返し `last_arm="cloud"`、それ以外は従来ローカル text で `last_arm="local"`。

- [ ] **Step 1: Write the failing tests**

`tests/test_agent_core.py` の `TestRunTurn` クラス内に、まず `_agent` ヘルパを `interp_fn` 対応へ拡張し（L92-98 を置換）、続けてテストを追加する。

`_agent` ヘルパ置換:

```python
    def _agent(self, chat_fn, execute_fn, phase="ARF", interp_fn=None):
        return ac.Agent(
            tool_schemas=self.schemas,
            chat_fn=chat_fn,
            execute_fn=execute_fn,
            classify_fn=lambda q, c: phase,
            interp_fn=interp_fn,
        )
```

追加テスト（`TestRunTurn` 内、`import httpx` をファイル先頭へ追加）:

```python
    def test_high_value_tool_escalates_to_cloud(self):
        captured = {}
        def interp_fn(messages):
            captured["messages"] = messages
            return "クラウド解釈です"
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_re_pca", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        conv = []
        out = self._agent(chat, ex, interp_fn=interp_fn).run_turn("PCAを解釈して", state, conv)
        self.assertEqual(out, "クラウド解釈です")
        self.assertEqual(state.last_arm, "cloud")
        # 会話末尾はクラウド解釈（ローカル散文は積まれない）
        self.assertEqual(conv[-1], {"role": "assistant", "content": "クラウド解釈です"})
        self.assertNotIn("ローカル解釈", [m.get("content") for m in conv])
        # interp_fn への messages: 先頭が INTERP_SYSTEM、今ターンの証拠のみ
        import interp_eval
        self.assertEqual(captured["messages"][0],
                         {"role": "system", "content": interp_eval.INTERP_SYSTEM})
        self.assertEqual(captured["messages"][1], {"role": "user", "content": "PCAを解釈して"})

    def test_differential_veto_stays_local(self):
        called = {"n": 0}
        def interp_fn(messages):
            called["n"] += 1
            return "クラウド"
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_preprocess", "arguments": {}}},
                            {"function": {"name": "arf_differential", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル差次解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        out = self._agent(chat, ex, interp_fn=interp_fn).run_turn("差次を解釈して", state, [])
        self.assertEqual(out, "ローカル差次解釈")
        self.assertEqual(state.last_arm, "local")
        self.assertEqual(called["n"], 0)  # veto で interp_fn は呼ばれない

    def test_cloud_failure_falls_back_to_local(self):
        def interp_fn(messages):
            raise httpx.HTTPError("boom")
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_re_pca", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        conv = []
        out = self._agent(chat, ex, interp_fn=interp_fn).run_turn("PCAを解釈して", state, conv)
        self.assertEqual(out, "ローカル解釈")
        self.assertEqual(state.last_arm, "local")
        self.assertEqual(conv[-1], {"role": "assistant", "content": "ローカル解釈"})

    def test_empty_cloud_response_falls_back_to_local(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "paper_search", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル文献解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        out = self._agent(chat, ex, phase="LITERATURE",
                          interp_fn=lambda m: "   ").run_turn("文献を解釈して", state, [])
        self.assertEqual(out, "ローカル文献解釈")
        self.assertEqual(state.last_arm, "local")

    def test_high_value_without_interp_fn_stays_local(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_re_pca", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        out = self._agent(chat, ex).run_turn("PCAを解釈して", state, [])  # interp_fn=None（既定）
        self.assertEqual(out, "ローカル解釈")
        self.assertEqual(state.last_arm, "local")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_agent_core.py::TestRunTurn -v`
Expected: FAIL（`TypeError: Agent.__init__() got an unexpected keyword argument 'interp_fn'` および `AttributeError: 'RouterState' object has no attribute 'last_arm'`）

- [ ] **Step 3: Add last_arm to RouterState**

`phase_router.py:59-63` の `RouterState` を次へ置換:

```python
@dataclass
class RouterState:
    """Agent が保持する軽量ビュー。route はこれを読むだけで書き換えない。"""
    dataset_loaded: bool
    last_phase: str | None = None
    last_arm: str | None = None  # run_turn が最終応答時に書く（"local" / "cloud"）
```

- [ ] **Step 4: Add imports and interp_fn field to agent_core.py**

`agent_core.py` の import ブロック（L15 `import server` の直後）に追加:

```python
from interp_eval import INTERP_SYSTEM
```

`agent_core.py` の `Agent` データクラス（L86-93）に `interp_fn` を追加（`max_rounds` の前）:

```python
@dataclass
class Agent:
    tool_schemas: dict           # name -> Ollama tool schema（起動時 list_tools() から構築）
    chat_fn: Callable            # (messages: list, tools: list) -> message dict
    execute_fn: Callable         # (name: str, args: dict) -> str
    classify_fn: Callable        # (query: str, candidates: list) -> str（route 用）
    interp_fn: Callable | None = None  # (messages: list) -> str（高価値ターンのクラウド最終解釈。None=純ローカル）
    max_rounds: int = 5
    system_prompt: str = DEFAULT_SYSTEM
```

- [ ] **Step 5: Rewrite run_turn for 2-stage escalation**

`agent_core.py:95-121` の `run_turn` 本体を次へ置換:

```python
    def run_turn(self, query: str, state: RouterState, conversation: list) -> str:
        """1 ユーザーターンを実行する。route を1回引いてフェーズ固定、有界ループでツール実行。

        ループ終端（tool_call なし）で、このターンの実行ツール集合が高価値なら（interp_fn
        注入時のみ）蓄積会話をクラウドへ渡して最終解釈を得る。非高価値・interp_fn 無し・
        クラウド例外・空応答はローカル最終散文へ縮退する。state と conversation を更新し、
        最終応答テキストを返す。
        """
        routed = phase_router.route(query, state, self.classify_fn)
        state.last_phase = routed.phase
        tools = [self.tool_schemas[n] for n in routed.tool_names if n in self.tool_schemas]

        conversation.append({"role": "user", "content": query})
        turn_start = len(conversation) - 1  # 今ターンの証拠スライス起点（user を含む）
        executed: set[str] = set()
        for _ in range(self.max_rounds):
            messages = [{"role": "system", "content": self.system_prompt}] + conversation
            msg = self.chat_fn(messages, tools)
            calls = msg.get("tool_calls") or []
            if not calls:
                local_content = msg.get("content") or ""
                if self.interp_fn is not None and should_escalate(executed):
                    try:
                        cloud = self.interp_fn(
                            [{"role": "system", "content": INTERP_SYSTEM}]
                            + conversation[turn_start:])
                        if cloud.strip():
                            conversation.append({"role": "assistant", "content": cloud})
                            state.last_arm = "cloud"
                            return cloud
                    except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
                        pass  # クラウド失敗 → ローカル最終散文へフォールバック
                conversation.append({"role": "assistant", "content": local_content})
                state.last_arm = "local"
                return local_content
            conversation.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments") or {}
                executed.add(name)
                out = _truncate(self.execute_fn(name, args))
                conversation.append({"role": "tool", "content": out, "tool_name": name})
                if name == "load_dataset" and _LOAD_SUCCESS_MARKER in out:
                    state.dataset_loaded = True
        return "（ツール呼び出しが上限に達しました）"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_agent_core.py -v`
Expected: PASS（新規5件＋`TestShouldEscalate` 9件＋既存 `TestRunTurn`/`TestExecuteTool`/`TestTruncate`/`TestIsError` すべて緑。既存の `test_tool_then_final_answer` 等は `interp_fn=None` 既定で挙動不変）

- [ ] **Step 7: Commit**

```bash
git add agent_core.py phase_router.py tests/test_agent_core.py
git commit -m "feat(agent): run_turn を2段ターン化（高価値ターンのみクラウド最終解釈）"
```

---

## Task 4: agent_repl 配線（creds/トグルゲート・注入・可視化）

**Files:**
- Modify: `agent_repl.py:1-13`（import 追加＋`.env` 取り込み）, `agent_repl.py:39-46`（`_build_interp_fn` 追加・注入）, `agent_repl.py:63`（表示に腕を追加）
- Test: `tests/test_agent_repl.py`（新規）

**Interfaces:**
- Consumes: `interp_eval.azure_generate`（既存）, `agent_core.Agent.interp_fn`（Task 3）, `phase_router.RouterState.last_arm`（Task 3）。
- Produces: `agent_repl._build_interp_fn() -> Callable | None` — creds/トグルゲート。`LIPIDMIX_CLOUD_INTERP=="0"` か AZURE_ creds 欠落なら `None`、揃えば `azure_generate` ラッパを返す。

- [ ] **Step 1: Write the failing test**

`tests/test_agent_repl.py`（新規）:

```python
import unittest
from unittest import mock

import agent_repl


class BuildInterpFnGateTests(unittest.TestCase):
    _CREDS = {
        "AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com",
        "AZURE_OPENAI_API_KEY": "k",
        "AZURE_OPENAI_DEPLOYMENT": "d",
    }

    def test_kill_switch_returns_none(self):
        env = dict(self._CREDS, LIPIDMIX_CLOUD_INTERP="0")
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(agent_repl._build_interp_fn())

    def test_missing_creds_returns_none(self):
        # creds を消し、トグルは既定（未設定）
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(agent_repl._build_interp_fn())

    def test_partial_creds_returns_none(self):
        env = {"AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com"}  # key/deployment 欠落
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(agent_repl._build_interp_fn())

    def test_creds_present_returns_callable(self):
        with mock.patch.dict("os.environ", dict(self._CREDS), clear=True):
            fn = agent_repl._build_interp_fn()
        self.assertTrue(callable(fn))

    def test_creds_present_dispatches_to_azure_generate(self):
        with mock.patch.dict("os.environ", dict(self._CREDS), clear=True):
            fn = agent_repl._build_interp_fn()
        with mock.patch("interp_eval.azure_generate", return_value="解釈X") as m:
            out = fn([{"role": "system", "content": "s"}])
        self.assertEqual(out, "解釈X")
        m.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_agent_repl.py -v`
Expected: FAIL（`AttributeError: module 'agent_repl' has no attribute '_build_interp_fn'`）

- [ ] **Step 3: Add imports and .env loading to agent_repl.py**

`agent_repl.py` の import ブロック（L6-13）を次へ置換:

```python
import asyncio
import os

import httpx

import interp_eval
import phase_router
import server
from agent_core import Agent, execute_tool, ollama_chat
from phase_router import RouterState

# .env からは AZURE_ のみ取り込む（interp_eval_run.py と同じ根拠＝LIPIDMIX_* はテンプレの
# ダミーで server import を壊すため除外。既存 os.environ は上書きしない）。
try:
    from dotenv import dotenv_values, find_dotenv
    _envpath = find_dotenv(usecwd=True)
    for _k, _v in (dotenv_values(_envpath) if _envpath else {}).items():
        if _k.startswith("AZURE_") and _v and _k not in os.environ:
            os.environ[_k] = _v
except ImportError:
    pass
```

- [ ] **Step 4: Add _build_interp_fn and inject it**

`agent_repl.py` の `main()` 定義（現 L39）の直前に `_build_interp_fn` を追加:

```python
def _build_interp_fn():
    """高価値ターンのクラウド最終解釈腕を組み立てる。creds/トグル未充足なら None（純ローカル）。"""
    if os.environ.get("LIPIDMIX_CLOUD_INTERP", "1") == "0":
        return None  # 明示 kill-switch
    if not (os.environ.get("AZURE_OPENAI_ENDPOINT")
            and os.environ.get("AZURE_OPENAI_API_KEY")
            and os.environ.get("AZURE_OPENAI_DEPLOYMENT")):
        return None  # creds なし → 完全ローカル縮退
    return lambda messages: interp_eval.azure_generate(messages)
```

`main()` 内の `Agent(...)` 生成（L41-46）へ `interp_fn` を追加:

```python
    agent = Agent(
        tool_schemas=schemas,
        chat_fn=ollama_chat,
        execute_fn=execute_tool,
        classify_fn=safe_classify,
        interp_fn=_build_interp_fn(),
    )
```

- [ ] **Step 5: Surface the arm in the REPL output**

`agent_repl.py:63` の

```python
        print(f"[{state.last_phase}] {answer}")
```

を次へ置換（どちらの腕が答えたかを可視化）:

```python
        print(f"[{state.last_phase}·{state.last_arm}] {answer}")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest tests/test_agent_repl.py -v`
Expected: PASS（5件）

- [ ] **Step 7: Run the full suite (regression)**

Run: `PYTHONPATH="C:/Users/yuu18/Lipidmix_with_LLM" ./.venv-1/Scripts/python.exe -m pytest -q`
Expected: PASS（既存 296 ＋ Task1-4 追加分がすべて緑）

- [ ] **Step 8: Commit**

```bash
git add agent_repl.py tests/test_agent_repl.py
git commit -m "feat(repl): クラウド解釈腕の配線（creds/トグルゲート・腕の可視化）"
```

---

## Self-Review

**Spec coverage:**
- 2段ターン / データフロー → Task 3（`run_turn` 改修）。
- `should_escalate` 純述語＋tier 定数 → Task 2。判定表5行＋veto端＝テスト。
- INTERP_SYSTEM 逐語共有 → Task 1。
- クラウド継ぎ目（`interp_fn` 注入・`conversation[turn_start:]` スライス・`_openai_messages` 再利用）→ Task 3（`interp_fn` は `azure_generate` を叩く Task 4 のラッパ経由で `_openai_messages` を通す）。
- フォールバック（例外・空応答）→ Task 3（`test_cloud_failure_falls_back_to_local` / `test_empty_cloud_response_falls_back_to_local`）。
- `RouterState.last_arm` 観測用 → Task 3。
- 配線（`.env` 取り込み・creds/トグルゲート・自動ON・表示）→ Task 4。
- 実 API を叩かない → 全テストでフェイク／`monkeypatch`（Task 4 は `azure_generate` をモック）。
- 付随更新（HISTRY・メモリ）→ 実装完了後にコントローラが実施（プラン外の運用手順）。
- 非目標（tiered payload・ルーティングのクラウド化・非同期化・cloud にツール）→ どのタスクも触れない（YAGNI 遵守）。

**Placeholder scan:** プレースホルダ無し。各コード手順に完全なコードを記載。

**Type consistency:** `should_escalate(executed: set[str]) -> bool`（Task 2 定義＝Task 3 使用で一致）。`Agent.interp_fn`（Task 3 定義）＝`agent_repl` の注入（Task 4）で一致。`interp_fn` シグネチャ `(messages: list) -> str`（Task 3 の呼び出し／Task 4 のラッパ／テストのフェイクで一致）。`RouterState.last_arm`（Task 3 定義）＝REPL 表示（Task 4）とテスト参照で一致。`INTERP_SYSTEM`（Task 1 定義）＝Task 3 の import で一致。
