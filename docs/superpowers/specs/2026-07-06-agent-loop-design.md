# Agent-loop（ローカルLLM会話ループ） 設計

- 日付: 2026-07-06
- 対象: 将来の Python Agent 側の新規部品 `agent_core.py` / `agent_repl.py`（MCP サーバ・phase_router は非改変）
- ステータス: 設計確定（実装計画へ移行予定）

## 1. 背景と目的

ローカルLLM移行の狙う構成は「独自UI → Python Agent →（Ollama ＋ MS-DIAL MCP）」。
[[phase-router]]（`phase_router.py`）でツール氾濫問題（35ツール一括で選択精度崩壊、実測 5/12）は
解決済みで、`route(query, state, classify_fn)` が現在フェーズのツールサブセットだけを返す。

本設計はその周りの**会話ループ**を実装する。すなわち Claude Desktop（フロンティア1体が
オーケストレーションも解釈も担う）を置き換える、ローカルモデル駆動の対話エージェント本体:

```
ユーザー ↔ CLI REPL ↔ Agent ↔ Ollama(qwen3:14b)
                        │
                        ├─ phase_router.route()  … フェーズ判定＋ツールサブセット（実装済み）
                        ├─ execute_tool()          … 選ばれたツールを in-process 実行（本設計）
                        └─ ローカルモデルが結果を解釈して応答（クラウド委譲は別タスク）
```

従来の Claude Desktop パス（`server.py` に stdio MCP 接続、35ツール全露出、フロンティアが
氾濫に強い）は**無傷で併存**する。本エージェントは同じ `server.py` ツールを別フロントエンドから
叩く代替パスであり、ローカルモデルには phase_router が必須という違いだけがある。

## 2. スコープ

### v1 に含める
- `agent_core.py`: ループ論理（`Agent.run_turn`）・`execute_tool`・`ollama_chat`・戻り値切り詰め。
- `agent_repl.py`: CLI REPL（マルチターン対話）、実 Ollama/実ツールの配線、`RouterState` の生存管理。
- 1ユーザーターン=`route()` を1回引いて**フェーズ固定**、その中で**有界ツールループ**（最大 `max_rounds`）。
- ツール実行は **in-process 直呼び**（`getattr(server, name)(**args)`）。
- ローカル単一モデルによる解釈（ツール結果を受けた自然文応答）。
- 基本エラー処理: Ollama ダウン・ツール例外・未知ツール・戻り値肥大・ループ上限。

### v1 に含めない（follow-up）
- Web UI（本エージェントをバックエンドに載せる別タスク）。
- クラウド委譲（gpt-4o-mini 等への最終解釈ハンドオフ＝ハイブリッド分界点、別テーマ）。
- 会話履歴の永続化（起動ごとに新規会話）。
- 戻り値の高度な要約（v1は文字数上限の切り詰めのみ）。
- 同一ターン内のフェーズ跨ぎ自動化（ユーザーが次ターンで出す）。
- MCP プロトコル越しのツール呼び出し（in-process の継ぎ目 `execute_tool` を後日差し替え）。

## 3. アーキテクチャ

ループ論理（テスト可能な純ロジック）と、CLI I/O・実 Ollama HTTP・実ツール呼び出し（副作用）を
分離する。全ての I/O は依存注入（DI）にして、`run_turn` をフェイクで単体検証できるようにする。
`phase_router` と `server` はいずれも無改変で import して使う。

