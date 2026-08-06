# USAGE — ms-data-parser MCP ツール一覧(全39ツール)

MS-DIAL 出力(`.arf` / `.arf2` / `.pai2` / `.EIC.aef`)を解析し、PCA・差次的解析・
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
| `sample_search` | 因子トークン(`ILG_6h` 等)でサンプルを検索し、`file_id` と `.pai2`/`.dcl` の実パスを返す。specs 省略で語彙一覧。ARF ロード前でも動く。 |

## 2. ARF2 解析(データセット全体カタログ)

| ツール | 機能 |
|--------|------|
| `arf2_parser` | `.arf2`(全体カタログ)を解析しメタデータ概観を要約。サンプル別強度は含まないため多変量解析は不可。 |
| `arf2_annotate_identities` | ARF2 スポット注釈を GOSLIN 正規化・RefMet/LIPID MAPS ID・MSI レベルで一括標準化(オフライン、上位 `max_rows` 件)。MSI はクラス上限の保守評価。 |

## 3. ARF 解析(サンプル別強度・PCA・差次的解析)

| ツール | 機能 |
|--------|------|
| `arf_parser` | `.arf` を読み込み PCA を実行。スコアプロット用JSON＋Loading上位を返す。タグ/Class ID/群色分け・log変換・検出率足切りに加え、強度閾値(`min_intensity`)・アノテーションキーワード(`annotation_keyword`)でのフィルタも吸収。フィルタ条件を変えた再PCAは引数を変えて本ツールを再呼び出しする(再パースは走らない)。 |
| `arf_list_classes` | 現在の ARF データセットで利用可能な MS-DIAL Class ID 値を一覧。 |
| `arf_list_tags` | ロード済み ARF データセットの MS-DIAL タグを一覧。 |
| `arf_list_sample_roles` | サンプルを sample/qc/blank に分類して返す(前処理適用前の確認)。 |
| `arf_exclude` | PCA 外れサンプルや特定ピークを名前/ID で手動除外・再包含(可逆・非破壊)。 |
| `arf_preprocess` | ロード済み ARF 行列に前処理レシピ(正規化・補完・ブランク/QC RSD 足切り・ドリフト補正)を適用し session を更新。 |
| `arf_pca_preprocessed` | `arf_preprocess` 後の前処理済み行列で PCA を実行(生行列経路とは独立)。 |
| `arf_differential` | 前処理後行列で差次的解析(2群 Welch t 検定＋log2FC、BH 補正)。因子トークンによるプール群指定に対応。多群 ANOVA は MCP から非公開(関心の2群を因子指定で切り出す)。 |

## 4. EIC 解析(`.EIC.aef`)

| ツール | 機能 |
|--------|------|
| `eic_parser` | `.EIC.aef` を解析しテキスト要約を返す。 |
| `eic_search_by_mz_range` | m/z 範囲でスポットを検索。 |
| `eic_search_by_rt_range` | RT 範囲でスポットを検索。 |
| `eic_rank_by_max_intensity` | 各試料のクロマトグラム最大強度の最大値で降順に並べた強度上位ランキングを返す。 |
| `eic_plot_chromatograms` | 指定 `spot_id` のEIC系列を読み、クライアント中立の構造化プロット情報(`lipidmix.eic.v1`)を返す。`file_ids` で最大12試料を選択でき、画像生成・ファイル保存は行わない。 |
| `eic_plot_compounds` | 脂質名/オントロジーで選んだ複数物質のEICを、指定 `file_id` の**1試料分だけ**同一グラフへ重ねる構造化プロット情報(`lipidmix.eic.multi.v1`)を返す。ARF2の同定をrt/mzで検証し、除外した物質は `selection.dropped` に理由付きで残す。画像生成・ファイル保存は行わない。 |

### EICの描画フロー

