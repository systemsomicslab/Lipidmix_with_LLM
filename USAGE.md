# USAGE — ms-data-parser MCP ツール一覧(全51ツール)

MS-DIAL 出力(`.arf` / `.arf2` / `.pai2` / `.dcl` / `.EIC.aef`)と mzTab-M を解析し、PCA・差次的解析・
アノテーション検証・文献探索・レポート記録までを行う MCP サーバーのツール群です。
おおまかな標準フロー:

```
list_data_files → load_dataset → arf_list_classes / arf_preprocess
   → arf_pca_preprocessed / arf_differential → save_*_figure
   → record_objective → knowledge_coverage → paper_search → ingest_* → write_report

生データから始める場合(別経路):
console_plan → console_run → dataset_load → dataset_preprocess
   → dataset_pca / dataset_differential → dataset_export_differential
```

この文書は各ツールの**外形**（引数と用途）を扱います。内部でどのファイルのどの関数を
どの順に呼ぶかは [docs/workflow/](docs/workflow/index.md) を、出力フィールドの意味は
[docs/output_format/](docs/output_format/core.md) を参照してください。

---

## 1. データ探索・ロード(入口)

| ツール | 機能 |
|--------|------|
| `list_data_files` | 指定ディレクトリ内のファイル一覧を拡張子別に取得。**既定は解析できる拡張子のみ**(.arf/.arf2/.pai2/.dcl/.EIC.aef/.mddata/.mdproject)。測定生データ(.wiff 等)も見たいときは `all_files=True`。`extension`(例 `.arf2`)で絞り込みも可。 |
| `load_dataset` | MS-DIAL 出力フォルダの**入口**。arf2 概観→arf 詳細PCAを一括実行。最新バッチ・PeakProperties.arf を自動選択し、以降のツールの既定探索先を更新。 |
| `sample_search` | 因子トークン(`ILG_6h` 等)でサンプルを検索し、`file_id` と `.pai2`/`.dcl` の実パスを返す。specs 省略で語彙一覧。ARF ロード前でも動く。 |

## 2. ARF2 解析(データセット全体カタログ)

| ツール | 機能 |
|--------|------|
| `arf2_parser` | `.arf2`(全体カタログ)を解析しメタデータ概観を要約。サンプル別強度は含まないため多変量解析は不可。 |
| `arf2_annotate_identities` | ARF2 スポット注釈を GOSLIN 正規化・RefMet/LIPID MAPS ID・MSI レベルで一括標準化し TSV 表で返す(オフライン)。**ファイル先頭から `max_rows` 件**で強度順ではない(総数と未表示件数はヘッダ行に出る)。MSI はクラス上限の保守評価。 |

## 3. ARF 解析(サンプル別強度・PCA・差次的解析)

| ツール | 機能 |
|--------|------|
| `arf_parser` | `.arf` を読み込み PCA を実行。スコアプロット用JSON＋Loading上位を返す。タグ/Class ID/群色分け・log変換・検出率足切りに加え、強度閾値(`min_intensity`)・アノテーションキーワード(`annotation_keyword`)でのフィルタも吸収。フィルタ条件を変えた再PCAは引数を変えて本ツールを再呼び出しする(再パースは走らない)。 |
| `arf_list_classes` | 現在の ARF データセットで利用可能な MS-DIAL Class ID 値を一覧。 |
| `arf_list_tags` | ロード済み ARF データセットの MS-DIAL タグを一覧。 |
| `arf_list_sample_roles` | サンプルを sample/qc/blank に分類し TSV 表で返す(前処理適用前の確認)。列= sample/role/group/batch/run_order/excluded。 |
| `arf_exclude` | PCA 外れサンプルや特定ピークを名前/ID で手動除外・再包含(可逆・非破壊)。 |
| `arf_preprocess` | ロード済み ARF 行列に前処理レシピ(正規化・補完・ブランク/QC RSD 足切り・ドリフト補正)を適用し session を更新。 |
| `arf_pca_preprocessed` | `arf_preprocess` 後の前処理済み行列で PCA を実行(生行列経路とは独立)。 |
| `arf_differential` | 前処理後行列で差次的解析(2群 Welch t 検定＋log2FC、BH 補正)。因子トークンによるプール群指定に対応。多群 ANOVA は MCP から非公開(関心の2群を因子指定で切り出す)。 |
| `arf_export_differential` | 直近の差次的結果を InChIKey 付きの 1 ファイルへ書き出す(`output_path`)。同一アラインメントの兄弟 `.arf2` から同定情報を `MasterAlignmentID` で結合する。濃縮解析の背景を保つため、有意行だけでなく **InChIKey が付いた全行**を出す。列定義は `lipidmix/analysis/export_contract.py` が正準(下流リポジトリとの契約)。 |