```
agent_core.py（新規・ループ論理の中核）
  DEFAULT_SYSTEM : str                     # 簡潔なシステムプロンプト（解析アシスタント／ツールを使え／結果を解釈せよ）
  execute_tool(name: str, args: dict) -> str
      # ★MCP境界の継ぎ目。in-process で getattr(server, name)(**args) を呼ぶ。
      #   戻り値(list/dict/str)を JSON 文字列へ統一。未知ツール・呼び出し例外は
      #   {"status":"error","error":...} の JSON 文字列にして返す（raiseしない）。
  AGENT_MODEL : str                        # os.environ.get("LIPIDMIX_AGENT_MODEL", "qwen3:14b")
  ollama_chat(messages: list[dict], tools: list[dict]) -> dict
      # /api/chat（model=AGENT_MODEL, think=false, options.temperature=0, stream=false）
      #   → message dict（"tool_calls" / "content"）。httpx 例外はここでは捕えず run_turn 直下に委ねる。
  _truncate(text: str, limit: int = 8000) -> str   # 戻り値サイズ上限（超過時は末尾にマーカー）

  @dataclass Agent:
      tool_schemas: dict[str, dict]          # name -> Ollama tool schema（起動時 list_tools() から一括構築）
      chat_fn: Callable[[list, list], dict]  # 既定 ollama_chat（テストはフェイク）
      execute_fn: Callable[[str, dict], str] # 既定 execute_tool（テストはフェイク）
      classify_fn: Callable[[str, list], str]# route 用（注入必須。REPL が safe_classify を渡す）
      max_rounds: int = 5
      system_prompt: str = DEFAULT_SYSTEM
      def run_turn(self, query: str, state: RouterState, conversation: list[dict]) -> str

agent_repl.py（新規・薄いCLI）
  safe_classify(query, candidates) -> str    # ollama_classify_fn を包み httpx 例外時 "" を返す（→routeフォールバック）
  main():
    起動時: schemas = asyncio.run(_get_tool_schemas())   # server.mcp.list_tools() から
    state = RouterState(dataset_loaded=False)            # 会話全体で1個・永続
    conversation: list[dict] = []
    agent = Agent(tool_schemas=schemas, chat_fn=ollama_chat, execute_fn=execute_tool, classify_fn=safe_classify)
    while True:
        query = input("> ")   # 空/quit で終了
        try: print(agent.run_turn(query, state, conversation))
        except httpx.HTTPError: print("Ollamaに接続できません…")   # REPLは落ちない
```

- `Agent` は設定の容れ物。`state`（RouterState）と `conversation` は REPL が所有し引数で渡す。
  `run_turn` はそれらを更新する（副作用は渡された引数オブジェクトに限定）。
- ツールスキーマは `server.mcp.list_tools()`（async）を**起動時に1回**取得して name→schema の辞書に。
  `run_turn` は route の返す tool_names でこの辞書を引いてサブセットを組む（`phase_router_eval.py` と同流儀）。

## 4. run_turn アルゴリズム（1ターン=1フェーズ固定＋有界ループ）

```
run_turn(query, state, conversation):
  1. phase, tool_names = route(query, state, self.classify_fn)   # ターン頭で1回、フェーズ固定
     state.last_phase = phase
     tools = [self.tool_schemas[n] for n in tool_names if n in self.tool_schemas]
  2. conversation.append(user(query))
  3. for _ in range(self.max_rounds):
       msg = self.chat_fn([system] + conversation, tools)
       calls = msg.get("tool_calls") or []
       if calls:
           conversation.append(assistant_with_tool_calls(msg))
           for call in calls:
               name = call["function"]["name"]; args = call["function"].get("arguments") or {}
               result = _truncate(self.execute_fn(name, args))
               conversation.append(tool_msg(name, result))
               if name == "load_dataset" and not _is_error(result):
                   state.dataset_loaded = True          # ★以後ENTRYゲートを抜け、フェーズ判定が有効化
           continue        # 次ラウンドで結果を踏まえて再度モデルに問う
       else:
           conversation.append(assistant(msg.get("content") or ""))
           return msg.get("content") or ""              # 最終応答＝ローカル解釈
  4. return "（ツール呼び出しが上限に達しました）"          # max_rounds 到達
```

- **状態遷移**: `load_dataset` 成功で `dataset_loaded=True`。以降のターンで route が ENTRY ゲートを抜け、
  キーワード/LLM 分類が有効になる（「まず読め」→「読んだので解析フェーズへ」の自然な流れ）。
- **フェーズ内多段**: ARF 前処理→差次的解析のような複数ツールは round ループ内で回る。
- **フェーズ跨ぎ**: 同一ターンでは非対応。ユーザーが次ターンで別要求を出せば別フェーズに route される。
- `_is_error(result)`: `result` を json.loads して dict かつ `status=="error"` なら真（パース不能時は偽）。
- メッセージ整形（`user`/`assistant`/`assistant_with_tool_calls`/`tool_msg`）は Ollama `/api/chat` の
  role 規約に従う（tool 結果は role="tool"、content=result、name=ツール名）。

