# 肝臓リピドーム LLM解釈精度評価 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 5モデル（Opus正解基準＋ローカルハイブリッド/Azure/Sonnet 5/Haiku 4.5）が肝臓リピドーム(POS/NEG)をフェーズ別10ケースでフルツール駆動して解釈した結果を、Opus正解へのルーブリック照合で採点する評価ハーネスを実装し、実行・採点・報告する。

**Architecture:** 既存 `interp_eval.py`（純ロジック）＋ `agent_core.Agent`（DIエージェントループ）を土台に、(1) Azure/OpenAI をツールドライバにする `openai_chat` アダプタ、(2) 新データセットの10ケース定義、(3) 採点集計の純ロジック、(4) 自動モデルを `run_turn` で回す liver2 オーケストレータ、を追加する。gold（Opus）は私が `execute_tool` をライブ駆動して正解解釈＋rubricを作る。出力は前回分と分離して `interp_eval_out/liver2/` に置く。

**Tech Stack:** Python 3（`.venv-1`）、httpx、既存 MCP サーバ（`server.py`）、Ollama（qwen3:14b）、Azure AI Foundry（gpt-5.4-mini）、Claude Desktop（MCP クライアント、Sonnet/Haiku 手動）、unittest。

## Global Constraints

- Python 実行は `.venv-1/Scripts/python.exe`。テストは `.venv-1/Scripts/python.exe -m unittest discover -s tests -t .`。
- 既存 `interp_eval_out/`（前回eval）と `interp_eval_cases.py` の `CASES` は**改変・クロバーしない**。新規は `interp_eval_out/liver2/` と `interp_eval_cases_liver2.py` に隔離。
- `Case` / `ToolStep` / `AXES` / `PHASE_LABELS` / `validate_cases` は `interp_eval` から再利用（再定義しない）。DRY。
- データセットは `C:\Users\yuu18\datasets\20230824_liver\20230824_liver` の `NEG` / `POS`。
- LLM 呼び出しは全て temperature=0（決定性）。
- 作業ブランチは既存の `feat/liver-interp-accuracy-eval`。各タスク末尾でコミット。
- Azure 認証は `.env` の `AZURE_OPENAI_*`（既存 `interp_eval_run.py` / `agent_repl.py` と同じ dotenv 取り込み規約）。
- 採点は Opus 正解基準の**絶対採点**（盲検ペアではない）。5軸スケールは各軸 **0–2 整数**（0=不良/捏造失格、1=部分一致、2=正解と一致）。

---

### Task 1: Azure/OpenAI ツール呼び出しアダプタ `openai_chat`

`agent_core.Agent` の `chat_fn` として Azure gpt-5.4-mini をツールドライバにする。既存 `azure_generate` の認証・URL ロジックをヘルパへ抽出して DRY 化（既存テストは URL 不変で緑のまま）。

**Files:**
- Modify: `interp_eval.py`（先頭に `import json` 追加、`_resolve_azure_creds` / `_azure_chat_url` 抽出、`azure_generate` をヘルパ利用へ、`openai_chat` 追加）
- Test: `tests/test_interp_eval_openai_chat.py`（新規）
- 既存 `tests/test_interp_eval_cloud.py` は不変で緑を維持（回帰確認）

**Interfaces:**
- Consumes: `interp_eval._openai_messages(messages) -> list`（既存）、`interp_eval.httpx`
- Produces:
  - `interp_eval.openai_chat(messages: list, tools: list, deployment=None, temperature=0.0, timeout=300, endpoint=None, api_key=None, api_version=None) -> dict`。返り値は `{"content": str, "tool_calls": [{"function": {"name": str, "arguments": dict}}]}`（`agent_core.run_turn` が消費する形）。
  - `interp_eval._resolve_azure_creds(endpoint, api_key, deployment, api_version) -> tuple[str,str,str,str]`（未設定時 `RuntimeError`）
  - `interp_eval._azure_chat_url(endpoint, deployment, api_version) -> tuple[str, bool]`（bool=deployment を body.model に載せる v1 サーフェスか）

- [ ] **Step 1: Write the failing test**

`tests/test_interp_eval_openai_chat.py`:

```python
import unittest
from unittest import mock

import interp_eval as ie


def _resp(tool_calls=None, content=None):
    r = mock.Mock()
    r.raise_for_status = mock.Mock()
    r.json = mock.Mock(return_value={
        "choices": [{"message": {"content": content, "tool_calls": tool_calls}}]})
    return r


class TestOpenAIChat(unittest.TestCase):
    def test_parses_tool_calls_and_sends_tools_in_body(self):
        tools = [{"type": "function",
                  "function": {"name": "arf_re_pca", "description": "",
                               "parameters": {"type": "object", "properties": {}}}}]
        tc = [{"id": "c1", "type": "function",
               "function": {"name": "arf_re_pca",
                            "arguments": '{"top_features": 10}'}}]
        with mock.patch.object(ie.httpx, "post",
                               return_value=_resp(tool_calls=tc, content="")) as post:
            out = ie.openai_chat([{"role": "user", "content": "PCAして"}], tools,
                                 deployment="d",
                                 endpoint="https://ex.openai.azure.com/", api_key="K")
        self.assertEqual(out["tool_calls"],
                         [{"function": {"name": "arf_re_pca",
                                        "arguments": {"top_features": 10}}}])
        self.assertEqual(out["content"], "")
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["tools"], tools)
        self.assertEqual(kwargs["json"]["temperature"], 0.0)

    def test_returns_content_when_no_tool_calls(self):
        with mock.patch.object(ie.httpx, "post",
                               return_value=_resp(tool_calls=None, content="解釈Z")):
            out = ie.openai_chat([{"role": "user", "content": "U"}], [],
                                 deployment="d",
                                 endpoint="https://ex.openai.azure.com/", api_key="K")
        self.assertEqual(out, {"content": "解釈Z", "tool_calls": []})

    def test_malformed_arguments_string_degrades_to_empty_dict(self):
        tc = [{"function": {"name": "x", "arguments": "not json"}}]
        with mock.patch.object(ie.httpx, "post",
                               return_value=_resp(tool_calls=tc, content="")):
            out = ie.openai_chat([{"role": "user", "content": "U"}], [],
                                 deployment="d",
                                 endpoint="https://ex.openai.azure.com/", api_key="K")
        self.assertEqual(out["tool_calls"][0]["function"]["arguments"], {})

    def test_v1_foundry_puts_model_in_body(self):
        with mock.patch.object(ie.httpx, "post",
                               return_value=_resp(content="Y")) as post:
            ie.openai_chat([{"role": "user", "content": "U"}], [],
                           deployment="gpt-5.4-mini-kamegai",
                           endpoint="https://t.services.ai.azure.com/openai/v1",
                           api_key="K")
        _, kwargs = post.call_args
        url = post.call_args.args[0] if post.call_args.args else kwargs["url"]
        self.assertEqual(
            url, "https://t.services.ai.azure.com/openai/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "gpt-5.4-mini-kamegai")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_openai_chat -v`