## 4. EIC 解析(`.EIC.aef`)

| ツール | 機能 |
|--------|------|
| `eic_parser` | `.EIC.aef` を解析しテキスト要約を返す。 |
| `eic_search_by_mz_range` | m/z 範囲でスポットを検索。既定上限は **1500**(TG/DGDG/CL など m/z 1000 超の脂質を既定で切り落とさないため)。 |
| `eic_search_by_rt_range` | RT 範囲でスポットを検索。 |
| `eic_rank_by_max_intensity` | 各試料のクロマトグラム最大強度の最大値で降順に並べた強度上位ランキングを返す。 |
| `eic_plot_chromatograms` | 指定 `spot_id` のEIC系列を読み、クライアント中立の構造化プロット情報(`lipidmix.eic.v1`)を返す。`file_ids` で最大12試料を選択でき、画像生成・ファイル保存は行わない。 |
| `eic_plot_compounds` | 脂質名/オントロジーで選んだ複数物質のEICを、指定 `file_id` の**1試料分だけ**同一グラフへ重ねる。**既定はPNG画像＋1行キャプション**(`output="image"`)。点列(`lipidmix.eic.multi.v1`)が要るクライアントは `output="payload"` または env `LIPIDMIX_PLOT_OUTPUT=payload`。重ねる本数の既定は **8本**。ARF2の同定をrt/mzで検証し、除外した物質は `selection.dropped` に理由付きで残す。ファイル保存はしない。 |

### EICの描画フロー

1. `eic_search_by_mz_range` または `eic_search_by_rt_range` で描画対象の `spot_id` を確認する。
2. `eic_plot_chromatograms(spot_id, file_ids=...)` を呼び、`series[].x` / `series[].y`、軸、試料、ピーク範囲を含む構造化プロット情報を取得する(座標は4桁に丸め済み)。
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
| `arf_plot_volcano` | 直近の2群差次的解析を volcano 図として返す。**既定はPNG画像＋件数入りキャプション**(`output="image"`、全特徴を描画)。点列(`lipidmix.volcano.v1`)が要るクライアントは `output="payload"` または env `LIPIDMIX_PLOT_OUTPUT=payload` —— その場合 `up`/`down` は全件、`ns` は `max_points`(既定800)まで等間隔で間引き、件数は `selection` に出る。ファイル保存はしない。 |
| `save_pca_figure` | ユーザーがPNGを明示的に希望した場合だけ、直近セッションの PCA 結果を `reports/figures/` に保存。通常の描画はクライアントUIに任せる。 |
| `save_volcano_figure` | ユーザーが**ファイルとしての**PNGを希望した場合だけ、直近の差次的解析(2群)を volcano PNG として保存(レポート埋め込み用)。画面で見るだけなら `arf_plot_volcano` が画像を直接返す。描画関数は共通なので同じ図。 |
| `save_eic_figure` | ユーザーが明示的に保存を希望した場合だけ、直近のEICプロット情報を PNG 化し `reports/figures/<analysis_id>_eic.png` に保存。Use-LLLMではローカル書き込みとして承認が必要。 |
| `write_report` | 解析・解釈レポートを `reports/<analysis_id>.md` に上書き保存。 |
| `read_report` | 過去レポートを読み戻す(最新更新のものを返す。セッション継続用)。 |
| `list_reports` | 既存レポートの1行索引(analysis_id / date / status)を返す。 |

