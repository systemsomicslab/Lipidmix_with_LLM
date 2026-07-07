# 高価値ツール戻り値の構造化スリム化 設計（C）

- 日付: 2026-07-07
- 位置づけ: ローカルLLM移行の残タスク分解のうち **C（構造化要約）**。順序は **C → A（クラウド解釈切替）**。本 spec は C 単独。
- 先行パターン: `2026-07-07-differential-payload-slim-design.md`（差次スリム）と同型。冗長なプロット用配列を解釈 payload から外し、全量は session に残して図ツールが参照、`_truncate(8000)` は backstop として継続。

## 背景（実測）

`agent_core.execute_tool` の戻り値は `_truncate(text, 8000)`（`agent_core.py:23`）で 8000字に切り詰められてからモデル文脈へ入る。実データ（liver NEG n=60 / POS n=3）で高価値10ケースの最終 payload を実測した結果、**8000字を溢れて truncate される（＝結論が埋没する）のは PCA と文献のみ**：

| ケース | 実サイズ | 8000後 | 判定 |
|---|---|---|---|
| pca_neg（実60サンプル） | 14,360字 | 8,015 | **埋没** |
| literature_pos（10件） | 14,024字 | 8,015 | **埋没** |
| pca_pos | 5,140 | — | ok |
| differential_lps / _ilg（slim済） | ~4,578 | — | ok |
| qc_neg / qc_pos | 749 | — | ok |
| identity_neg / _pos | ~5,589 / ~5,184 | — | ok |
| literature_neg | 1,816 | — | ok |

**PCA の病理は差次と同型**: レポート先頭に結論（PC1=24.04%/PC2=15.20%・行列形状・群分離）があるが、中盤に「PCAスコアプロット用データ」= 60サンプル×{pc1,pc2,group} の散布図座標 JSON（`indent=2` で膨張）が居座り、truncate がこの配列の途中で切れて、**末尾の PCA Loadings（各PC上位10＝どの脂質がPC1/PC2を駆動するか。脂質名・m/z・RT 付き＝解釈の金脈）が丸ごと消える**。散布図座標は `session.last_pca_plot` に独立保存されており（実測: ARF PCA 経路後に 60点で populate、`save_pca_figure` 動作確認済）、payload 内の埋め込みは**冗長**。

**文献**は 10件×全文抄録で 14k。差次・PCA と違い配列ノイズではなく正当な10件だが、各抄録全文は関連度トリアージには過剰で、全文は `ingest_stage` 時にモデルが再提示するため payload では冒頭で足りる。

## 目的

高価値ツール（PCA・文献）の payload を、`_truncate(8000)` 後もモデルが結論と解釈の実体（PCA=loadings、文献=全候補の title＋citation＋抄録冒頭）を必ず見られる形へスリム化する。差次で確立した「冗長なプロット用配列を外し、全量は session、要約を payload」の per-tool パターンを踏襲する。

## スコープ

- 対象:
  - `tool_helpers.py` の `_format_pca_plot_block`（3つの ARF PCA ツール = `arf_pca_preprocessed` / `arf_parser` / `arf_re_pca` が共有）。
  - `tools_objective.py` の `paper_search`。
- 非対象:
  - `arf_reader.run_pca` / `session.run_pca` / `last_pca_plot` 保存経路・`save_pca_figure`（不変。図データの供給源）。
  - PCA loadings 生成（`get_pca_loading_features`、top_features 既定10）は不変。
  - 差次（既に slim）・QC・identity（既に 8k 内）への変更。
  - `agent_core._truncate`（backstop として不変）。

## 設計

### データフロー（責務分離、差次と同型）
- **全量プロットデータ → session 経由で図へ**: PCA 散布図座標は `session.last_pca_plot`（`tool_helpers` の保存ヘルパーが populate）に残す。`save_pca_figure`（`tools_reports.py:112`）はここから読むため無傷。
- **要約 → payload 経由でモデルへ**: payload は結論＋解釈の実体のみ。

### 変更1: PCA（`_format_pca_plot_block`）

現行はヘッダ導入文 `intro` の後に、60サンプル分の `data` 配列を含む `plot_json_data` を `json.dumps(..., indent=2)` でコードブック埋め込みしている（`tool_helpers.py:166–172`）。これを以下へ変更する:

