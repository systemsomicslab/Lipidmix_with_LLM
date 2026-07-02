# フェーズ2 科学的完全性（前処理・QC / 差次的解析 / 同定信頼度）設計

- 日付: 2026-07-02
- 対象: MS-DIAL 解析 MCP サーバ（`server.py`）＋新規純ロジックモジュール3本
- ステータス: 設計確定（実装計画へ移行予定）
- 実装順: P2a（前処理・QC）→ P2b（差次的解析）→ P2c（同定信頼度・標準化）

## 1. 背景と目的

本システムは MS-DIAL 出力の解釈を LLM に安全に行わせる MCP サーバとして、認識論の統制
（決定論的データ=事実 / 文献=仮説、矛盾の顕在化、人間昇格ゲート）は世界水準にある。
一方で **科学的中核機能** が欠けており、実際のリピドミクス研究の需要に応えられない。
本設計はその欠落を3つのサブプロジェクトで埋める。

- **P2a 前処理・QC**: 正規化・ドリフト補正・ブランク処理・フィルタ・欠損補完が無く、
  欠損補完は列平均のみ。定量的結論の信頼性が担保できない。
- **P2b 差次的解析**: 群がファイル名から導出可能（`docs/task.md` T3）なのに、Fold Change /
  検定 / 多重検定補正 / volcano が無く、解析が PCA 止まり。「どの脂質が群間で変わったか」
  というオミクスの中核問いに答えられない。
- **P2c 同定信頼度・標準化**: MSI レベル・GOSLIN 名正規化・LIPID MAPS/RefMet ID が無く、
  Ontology は MS-DIAL 独自文字列のまま。世界のリピドミクス報告と突合できない。

### 設計思想の継承（最重要）

全ステップは決定論的に実行し、各操作の前提・限界（プールQC不在、バッチ交絡、小n、
注入順欠落、マッピング表のカバレッジ限界）を **出力に必ず caveat として前景化** する。
統計・計算は「事実」、解釈・最終判定は LLM/人間に委ねる既存の分業を維持する
（`knowledge_store.py` 冒頭コメント、`verify_peak_annotation` のハイブリッド判定に整合）。

## 2. アーキテクチャ方針（案A: 既存パターン踏襲）

`knowledge_store.py` / `peak_verification.py` / `paper_ingest.py` と同じく、MCP 非依存で
単体テスト可能な純ロジックモジュールを新設し、`server.py` はオーケストレーションに徹する。

- `preprocessing.py`（P2a）
- `differential.py`（P2b）
- `lipid_identity.py`（P2c）— アダクト/元素表の拡張は `peak_verification.py` を延長

案Bの良い点を1つだけ採用: 適用した前処理・統計の **「レシピ」を session とレポートに記録**
（再現性）。パイプライン全面刷新はしない。

### 2.1 session の小改修（正準行列の共有）

現状 `build_pca_matrix()` はサンプル×特徴量行列を作り直接 PCA に渡す。P2a はこの行列を
変換し、P2b はその変換後行列＋群ラベルを消費する。そこで `AnalysisSession` に以下を保持し、
PCA・前処理・差次的解析が共有する（全面刷新ではない小改修）。

- `feature_matrix`: サンプル×特徴量の正準行列（numpy）
- `sample_names` / `feature_names`
- `sample_meta`: サンプルごとの `role`（sample/qc/blank）・`group`（factor 由来）・
  `run_order`・`batch`（取得日等）
- `preprocessing_recipe`: 適用した前処理ステップの順序付き記録（既定は空=未適用）

後方互換: 既存 `arf_parser` / `run_pca` の既定挙動は不変。前処理は明示呼び出し時のみ適用。
前処理未適用時は `feature_matrix` = 生の `build_pca_matrix()` 出力。

### 2.2 データフロー

```
ARFパース
  → session.feature_matrix + session.sample_meta(role/group/run_order/batch)
      ├─ preprocessing.py:  役割検出 → ブランク処理 → 正規化 → ドリフト補正 → フィルタ → 補完
      │      （各ステップ選択式、適用レシピを session.preprocessing_recipe に記録）
      ├─ run_pca()          （前処理後行列を消費。既存と後方互換）
      └─ differential.py:   群指定 → 特徴量ごと統計 → log2FC/FDR/volcano
  lipid_identity.py（直交）: 注釈名 → GOSLIN正規化 → RefMet/LIPID MAPS ID(同梱表) → MSIレベル
      （verify_peak_annotation と ARF2 注釈に付与）
```

