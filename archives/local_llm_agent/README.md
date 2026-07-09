# archives/local_llm_agent — ローカルLLM ツール駆動エージェント（廃止）

ローカルLLM（Ollama / Qwen3-14B）に MS-DIAL MCP の35ツールを駆動させる
**エージェントループ**の実装。トークン課金削減のためデスクトップアプリ（Sonnet/GPT）を
ローカル＋任意Azureへ置き換える構想の中核だったが、**検証の結果、解釈品質が実用水準に
届かないと判明したため退避**した。

## なぜアーカイブしたか
最終検証（肝臓リピドーム 20230824_liver、フルツール駆動 10ケース、Opus 4.8 正解基準）で:

| モデル | 総合スコア(満点10) |
|---|---|
| Sonnet 5 | 9.60 |
| Haiku 4.5 | 9.60 |
| Azure gpt-5.4-mini | 7.00 |
| **ローカルハイブリッド(qwen3:14b)** | **2.90** |

ローカルは自ら多段ツールを駆動する運用では収束せず（差次をRNA-seq誤読・同定空・
文献「該当なし」誤判定）、**解釈担当として不適格**。運用は Claude Desktop の
Haiku 4.5 で足りることが確定したため、この経路は不要になった。

## 中身
- `agent_core.py` — `Agent.run_turn`（1ターン = 1route + 有界ツールループ）。
  `execute_tool` は in-process 直呼び（never-raises）、`ollama_chat` は qwen3:14b think OFF。
  2段ターン設計で高価値フェーズのみ最終解釈をクラウド委譲する `interp_fn` DI を持つ。
- `agent_repl.py` — CLI REPL。`_build_interp_fn` にクラウド creds/トグル判定を閉じ込め。
- `phase_router.py` — 純粋関数 `route(query, state, classify_fn)`。35ツールを
  フェーズ別サブセットへ絞る「35を一度に出さない」ルーター（状態ゲート＋キーワード短絡＋LLM分類）。
- `tests/` — 対応ユニットテスト（`test_agent_core.py` / `test_agent_repl.py` / `test_phase_router.py`）。

## 依存関係と実行
これらは**ライブの ms-data-parser（server.py / tools_* / パーサ群）から一切参照されていない**。
逆に、ここのコードはリポジトリルートの `server` / `session_state` を import する。
また `agent_core` は姉妹フォルダ `../interp_eval/interp_eval.py` の `INTERP_SYSTEM` に依存する。
参考実行するにはリポジトリルート＋両アーカイブフォルダを `PYTHONPATH` に載せる必要がある
（＝現状はそのままでは動かない。参照用の退避）。

設計: `docs/superpowers/specs/2026-07-06-phase-router-design.md`,
`docs/superpowers/specs/2026-07-06-agent-loop-design.md`,
`docs/superpowers/specs/2026-07-07-live-cloud-interp-switch-design.md`。