Expected: FAIL（`AttributeError: module 'interp_eval' has no attribute 'openai_chat'`）

- [ ] **Step 3: Add `import json` and the helpers + `openai_chat` to `interp_eval.py`**

`interp_eval.py` の先頭 import 群に `import json` を追加（`import httpx` の下）。
`azure_generate` の直前にヘルパを追加し、`azure_generate` 本体をヘルパ利用へ置換、続けて `openai_chat` を追加:

```python
def _resolve_azure_creds(endpoint, api_key, deployment, api_version):
    """Azure 認証情報を引数優先・無ければ env から解決する。未設定なら RuntimeError。"""
    endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")
    deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    api_version = api_version or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    if not (endpoint and api_key and deployment):
        raise RuntimeError(
            "Azure 認証情報が未設定です（AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / "
            "AZURE_OPENAI_DEPLOYMENT を設定してください）。")
    return endpoint, api_key, deployment, api_version


def _azure_chat_url(endpoint, deployment, api_version):
    """chat completions の URL と、deployment を body.model に載せるか(=v1 Foundry)を返す。"""
    base = endpoint.rstrip("/")
    if base.endswith("/openai/v1"):
        return f"{base}/chat/completions", True
    return (f"{base}/openai/deployments/{deployment}"
            f"/chat/completions?api-version={api_version}"), False
```

`azure_generate` の本体を次へ置換（docstring は既存を維持）:

```python
def azure_generate(messages, deployment=None, temperature=0.0, timeout=300,
                   endpoint=None, api_key=None, api_version=None):
    endpoint, api_key, deployment, api_version = _resolve_azure_creds(
        endpoint, api_key, deployment, api_version)
    url, use_model_field = _azure_chat_url(endpoint, deployment, api_version)
    body = {"messages": _openai_messages(messages), "temperature": temperature}
    if use_model_field:
        body["model"] = deployment
    resp = httpx.post(url, headers={"api-key": api_key}, json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"].get("content") or ""
```

`generate_interp` の下（ファイル末尾）に `openai_chat` を追加:

```python
def openai_chat(messages, tools, deployment=None, temperature=0.0, timeout=300,
                endpoint=None, api_key=None, api_version=None):
    """Azure/OpenAI をツールドライバにする chat_fn（agent_core.Agent 用）。

    messages（Ollama 形式の会話）を _openai_messages で OpenAI 有効 role/content へ畳み、
    tools（既に OpenAI function 形式の schema 群）を付けて chat completions を叩く。応答の
    tool_calls を agent_core が期待する {"function":{"name","arguments":dict}} へ逆変換して返す
    （OpenAI の arguments は JSON 文字列なので json.loads する。壊れていれば空 dict へ縮退）。
    """
    endpoint, api_key, deployment, api_version = _resolve_azure_creds(
        endpoint, api_key, deployment, api_version)
    url, use_model_field = _azure_chat_url(endpoint, deployment, api_version)
    body = {"messages": _openai_messages(messages), "temperature": temperature}
    if tools:
        body["tools"] = tools
    if use_model_field:
        body["model"] = deployment
    resp = httpx.post(url, headers={"api-key": api_key}, json=body, timeout=timeout)
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        if isinstance(raw, str):
            try:
                args = json.loads(raw) if raw.strip() else {}
            except ValueError:
                args = {}
        else:
            args = raw or {}
        calls.append({"function": {"name": fn.get("name"), "arguments": args}})
    return {"content": msg.get("content") or "", "tool_calls": calls}
```

- [ ] **Step 4: Run new + existing cloud tests to verify pass**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_openai_chat tests.test_interp_eval_cloud -v`
Expected: PASS（全件。既存 azure_generate テストも URL 不変で緑）

- [ ] **Step 5: Commit**

```bash
git add interp_eval.py tests/test_interp_eval_openai_chat.py
git commit -m "feat(interp-eval): Azure/OpenAI ツール呼び出しアダプタ openai_chat と認証ヘルパ抽出

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 新データセットの10ケース定義 `interp_eval_cases_liver2.py`

20230824_liver（GF/SPF×AIN/HFD/NC）に対する5フェーズ×POS/NEG=10ケース。差次コントラストは暫定（Task 5 のライブ解析で最終化）。

