# phase-router（フェーズ別ツール露出ルータ） 設計

- 日付: 2026-07-06
- 対象: 将来の Python Agent 側の新規部品 `phase_router.py`（MCP サーバ `server.py` は非改変）
- ステータス: 設計確定（実装計画へ移行予定）

## 1. 背景と目的

ローカル LLM（Ollama）にオーケストレーションを担わせる移行の中核課題は「**ツール氾濫**」。
MCP サーバは 35 ツールを露出するが、35 を一度にモデルへ渡すとツール選択精度が崩壊する。
GPU 装着後の実測（代表 12 クエリ・8 フェーズ横断・temp0）で決定的に裏付けられた:

| model | full(35 ツール) | subset(該当フェーズ 3〜8 ツールのみ) | subset 所要 |
|---|---|---|---|
| qwen2.5:7b | 4/12 | 10/12 | — |
| qwen2.5:14b | 3/12 | 11/12 | — |
| qwen3:14b think OFF | 5/12 | **12/12（満点）** | 21s |
| qwen3:14b think ON | 5/12 | 12/12（満点） | 123s |

決定的所見:
- **サブセット化が支配的レバー**。ツール数を絞るだけで全モデルが 4〜5 → 10〜12 へ跳ね上がる。
- モデルサイズはツール選択の主因ではない（35 フラットでは 14B が 7B より悪化、narration 冗長化・多言語ドリフト）。
- **ルーティング担当は qwen3:14b(think OFF) で確定**（subset 満点、qwen2.5:14b の唯一の残ミス
  `arf_differential`→`arf_preprocess` も解消）。think ON は routing に無益・コスト約 6 倍・
  full では存在するツールを幻覚的に拒否する退行 → routing では常時 OFF。

本設計はこの実証を製品化する。「**35 を絶対に一度に出さない**」を鉄則に、
クエリごとに現在フェーズを判定し、そのフェーズのツール群だけをモデルへ露出する
純粋関数 `route()` を定義する。

## 2. スコープ

### v1 に含める（スコープ A: router 単体＋検証ハーネス）
- 新規モジュール `phase_router.py`（リポジトリ直下、`server.py` と同階層）。
- 純粋関数 `route(query, state, classify_fn) -> RouteResult`。フェーズ判定と露出ツール名の決定のみ。
- ハイブリッド判定（状態ゲート → キーワード短絡 → LLM 分類 → フォールバック）。
- 判定フェーズ ＋ 常時コアツールの露出（dedup）。
- ユニットテスト（Ollama 不要、フェイク LLM 注入）＋ ライブ回帰ハーネス（fc_probe を router 経由に改修）。

### v1 に含めない（follow-up）
- 実際の Ollama 呼び出しループ・MCP ツール実行・戻り値要約・マルチターン会話管理・Web UI。
  → これらは phase-router を土台に載る後続タスク（別 spec）。
- ARF フェーズ（8 ツール）の更なる分割。実測で満点のため YAGNI。ARF 精度が劣化したら再検討。
- ステップ 4「解釈品質」評価・ステップ 5「ハイブリッド分界点」確定（別テーマ）。

## 3. アーキテクチャ

MCP サーバ（`server.mcp`、35 ツール全露出）は**一切変更しない**。
phase-router は将来の Python Agent 側（MCP クライアント側＝別プロセス）の部品として置く。
Agent はサーバの `AnalysisSession` を直接参照できない（別プロセス・MCP 越し）ため、
「dataset をロード済みか」等の軽量ビュー（`RouterState`）を **Agent 自身が保持**する。
router は状態を書き換えず、判定結果を返すのみ（純粋関数）。

```
phase_router.py（新規）
  PHASES        : dict[str, list[str]]    # 8 フェーズ → ツール名（fc_probe から移設し単一ソース化）
  CORE_TOOLS    : list[str]               # 常時露出（下記 §4.3）
  KEYWORD_RULES : list[tuple[Pattern, str]]  # 高精度短絡のみ（下記 §4.2）
  ANALYSIS_PHASES : list[str]             # LLM 分類の候補（ENTRY を除く 7 フェーズ）

  @dataclass RouterState:
      dataset_loaded: bool
      last_phase: str | None = None

  @dataclass RouteResult:
      phase: str
      tool_names: list[str]
      reason: str        # どのステップ（gate/keyword/llm/fallback）で決まったか

  classify_phase(query: str, state: RouterState, classify_fn) -> str
  route(query: str, state: RouterState, classify_fn) -> RouteResult
```