## 3. P2a — `preprocessing.py`（前処理・QC）

サンプル×特徴量行列に対する順序付き・選択式ステップ群。各ステップは純関数。
**「QC・ブランクがある前提」で設計し、無い場合をフォールバックにする**（ユーザー確定方針）。

### 3.1 サンプル役割検出

`detect_sample_roles(sample_names, class_ids, config=None) -> dict[str, str]`

- 役割 = `sample` / `qc` / `blank`。
- `_` 区切りセグメントの `qc` / `blank` トークン（大小無視）を **ファイル名と Class ID の両方**
  で照合。既定辞書 `{"qc": {"qc"}, "blank": {"blank"}}`、`config` で上書き/追加可。
- 実データ命名の実測（`20240314_brain/NEG`）:
  QC=`..._QC_<部位>_ICR_NEG_<n>`、ブランク=`..._blank_<部位>_NEG_1`。
- どのセグメントにも該当しなければ `sample`。

### 3.2 ブランク処理

`blank_filter(matrix, roles, min_fold=3.0) -> (keep_mask, report)`

- 特徴量ごとに「生体試料平均 < `min_fold` × ブランク平均」なら **背景として除去**（keep_mask=False）。
- セル単位差引きはノイズを増やすため採らず、特徴量単位で判定（標準手法）。
- ブランク不在時: スキップし caveat「ブランク試料が無いため背景除去は未実施」。

### 3.3 正規化

`normalize(matrix, method, roles=None) -> (matrix2, factors, report)`

- `method` ∈ `none` / `tic`（サンプル総和で除算）/ `median`（サンプル中央値）/
  `pqn`（Probabilistic Quotient Normalization、参照=QC 中央値、無ければ全サンプル中央値）。
- 内部標準指定のフック（`method="internal_standard"`, `is_feature_ids=[...]`）は
  シグネチャに残すが v1 の既定経路ではない（対象データに IS 試料が無いため）。

### 3.4 ドリフト補正

`qc_drift_correct(matrix, roles, run_order, method="qc_rlsc", min_qc=4) -> (matrix2, report)`

- QC を `run_order` 昇順に並べ、特徴量ごとに頑健スプライン（LOESS 相当）で系統ドリフトを
  推定し、生体試料へ内挿補正（QC-RLSC）。
- **`run_order` は `.mddata` の解析順フィールドから取得**。取得できない、または QC が `min_qc`
  未満なら **本ステップのみ無効化** し caveat「注入順/QC 不足のためドリフト補正は未実施」。
- プールQCが層別（例: 部位別 QC）と検出された場合は caveat「プールQC が層別のため全体一律の
  ドリフト補正は近似である」を付す（v1 は全 QC を一系列として扱う）。

### 3.5 特徴量フィルタ

`filter_features(matrix, roles, config) -> (keep_mask, report)`

- QC RSD フィルタ: QC 群での CV%（SD/mean）> `max_qc_rsd`（既定 0.30）の特徴量を除去。QC 不在時はスキップ＋caveat。
- 検出率フィルタ: 既存 `build_pca_matrix(min_detection_rate=...)` のロジックを再利用。
- ブランク比フィルタ: 3.2 の keep_mask を統合。

### 3.6 欠損補完

`impute(matrix, method) -> (matrix2, report)`

- `method` ∈ `half_min`（特徴量最小値の半分、既定）/ `knn` / `column_mean`（現行互換）/ `none`。
- 現行の列平均のみを置換。MS データでは未検出=低強度が多いため `half_min` を既定とする。
- 注意: MS-DIAL のギャップフィル値（`IsGapFilled`）は元から埋まっているセル。本補完は
  行列生成後に残る欠損（None）に対して行う。両者を report で区別。

### 3.7 オーケストレーションと MCP ツール

`preprocess(matrix, meta, recipe) -> (matrix2, meta2, report, recipe_applied)`

