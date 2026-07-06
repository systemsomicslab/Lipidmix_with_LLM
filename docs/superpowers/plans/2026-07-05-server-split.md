# server.py 分割リファクタ プラン（#8）

作成: 2026-07-05 / 対象: `server.py`（2384行・35 tools・6 resources）

## 目的と非目的

- **目的**: 単一巨大モジュールを機能別モジュールへ分割し、可読性・変更容易性・レビュー可能性を上げる。
- **非目的**: 挙動変更・ツール仕様変更・API 名変更は行わない。**外形（MCP に登録されるツール群と入出力）は完全維持**する。純粋な構造リファクタ。

## 制約（実測した後方互換の要件）

テストと外部が依存する `server` の公開面は維持する:

1. `server.mcp` … `list_tools()` で 35 ツールが取れること（テスト3ファイルが参照）。
2. `server.session` / `server.AnalysisSession` … テスト6ファイルが **`server.session = AnalysisSession()` を計49回再代入**。
3. `server.<tool>()` … 約30ツールをテストが直接呼ぶ。
4. `server._<helper>` … テストが直接呼ぶ private ヘルパ11種:
   `_build_report_meta` `_build_sample_meta` `_build_verification_dossier`
   `_describe_batch_selection` `_dir_is_writable` `_first_writable_dir`
   `_pca_scatter_arrays` `_pp_build_matrix` `_pp_has_preprocessed`
   `_remember_arf_pca_plot` `_select_latest_batch`
5. `server.arf_reader` … `patch.object(server.arf_reader, ...)` でモックされる（module オブジェクト共有なので再エクスポートで維持可）。
6. `python server.py` の起動挙動（`__main__` → `mcp.run()`）を不変に保つ。

## 最重要の設計判断: session 参照の一元化

現状 `session` は module グローバルで、ツール本体が **ベタ名 `session.` を84箇所**参照する。
ツールを別モジュールへ動かすと「テストが `server.session` を再代入 → 別モジュールは自分の
`session` を見て不整合」という罠が生じる。

**方針: 正準の `session` を `session_state.py` に置き、全ツールは `session_state.session`
（module 修飾・動的参照）でアクセスする。** テストの `server.session = X` は
`session_state.session = X` に置換する（6ファイル・mechanical）。
`server.py` も `from session_state import session, AnalysisSession` を再エクスポートし、
`server.session` の**読み取り**は従来通り可能に保つ（再代入の宛先だけを正準へ寄せる）。

- 代替案B（テスト無改修）: `session` を `server.py` に残し、ツールは関数内で遅延 `import server`
  → `server.session` 参照。テスト改修ゼロだが「tools → server」の逆依存が残る。**採用しない**
  （層構造が濁る）。ただしテスト churn を避けたい場合のフォールバックとして記録。

## 目標モジュール構成（フラット、既存の命名流儀に合わせる）

```
mcp_core.py       # mcp インスタンス, MCP_INSTRUCTIONS, BASE_DIR/DATA_DIR, state-dir ヘルパ,
                  #   KNOWLEDGE/PLAYBOOK/ANALYSES_DIR, report-dir 解決, _build_report_meta
                  #   → leaf（stdlib / FastMCP / data_config のみ。tools を import しない）
session_state.py  # AnalysisSession, session シングルトン, _build_sample_meta, _BATCH_DATE_RE
path_resolvers.py # timestamp 正規表現, _recency_key/_pick_latest/_batch_key/
                  #   _select_latest_batch/_describe_batch_selection, resolve_*_file_path,
                  #   _filter_arf_spots
tool_helpers.py   # PCA/ARF 整形ヘルパ (_pca_scatter_arrays,_remember_arf_pca_plot,_format_*),
                  #   _identity_tables, _build_verification_dossier, _pp_build_matrix,
                  #   _class_factors_by_position
tools_resources.py# @mcp.resource ×6（output-format, knowledge/playbook index・expand, inbox）
tools_objective.py# record/update_objective, log_search, knowledge_coverage, paper_search,
                  #   ingest_stage/review_queue/promote/reject, _resolve_objective_file
tools_reports.py  # write/read/list_reports, save_pca_figure, save_volcano_figure
tools_dataset.py  # list_data_files, load_dataset
tools_pai2.py     # pai2_parser, pai2_get_top_metabolites, pai2_inspect_metabolite_details,
                  #   verify_peak_annotation, pai2_update_analysis_filter
tools_arf.py      # arf_list_tags, arf_list_classes, arf_list_sample_roles, arf_preprocess,
                  #   arf_pca_preprocessed, arf_parser, arf_re_pca, arf_differential
tools_arf2.py     # arf2_parser, arf2_annotate_identities
tools_eic.py      # eicaef_parser/top_peak_tops/search_by_mz_range/search_by_rt_range
server.py         # 薄いファサード: from mcp_core import mcp; from tools_* import *;
                  #   private ヘルパ11種を明示再エクスポート; __main__ で mcp.run()
```

