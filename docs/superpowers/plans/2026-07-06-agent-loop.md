# Agent-loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** phase_router を使うローカルLLM会話エージェント（CLI REPL）を実装する。1ユーザーターンごとにフェーズを固定し、そのサブセットのツールをローカルモデルに呼ばせ、in-process で実行し、結果を踏まえてローカルモデルが応答する。

**Architecture:** ループ論理（`agent_core.Agent.run_turn`）と CLI I/O・実 Ollama HTTP・実ツール呼び出しを分離。全 I/O は依存注入（chat_fn / execute_fn / classify_fn）にして `run_turn` をフェイクで単体検証。ツール実行は `execute_tool` が `getattr(server, name)(**args)` で in-process 直呼びし、MCP 境界の継ぎ目とする。`phase_router` と `server` は無改変で import。

**Tech Stack:** Python 3.12、stdlib `unittest` / `unittest.mock`、`httpx`（既存依存）、Ollama `/api/chat`。

## Global Constraints

- MCP サーバ（`server.py` / `tools_*.py`）と `phase_router.py` に差分を出さない。import して使うのみ。
- `run_turn` の全 I/O は注入（`chat_fn` / `execute_fn` / `classify_fn`）。Ollama 非依存で単体テストできること。
- Ollama 呼び出しは model=`AGENT_MODEL`（`os.environ.get("LIPIDMIX_AGENT_MODEL", "qwen3:14b")`）、top-level `think=false`、`options.temperature=0`、`stream=false`。
- 1 ユーザーターン = `route()` を 1 回だけ引いてフェーズ固定。その中で有界ループ（既定 `max_rounds=5`）。
- `execute_tool` は **raise しない**。未知ツール・呼び出し例外は `{"status":"error","error":...}` の JSON 文字列を返す。ツール名は phase_router の 35 ツール allowlist で検証。
- テストは `unittest.TestCase`。実行は repo ルートから `.venv-1/Scripts/python.exe -m unittest <module> -v`。

---

### Task 1: agent_core のツール実行の継ぎ目（execute_tool ＋ ヘルパ）

`execute_tool`（in-process ツール実行）と `_truncate`（戻り値サイズ上限）・`_is_error`（エラー判定）を実装する。実 server に対する in-process テストと、ヘルパの単体テスト。Ollama 不要。

**Files:**
- Create: `agent_core.py`
- Test: `tests/test_agent_core.py`

**Interfaces:**
- Consumes: `server.<tool>`（in-process 関数、例 `server.arf_differential`、`server.load_dataset`）、`phase_router.PHASES`（allowlist 構築用）。
- Produces:
  - `execute_tool(name: str, args: dict) -> str` — 未知/例外は error JSON、list/dict は JSON 文字列化、str はそのまま。
  - `_truncate(text: str, limit: int = 8000) -> str` — 上限超過で切り詰め＋マーカー。
  - `_is_error(result: str) -> bool` — JSON dict かつ `status=="error"` で真。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_agent_core.py`:

```python
import json
import unittest

import server
import session_state
import agent_core as ac