**Files:**
- Create: `interp_eval_cases_liver2.py`
- Test: `tests/test_interp_eval_cases_liver2.py`（新規）

**Interfaces:**
- Consumes: `interp_eval.Case`, `interp_eval.ToolStep`, `interp_eval.validate_cases`, `phase_router.PHASES`
- Produces: `interp_eval_cases_liver2.LIVER2_CASES: list[Case]`（長さ10）、`DIR_NEG: str`、`DIR_POS: str`

- [ ] **Step 1: Write the failing test**

`tests/test_interp_eval_cases_liver2.py`:

```python
import unittest

import interp_eval as ie
import phase_router
from interp_eval_cases_liver2 import LIVER2_CASES

ALLOWED = {n for names in phase_router.PHASES.values() for n in names}


class TestLiver2Cases(unittest.TestCase):
    def test_structure_valid_and_covers_all_phases(self):
        self.assertEqual(ie.validate_cases(LIVER2_CASES, ALLOWED), [])

    def test_has_neg_and_pos_and_unique_ids(self):
        modes = {c.mode for c in LIVER2_CASES}
        self.assertEqual(modes, {"NEG", "POS"})
        ids = [c.id for c in LIVER2_CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_pipeline_step_tool_is_allowed(self):
        for c in LIVER2_CASES:
            for step in c.pipeline:
                self.assertIn(step.name, ALLOWED, f"{c.id}:{step.name}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_cases_liver2 -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'interp_eval_cases_liver2'`）

- [ ] **Step 3: Create `interp_eval_cases_liver2.py`**

```python
"""解釈精度評価の10ケース（20230824_liver, GF/SPF×AIN/HFD/NC の 2×3）。

各 Case の pipeline は gold 凍結（frozen 正準証拠）の生成に使う。フルツール駆動の
自動ランナー（interp_eval_liver2.do_run_auto）は pipeline を実行せず case.query のみを
モデルへ渡し、モデル自身にツールを駆動させる。差次コントラスト(#3/#4)は暫定で、
Task 5 のライブ再PCAで主分離因子を見極めてから最終確定する。
"""
from interp_eval import Case, ToolStep

DIR_NEG = r"C:\Users\yuu18\datasets\20230824_liver\20230824_liver\NEG"
DIR_POS = r"C:\Users\yuu18\datasets\20230824_liver\20230824_liver\POS"


def _pca(mode, directory):
    return Case(
        id=f"pca_{mode.lower()}", phase_label="PCA", mode=mode,
        query=("このデータセットのPCAを実行し、群分離の有無と主因（腸内細菌叢 GF/SPF か "
               "食餌 AIN/HFD/NC か）、および生物学的な意味を解釈して。分離が弱ければ"
               "フィルタや群指定を変えて再PCAして。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_re_pca", {"top_features": 10}),
        ],
    )


def _differential(case_id, mode, directory, group_a, group_b):
    return Case(
        id=case_id, phase_label="DIFFERENTIAL", mode=mode,
        query=(f"{group_a} 群と {group_b} 群の差次的解析を実行し、有意な脂質と"
               "注意点（多重比較・バッチ交絡・n）を解釈して。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_preprocess", {"normalize": "median", "impute": "half_min"}),
            ToolStep("arf_differential", {"group_a": group_a, "group_b": group_b}),
        ],
    )


def _qc(mode, directory):
    return Case(
        id=f"qc_{mode.lower()}", phase_label="QC", mode=mode,
        query=("前処理/QCを実行し、QC-RSD・drift・欠測補完・blank 由来などデータ品質の"
               "問題と対処を解釈して。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_preprocess",
                     {"normalize": "median", "max_qc_rsd": 30, "impute": "half_min"}),
        ],
    )


def _identity(mode, directory):
    return Case(
        id=f"identity_{mode.lower()}", phase_label="IDENTITY", mode=mode,
        query="脂質同定結果を確認し、確信度（MSIレベル）と過剰主張のリスクを解釈して。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf2_annotate_identities", {"max_rows": 30}),
        ],
    )


def _literature(mode, query_text):
    return Case(
        id=f"literature_{mode.lower()}", phase_label="LITERATURE", mode=mode,
        query=("この解析に関連する文献を検索し、どの知見が本データの仮説（腸内細菌叢・"
               "食餌による肝リピドーム再構成）を支持するか解釈して。"),
        pipeline=[ToolStep("paper_search", {"query": query_text, "max_results": 10})],
    )


# 差次コントラストは暫定（Task 5 で確定）: 菌叢効果 SPF vs GF / 食餌効果 HFD vs NC。
LIVER2_CASES = [
    _pca("NEG", DIR_NEG),
    _pca("POS", DIR_POS),
    _differential("differential_microbiome", "NEG", DIR_NEG, group_a="SPF", group_b="GF"),
    _differential("differential_diet", "POS", DIR_POS, group_a="HFD", group_b="NC"),
    _qc("NEG", DIR_NEG),
    _qc("POS", DIR_POS),
    _identity("NEG", DIR_NEG),
    _identity("POS", DIR_POS),
    _literature("NEG", "germ-free SPF mouse liver lipidome gut microbiota phospholipid"),
    _literature("POS", "high-fat diet mouse liver lipidomics triacylglycerol ceramide"),
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_cases_liver2 -v`
Expected: PASS（全3件）

- [ ] **Step 5: Commit**

