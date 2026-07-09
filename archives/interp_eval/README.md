# archives/interp_eval — 解釈精度 評価ハーネス（役目終了）

「MS-DIALツール出力をどのLLMに解釈させるべきか」を判定するための**評価ハーネス**。
凍結したツール出力／フルツール駆動でモデルにリピドーム解釈をさせ、ルーブリックで採点し
モデル間を比較した。**比較の結論が出て役目を終えたため退避**した。

## 結論（なぜアーカイブしたか）
最終評価（肝臓リピドーム 20230824_liver、フェーズ別10ケース、Opus 4.8 正解基準、5軸採点）:

| モデル | 総合スコア(満点10) |
|---|---|
| Sonnet 5 | 9.60 |
| Haiku 4.5 | 9.60 |
| Azure gpt-5.4-mini | 7.00 |
| ローカルハイブリッド(qwen3:14b) | 2.90 |

**Sonnet 5 ≒ Haiku 4.5 ≫ Azure ≫ ローカル**。運用は Claude Desktop の Haiku 4.5 で足りる
ことが確定し、ローカル/Azure 経路と、その良否を測るこの評価基盤は不要になった。
（限界: 審判は当該セッション単独＝**人手校正は残課題**。）

## 中身
- `interp_eval.py` — 採点・集計・トランスクリプト化・system プロンプト（`INTERP_SYSTEM`）、
  Azure/OpenAI ツール呼び出しアダプタ（`openai_chat` / `azure_generate`）。
- `interp_eval_cases.py` / `interp_eval_cases_liver2.py` — 評価ケース定義。
- `interp_eval_run.py` / `interp_eval_liver2.py` — ランナー（凍結出力生成・採点実行）。
- `liver2_drive.py` — サブエージェント用ツール駆動ヘルパ。
- `phase_router_eval.py` — phase_router のライブ回帰（命中率）評価。
- `out/` — 評価アウトプット一式（`liver2/` 等。**gitignore下＝ディスク保持のみ**）。
- `tests/` — 対応ユニットテスト（`test_interp_eval*.py`, `test_liver2_drive.py`）。

## 依存関係と実行
**ライブの ms-data-parser からは一切参照されていない**。ここのコードはルートの
`server` / `session_state` と、姉妹フォルダ `../local_llm_agent/`（`agent_core` /
`phase_router`）に依存する。参考実行にはリポジトリルート＋両アーカイブフォルダを
`PYTHONPATH` に載せる必要がある（現状そのままでは動かない。参照用の退避）。

所見: `docs/superpowers/notes/2026-07-07-interpretation-quality-findings.md`。
設計: `docs/superpowers/specs/2026-07-07-interpretation-quality-eval-design.md`,
`docs/superpowers/specs/2026-07-07-cloud-arm-champion-design.md`,
`docs/superpowers/specs/2026-07-08-liver-interp-accuracy-eval-design.md`。