- 散布図座標 `data`（per-sample 配列）を payload から**削除**する。
- 代わりに温存するもの:
  - ヘッダ: `title`、`x_axis`=`PC1 (NN.NN%)`、`y_axis`=`PC2 (NN.NN%)`（説明分散比）。
  - **群別サンプル数の一行**（`groups` から集計。例 `群別サンプル数: G=15, ILG=15, LPS=15, control=15`）。群分離主張の接地に有用で低コスト。`groups` が空（群ラベル無し）のときは総サンプル数のみ。
  - note 一行: `散布図の点列は本要約に非同梱。save_pca_figure で図示できます。`
- 呼び出し 3 箇所（`tools_arf.py:184` / `:308` / `:458`）は本ヘルパー戻り値の差し替えのみで、site 側の変更は不要。loadings ブロックは各ツールが別途連結しており不変（末尾に残る）。

**期待効果**: pca_neg 14,360字 → 8,000字未満、かつ loadings が truncate に消えない。

### 変更2: 文献（`paper_search`）

現行は各候補で `out.append(f"- abstract: {cand['abstract']}")`（`tools_objective.py:186`）と全文抄録を連結。これを以下へ変更する:

- 抄録を payload 内で **500字上限**にし、超過時は末尾に `…（截断）` を付す（全文が必要なら `ingest_stage` 時にモデルが本文から再提示する運用は既存のまま）。
- title・citation・DOI/PMID・件数見出し・「抄録は非信頼データ」注記は不変。上限化は `abstract` 行のみ。
- `max_results`（既定10）は不変（breadth を保ち全候補の title を見せる方が関連度トリアージに資する）。

**期待効果**: literature_pos 14,024字 → 8,000字未満、かつ全10候補の title＋citation＋抄録冒頭が残る。

### backstop
`agent_core._truncate(text, 8000)` は不変。将来の新ツール／想定外の溢れを引き続き捕捉する安全網として残す。

## テスト（TDD）

新規／拡張するユニット（既存テスト構成に合わせて配置。PCA=`tests/test_*`（PCA整形ヘルパー）、文献=`tests/test_objective_tools.py` 相当）:

1. **PCA payload 形状**: `_format_pca_plot_block`（または `arf_re_pca` 実行）の戻り文字列に、per-sample 散布キー（`"pc1"` / `"sample"` の JSON 配列）が**含まれない**こと。PC1/PC2 の説明分散%・note 文言・群別サンプル数行が**含まれる**こと。
2. **PCA 図データは session に残る**: PCA 実行後 `session.last_pca_plot["points"]` が 60 点（実データ）で非空、`save_pca_figure` が従来通り図パスを返すこと。
3. **PCA loadings 温存**: `arf_re_pca` の payload に loadings 節（脂質名・Loading 値）が含まれ、8000字切り詰め後も残ること（実測 < 8000）。
4. **文献抄録上限**: 長い抄録を持つ候補で各 `abstract` 行が 500字（＋截断マーカー）以内に収まり、全候補の `## {title}` 見出しが残ること。検索モック（`paper_ingest.search_europepmc` 差し替え）で検証。
5. **回帰**: `test_differential_tools` / `test_report_tools` / `test_server_registration` / 全体スイートが緑。

## 実装後の検証

- `scratchpad/measure_payloads.py`（本設計中に作成した実測ハーネス。repo へ移すなら `tools/` 等）を再走し、**高価値10ケースすべてが ≤ 8000字（BURIED 消滅）** を確認する。
- 任意: eval harness で `pca_neg` と `literature_*` を freeze→generate→再採点し、loadings/全候補が可視化されて解釈が改善するか（特に PCA で「どの脂質が群分離を駆動するか」に言及できるか）を実測する。これは後続 A（クラウド解釈切替）で「クラウドがさらに多くの loadings/抄録を欲するか＝tiering の要否」を判断する土台にもなる。

## 非目標（YAGNI）

- agent 側の汎用構造化要約層は作らない（どのフィールドが実体か判別できず金脈を落とす）。
- ローカル/クラウドで詳細度を変える tiered payload は本 spec では扱わない（A で実測に基づき判断）。
- payload 全体の `indent=2` 廃止はしない（該当2ツールは配列除去・抄録上限で 8k に収まるため不要）。
- PCA loadings の top_features 変更・差次/QC/identity の payload 変更はしない。

## 付随更新

- `docs/output_format.md` が PCA payload 形状（散布図 JSON 埋め込み）や文献 payload を記載していれば、散布図配列→ヘッダ＋note、抄録全文→上限化へ同期する。