- `classify_fn(query: str, candidate_phases: list[str]) -> str` が唯一の LLM 依存点（DI）。
  既定実装は qwen3:14b(think OFF) バックエンド（Ollama `/api/chat`、temp0、think OFF、enum 厳格）。
  テストはフェイク `classify_fn` を注入し、Ollama 非依存でロジックを検証する。
- `route` は純粋関数。`RouterState` の更新（`last_phase` の書き戻し等）は呼び出し側 Agent の責務。

### フェーズ定義（PHASES）

fc_probe.py で確立済みのマップを単一ソースとして移設する:

| phase | tools |
|---|---|
| ENTRY | load_dataset, list_data_files, list_reports, read_report |
| OBJECTIVE | record_objective, update_objective, knowledge_coverage |
| ARF | arf_parser, arf_re_pca, arf_list_classes, arf_list_tags, arf_list_sample_roles, arf_preprocess, arf_pca_preprocessed, arf_differential |
| ARF2 | arf2_parser, arf2_annotate_identities |
| PAI2 | pai2_parser, pai2_get_top_metabolites, pai2_inspect_metabolite_details, pai2_update_analysis_filter |
| EIC | eicaef_parser, eicaef_search_by_mz_range, eicaef_search_by_rt_range, eicaef_top_peak_tops |
| LITERATURE | paper_search, ingest_stage, ingest_review_queue, ingest_promote, ingest_reject, log_search |
| FIGURES | save_pca_figure, save_volcano_figure, write_report, verify_peak_annotation |

`ANALYSIS_PHASES` = ENTRY を除く 7 フェーズ（LLM 分類の候補集合）。

## 4. route アルゴリズム（ハイブリッド ＋ コア露出）

```
route(query, state, classify_fn):
  1. 状態ゲート（無料・決定的）
       state.dataset_loaded == False → phase = ENTRY, reason = "gate"
       （必ず先にロードさせる。ロード前はほぼ全要求が ENTRY へ）
  2. キーワード短絡（無料・高精度のみ）※dataset_loaded == True のときのみ
       KEYWORD_RULES に最初にヒットした phase, reason = "keyword"
  3. LLM 分類（曖昧な時だけ課金）
       candidate = ANALYSIS_PHASES
       p = classify_fn(query, candidate)
       p が既知フェーズ名 → phase = p, reason = "llm"
  4. フォールバック
       p が既知フェーズ名でない → phase = state.last_phase or ENTRY, reason = "fallback"

  露出ツール = dedup_preserve_order(PHASES[phase] + CORE_TOOLS)
  return RouteResult(phase, tool_names, reason)
```

### 4.1 状態ゲート方針
- ゲートするのは「**dataset をロード済みか**」の 1 点のみ（hard）。
- **objective 確定状態ではゲートしない**。ツールの可用性は制限せず、ワークフロー助言
  （objective 確認を先に、等）はサーバ側 `MCP_INSTRUCTIONS` に委ねる。理由: MCP_INSTRUCTIONS
  自身が「These steps are guidance, not hard gates」と明言しており、ツール露出を objective 状態で
  絞ると柔軟性を失い brittle になる。

### 4.2 キーワード短絡（KEYWORD_RULES）
高精度・低誤爆のトークンだけを短絡に使う（曖昧語は入れない）:
- `\.pai2\b` または `pai2` → PAI2
- `m/?z\b` / `EIC` / `\.aef\b` / `AEF` → EIC
- `PMC` / `Europe ?PMC` / `文献` / `論文` / `paper_search` / `paper` → LITERATURE

ヒットしなければ LLM 分類へ。短絡は「明示的に別ファイル種別/文献を名指しした」ケースの
LLM 呼び出し節約が目的で、網羅は狙わない（曖昧語で誤爆させない方を優先）。

### 4.3 常時コアツール（CORE_TOOLS）
`CORE_TOOLS = [load_dataset, list_data_files, list_reports]`
- どのフェーズでも「入口へ戻る」動作（別フォルダ読み直し・データ一覧・既存レポート確認）は
  意味が通るため常時露出。フェーズまたぎの言い回しと LLM 誤判定を小さくヘッジ。