## 10. MS-DIAL Console 実行(生データ → mzTab-M)

MS-DIAL 本体を CLI 実行して解析結果そのものを生成する経路。GUI であらかじめ
メソッドファイルを作っておき、以降をこのサーバから回す。成果物は
`analysis-job.json`(受け渡しスキーマ)と mzTab-M で、`dataset_load` が続きを引き取る。

| ツール | 機能 |
|--------|------|
| `console_plan` | 実行計画を作り `analysis-job.json` を生成(`dataset_root`, `method_file`, `polarity`, `measure`, `omics`)。`dataset_root` は生データフォルダ(リポジトリ外)、`method_file` は `.msdial`/`.mdproject`。`measure` は `peak_height`(既定)のみ正確で、`peak_area_above_zero` は `UNSUPPORTED_AREA_CONSOLE` で停止する。成功すると `session.current_job_path` が設定される。 |
| `console_run` | MS-DIAL Console を実行(`job_path` 省略時は `session.current_job_path`)。要 `console_plan` 先行。結果は `console_status` か `dataset_load` で確認する。 |
| `console_status` | ジョブの現在のステータスを返す(`job_path` 省略時は `session.current_job_path`)。 |
| `job_list` | `dataset_root/runs/` 以下のジョブ一覧を新しい順に返す。 |

## 11. DatasetState 解析(mzTab-M 経路)

mzTab-M 2.0 を正準状態(`DatasetState`)として読み、ARF 経路と同じ前処理・PCA・
差次的解析を回す。**`session.arf` / `.arf2` / `.pai2` / `.eic` とは独立したスロット**なので、
ARF 経路の状態を壊さない。

| ツール | 機能 |
|--------|------|
| `dataset_load` | mzTab-M 2.0 を読み `session.dataset` を作る。`mztab_path`(絶対パス直指定)か `job_path`(ジョブの `primary_mztab_files[0]` を自動選択)の**どちらか一方**を渡す(両方省略・両方指定はエラー)。`console_run` 後は `job_path` 推奨(polarity・measure が確定済み)。 |
| `dataset_status` | 現在の `DatasetState` の概要を返す。`samples` に name/role の TSV が入り、`dataset_differential` の `group_a`/`group_b` はここに出る名前をそのまま使う。 |
| `dataset_preprocess` | 定量行列に前処理レシピを適用(引数は `arf_preprocess` と同一: `normalize` / `blank_min_fold` / `drift_correct` / `max_qc_rsd` / `impute`)。**`drift_correct` は現状 mzTab-M から注入順を読めないため常に未実施**になり caveat で報告される。注入順が要るなら ARF 経路を使う。 |
| `dataset_pca` | 前処理済み `DatasetState` で PCA(`n_components` 既定 5、`log_transform` 既定 False)。ローディング全量は戻り値に載せず `session.dataset.last_pca` に保持する。 |
| `dataset_differential` | 前処理済み行列で 2 群比較(Welch t 検定＋BH-FDR)。`group_a`/`group_b` は**サンプル名のリスト**(`dataset_status` の `samples` で確認)。**log2FC は正なら `group_b` が高い**(`group_a` が基準)。全特徴量の結果と volcano 点列は `session.dataset.last_differential` に保持する。 |
| `dataset_export_differential` | 直近の差次的結果を InChIKey 付きの 1 ファイルへ書き出す。**`arf_export_differential` と同一の契約**(15 列 + `contract_version` メタ行)なので下流のパスウェイ解析にそのまま渡せる。InChIKey は mzTab-M 由来(`.arf2` との結合は不要)。`ontology` と `msi_level` は mzTab-M に対応物が無く空欄で、その旨をメタ行に書く。 |

## 12. Class ID に無い因子で絞る・比べる

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