1. `eic_search_by_mz_range` または `eic_search_by_rt_range` で描画対象の `spot_id` を確認する。
2. `eic_plot_chromatograms(spot_id, file_ids=...)` を呼び、`series[].x` / `series[].y`、軸、試料、ピーク範囲を含む構造化プロット情報を取得する。
3. 描画方法はクライアントに任せる。Use-LLLMではPlotlyのインタラクティブな線グラフとして表示し、Claude Desktop等は各UIの描画方式を使用する。
4. 通常の対話描画ではPNGを生成しない。ユーザーがPNGの生成・保存を明示的に希望した場合だけ `save_eic_figure` を実行する。

複数物質を1枚に重ねる場合は `eic_plot_compounds(file_id, names=[...], ontologies=[...])` を使う。
1呼び出し1試料なので、試料間で比べるときは試料ごとに呼び出して図を並べる。図に現れない物質は
`selection.dropped` の理由（`rt_mismatch` / `file_id_absent` / `below_top_n` など）を確認する。

`normalize` は既定の `"none"` のほか、系列ごとの最大値を1にする `"per_trace_max"` を指定できる。`file_ids` を省略した場合、対象スポットの試料数が12以下のときだけ全系列を返す。

## 5. PAI2 解析(`.pai2`)

| ツール | 機能 |
|--------|------|
| `pai2_parser` | `.pai2`(単一測定ファイル)を解析し、ピーク在庫要約(注釈状況・m/z・RT・強度・S/N の分布と強度上位ピーク)をテキストで返す。単一サンプルのためオミクスPCAは行わない(多変量比較は ARF/ARF2)。 |
| `pai2_inspect_peak` | `peak_id` または `peak_name` で特定ピークの強度・S/N・MS/MS 相当フィールドの有無を返す。 |

## 6. アノテーション検証

| ツール | 機能 |
|--------|------|
| `verify_peak_annotation` | 1ピークのアノテーション妥当性を検証するドシエ(精密質量誤差ppm・アダクト整合・MS/MS 証拠)を返す。要 `pai2_parser` 先行。`analytical_checks.msms.band` は `PASS`(実スペクトル)/`FLAG_ONLY`(取得フラグのみ)/`ABSENT` の3値。 |
| `dcl_parser` | `.dcl`(MSDecResult＝デコンボリューション済み MS/MS)を解析し在庫要約と先頭数件の主要フラグメントを返す。 |
| `dcl_find_msms` | precursor m/z(任意で RT)に一致する MS/MS を引く。アノテーションの裏取り用。該当ゼロは「MS/MS 未取得」であって「フラグメント不在」ではない。 |

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

## 10. Class ID に無い因子で絞る・比べる

MS-DIAL の Class ID は入力された1文字列にすぎず、時点や複製がサンプル名にしか
無いことがある(例: Class ID = `ILG`/`control` だけで、時点 `6h` はサンプル名のみ)。
`class_ids` / `group_a` / `group_b` / `group_levels` / `group_factors` のトークンは
Class ID とサンプル名の**両方**から解決されるため、そのまま書ける。

```python
sample_search()                                    # 何で絞れるかの語彙一覧
arf_parser(class_ids=["ILG_6h", "control_6h"])     # 6h だけで PCA
arf_parser(group_factors=[["control", "LPS", "ILG", "G_uralensis"],
                          ["0h", "15min", "1h", "6h", "24h"]])  # 処置x時点の20群で色分け
arf_preprocess(normalize="median")
arf_differential(group_a="ILG_6h", group_b="control_6h")        # 時点を揃えた2群比較
```

QC/blank の扱いはツールごとに異なる。`arf_parser` の `class_ids` フィルタでは既定で除外され
(`include_roles=["sample","qc"]` で戻せる)。`sample_search` は既定で全 role を返す
(role を絞りたいときだけ `include_roles` を渡す)。`arf_differential` は QC/blank を常に
比較対象から除外し、オーバーライドはできない。PCA の色分けは `group_levels`/`group_factors`
で軸を指定した場合に `"qc"`/`"blank"` ラベルとして残る(前処理品質の判断材料になるため。
軸を指定しない場合は生の Class ID でラベルされる)。