- 3 個と少数のため氾濫にほぼ寄与しない（氾濫は 35 個ゆえに起きる。+3 は無害＝実測の subset は
  3〜8 ツールで満点）。
- ENTRY フェーズは既に CORE_TOOLS を含むため dedup で重複しない。
- `knowledge_coverage` 等はコアに入れない（OBJECTIVE フェーズを状態/分類で出せば足りる）。

### 4.4 スティッキー last_phase
- フォールバック時は `state.last_phase`（無ければ ENTRY）を採用。多ターンの同一フェーズ継続と
  LLM 誤判定からの復帰を両取りする。`last_phase` の書き戻しは Agent 側の責務（route は純粋）。

## 5. LLM 分類器（既定 classify_fn）

- バックエンド: Ollama `/api/chat`、model = qwen3:14b、`options.temperature=0`、top-level `think=false`。
- プロンプト: システムで「与えられたクエリが属する解析フェーズを、候補列挙から**厳密に 1 つ**、
  フェーズ名のみで答えよ」と制約。候補（ANALYSIS_PHASES）と各フェーズの一行説明を提示。
- 出力パース: 応答から既知フェーズ名を抽出（前後空白・記号を除去し完全一致で照合）。
  一致しなければ「未知」として route の §4.4 フォールバックに委ねる。
- think OFF 固定（実測で routing に思考は無益・コスト約 6 倍・退行あり）。

## 6. テスト戦略

### 6.1 ユニット（pytest・Ollama 不要・フェイク LLM 注入）
- **分割整合テスト**（ドリフト検出）: `PHASES` の全ツールが `server.mcp.list_tools()` の 35 に実在し、
  **非コアツールがちょうど 1 フェーズに属する**（網羅かつ重複なし＝分割）。CORE_TOOLS も 35 に実在。
  ツール追加/改名時に即失敗する。
- **状態ゲート**: `dataset_loaded=False` の任意クエリ → phase=ENTRY, reason="gate"。
- **キーワード短絡**: `dataset_loaded=True` で "…pai2…" → PAI2, "m/z 700-720" → EIC,
  "Europe PMC で検索" → LITERATURE、いずれも reason="keyword"（LLM を呼ばないことをフェイクで確認）。
- **LLM 経路**: キーワード非ヒット時にフェイク classify_fn が返すフェーズが採用され reason="llm"。
- **フォールバック**: フェイクが未知文字列を返す → phase=last_phase（未設定なら ENTRY）, reason="fallback"。
- **コア露出/dedup**: 露出ツールに CORE_TOOLS が含まれ、ENTRY 選択時に重複しない。順序保存。

### 6.2 ライブ回帰（fc_probe を router 経由に改修）
- 既存 12 ケースを実 qwen3:14b(think OFF) で `route()` に通し、返った subset でツール呼び出し、
  期待ツール命中率を測定。現行の素 subset 12/12 を **router 経由でも維持**できるかを検証。
- 各ケースに `dataset_loaded` の前提（ENTRY ケースは False、他は True）と、必要なら `last_phase` を付与。
- これを継続的回帰ハーネスとする（`THINK=0` 既定、所要時間も記録）。

## 7. 受け入れ基準

- `phase_router.py` が上記インターフェースで存在し、`route` が純粋関数である。
- ユニットテスト（§6.1）が全て通る（特に分割整合テストが 35 ツールと一致）。
- ライブ回帰（§6.2）で router 経由の 12 ケース命中が **≥ 11/12**（素 subset 12/12 に対し、
  分類器の実運用ばらつきを許容した実質同等ライン。理想は 12/12）。
- MCP サーバ（`server.py` 及び `tools_*`）に差分がない。

## 8. 未解決事項・将来検討

- ARF フェーズ（最大 8 ツール）の分割 or `arf_differential` の description 明確化。現状は不要（実測満点）。
- 複数フェーズ露出（top-2）は氾濫再導入リスクのため v1 では不採用。誤判定が実運用で問題化したら再検討。
- `classify_fn` のキャッシュ（同一クエリ再分類の節約）は最適化として後回し。
- ARF フェーズの実効露出は 8＋コア3＝**11ツール**（LITERATURE は 9）で、実測満点の 3〜8 帯の上限。今は 12/12 で問題ないが、ARF 精度が劣化したら最初に分割すべきフェーズ。