依存方向（非循環）:
`mcp_core`（leaf）← `session_state` / `path_resolvers` / `tool_helpers` ← `tools_*` ← `server`。
ツールモジュールは decorator 実行のため import 時に `from mcp_core import mcp` するが、
**`server` は決して import しない**（循環回避の絶対ルール）。

## フェーズ（各フェーズ完了時に必ず全テスト green → コミット）

- **Phase 0 — 特性化テスト（安全網を先に敷く）**
  `tests/test_server_registration.py` を追加:
  - `len(mcp.list_tools()) == 35`、ツール名の sorted スナップショット一致
  - 6 resource の存在、`import server` が例外を出さないこと
  各フェーズ後にこれを回し、登録漏れ・名前変化を即検出する。
- **Phase 1 — `mcp_core.py` 抽出**（mcp/config/report-dir）。`server.py` は re-import。
- **Phase 2 — `session_state.py` 抽出 ＋ session 参照一元化**
  84 箇所の `session.` → `session_state.session.` に統一（まず server.py 内で実施）。
  テスト6ファイルの `server.session` → `session_state.session` へ置換。suite green を確認。
  ここで「参照場所非依存」が確立し、以降のツール移動が安全になる。
- **Phase 3 — `path_resolvers.py` 抽出**。
- **Phase 4 — `tool_helpers.py` 抽出**（整形・identity・dossier・pp_build_matrix）。
- **Phase 5〜12 — ツール群を1グループずつ移動**
  順序（依存の浅い順）: resources → objective → reports → dataset → arf2 → eic → pai2 → arf。
  各移動で `server.py` に `from tools_x import *` を追加。`*` は `_` 付きを取り込まないため、
  **テスト可視の private ヘルパは server.py で明示再エクスポート**する。
- **Phase 13 — server.py をファサード化**
  残った本体を除去し、import 束・再エクスポート・`__main__` のみに。全 suite + Phase 0 テスト +
  `python -c "import server"` + `python server.py`（起動 smoke、即終了）で確認。

各フェーズは独立コミット（`refactor(server): extract <module>`）で、問題時に単体 revert 可能。

## リスクレジスタと緩和

| リスク | 影響 | 緩和 |
|---|---|---|
| ツール登録漏れ（import 忘れ） | ツール消失 | Phase 0 の count/name スナップショット |
| session の参照不整合 | テスト失敗・状態共有崩壊 | 正準 `session_state.session` に一元化＋テスト置換 |
| 循環 import | 起動不能 | 厳格な層構造。tools は `server` を import しない |
| `import *` が `_helper` を運ばない | `server._x` の AttributeError | 11 ヘルパを明示再エクスポート（`__all__` 併用） |
| 84 箇所の機械置換ミス | NameError | 各フェーズ後に全 suite。少量ずつ移動 |
| matplotlib 重 import の位置 | 起動コスト変化 | figure 系は tools_reports/tool_helpers に限定 |

## 完了の定義

1. 全 232 テスト green（新規 registration テスト含む）。
2. `mcp.list_tools()` が 35、名前スナップショット不変、6 resource 健在。
3. `server.py` が概ね < 120 行のファサードに縮小。
4. 各ツールモジュールが単一責務・非循環。
5. `python server.py` が従来通り起動、README のツール表と齟齬なし。

## 見積り

大部分は機械的移動。フェーズ数 14、各 15〜40 分程度。分割コミットで漸進。
最大の注意点は Phase 2（session 一元化）— ここだけ本質的な意味変更を含むため慎重に。