```bash
git add interp_eval_cases_liver2.py tests/test_interp_eval_cases_liver2.py
git commit -m "feat(interp-eval): 20230824_liver の10ケース定義（暫定差次コントラスト）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 採点集計とトランスクリプト整形の純ロジック

Opus基準の絶対採点（各軸0–2）をモデル別・フェーズ別に集計する `score_aggregate` と、会話ログを保存用テキストへ整形する `transcript_text` を `interp_eval.py` に追加。

**Files:**
- Modify: `interp_eval.py`（末尾に `transcript_text` と `score_aggregate` を追加）
- Test: `tests/test_interp_eval_scoring.py`（新規）

**Interfaces:**
- Consumes: `interp_eval.AXES`（既存 `["hallucination","accuracy","completeness","utility","language"]`）、`json`（Task 1 で import 済み）
- Produces:
  - `interp_eval.transcript_text(conversation: list, final: str) -> str`
  - `interp_eval.score_aggregate(scores: list[dict], model_keys: list[str]) -> dict`。
    入力 score は `{"case_id","phase_label","model","axes":{axis:int}}`。返り値
    `{"by_model": {model: {axis: mean}}, "overall": {model: mean_total}, "by_phase": {phase: {model: mean_total}}}`（mean_total = 1ケースの5軸合計の平均）。

- [ ] **Step 1: Write the failing test**

`tests/test_interp_eval_scoring.py`:

```python
import unittest

import interp_eval as ie


class TestTranscriptText(unittest.TestCase):
    def test_renders_tool_calls_and_final(self):
        conversation = [
            {"role": "user", "content": "PCAして"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_re_pca",
                                          "arguments": {"top_features": 10}}}]},
            {"role": "tool", "content": "PCA_RESULT", "tool_name": "arf_re_pca"},
            {"role": "assistant", "content": "PC1が分離"},
        ]
        out = ie.transcript_text(conversation, "PC1が分離")
        self.assertIn("arf_re_pca", out)
        self.assertIn("PCA_RESULT", out)
        self.assertIn("FINAL", out)
        self.assertIn("PC1が分離", out)