- `recipe` は順序付き config（各ステップの有効/無効とパラメータ）。適用結果を
  `session.preprocessing_recipe` に記録。
- MCP ツール:
  - `arf_list_sample_roles()`: 検出した sample/qc/blank 件数と根拠（トークン）を返す（適用前確認）。
  - `arf_preprocess(normalize="none", blank_min_fold=None, drift_correct=False, max_qc_rsd=None, min_detection_rate=0.0, impute="half_min", ...)`:
    キャッシュ済み ARF セッション行列にレシピを適用、session を更新、各段の除去特徴量数・
    適用レシピ・caveat を返す。以降 `arf_re_pca` / `arf_differential` は前処理後行列を消費。

## 4. P2b — `differential.py`（差次的解析）

群指定は既存の factor トークン / Class ID 機構（`msdial_classes.py`, `_class_factors_by_position`）
を再利用。統計スコープは **2群比較＋一元配置ANOVA**（ユーザー確定=案1）。

### 4.1 純関数

- `two_group_test(matrix, feature_names, group_labels, group_a, group_b, *, log2=True, equal_var=False) -> list[dict]`
  特徴量ごと: `mean_a` / `mean_b` / `log2fc` / `t` / `p`。既定 Welch（`equal_var=False`）。
  小n・分散0・全欠損は理由付きで `p=NaN`。
- `one_way_anova(matrix, feature_names, group_labels) -> list[dict]`: 3群以上の `F` / `p`。
- `bh_fdr(pvalues) -> qvalues`: Benjamini-Hochberg（NaN は除外して補正、位置は保持）。
- `volcano_data(results, q_thr=0.05, log2fc_thr=1.0) -> list[dict]`: volcano 点列と有意フラグ
  （`up` / `down` / `ns`）。
- `check_confounding(group_labels, batch_labels) -> dict`: 群が取得日/バッチと交絡していれば
  強く警告（各群が単一バッチに偏る度合いを判定）。**本モジュールの caveat 前景化の核**。
  実測: `2_lipidome_lcms/NEG` は control/LPS=20220901、ILG/G_uralensis=20220902 で交絡。
- `summarize_differential(results) -> dict`: 有意 up/down 件数、上位特徴量。

### 4.2 caveat（必ず出力に含める）

- 交絡: `check_confounding` の結果（該当時は「処理効果と測定日を分離不可」）。
- 小n: 反復数（3〜4）を明示し検出力の限界を注記。
- 正規化状態: `session.preprocessing_recipe` に正規化が含まれなければ「未正規化データでの
  log2FC は測定量の差を含み得る」と警告。
- ギャップフィル/検出率: いずれかの群で検出率が低い特徴量を注記（P2a フィルタ推奨）。
- log2FC のゼロ回避: 補完値/擬似カウントを用いる旨。

### 4.3 MCP ツール

- `arf_differential(group_factor=None, group_a=None, group_b=None, q_threshold=0.05, log2fc_threshold=1.0, ...)`:
  `group_a`/`group_b` 指定時は2群 Welch、`group_factor` のみ指定時はその factor の全水準で
  一元 ANOVA。前処理後 `session.feature_matrix` で実行。上位特徴量・volcano 用 JSON・
  有意数・4.2 の caveat を返す。群指定は P2a/既存の factor トークン機構に準拠。
- `save_volcano_figure(analysis_id, title=None)`: 最新差次的結果を
  `reports/figures/<analysis_id>_volcano.png` に描画（`save_pca_figure` と同型）。

## 5. P2c — `lipid_identity.py`（同定信頼度・標準化、オフライン）

外部識別子の取得は **オフライン/同梱のみ**（ユーザー確定方針）。

### 5.1 GOSLIN 名正規化

`normalize_lipid_name(name) -> dict`

- `pygoslin`（オフライン、純Python）で脂質ショートハンド名を正規化。
- 返り値: `normalized`（正規化名）/ `level`（構造レベル: species/molecular species/sn-position）/
  `lipid_maps_category` / `parse_ok`（失敗時 False＋理由）。
- エーテル `O-/P-` を正しく解釈するため、既存のプラズマローゲン caveat
  （`knowledge/pe-p-vs-pe-o-annotation.md`, `plasmalogen-oxidation.md`）に直結。