class TestExecuteTool(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()

    def test_unknown_tool_returns_error_json(self):
        out = json.loads(ac.execute_tool("no_such_tool", {}))
        self.assertEqual(out["status"], "error")

    def test_tool_error_is_returned_as_string(self):
        # arf_differential は行列未ロードなら自前の error JSON を返す
        out = json.loads(ac.execute_tool("arf_differential", {"group_a": "A", "group_b": "B"}))
        self.assertEqual(out["status"], "error")

    def test_list_return_is_json_stringified(self):
        # load_dataset は list を返す（不正ディレクトリでも list）。JSON 文字列化される。
        out = ac.execute_tool("load_dataset", {"directory": "C:/nonexistent_xyz_123"})
        self.assertIsInstance(json.loads(out), list)

    def test_bad_kwarg_exception_is_caught(self):
        out = json.loads(ac.execute_tool("arf_list_classes", {"unexpected_kw": 1}))
        self.assertEqual(out["status"], "error")


class TestTruncate(unittest.TestCase):
    def test_within_limit_passthrough(self):
        self.assertEqual(ac._truncate("abc", 10), "abc")

    def test_over_limit_truncated_with_marker(self):
        out = ac._truncate("x" * 100, 10)
        self.assertIn("切り詰め", out)
        self.assertTrue(out.startswith("x" * 10))


class TestIsError(unittest.TestCase):
    def test_status_error_true(self):
        self.assertTrue(ac._is_error('{"status":"error","error":"x"}'))

    def test_success_dict_false(self):
        self.assertFalse(ac._is_error('{"status":"success"}'))

    def test_non_json_false(self):
        self.assertFalse(ac._is_error("plain text"))

    def test_list_json_false(self):
        self.assertFalse(ac._is_error('["a","b"]'))
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_agent_core -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'agent_core'`）

- [ ] **Step 3: agent_core.py を実装**

`agent_core.py`:

```python
"""Agent-loop の中核: ツール実行の継ぎ目とループ論理。

MCP サーバと phase_router は無改変で import する。ツール実行は in-process 直呼び
（getattr(server, name)）で、これが将来 MCP プロトコル越しへ差し替える継ぎ目。
全 I/O（Ollama チャット・ツール実行・フェーズ分類）は Agent に注入され、run_turn を
Ollama 非依存でテストできる。
"""
import json

import phase_router
import server

# LLM が呼べるのは phase_router が定義する 35 ツールだけ（server の任意属性を弾く allowlist）。
_ALLOWED_TOOLS = {n for names in phase_router.PHASES.values() for n in names}


def _truncate(text: str, limit: int = 8000) -> str:
    """ツール戻り値が文脈を圧迫しないよう上限で切り詰める。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（{len(text) - limit}文字を切り詰め）"


def _is_error(result: str) -> bool:
    """ツール戻り値が構造化エラー（dict かつ status==error）かを判定する。"""
    try:
        obj = json.loads(result)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and obj.get("status") == "error"


def execute_tool(name: str, args: dict) -> str:
    """ツールを in-process で実行し、結果を JSON 文字列で返す。raise はしない。"""
    if name not in _ALLOWED_TOOLS:
        return json.dumps({"status": "error", "error": f"unknown tool: {name}"}, ensure_ascii=False)
    fn = getattr(server, name, None)
    if not callable(fn):
        return json.dumps({"status": "error", "error": f"not callable: {name}"}, ensure_ascii=False)
    try:
        result = fn(**args)
    except Exception as e:  # ツール実行時の例外はモデルに見せてループ継続させる
        return json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_agent_core -v`
Expected: PASS（10 テスト: ExecuteTool 4 + Truncate 2 + IsError 4）

- [ ] **Step 5: コミット**

```bash
git add agent_core.py tests/test_agent_core.py
git commit -m "feat(agent): execute_tool（in-processツール実行の継ぎ目）＋切り詰め/エラー判定ヘルパ"
```

---

### Task 2: Agent.run_turn（1ターン=1フェーズ固定＋有界ループ）

会話ループ本体。`Agent` データクラスと `run_turn` を実装し、フェイク `chat_fn`/`execute_fn`/`classify_fn` で全経路を検証する。

**Files:**
- Modify: `agent_core.py`
- Test: `tests/test_agent_core.py`

**Interfaces:**
- Consumes: Task 1 の `_truncate`；`phase_router.route`、`phase_router.RouterState`。
- Produces:
  - `DEFAULT_SYSTEM: str`。
  - `@dataclass Agent(tool_schemas: dict, chat_fn, execute_fn, classify_fn, max_rounds: int = 5, system_prompt: str = DEFAULT_SYSTEM)`。
  - `Agent.run_turn(query: str, state: RouterState, conversation: list[dict]) -> str` — フェーズを1回 route して固定、有界ループでツール実行、最終 content を返す。`state`（last_phase / dataset_loaded）と `conversation` を更新。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_agent_core.py` に追記:

```python
import phase_router
from phase_router import RouterState


class TestRunTurn(unittest.TestCase):
    def setUp(self):
        # route が返しうる全ツール名にダミースキーマを用意
        all_names = {n for names in phase_router.PHASES.values() for n in names}
        self.schemas = {n: {"type": "function", "function": {"name": n}} for n in all_names}

    @staticmethod
    def _chat_from(script):
        state = {"i": 0}
        def chat_fn(messages, tools):
            msg = script[state["i"]]
            state["i"] += 1
            return msg
        return chat_fn

    @staticmethod
    def _recording_execute(load_result='{"status":"success"}'):
        calls = []
        def execute_fn(name, args):
            calls.append((name, args))
            if name == "load_dataset":
                return load_result
            return '{"status":"success","tool":"' + name + '"}'
        execute_fn.calls = calls
        return execute_fn

    def _agent(self, chat_fn, execute_fn, phase="ARF"):
        return ac.Agent(
            tool_schemas=self.schemas,
            chat_fn=chat_fn,
            execute_fn=execute_fn,
            classify_fn=lambda q, c: phase,
        )

    def test_tool_then_final_answer(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_list_classes", "arguments": {}}}]},
            {"role": "assistant", "content": "クラスは3種です"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        conv = []
        out = self._agent(chat, ex).run_turn("ARFのクラス一覧", state, conv)
        self.assertEqual(out, "クラスは3種です")
        self.assertEqual(ex.calls, [("arf_list_classes", {})])
        self.assertEqual(state.last_phase, "ARF")
        # 会話順序: user -> assistant(tool_calls) -> tool -> assistant(content)
        self.assertEqual([m["role"] for m in conv], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(conv[2]["tool_name"], "arf_list_classes")

    def test_load_dataset_sets_flag(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "load_dataset", "arguments": {"directory": "C:/d"}}}]},
            {"role": "assistant", "content": "読み込みました"},
        ])
        ex = self._recording_execute(load_result="## 📂 データセット読み込み: C:/d\n...")
        state = RouterState(dataset_loaded=False)
        out = self._agent(chat, ex).run_turn("C:/d を読み込んで", state, [])
        self.assertTrue(state.dataset_loaded)
        self.assertEqual(out, "読み込みました")

    def test_load_dataset_failure_does_not_set_flag(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "load_dataset", "arguments": {"directory": "C:/bad"}}}]},
            {"role": "assistant", "content": "失敗しました"},
        ])
        ex = self._recording_execute(load_result='["データディレクトリが存在しません: C:/bad"]')
        state = RouterState(dataset_loaded=False)
        self._agent(chat, ex).run_turn("C:/bad を読み込んで", state, [])
        self.assertFalse(state.dataset_loaded)

    def test_max_rounds_stops_infinite_loop(self):
        # 常に tool_call を返し続けるフェイク
        def chat_fn(messages, tools):
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": "arf_list_classes", "arguments": {}}}]}
        ex = self._recording_execute()
        agent = ac.Agent(self.schemas, chat_fn, ex, lambda q, c: "ARF", max_rounds=3)
        out = agent.run_turn("ループ", RouterState(dataset_loaded=True), [])
        self.assertEqual(out, "（ツール呼び出しが上限に達しました）")
        self.assertEqual(len(ex.calls), 3)

    def test_no_tool_call_returns_content_directly(self):
        chat = self._chat_from([{"role": "assistant", "content": "こんにちは"}])
        ex = self._recording_execute()
        out = self._agent(chat, ex).run_turn("やあ", RouterState(dataset_loaded=True), [])
        self.assertEqual(out, "こんにちは")
        self.assertEqual(ex.calls, [])
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_agent_core.TestRunTurn -v`
Expected: FAIL（`AttributeError: module 'agent_core' has no attribute 'Agent'`）

- [ ] **Step 3: Agent.run_turn を実装**

`agent_core.py` の import に追加:

```python
from dataclasses import dataclass
from typing import Callable

from phase_router import RouterState
```

`agent_core.py` の末尾に追加:

```python
DEFAULT_SYSTEM = (
    "あなたはMS-DIALリピドミクス解析アシスタントです。提供されたツールを使って"
    "ユーザーの要求に応え、ツールの結果を踏まえて日本語で簡潔に解釈・回答してください。"
)

# load_dataset 成功時の戻り値ヘッダ（tools_dataset.py 参照）。成功/失敗とも list を返すため、
# このマーカーの有無で成功を判定する（v1 の実務的ヒューリスティック）。
_LOAD_SUCCESS_MARKER = "データセット読み込み"


@dataclass
class Agent:
    tool_schemas: dict           # name -> Ollama tool schema（起動時 list_tools() から構築）
    chat_fn: Callable            # (messages: list, tools: list) -> message dict
    execute_fn: Callable         # (name: str, args: dict) -> str
    classify_fn: Callable        # (query: str, candidates: list) -> str（route 用）
    max_rounds: int = 5
    system_prompt: str = DEFAULT_SYSTEM

    def run_turn(self, query: str, state: RouterState, conversation: list) -> str:
        """1 ユーザーターンを実行する。route を1回引いてフェーズ固定、有界ループでツール実行。

        state（last_phase / dataset_loaded）と conversation を更新し、最終応答テキストを返す。
        """
        routed = phase_router.route(query, state, self.classify_fn)
        state.last_phase = routed.phase
        tools = [self.tool_schemas[n] for n in routed.tool_names if n in self.tool_schemas]

        conversation.append({"role": "user", "content": query})
        for _ in range(self.max_rounds):
            messages = [{"role": "system", "content": self.system_prompt}] + conversation
            msg = self.chat_fn(messages, tools)
            calls = msg.get("tool_calls") or []
            if not calls:
                content = msg.get("content") or ""
                conversation.append({"role": "assistant", "content": content})
                return content
            conversation.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments") or {}
                out = _truncate(self.execute_fn(name, args))
                conversation.append({"role": "tool", "content": out, "tool_name": name})
                if name == "load_dataset" and _LOAD_SUCCESS_MARKER in out:
                    state.dataset_loaded = True
        return "（ツール呼び出しが上限に達しました）"
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_agent_core -v`
Expected: PASS（Task 1 の 10 ＋ Task 2 の 5 = 15 テスト）

- [ ] **Step 5: コミット**

```bash
git add agent_core.py tests/test_agent_core.py
git commit -m "feat(agent): Agent.run_turn（1ターン=1フェーズ固定＋有界ツールループ）"
```

---

### Task 3: 実 Ollama チャット（ollama_chat）＋ CLI REPL（agent_repl）

実 Ollama の tools 付きチャットと、CLI REPL を実装する。`safe_classify`（transport 例外を `""` に落とすラッパ）は単体テスト、`ollama_chat` と `main` は手動スモーク。

**Files:**
- Modify: `agent_core.py`
- Create: `agent_repl.py`
- Test: `tests/test_agent_repl.py`

**Interfaces:**
- Consumes: `agent_core.Agent` / `execute_tool` / `ollama_chat`；`phase_router.ollama_classify_fn` / `route` / `RouterState`；`server.mcp.list_tools()`。
- Produces:
  - `agent_core.AGENT_MODEL: str`、`agent_core.ollama_chat(messages: list, tools: list) -> dict`。
  - `agent_repl.safe_classify(query: str, candidates: list) -> str`（httpx 例外時 `""`）。
  - `agent_repl.main()`（REPL、`if __name__ == "__main__"` から起動）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_agent_repl.py`:

```python
import unittest
from unittest import mock

import httpx

import agent_repl


class TestSafeClassify(unittest.TestCase):
    def test_returns_empty_on_httpx_error(self):
        with mock.patch.object(agent_repl.phase_router, "ollama_classify_fn",
                               side_effect=httpx.ConnectError("down")):
            self.assertEqual(agent_repl.safe_classify("q", ["ARF"]), "")

    def test_passes_through_normal_result(self):
        with mock.patch.object(agent_repl.phase_router, "ollama_classify_fn",
                               return_value="ARF"):
            self.assertEqual(agent_repl.safe_classify("q", ["ARF"]), "ARF")
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_agent_repl -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'agent_repl'`）

- [ ] **Step 3: ollama_chat と agent_repl を実装**

`agent_core.py` の import に追加:

```python
import os

import httpx
```

`agent_core.py` に追加（`_truncate` 等の近く）:

```python
AGENT_MODEL = os.environ.get("LIPIDMIX_AGENT_MODEL", "qwen3:14b")


def ollama_chat(messages: list, tools: list) -> dict:
    """Ollama /api/chat に tools 付きで問い合わせ、message dict を返す。

    httpx 例外はここでは捕えず呼び出し側（run_turn 直下の REPL）に委ねる。
    """
    payload = {
        "model": AGENT_MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    resp = httpx.post(phase_router.OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["message"]
```

`agent_repl.py`（新規）:

```python
"""Agent-loop の CLI REPL。ローカル qwen3:14b で MS-DIAL 解析を対話実行する。

起動: .venv-1/Scripts/python.exe agent_repl.py
前提: Ollama 起動＋qwen3:14b。server.mcp からツールスキーマを取得する。
"""
import asyncio

import httpx

import phase_router
import server
from agent_core import Agent, execute_tool, ollama_chat
from phase_router import RouterState


def safe_classify(query: str, candidates: list) -> str:
    """route 用分類器。Ollama transport 例外時は "" を返し route のフォールバックに委ねる。"""
    try:
        return phase_router.ollama_classify_fn(query, candidates)
    except httpx.HTTPError:
        return ""


async def _get_tool_schemas() -> dict:
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


def main() -> None:
    schemas = asyncio.run(_get_tool_schemas())
    agent = Agent(
        tool_schemas=schemas,
        chat_fn=ollama_chat,
        execute_fn=execute_tool,
        classify_fn=safe_classify,
    )
    state = RouterState(dataset_loaded=False)
    conversation: list = []
    print("MS-DIAL ローカル解析エージェント（空行 or 'quit' で終了）")
    while True:
        try:
            query = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not query or query.lower() == "quit":
            break
        try:
            answer = agent.run_turn(query, state, conversation)
        except httpx.HTTPError:
            print("Ollamaに接続できません（起動とモデルを確認してください）")
            continue
        print(f"[{state.last_phase}] {answer}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_agent_repl -v`
Expected: PASS（2 テスト）。

全体回帰も確認:
Run: `.venv-1/Scripts/python.exe -m unittest tests.test_agent_core tests.test_agent_repl tests.test_phase_router -v`
Expected: PASS（15 + 2 + 18 = 35 テスト）

- [ ] **Step 5: 手動スモーク（実 Ollama・任意だが推奨）**

Ollama 起動済みで、実データフォルダがあれば:
Run: `.venv-1/Scripts/python.exe agent_repl.py`
手順: `> C:/実データフォルダ を読み込んで解析して` → ロード後 `> ARFのクラス一覧を見せて` 等。
Expected: 1手目は `[ENTRY]`（gate）でロード実行、以降 `[ARF]` 等へ遷移して応答が返る。
（自動テストではない。接続不可なら「Ollamaに接続できません」が出て REPL は継続。）

- [ ] **Step 6: コミット**

```bash
git add agent_core.py agent_repl.py tests/test_agent_repl.py
git commit -m "feat(agent): 実Ollamaチャット（ollama_chat）＋CLI REPL（agent_repl, safe_classify）"
```

---

## Self-Review

**Spec coverage:**
- §3 execute_tool（in-process・error JSON・allowlist）→ Task 1。
- §3 _truncate → Task 1。§4 _is_error → Task 1。
- §3/§4 Agent.run_turn（1ターン1route・有界ループ・load_dataset で dataset_loaded 遷移）→ Task 2。
  - 注: spec §4 は「`not _is_error` で flip」と書いたが、load_dataset は成功/失敗とも list を返すため
    `_is_error`（dict 前提）では判別不能。計画では**成功ヘッダ `_LOAD_SUCCESS_MARKER` の存在**で判定するよう
    精緻化した（`test_load_dataset_failure_does_not_set_flag` で False 側も固定）。`_is_error` は一般の
    エラー判定ヘルパとして Task 1 に残す。
- §3 AGENT_MODEL / ollama_chat → Task 3。§5 safe_classify（transport→""）→ Task 3。
- §3 agent_repl（REPL・状態生存・schemas 取得）→ Task 3。§5 REPL の httpx 捕捉 → Task 3（main）。
- §6.1 run_turn ユニット → Task 2。§6.2 execute_tool 実ツール → Task 1。§6.3 手動スモーク → Task 3 Step 5。
- §7 受け入れ基準（DI・ユニット通過・サーバ/router 無改変）→ 全タスク＋Global Constraints。

**Placeholder scan:** "TBD"/"TODO"/"適切に処理" 等なし。全コード実体を記載。

**Type consistency:** `execute_tool(name, args)->str`、`_truncate(text, limit=8000)->str`、`_is_error(result)->bool`、
`Agent(tool_schemas, chat_fn, execute_fn, classify_fn, max_rounds=5, system_prompt=DEFAULT_SYSTEM)`、
`run_turn(query, state, conversation)->str`、`ollama_chat(messages, tools)->dict`、`safe_classify(query, candidates)->str`
は Task 1〜3 で一貫。`AGENT_MODEL`/`_LOAD_SUCCESS_MARKER`/`_ALLOWED_TOOLS` は定義タスクで導入し後続で参照。
`chat_fn` の戻り dict は `.get("tool_calls")`/`.get("content")` で読み、tool_call は `call["function"]["name"]`/
`["arguments"]`（Ollama 形式、`phase_router_eval.py` と同一）で読む——テストのフェイクも同形式。

ギャップなし。