class TestScoreAggregate(unittest.TestCase):
    def _scores(self):
        full = {ax: 2 for ax in ie.AXES}   # 5軸合計=10
        half = {ax: 1 for ax in ie.AXES}   # 5軸合計=5
        return [
            {"case_id": "pca_neg", "phase_label": "PCA", "model": "azure", "axes": full},
            {"case_id": "qc_neg", "phase_label": "QC", "model": "azure", "axes": full},
            {"case_id": "pca_neg", "phase_label": "PCA", "model": "hybrid", "axes": half},
            {"case_id": "qc_neg", "phase_label": "QC", "model": "hybrid", "axes": half},
        ]

    def test_by_model_axis_mean(self):
        agg = ie.score_aggregate(self._scores(), ["azure", "hybrid"])
        self.assertEqual(agg["by_model"]["azure"]["accuracy"], 2.0)
        self.assertEqual(agg["by_model"]["hybrid"]["accuracy"], 1.0)

    def test_overall_mean_total(self):
        agg = ie.score_aggregate(self._scores(), ["azure", "hybrid"])
        self.assertEqual(agg["overall"]["azure"], 10.0)
        self.assertEqual(agg["overall"]["hybrid"], 5.0)

    def test_by_phase_mean_total(self):
        agg = ie.score_aggregate(self._scores(), ["azure", "hybrid"])
        self.assertEqual(agg["by_phase"]["PCA"]["azure"], 10.0)
        self.assertEqual(agg["by_phase"]["QC"]["hybrid"], 5.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_scoring -v`
Expected: FAIL（`AttributeError: module 'interp_eval' has no attribute 'transcript_text'`）

- [ ] **Step 3: Add `transcript_text` and `score_aggregate` to `interp_eval.py`（末尾）**

```python
def transcript_text(conversation, final):
    """agent_core.run_turn が積んだ会話ログを、保存用の人間可読テキストへ整形する。"""
    lines = []
    for m in conversation:
        role = m.get("role")
        if role == "user":
            lines.append(f"### user\n{m.get('content', '')}")
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            for c in calls:
                fn = c.get("function") or {}
                args = json.dumps(fn.get("arguments") or {}, ensure_ascii=False)
                lines.append(f"### assistant→tool_call: {fn.get('name')}({args})")
            if m.get("content"):
                lines.append(f"### assistant\n{m.get('content')}")
            elif not calls:
                lines.append("### assistant\n")
        elif role == "tool":
            lines.append(f"### tool[{m.get('tool_name')}]\n{m.get('content', '')}")
    lines.append(f"### FINAL\n{final}")
    return "\n\n".join(lines)


def score_aggregate(scores, model_keys):
    """Opus基準の絶対採点（各軸0–2）をモデル別・フェーズ別に集計する。

    score: {"case_id","phase_label","model","axes":{axis:int}}。
    返り値: by_model[model][axis]=軸平均, overall[model]=1ケース5軸合計の平均,
    by_phase[phase][model]=同フェーズの5軸合計平均。
    """
    axis_vals = {m: {ax: [] for ax in AXES} for m in model_keys}
    totals = {m: [] for m in model_keys}
    phase_totals = {}
    for s in scores:
        m = s["model"]
        if m not in axis_vals:
            continue
        total = 0
        for ax in AXES:
            v = s["axes"][ax]
            axis_vals[m][ax].append(v)
            total += v
        totals[m].append(total)
        phase_totals.setdefault(s["phase_label"], {}).setdefault(m, []).append(total)

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    by_model = {m: {ax: _mean(axis_vals[m][ax]) for ax in AXES} for m in model_keys}
    overall = {m: _mean(totals[m]) for m in model_keys}
    by_phase = {ph: {m: _mean(v.get(m, [])) for m in model_keys}
                for ph, v in phase_totals.items()}
    return {"by_model": by_model, "overall": overall, "by_phase": by_phase}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_scoring -v`
Expected: PASS（全4件）

- [ ] **Step 5: Commit**

```bash
git add interp_eval.py tests/test_interp_eval_scoring.py
git commit -m "feat(interp-eval): 絶対採点集計 score_aggregate とトランスクリプト整形 transcript_text

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: liver2 オーケストレータ（gold凍結・自動モデル実行）

自動2モデル（ローカルハイブリッド・Azure）を `run_turn` で各ケース実行し、gold の正準ツール出力を凍結する I/O 層。出力は `interp_eval_out/liver2/`。

**Files:**
- Create: `interp_eval_liver2.py`
- Test: `tests/test_interp_eval_liver2.py`（新規、`build_agent` の配線のみ検証。ネットワークは叩かない）

**Interfaces:**
- Consumes: `agent_core`（`Agent`, `execute_tool`, `ollama_chat`, `DEFAULT_SYSTEM`）、`agent_repl._get_tool_schemas`, `agent_repl.safe_classify`、`interp_eval`（`openai_chat`, `azure_generate`, `transcript_text`, `_truncate`）、`interp_eval_cases_liver2`（`LIVER2_CASES`, `DIR_NEG`, `DIR_POS`）、`phase_router.RouterState`、`server`, `session_state`
- Produces:
  - `interp_eval_liver2.build_agent(key: str, schemas: dict) -> agent_core.Agent`（key ∈ {"hybrid","azure"}）
  - CLI: `freeze`（gold正準凍結）, `run_auto`（自動2モデル実行）

- [ ] **Step 1: Write the failing test**

`tests/test_interp_eval_liver2.py`:

```python
import unittest

import interp_eval_liver2 as run


class TestBuildAgent(unittest.TestCase):
    def test_azure_agent_has_no_cloud_interp_fn(self):
        agent = run.build_agent("azure", schemas={})
        self.assertIsNone(agent.interp_fn)          # Azure 自身が最終解釈も書く
        self.assertEqual(agent.execute_fn.__name__, "execute_tool")

    def test_hybrid_agent_has_cloud_interp_fn(self):
        agent = run.build_agent("hybrid", schemas={})
        self.assertIsNotNone(agent.interp_fn)        # 高価値ターンで Azure へエスカレート

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            run.build_agent("nope", schemas={})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_liver2 -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'interp_eval_liver2'`）

- [ ] **Step 3: Create `interp_eval_liver2.py`**

```python
"""解釈精度評価（20230824_liver）の手動オーケストレータ。

使い方（repo ルート、Ollama 起動＋Azure creds 前提）:
    .venv-1/Scripts/python.exe interp_eval_liver2.py freeze     # gold 正準ツール出力を凍結
    .venv-1/Scripts/python.exe interp_eval_liver2.py run_auto   # ハイブリッド/Azure を run_turn 実行

gold(Opus) 解析・rubric 作成・採点は別途（Claude Code セッションが実施）。
"""
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

# .env からは AZURE_ のみ取り込む（interp_eval_run.py と同じ規約）。
try:
    from dotenv import dotenv_values, find_dotenv
    _envpath = find_dotenv(usecwd=True)
    for _k, _v in (dotenv_values(_envpath) if _envpath else {}).items():
        if _k.startswith("AZURE_") and _v and _k not in os.environ:
            os.environ[_k] = _v
except ImportError:
    pass

import agent_core as ac
import interp_eval as ie
import server
import session_state
from agent_repl import _get_tool_schemas, safe_classify
from interp_eval_cases_liver2 import DIR_NEG, DIR_POS, LIVER2_CASES
from phase_router import RouterState

OUT = Path("interp_eval_out/liver2")
FROZEN = OUT / "frozen"
INTERP = OUT / "interp"
AUTO_MODELS = ["hybrid", "azure"]


def build_agent(key, schemas):
    """自動モデルの Agent を組み立てる。hybrid=qwen3:14b+Azureエスカレーション、azure=Azure単体。"""
    if key == "azure":
        chat_fn = lambda messages, tools: ie.openai_chat(messages, tools)
        interp_fn = None
    elif key == "hybrid":
        chat_fn = ac.ollama_chat
        interp_fn = lambda messages: ie.azure_generate(messages)
    else:
        raise ValueError(f"unknown model key: {key}")
    return ac.Agent(
        tool_schemas=schemas, chat_fn=chat_fn, execute_fn=ac.execute_tool,
        classify_fn=safe_classify, interp_fn=interp_fn,
        system_prompt=ac.DEFAULT_SYSTEM)


def do_freeze():
    """各ケースの pipeline を正準実行し、最後のツール出力を gold 証拠として凍結する。"""
    FROZEN.mkdir(parents=True, exist_ok=True)
    for case in LIVER2_CASES:
        session_state.session = server.AnalysisSession()
        out = ""
        for step in case.pipeline:
            out = ac.execute_tool(step.name, step.args)
        out = ac._truncate(out)
        last = case.pipeline[-1]
        fc = ie.FrozenCase(case.id, case.phase_label, case.mode, case.query,
                           last.name, last.args, out)
        (FROZEN / f"{case.id}.json").write_text(
            json.dumps(asdict(fc), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"froze {case.id} ({len(out)} chars)")


def _directory(mode):
    return DIR_NEG if mode == "NEG" else DIR_POS


def do_run_auto():
    """自動2モデルを run_turn で各ケース実行し、トランスクリプトを保存する（冪等）。"""
    INTERP.mkdir(parents=True, exist_ok=True)
    schemas = asyncio.run(_get_tool_schemas())
    for case in LIVER2_CASES:
        directory = _directory(case.mode)
        for key in AUTO_MODELS:
            dest = INTERP / f"{case.id}__{key}.txt"
            if dest.exists():
                print(f"skip {case.id} / {key} (exists)")
                continue
            os.environ["LIPIDMIX_DATA_DIR"] = directory
            session_state.session = server.AnalysisSession()
            # データロードは全モデル共通の決定的セットアップ（解析ツール駆動を単離）。
            ac.execute_tool("load_dataset", {"directory": directory})
            state = RouterState(dataset_loaded=True)
            conversation = []
            agent = build_agent(key, schemas)
            final = agent.run_turn(case.query, state, conversation)
            dest.write_text(ie.transcript_text(conversation, final), encoding="utf-8")
            print(f"ran {case.id} / {key} ({len(final)} chars, arm={state.last_arm})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"freeze": do_freeze, "run_auto": do_run_auto}.get(
        cmd, lambda: print("usage: interp_eval_liver2.py freeze|run_auto"))()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_liver2 -v`
Expected: PASS（全3件）

- [ ] **Step 5: Run full unit suite (regression)**

Run: `.venv-1/Scripts/python.exe -m unittest discover -s tests -t .`
Expected: OK（既存＋新規すべて緑。ネットワーク/Ollama 非依存でパスすること）

- [ ] **Step 6: Commit**

```bash
git add interp_eval_liver2.py tests/test_interp_eval_liver2.py
git commit -m "feat(interp-eval): liver2 オーケストレータ（gold凍結・自動モデルrun_turn実行）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Gold（Opus）ライブ解析・rubric作成・差次確定・凍結

**種別: 解析（Claude Code セッション＝私が実施。TDD 対象外）。** ツールを `execute_tool` でライブ駆動し、科学的分離が出るまで再PCAして正解解釈を確立、必須所見と5軸基準の `rubric.md` を作る。

**Files:**
- Create: `interp_eval_out/liver2/gold/analysis.md`（再PCAの反復過程と到達解釈）
- Create: `interp_eval_out/liver2/rubric.md`（フェーズ別 必須所見＋5軸0–2採点基準）
- Create: `interp_eval_out/liver2/gold/*.png`（主要PCA/volcano図。`arf_reader.py`/`server` の図保存 or matplotlib）
- Modify（必要時）: `interp_eval_cases_liver2.py`（差次コントラストを実測主因へ差し替え）
- Create: `interp_eval_out/liver2/frozen/*.json`（`freeze` サブコマンド出力）

- [ ] **Step 1: NEG を全フェーズでライブ駆動**

`.venv-1/Scripts/python.exe` の対話 or スクラッチ（`check.py`）で、NEG に対し:
`execute_tool("load_dataset",{directory:DIR_NEG})` → `arf_list_classes` → `arf_re_pca`（全サンプル）を実行。
GF/SPF・食餌のどちらで PC1/PC2 が分離するかを確認。QC/Blank を除外し、`arf_re_pca` の
`class_ids`/`group_levels` を変えて**分離が明瞭になるまで再PCA**（例: `group_levels=["GF","SPF"]`、
次に `group_levels=["HFD","NC","AIN"]`、必要なら `class_ids` で QC/Blank 除外）。
到達した分離の主因・寄与率・支配脂質クラスを `gold/analysis.md` に記録。

Expected: 主分離因子（菌叢 or 食餌）が特定でき、寄与率と方向が数値で得られる。

- [ ] **Step 2: NEG の差次・QC・同定・文献を駆動**

- 差次: 主因に沿った2群（例 SPF vs GF）で `arf_preprocess(normalize=median,impute=half_min)` →
  `arf_differential(group_a=..., group_b=...)`。`n_significant`・上位有意脂質・方向・交絡注意を記録。
- QC: `arf_preprocess(normalize=median, max_qc_rsd=30, impute=half_min)`。QC-RSD 水準・drift・
  補完・blank 除去の所見を記録（QC n=5 で実測 RSD が出る）。
- 同定: `arf2_annotate_identities(max_rows=30)`。MSIレベル分布・過剰主張リスクを記録。
- 文献: `paper_search` を菌叢/食餌-肝リピドームのクエリで実行し関連度を評価。

Expected: 各フェーズの gold 所見が `analysis.md` に揃う。

- [ ] **Step 3: POS を同様に駆動**

`DIR_POS` で Step 1–2 を反復。POS で成立する分離・差次（例 HFD vs NC）を確認し、POS 側 gold 所見を記録。
POS で差次/QC が退化する場合はその旨を `analysis.md` に明記し、差次コントラストを NEG 主因へ寄せる。

Expected: POS の gold 所見が揃い、POS/NEG 統合の総合解釈が `analysis.md` に書ける。

- [ ] **Step 4: 差次コントラストを確定し cases を更新**

Step 1–3 の実測主因に基づき、`interp_eval_cases_liver2.py` の `differential_microbiome`/
`differential_diet` の `group_a`/`group_b`（および必要なら mode/directory）を最も生物学的に読める
2コントラストへ確定。`arf_differential` が実際に有効群を返す指定であることをライブ実行で確認済みにする。

- [ ] **Step 5: rubric.md を作成**

`interp_eval_out/liver2/rubric.md` に、10ケースごとの**必須所見リスト**（gold から抽出）＋
5軸（hallucination/accuracy/completeness/utility/language）の**0–2 採点基準**を明記。
hallucination は最上位ゲート（致命的捏造は当該ケース0点）。completeness は必須所見の被覆率で
0=<34% / 1=34–66% / 2=>66% の目安を書く。

- [ ] **Step 6: gold 正準出力を凍結**

Run: `.venv-1/Scripts/python.exe interp_eval_liver2.py freeze`
Expected: `interp_eval_out/liver2/frozen/*.json` が10件生成（差次確定後の cases を反映）。

- [ ] **Step 7: cases テスト再実行（確定後の回帰）**

Run: `.venv-1/Scripts/python.exe -m unittest tests.test_interp_eval_cases_liver2 -v`
Expected: PASS（差次確定後も構造妥当）。

- [ ] **Step 8: Commit**

```bash
git add interp_eval_out/liver2/gold interp_eval_out/liver2/rubric.md \
        interp_eval_out/liver2/frozen interp_eval_cases_liver2.py
git commit -m "analysis(interp-eval): Opus gold 解析・rubric・差次確定・正準凍結

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 自動モデル（ハイブリッド・Azure）の実行

**種別: オーケストレーション（実データ・実LLM。TDD 対象外）。** Ollama 起動＋Azure creds を前提に自動2モデルを全ケース実行。

- [ ] **Step 1: 前提確認**

Run: `curl -s -m 5 http://localhost:11434/api/tags` で qwen3:14b の在庫、`.env` の `AZURE_OPENAI_*` を確認。

- [ ] **Step 2: 1ケースでスモーク**

`interp_eval_liver2.do_run_auto` を pca_neg のみ手早く確認（例: 一時的に `LIVER2_CASES[:1]` を回すスクラッチ）。
Azure アダプタがツール駆動→最終解釈まで通り、`interp/pca_neg__azure.txt` にツール呼び出し列＋FINAL が残ることを確認。
ハイブリッドが `arm=cloud`（高価値ターン）に入るかも確認。

Expected: トランスクリプトにツール呼び出しと最終解釈が両方残る。エラー時は §失敗時対応。

- [ ] **Step 3: 全ケース実行**

Run: `.venv-1/Scripts/python.exe interp_eval_liver2.py run_auto`
Expected: `interp_eval_out/liver2/interp/<case>__{hybrid,azure}.txt` が計20件（冪等: 既存はスキップ）。

- [ ] **Step 4: Commit**

```bash
git add interp_eval_out/liver2/interp
git commit -m "run(interp-eval): 自動モデル（hybrid/azure）全10ケース実行

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

**失敗時対応:** Azure が tools 非対応/400 の場合は body の tools 形式・v1 URL を確認。ツールループが
max_rounds で終わる場合は system_prompt を微調整（`DEFAULT_SYSTEM` に「必要なら arf_list_classes で群名を確認」を追記）。qwen が中国語ドリフトする場合はハイブリッドの既知弱点として記録（採点対象）。

---

### Task 7: Sonnet 5 / Haiku 4.5（Claude Desktop 手動）

**種別: 手動ハンドオフ（ユーザーが Desktop で実行）。** 手順書を作り、ユーザーが実行、結果を貼り戻す。

**Files:**
- Create: `interp_eval_out/liver2/desktop_protocol.md`
- Create（貼り戻し先）: `interp_eval_out/liver2/interp/<case>__sonnet5.txt`, `<case>__haiku45.txt`

- [ ] **Step 1: desktop_protocol.md を作成**

内容: (1) `claude_desktop_config.json` に `ms-data-parser`（`server.py`）を登録する手順と
`LIPIDMIX_DATA_DIR` の NEG/POS 切替、(2) モデル選択手順（Sonnet 5、Haiku 4.5）、
(3) 極性ごと1会話で流す10クエリ（`LIVER2_CASES` の `query` を NEG5→POS5 の順で列挙）、
(4) 各回答（ツール呼び出しを含むトランスクリプト）を `interp/<case>__<model>.txt` へ保存する様式。

- [ ] **Step 2: ユーザーが Sonnet 5 を実行**

ユーザーが Desktop で NEG会話・POS会話を実行し、10ケース分のトランスクリプトを貼り戻す。私は貼られた
内容を `interp/<case>__sonnet5.txt` に保存する。

- [ ] **Step 3: ユーザーが Haiku 4.5 を実行（可能なら）**

Desktop のモデルピッカーに Haiku 4.5 があれば同様に実行・保存。**無ければ** `desktop_protocol.md` に
「Haiku 4.5 は Desktop 選択不可のため除外（3モデル評価に縮退）」と記録し、以降のスコープから外す。

- [ ] **Step 4: Commit**

```bash
git add interp_eval_out/liver2/desktop_protocol.md interp_eval_out/liver2/interp
git commit -m "run(interp-eval): Desktop 手順書＋Sonnet5/Haiku45 トランスクリプト

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 採点（Opus基準ルーブリック照合）と集計

**種別: 解析（私が審判。TDD 対象外だが集計は Task 3 の関数を使う）。**

**Files:**
- Create: `interp_eval_out/liver2/scores.json`（全モデル×全ケースの5軸0–2＋notes）
- Create: `interp_eval_out/liver2/scores.md`（`score_aggregate` の集計）

- [ ] **Step 1: 全トランスクリプトを rubric 照合で採点**

私が `rubric.md`（必須所見）＋ `frozen/*.json`（gold証拠）＋各 `interp/<case>__<model>.txt` を突き合わせ、
モデル×ケースごとに5軸（各0–2）を採点し `scores.json` に記録（`{"case_id","phase_label","model","axes":{...},"notes":...}` の配列）。
hallucination の致命的捏造は当該ケース0点ゲート。

- [ ] **Step 2: 集計スクリプトで scores.md を生成**

`check.py` 等で:

```python
import json
from pathlib import Path
import interp_eval as ie
scores = json.loads(Path("interp_eval_out/liver2/scores.json").read_text(encoding="utf-8"))
models = sorted({s["model"] for s in scores})
agg = ie.score_aggregate(scores, models)
lines = ["# 解釈精度サマリ（Opus正解基準・各軸0–2）", "", "## 総合（1ケース5軸合計の平均, 満点10）"]
for m, v in sorted(agg["overall"].items(), key=lambda kv: -kv[1]):
    lines.append(f"- {m}: {v:.2f}")
lines += ["", "## 軸別平均"]
for m in models:
    lines.append(f"- {m}: " + ", ".join(f"{ax}={agg['by_model'][m][ax]:.2f}" for ax in ie.AXES))
lines += ["", "## フェーズ別 総合平均"]
for ph, d in agg["by_phase"].items():
    lines.append(f"- {ph}: " + ", ".join(f"{m}={d[m]:.2f}" for m in models))
Path("interp_eval_out/liver2/scores.md").write_text("\n".join(lines), encoding="utf-8")
print("wrote scores.md")
```

Run: `.venv-1/Scripts/python.exe check.py`
Expected: `scores.md` に総合ランキング・軸別・フェーズ別が出る。

- [ ] **Step 3: 人手校正（ユーザー）**

ユーザーが `interp/` と `rubric.md` から十数項目を抜き取り採点し、私の `scores.json` と一致するか確認。
ズレた項目は notes に理由を残して補正。

- [ ] **Step 4: Commit**

```bash
git add interp_eval_out/liver2/scores.json interp_eval_out/liver2/scores.md
git commit -m "score(interp-eval): 全モデル rubric 照合採点＋集計

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: 最終比較レポートと記録更新

**種別: 文書化。**

**Files:**
- Create: `interp_eval_out/liver2/report.md`
- Modify: `docs/HISTRY.md`（本 eval の結論を追記）、`docs/task.md`（タスク状態更新）
- Modify（メモリ）: `C:\Users\yuu18\.claude\projects\C--Users-yuu18-Lipidmix-with-LLM\memory\local-llm-architecture.md` と `MEMORY.md`（結論を一行反映）

- [ ] **Step 1: report.md を作成**

内容: (1) 目的・データ・手法（本 plan/spec 参照）、(2) 精度ランキング（`scores.md` 引用）、
(3) フェーズ別強弱（どのモデルがどのフェーズで gold から乖離したか）、(4) 主要所見（各下位モデルの
代表的な捏造・欠落・逸脱の実例）、(5) 限界（n=10・単一run・審判は私＋抜き取り校正・Haiku の Desktop 可否）、
(6) 結論（実運用でどのモデルが解釈担当に足るか）。

- [ ] **Step 2: docs/HISTRY.md・task.md 更新**

`docs/HISTRY.md` に 2026-07-08 エントリで結論を追記。`docs/task.md` の該当タスクを DONE に。

- [ ] **Step 3: メモリ更新**

`local-llm-architecture.md` に本 eval の結論（下位モデル精度ランキング・エスカレーション含意）を反映し、
`MEMORY.md` の該当行を更新。

- [ ] **Step 4: 全テスト最終確認**

Run: `.venv-1/Scripts/python.exe -m unittest discover -s tests -t .`
Expected: OK（全緑）。

- [ ] **Step 5: Commit**

```bash
git add interp_eval_out/liver2/report.md docs/HISTRY.md docs/task.md
git commit -m "report(interp-eval): 肝臓リピドーム解釈精度 最終レポート＋記録更新

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- §2 出場モデル/経路 → Task 1(Azureアダプタ)/4(hybrid・azure配線)/6(自動実行)/7(Desktop手動)。gold=Task 5。✓
- §3 データ/科学枠組み → Task 2(DIR/cases)/5(ライブ解析)。✓
- §4 10ケース → Task 2、差次確定=Task 5 Step4。✓
- §5 rubric照合採点(5軸)/二層審判 → Task 3(集計)/5(rubric)/8(採点・校正)。✓
- §6 成果物(analysis/rubric/frozen/interp/scores/desktop_protocol/report) → Task 5/6/7/8/9。✓
- §7 実装物(OpenAIアダプタ/cases/ランナー/採点集計) → Task 1/2/4/3。✓
- §8 実行手順 → Task 5–9 の順序に対応。✓
- §9 未解決(差次確定/POS成立/Haiku可否/rubric刻み) → Task 5 Step3–5/7 Step3 で処理。✓

**2. Placeholder scan:** コードステップは全て実コードを掲載。解析/手動タスク（5–9）は TDD 不能な作業だが、各ステップに具体的ツール・引数・出力先・検証コマンドを明記（"適切に" 等の曖昧語なし）。✓

**3. Type consistency:**
- `openai_chat` 返り値 `{"content","tool_calls":[{"function":{"name","arguments":dict}}]}` は `agent_core.run_turn` の消費形（`msg.get("tool_calls")` → `call["function"]["name"]` / `call["function"].get("arguments")`）と一致。✓
- `build_agent` は `agent_core.Agent`（`tool_schemas/chat_fn/execute_fn/classify_fn/interp_fn/system_prompt`）へ正しく対応。✓
- `score_aggregate` の入力 score スキーマは Task 8 の `scores.json` と一致（`case_id/phase_label/model/axes`）。✓
- `transcript_text` は run_turn が積む会話（`role`/`content`/`tool_calls`/`tool_name`）を消費。✓
- `Case`/`ToolStep`/`validate_cases`/`AXES` は `interp_eval` の既存定義を再利用（再定義なし）。✓