### 5.2 同梱マッピング表

- `reference/refmet_map.tsv`: 正規化名/クラス → RefMet 名。
- `reference/lipidmaps_classes.tsv`: クラス → LIPID MAPS カテゴリ/メインクラス。
- `map_to_reference(normalized_name, category) -> dict`: RefMet 名・LIPID MAPS カテゴリ/
  メインクラスを付与。**キュレート済み部分集合**（一般的な脂質クラスを網羅）。カバレッジ外は
  `matched=False`＋caveat「同梱マッピング表に無いためID未付与」。

### 5.3 MSI レベル推定

`msi_level(feature) -> dict`

- 既存の決定論的信号を組み合わせて推定＋根拠を返す:
  `has_msms`（MS/MS 取得）・質量誤差帯（`peak_verification.mass_error_ppm`）・
  アダクト整合（`adduct_consistency`）・注釈/Ontology の有無。
- 目安: Level 2（MS/MS あり＋精密質量整合の putative）/ Level 3（クラスレベル）/ Level 4（未知）。
  **Level 1（標準品照合）は主張しない**。返り値に「ヒューリスティック」と明記。

### 5.4 `peak_verification.py` 拡張

- アダクト表に追加: `[2M-H]-` / `[M+FA-H]-` / 多価 `[M-2H]2-` 等（多価は電荷数を考慮した m/z 計算）。
- 元素表に追加: D(²H) / F / Br / ¹³C（標識対応）。
- CCS/RT 照合は参照表未同梱のため **v1 対象外**（将来課題として明記）。

### 5.5 統合と MCP ツール

- `verify_peak_annotation` のドシエに `identity_normalization` ブロックを追加
  （GOSLIN 結果・RefMet/LIPID MAPS ID・MSI レベル）。
- `arf2_annotate_identities()`: ARF2 スポットカタログの注釈を一括で正規化・ID/レベル付与し、
  結果を返す（表示は軽量符号化 TSV に準拠）。

## 6. 横断事項

- 依存追加: `pygoslin`（オフライン、純Python）を `requirements.txt` に。
- レポート: `write_report` 推奨セクションに `## 前処理` と `## 差次的解析` を追加。前処理レシピ・
  差次的結果（有意数・上位種）・volcano 図参照を埋め込み可能に。
- MCP ゲートウェイ: `MCP_INSTRUCTIONS` に「差次的解析の前に前処理を検討し、交絡・小n・正規化
  状態を caveat として提示する」旨を追記（既存の GATEWAY/CONFLICTS 節と同じ流儀）。
- テスト: `tests/test_preprocessing.py` / `tests/test_differential.py` / `tests/test_lipid_identity.py`。
  純関数は合成データで検証。役割検出は実データ命名の代表例で検証。GOSLIN/マッピングは代表脂質名で検証。
- 後方互換: 既存ツール・既定挙動・出力を変えない。新機能は明示呼び出し時のみ作用。

## 7. スコープ外（follow-up）

- 二元配置 ANOVA（treatment×time）・時系列トレンド解析（ユーザー確定=案1のため v1 除外）。
- 内部標準ベースの絶対/相対定量（対象データに IS 試料が無い）。
- オンライン ID 解決（RefMet/LIPID MAPS API）— オフライン方針のため除外。
- CCS/RT 参照照合・同位体パターン照合。
- エンリッチメント/パスウェイ解析（フェーズ3）。
- mzTab-M 等の標準 I/O・マルチセッション化（フェーズ1/3）。

## 8. 実装順と依存

1. **P2a**: session 小改修（正準行列・sample_meta）→ `preprocessing.py` → `arf_preprocess`/`arf_list_sample_roles`。
2. **P2b**: `differential.py` → `arf_differential`/`save_volcano_figure`（P2a の前処理後行列に依存）。
3. **P2c**: `lipid_identity.py` ＋ `peak_verification.py` 拡張 → `verify_peak_annotation` 拡張/`arf2_annotate_identities`（直交、いつでも可）。

各サブプロジェクトは独立に単体テスト可能。P2b は P2a の正準行列を前提とするが、前処理未適用
（生行列）でも動作し、その場合は正規化未適用 caveat を出す。
