# USAGE — ms-data-parser MCP ツール一覧(全36ツール)

MS-DIAL 出力(`.arf` / `.arf2` / `.pai2` / `.eic.aef`)を解析し、PCA・差次的解析・
アノテーション検証・文献探索・レポート記録までを行う MCP サーバーのツール群です。
おおまかな標準フロー:

```
list_data_files → load_dataset → arf_list_classes / arf_preprocess
   → arf_pca_preprocessed / arf_differential → save_*_figure
   → record_objective → knowledge_coverage → paper_search → ingest_* → write_report
```

---

## 1. データ探索・ロード(入口)

| ツール | 機能 |
|--------|------|
| `list_data_files` | 指定ディレクトリ内のファイル一覧を取得。`extension`(例 `.arf2`)でフィルタ可。 |
| `load_dataset` | MS-DIAL 出力フォルダの**入口**。arf2 概観→arf 詳細PCAを一括実行。最新バッチ・PeakProperties.arf を自動選択し、以降のツールの既定探索先を更新。 |

## 2. ARF2 解析(データセット全体カタログ)

| ツール | 機能 |
|--------|------|
| `arf2_parser` | `.arf2`(全体カタログ)を解析しメタデータ概観を要約。サンプル別強度は含まないため多変量解析は不可。 |
| `arf2_annotate_identities` | ARF2 スポット注釈を GOSLIN 正規化・RefMet/LIPID MAPS ID・MSI レベルで一括標準化(オフライン、上位 `max_rows` 件)。MSI はクラス上限の保守評価。 |

## 3. ARF 解析(サンプル別強度・PCA・差次的解析)

| ツール | 機能 |
|--------|------|
| `arf_parser` | `.arf` を読み込み PCA を実行。スコアプロット画像＋Loading上位を返す。タグ/Class ID/群色分け・log変換・検出率足切り等の指定に対応。 |
| `arf_list_classes` | 現在の ARF データセットで利用可能な MS-DIAL Class ID 値を一覧。 |
| `arf_list_tags` | ロード済み ARF データセットの MS-DIAL タグを一覧。 |
| `arf_list_sample_roles` | サンプルを sample/qc/blank に分類して返す(前処理適用前の確認)。 |
| `arf_exclude` | PCA 外れサンプルや特定ピークを名前/ID で手動除外・再包含(可逆・非破壊)。 |
| `arf_preprocess` | ロード済み ARF 行列に前処理レシピ(正規化・補完・ブランク/QC RSD 足切り・ドリフト補正)を適用し session を更新。 |
| `arf_pca_preprocessed` | `arf_preprocess` 後の前処理済み行列で PCA を実行(生行列経路とは独立)。 |
| `arf_re_pca` | 強度閾値・アノテーションキーワード(脂質クラス等)でフィルタして PCA を再実行。要 `arf_parser` 先行。 |
| `arf_differential` | 前処理後行列で差次的解析。2群指定=Welch、因子のみ=一元配置 ANOVA。因子トークンのプール群指定・log2変換に対応。 |

## 4. EIC 解析(`.eic.aef`)

| ツール | 機能 |
|--------|------|
| `eicaef_parser` | `.eic.aef` を解析しテキスト要約を返す。 |
| `eicaef_search_by_mz_range` | m/z 範囲でスポットを検索。 |
| `eicaef_search_by_rt_range` | RT 範囲でスポットを検索。 |
| `eicaef_top_peak_tops` | PeakTop 値で上位スポットを返す。 |
| `eicaef_plot_chromatograms` | 指定 `spot_id` のEIC系列を読み、クライアント中立の構造化プロット情報(`lipidmix.eic.v1`)を返す。`file_ids` で最大12試料を選択でき、画像生成・ファイル保存は行わない。 |

### EICの描画フロー

1. `eicaef_search_by_mz_range` または `eicaef_search_by_rt_range` で描画対象の `spot_id` を確認する。
2. `eicaef_plot_chromatograms(spot_id, file_ids=...)` を呼び、`series[].x` / `series[].y`、軸、試料、ピーク範囲を含む構造化プロット情報を取得する。
3. 描画方法はクライアントに任せる。Use-LLLMではPlotlyのインタラクティブな線グラフとして表示し、Claude Desktop等は各UIの描画方式を使用する。
4. 通常の対話描画ではPNGを生成しない。ユーザーがPNGの生成・保存を明示的に希望した場合だけ `save_eic_figure` を実行する。

`normalize` は既定の `"none"` のほか、系列ごとの最大値を1にする `"per_trace_max"` を指定できる。`file_ids` を省略した場合、対象スポットの試料数が12以下のときだけ全系列を返す。

## 5. PAI2 解析(`.pai2`)

| ツール | 機能 |
|--------|------|
| `pai2_parser` | `.pai2` を解析し PCA スコアプロット(PNG)＋要約を同時返却(画像は自動インライン描画)。 |
| `pai2_update_analysis_filter` | `min_intensity` / `min_sn` を更新して PCA を再実行。 |
| `pai2_get_top_metabolites` | 直近 PCA から主成分寄与の上位代謝物リストを返す。 |
| `pai2_inspect_metabolite_details` | 特定代謝物の強度・S/N・MS/MS 相当情報を返す。 |

## 6. アノテーション検証

| ツール | 機能 |
|--------|------|
| `verify_peak_annotation` | 1ピークのアノテーション妥当性を検証するドシエ(精密質量誤差ppm・アダクト整合)を返す。要 `pai2_parser` 先行。 |

## 7. 実験目的の記録(gap駆動探索の前提)

| ツール | 機能 |
|--------|------|
| `record_objective` | 確定した実験目的を `analyses/<analysis_id>.md` に記録。目的・比較・群・小問(Q1..Qn)を保存。 |
| `update_objective` | 目的/文脈/状態を更新し、創発的な小問を追記。 |

## 8. 知識カバレッジ・文献探索・取り込み

| ツール | 機能 |
|--------|------|
| `knowledge_coverage` | objective の各小問 Qi を COVERED / WEAK / GAP に分類(未探索 GAP が自動探索対象)。 |
| `paper_search` | Europe PMC を検索し撤回・重複除外した候補を返す(ユーザー確認済みクエリ前提)。 |
| `log_search` | 探索結果を objective の探索ログに記録(既探索 Qi の再探索防止)。 |
| `ingest_stage` | 関連候補を `knowledge/_inbox` に speculative 隔離(出典必須)。 |
| `ingest_review_queue` | `_inbox` の保留ノートを analysis_id×Qi でグルーピングして返す。 |
| `ingest_promote` | `_inbox` の保留ノートを `knowledge/` へ昇格(信頼知識化の唯一の経路)。 |
| `ingest_reject` | `_inbox` の保留ノートを破棄。 |

## 9. 図の保存・レポート

| ツール | 機能 |
|--------|------|
| `save_pca_figure` | 直近セッションの PCA 結果を PNG 化し `reports/figures/` に保存。 |
| `save_volcano_figure` | 直近の差次的解析(2群)を volcano プロット PNG として保存。 |
| `save_eic_figure` | ユーザーが明示的に保存を希望した場合だけ、直近のEICプロット情報を PNG 化し `reports/figures/<analysis_id>_eic.png` に保存。Use-LLLMではローカル書き込みとして承認が必要。 |
| `write_report` | 解析・解釈レポートを `reports/<analysis_id>.md` に上書き保存。 |
| `read_report` | 過去レポートを読み戻す(最新更新のものを返す。セッション継続用)。 |
| `list_reports` | 既存レポートの1行索引(analysis_id / date / status)を返す。 |