## 5. エラー処理

- **Ollama ダウン（分類）**: `safe_classify` が `ollama_classify_fn` の `httpx.HTTPError` を捕え `""` を返す。
  route は既知フェーズ名でないと判定し `last_phase`（無ければ ENTRY）へフォールバック。
  → 最終レビューで繰越した「transport 例外フォールバック契約」を **phase_router 非改変のまま agent 層で解決**。
- **Ollama ダウン（本チャット）**: `ollama_chat` の `httpx.HTTPError` は `run_turn` を貫通し、REPL の
  `try/except` が捕えてメッセージ表示。REPL は継続（落ちない）。
- **ツール例外・未知ツール**: `execute_tool` が捕え `{"status":"error","error":...}` を返す。モデルに戻り、
  ループ継続。REPL は落ちない。
- **戻り値肥大**: `_truncate`（既定8000字）で文脈膨張を防ぐ。高度な要約は follow-up。
- **ループ上限**: `max_rounds`（既定5）到達で打ち切り、上限メッセージを返す（無限ループ防止）。

## 6. テスト戦略

### 6.1 ユニット（`tests/test_agent_core.py`・Ollama不要・フェイク注入・unittest）
- スクリプト化フェイク `chat_fn`（1回目=tool_call を返す→2回目=最終 content）＋フェイク `execute_fn`（定型JSON）で
  `Agent.run_turn` を駆動し検証:
  - ツールが正しい name/args で `execute_fn` に渡る。
  - `conversation` が user → assistant(tool_calls) → tool → assistant(content) の順で成長。
  - `load_dataset` 成功結果で `state.dataset_loaded` が True になる。
  - `state.last_phase` が route 済みフェーズに更新される。
  - `max_rounds` 到達時に上限メッセージを返し、無限ループしない（毎回 tool_call を返すフェイクで検証）。
  - `_is_error` が status=="error" JSON を正しく判定（True/False 双方）。
- `safe_classify`: 例外を投げるフェイク分類器を包むと `""` を返す（→ route フォールバック）。
- `_truncate`: 上限超過で切り詰め＋マーカー、上限内は素通し。

### 6.2 in-process 実ツール（`execute_tool` の実 server 適用・Ollama不要）
- 未知ツール名 → error JSON（raise しない）。
- `arf_differential`（行列未ロード）→ ツールが返す error JSON がそのまま文字列で返る。
- `load_dataset` の list 戻り値が JSON 文字列へ統一される（`session_state.session` を setUp でリセット）。

### 6.3 手動スモーク（実 Ollama）
- `.venv-1/Scripts/python.exe agent_repl.py` を起動 → 実データフォルダを「読み込んで」→ 1問。
  route の reason 遷移（初手 gate=ENTRY → load 後に keyword/llm）と応答を目視確認。自動化しない。

## 7. 受け入れ基準

- `agent_core.py` / `agent_repl.py` が上記インターフェースで存在し、`run_turn` の I/O が全て DI。
- ユニット（§6.1）と in-process 実ツール（§6.2）が全て通る。
- MCP サーバ（`server.py` / `tools_*`）と `phase_router.py` に差分がない。
- 手動スモーク（§6.3）で、ロード→解析ターンの遷移と応答生成が確認できる。

## 8. 未解決事項・将来検討

- **クラウド委譲の分界点**（ステップ5）: どのフェーズ/どの解釈からクラウドへ投げるか。本 v1 は
  ローカル単一モデルのみ。`chat_fn` が DI なので、モデル選択層を後から差し込める。
- **システムプロンプトの厚み**: v1 は簡潔版。サーバの `MCP_INSTRUCTIONS`（ワークフロー・ゲートウェイ）を
  どこまでローカルモデルへ注入するかは、文脈予算とのトレードオフで要チューニング。
- **戻り値要約**: 大きな JSON（PCA座標・differential 全行）は切り詰めでなく構造化要約が望ましい。
- **MCP プロトコル化**: 最終的な「別プロセス・MCP越し」構成へ `execute_tool` を差し替え。
- 会話履歴の永続化・トークン/レイテンシ計測の常設。
