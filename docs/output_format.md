# パーサー出力フォーマットとオントロジー

## 1. 目的と対象

この文書は、LLM が本リポジトリのパーサー出力を解析するときに、各行・列・JSON キーが何を表すかを誤解しないための参照資料である。README に記載された主要な MS-DIAL 出力パーサーを対象とする。

| パーサー | 入力 | 主な実装 |
|---|---|---|
| ARF | `*_PeakProperties.arf` | `arf_reader.py` |
| ARF2 | `*.arf2` | `arf2_reader.py` |
| PAI2 | `*.pai2` | `pai2_reader.py` |
| DCL | `*.dcl` | `dcl_reader.py` |
| EIC/AEF | `*.EIC.aef` | `eic_aef_reader.py` |

記載内容は、実装、`docs/AlignmentSpotProperty.md`、`docs/AlignmentChromPeakFeature.md`、`docs/ChromatogramPeakFeature.md`、および次の実ファイルに対する出力確認に基づく。

```text
C:\Users\yuu18\datasets\2_lipidome_lcms\NEG
```

代表テストファイル:

- `AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf`
- `AlignmentResult_2026_05_15_10_13_35.arf2`
- `AlignmentResult_2026_05_15_10_13_35.EIC.aef`
- `20220901_RAW_control_0h_1_NEG_202605151012.pai2`
- `20220901_RAW_control_0h_1_NEG_202605151012.dcl`

## 2. 共通オントロジー

本リポジトリでは、同じ LC-MS データを異なる粒度で表現する。

```text
データセット
  ├─ アラインメントスポット（全サンプルをまたぐ同一候補ピーク）
  │    ├─ ARF2: スポットの代表値、同定情報、品質統計
  │    ├─ ARF: サンプル別ピークプロパティ
  │    └─ EIC/AEF: サンプル別クロマトグラム点列
  └─ 個別測定ファイル（1サンプル）
       ├─ PAI2: そのサンプルで検出されたピーク
       └─ DCL: PAI2 ピークに対応するデコンボリューション済み MS/MS
```

主要エンティティと関係:

| エンティティ | 意味 | 主な識別子・対応 |
|---|---|---|
| Alignment spot | 複数サンプル間で同一とみなされたピーク集合 | ARF/ARF2 の `MasterAlignmentID`、`AlignmentID`、EIC の `spot_id`。ID はすべて 0 始まり |
| Aligned sample peak | 1スポットに属する1サンプルのピーク | ARF の1行。`MasterAlignmentID` と `FileID`/`SampleIndex` の組で特定 |
| Raw-file peak feature | 1測定ファイル中の検出ピーク | PAI2 の1要素。`id` は当該 PAI2 内のピークID |
| MSDec result | デコンボリューション済み MS/MS | DCL の1要素。`dcl_index` と PAI2 のリスト順序が対応する設計 |
| EIC sample trace | 1スポット・1サンプルの抽出イオンクロマトグラム | EIC の `spot_id` と `samples[].file_id` の組で特定 |
| Annotation | 候補化合物名 | `Name`/`name`。空文字、`Unknown`、`no MS2:`、`low score:`を含み得るため、存在するだけで確定同定を意味しない |
| Ontology | 化学・脂質クラス | `Ontology`/`ontology`。例: `FA`, `PC`, `PE`, `TG`, `Cer_NS`。化合物名より上位の分類概念 |

共通の値:

| 値 | 意味 |
|---|---|
| RT | retention time、保持時間。通常は分（min） |
| RI | retention index。未使用時は 0 または欠落 |
| m/z | 質量電荷比。単位なし |
| Drift / dt | イオンモビリティのドリフト時間。未使用時は `-1` または欠落 |
| Height | ピーク頂点強度 |
| Area | ピーク面積。`AboveBaseline` はベースラインより上だけを積分した値 |
| S/N | signal-to-noise ratio、信号対雑音比 |
| IonMode | イオン化極性。パーサーにより文字列、Enum、整数のいずれか |

## 3. ARF (`arf_reader.py`)

### 3.1 `deserialize()` のスポット出力

型は `list[dict]`。**1要素は1アラインメントスポット**であり、全サンプルのピークを `AlignedPeakProperties` に保持する。

| キー | 型 | 意味 |
|---|---|---|
| `MasterAlignmentID` | int | パーサーがスポット順に付与する 0 始まりのマスターID |
| `AlignmentID` | int | 現実装では `MasterAlignmentID` と同じ連番 |
| `RT` | float/null | 代表サンプルから取得したスポット代表RT（min） |
| `MassCenter` | float/null | 代表サンプルから取得したスポット代表 m/z |
| `IonMode` | str | `Positive`、`Negative`、`Both`、`Unknown` のいずれか |
| `Name` | str/null | 代表アノテーション名。空文字の場合は未注釈 |
| `HeightAverage` | float/null | 現実装ではグループ先頭サンプルの `height` を格納する。名前に反して全サンプル平均を再計算していない |
| `AlignedPeakProperties` | list[list] | そのスポットに属する全サンプルの生 MessagePack 配列。LLM 解析では通常、次節の表形式を使う |
| `TagIds` / `Tags` | list | アラインメント結果の `*_tags.xml` から `MasterAlignmentID` で結合したタグ |
| `SamplePeakTags` | dict | サンプル別 `*_tags.xml` から `FileName` と `MasterPeakID` で結合した、タグ付きピークのみの辞書 |
| `SampleClasses` | dict | `.mddata` の `AnalysisFileBean` と `FileID`/`FileName` で結合したサンプルClass IDメタデータ |

### 3.2 `extract_peak_properties()` の表/CSV

型は `pandas.DataFrame`。**1行は「1アラインメントスポット × 1サンプル」の1ピーク**である。同じ `MasterAlignmentID` がサンプル数だけ繰り返される。

| 列 | 型 | 意味 |
|---|---|---|
| `MasterAlignmentID` | int | 行が属するアラインメントスポットID |
| `AlignmentID` | int | 現実装では `MasterAlignmentID` と同じ値 |
| `SpotRT` | float/null | アラインメントスポット全体の代表RT（min） |
| `SpotMassCenter` | float/null | アラインメントスポット全体の代表 m/z |
| `IonMode` | str | スポットのイオンモード |
| `CompoundName` | str/null | スポットの候補化合物名。空文字は未注釈 |
| `AlignmentTags` | list[str] | アラインメントスポットに付与されたMS-DIALタグ |
| `SampleIndex` | int | `AlignedPeakProperties` 内での 0 始まり位置。サンプル順序を示す |
| `FileName` | str | 測定ファイル名。取得できない場合は `Sample_<SampleIndex>` |
| `ClassID` | str/null | MS-DIALのFile property settingで指定した `AnalysisFileClass` |
| `PeakID` | int/null | 測定ファイル内のピークID。ギャップフィルでは負値になり得る |
| `FileID` | int/null | データセット内の測定ファイルID |
| `MasterPeakID` | int/null | 元ピークのマスターID。負値は未検出を補間したギャップフィルを示す |
| `PeakHeight` | float/null | 当該サンプルのピーク頂点強度 |
| `PeakArea` | float/null | 当該サンプルのピーク面積 |
| `PeakAreaAboveBaseline` | float/null | ベースラインより上のピーク面積 |
| `PeakMZ` | float/null | 当該サンプルで観測されたピーク m/z |
| `PeakRT` | float/null | 当該サンプルで観測されたピークRT（min） |
| `SignalToNoise` | float/null | `PeakShape[1]` 由来の S/N |
| `IsMsms` | bool | MS2 raw spectrum ID-to-collision-energy map が非空か。MS/MS取得情報があることを示すが、スペクトル本体は含まない |
| `IsGapFilled` | bool | `MasterPeakID < 0` か。`true` は実検出ではなくギャップフィルされた値 |
| `PeakTags` | list[str] | 当該サンプルピークの `MasterPeakID` に付与されたMS-DIALタグ |

### 3.3 `build_pca_matrix()` の行列

返り値は `(matrix, sample_names, feature_names)`。

| 出力 | 行・列の意味 |
|---|---|
| `matrix` | 2次元 `numpy.ndarray`。**行=サンプル、列=スポット×選択プロパティ** |
| `sample_names` | 行ラベル。通常は `FileName` |
| `feature_names` | 列ラベル。`Spot_<MasterAlignmentID>_<property>` 形式。例: `Spot_0_height` |

`property` は `_convert_to_alignment_feature()` の `height`, `area`, `area_above_baseline`, `m_z`, `rt`, `signal_to_noise` などを指定できる。指定値が `None` のセルはまず欠損となり列平均で補完され、全欠損なら 0 となる。分散 0 の列は除外される。`min_detection_rate > 0` の場合は、非ギャップフィルサンプル率が閾値未満の列も除外される。

### 3.4 `run_pca()` と Loading 出力

PCA 前に各列を `StandardScaler` で標準化する。`log_transform=true` の場合は値を 1 以上にクリップして `log10` 変換してから標準化する。

| JSONキー | 形状 | 意味 |
|---|---|---|
| `components` | `[sample][PC]` | 各サンプルの主成分スコア。行順は `sample_names` と同じ |
| `explained_variance_ratio` | `[PC]` | 各主成分が説明する分散の比率。0..1 |
| `singular_values` | `[PC]` | 各主成分に対応する特異値 |
| `loadings` | `[PC][feature]` | 各主成分に対する各入力列の係数。列順は `feature_names` と同じ |

主成分の符号は数学的に反転可能なので、正負そのものよりサンプルと特徴量の相対関係を解釈する。

`get_pca_loading_features()` の各要素:

| キー | 意味 |
|---|---|
| `pc` | `PC1` などの主成分名 |
| `var_ratio` | 説明分散比を百分率にした値 |
| `positive` | Loading 値が大きい側の上位特徴量リスト |
| `negative` | Loading 値が小さい側の上位特徴量リスト |
| `positive/negative[].id` | `MasterAlignmentID` |
| `positive/negative[].value` | Loading 係数 |
| `positive/negative[].annotation` | スポットの `Name` |
| `positive/negative[].m_z` | スポット代表 m/z |
| `positive/negative[].rt` | スポット代表RT（min） |

### 3.5 ARF要約

`summarize_arf_data()` は次の辞書を返す。

| キー | 意味 |
|---|---|
| `total_peaks` | スポット数。名前は peaks だがサンプル別行数ではない |
| `rt_range` | スポット代表RTの `(min, max)` |
| `mass_range` | スポット代表 m/z の `(min, max)` |
| `height_average_mean` | `HeightAverage` の算術平均 |
| `height_average_max` | `HeightAverage` の最大値 |
| `ion_modes` | イオンモード別スポット数 |
| `named_compounds` | `Name` が null でない件数。**空文字も数えるため、真の注釈済み件数とは限らない** |

### 3.6 MS-DIALタグ

`msdial_tags.py` はARFと同じディレクトリの `*_tags.xml`（互換用に拡張子なしの `*_tags` も可）を読む。サンプル別ファイルは処理時刻の12桁接尾辞を除いた名前でARFの `FileName` と対応させ、XMLの `Peak/@Id` をARF行の `MasterPeakID` と結合する。アラインメント結果用ファイルは `Peak/@Id` を `MasterAlignmentID` と結合する。

タグ条件は `any`、`all`、`none`、`not_all` を使用できる。`sample_peak` スコープでは条件に一致しないサンプル別行をスポット内から除外し、`alignment_spot` スコープではスポット全体を除外する。タグ未付与ピークは `any`/`all` には一致せず、`none`/`not_all` には一致する。

サンプル名は完全一致を優先し、MS-DIAL処理時刻の12桁接尾辞を除く補助照合は一意に決まる場合だけ使用する。重複・曖昧照合はエラーとなる。タグファイル未対応サンプルは既定でエラーにし、`missing_sample_policy=exclude` で除外、`untagged` で明示的にタグなし扱いへ変更できる。サンプル数は固定せず、ARFから検出した件数を使用する。

### 3.7 Class ID

`msdial_classes.py` は `.mddata` の `MsdialDataStorageBase.Key0 AnalysisFiles` を読み、各 `AnalysisFileBean` の `AnalysisFileId`、`AnalysisFileName`、`AnalysisFileClass` を抽出する。`.mddata` は明示パス、`.mdproject` 内の参照、またはARFと同じディレクトリから解決する。

ARFサンプルとの結合は `FileID` を優先し、欠損時は正規化した `FileName` を使用する。両方が異なるサンプルへ解決された場合はエラーとする。`filter_arf_by_class_ids()` は選択Class ID以外のサンプル行を各スポットから除外し、その結果を `build_pca_matrix()` に渡すことでPCAの行をClass IDで選別できる。

## 4. ARF2 (`arf2_reader.py`)

### 4.1 `deserialize()` / `extract_arf2_data()`

型は `list[dict]`。**1行/1要素は1アラインメントスポットのカタログ情報**で、サンプル別強度は含まない。

| 列/キー | 型 | 意味 |
|---|---|---|
| `MasterAlignmentID` | int | MS-DIAL のマスターアラインメントID |
| `AlignmentID` | int | アラインメントID |
| `RT` | float | スポット中心RT（min） |
| `MassCenter` | float | スポット中心 m/z |
| `IonMode` | str | `Positive`、`Negative`、`Both`、`Unknown` |
| `Name` | str | 候補化合物名。欠落時は `Unknown`。`no MS2:` や `low score:` は注釈の確度に関する接頭辞 |
| `HeightAverage` | float | アラインメントスポットのサンプル間平均ピーク高さ |
| `Formula` | str | 分子式。例: `C5H10O2` |
| `Ontology` | str | 化学/脂質クラス。例: `FA`, `PC`, `Cer_NS` |
| `SMILES` | str | 分子構造の SMILES 表現 |
| `InChIKey` | str | 構造同定子 InChIKey |
| `AdductType` | str | 観測イオンの付加体。例: `[M-H]-` |
| `HeightMin` | float | サンプル間ピーク高さの最小値 |
| `HeightMax` | float | サンプル間ピーク高さの最大値 |
| `PeakWidthAverage` | float | サンプル間の平均ピーク幅。LC の場合は通常 min |
| `SignalToNoiseAve` | float | サンプル間 S/N の平均 |
| `SignalToNoiseMax` | float | サンプル間 S/N の最大値 |
| `SignalToNoiseMin` | float | サンプル間 S/N の最小値 |
| `MassMin` | float | サンプル間観測 m/z の最小値 |
| `MassMax` | float | サンプル間観測 m/z の最大値 |
| `FillPercentage` | float | 当該スポットが値を持つサンプルの割合。**0..1 の比率**で、百分率表示には100倍する |
| `MonoIsotopicPercentage` | float | 単同位体ピークとして扱われる割合。**0..1 の比率** |

### 4.2 `summarize_arf2_data()`

| キー | 意味 |
|---|---|
| `total_spots` | スポット総数 |
| `height_average_median` | 0より大きい `HeightAverage` の中央値 |
| `height_average_max` | 0より大きい `HeightAverage` の最大値 |
| `rt_range` | RT の `(min, max)` |
| `mass_range` | m/z の `(min, max)` |
| `ion_modes` | イオンモード別件数 |
| `annotated_count` | `Name` が空、null、`Unknown` ではない件数。注釈確度は考慮しない |
| `annotation_rate` | `annotated_count / total_spots * 100` |
| `ontology_top` | 空でない `Ontology` の件数上位10クラス。文字列 `Unknown` も集計対象 |
| `sn_median` | 0より大きい `SignalToNoiseAve` の中央値 |
| `error` | データが空のときのエラーメッセージ |

### 4.3 `format_spots_as_table()`

1行目が列名、2行目以降が1スポットの TSV/CSV 文字列。既定列順は上記22列の順である。浮動小数は最大4桁程度に丸め、区切り文字・改行・引用符を含むセルは二重引用符で囲む。空値は空セルになる。この丸め済み表はLLM受け渡し用であり、精密な再計算には元の辞書値を使う。

## 5. PAI2 (`pai2_reader.py`)

### 5.1 `deserialize()` のピーク出力

型は `list[dict]`。**1要素は1測定ファイル内の1検出ピーク**である。

| キー | 型 | 意味 |
|---|---|---|
| `time` | dict | ピーク頂点の座標。通常 `{"rt": value}` |
| `time_left` | dict | ピーク左端の座標 |
| `time_right` | dict | ピーク右端の座標 |
| `peak_height` | float | ピーク頂点強度 |
| `peak_height_left` | float | 左端の強度 |
| `peak_height_right` | float | 右端の強度 |
| `peak_area` | float | ピーク面積 |
| `peak_area_above_baseline` | float | ベースラインより上のピーク面積 |
| `m/z` | float | ピーク m/z |
| `S/N` | float | `PeakShape[1]` 由来の S/N |
| `id` | int | 当該 PAI2 ファイル内のピークID |
| `ion_mode` | `IonMode` Enum | `IonMode.Positive`（値0）、`IonMode.Negative`（値1）、`IonMode.Both`（値2）。JSON化時は文字列/数値への変換が必要 |
| `name` | str | 候補化合物名。空文字は未注釈 |
| `formula` | str | 分子式 |
| `ontology` | str | 化学/脂質クラス |
| `smiles` | str | SMILES |
| `inchikey` | str | InChIKey |
| `adduct` | str | 付加体。例: `[M-H]-` |
| `collision_cross_section` | float | CCS。イオンモビリティ未使用時は 0 のことがある |
| `comment` | str | MS-DIAL由来の注釈コメント |
| `has_msms` | bool | MS2 raw ID が非負、または collision-energy map が非空か。MS/MS取得情報の有無 |
| `ms2_raw_id` | int | 元データの MS2 raw spectrum ID。未設定は `-1` |
| `collision_energies` | list[float] | MS/MS取得時の衝突エネルギー一覧。負イオンモードでは `-42.0` など負値もある |
| `msms_peak_count` | int | PAI2 内 `msms_spectrum` の要素数。通常はスペクトル本体がDCL側にあるため0 |
| `msms_spectrum` | list[list] | PAI2自体に格納された `[fragment_mz, intensity]`。通常は空で、本体はDCL側にある |

`time` 系辞書には、入力に存在する非負値だけが入る。

| サブキー | 意味 |
|---|---|
| `rt` | retention time |
| `ri` | retention index |
| `m/z` | 座標表現内の m/z |
| `dt` | drift time |

### 5.2 フィルタ

`filter_features_by_params()` は元と同じピーク辞書を保持したリストを返す。

| パラメータ | 意味 |
|---|---|
| `min_intensity` / `min_height` | `peak_height` の下限 |
| `min_sn` | `S/N` の下限。S/Nが取得できないピークも除外 |

### 5.3 `perform_pca_summary()`

重要: **このPCAの行はサンプルではなくピーク**である。各ピークを `[RT, m/z, Height]` の3変数で標準化し、ピーク群の分布を2成分に射影する。サンプル間のオミクスPCAではない。

返り値は `(summary, img_bytes, pca_result, pca_index, filtered_features)`。

| 出力 | 意味 |
|---|---|
| `summary` | 下表の解析要約辞書 |
| `img_bytes` | PCA散布図のPNGバイト列。点=ピーク、色=`m/z` |
| `pca_result` | `[peak][PC1, PC2]` のスコア行列 |
| `pca_index` | DataFrameの行インデックス。`filtered_features` と同順 |
| `filtered_features` | フィルタ通過ピーク |

`summary` の全キー:

| キー | 意味 |
|---|---|
| `status` | `success` または `error` |
| `message` | エラー時の理由 |
| `total_peaks_initial` | フィルタ前ピーク数 |
| `total_peaks_filtered` | フィルタ後ピーク数 |
| `filter_params` | 実際に適用したフィルタ辞書 |
| `explained_variance.PC1/PC2` | 説明分散比の百分率文字列 |
| `pca_equation` | 表示用の一般式 `X = T P^T + E` |
| `sn_summary.available_fraction` | フィルタ後ピークのうちS/Nを取得できた割合 0..1 |
| `sn_summary.count_with_sn` | S/Nを取得できたピーク数 |
| `sn_summary.min/median/max` | S/Nの最小・中央値・最大値 |
| `loadings.PC1_main_factor` | PC1で絶対Loadingが最大の変数名 |
| `loadings.PC2_main_factor` | PC2で絶対Loadingが最大の変数名 |
| `loadings.weights.PC1/PC2.RT` | RTのLoading係数 |
| `loadings.weights.PC1/PC2.m/z` | m/zのLoading係数 |
| `loadings.weights.PC1/PC2.Height` | HeightのLoading係数 |
| `top_contributors.PC1/PC2` | 各PCの**スコア絶対値**が大きいピーク。変数Loading上位ではない |
`top_contributors` / `get_top_contributors()` の1項目:

| キー | 意味 |
|---|---|
| `id` | PAI2ピークID |
| `name` | 候補化合物名 |
| `score` | 指定PC上のピークスコア。絶対値で順位付けするため正負の両方が入る |
| `m/z` | ピーク m/z（小数4桁丸め） |
| `height` | ピーク高さ |
| `signal_to_noise` | S/N |
| `rt` | RT（小数2桁丸め） |

### 5.4 `inspect_metabolite_details()`

成功時は `{"status":"success", "matches":[...], "note":...}` を返す。`metabolite_name` は大文字小文字を無視した部分一致で、複数件返り得る。

| `matches[]` キー | 意味 |
|---|---|
| `id` | PAI2ピークID |
| `name` | 候補化合物名 |
| `m/z` | ピーク m/z |
| `rt` | ピーク頂点RT |
| `height` | ピーク高さ |
| `area` | ピーク面積 |
| `signal_to_noise` | S/N |
| `has_msms_like_fields` | キー名に `msms`、`fragment`、`spectrum` のいずれかを含むフィールドが存在するか。**実スペクトルが非空かは判定しない** |
| `formula` | 分子式 |
| `adduct` | 付加体 |
| `comment` | コメント |

検索条件なしは `status=error`、該当なしは `status=not_found` となる。

## 6. DCL (`dcl_reader.py`)

### 6.1 `deserialize_dcl()`

型は `list[dict]`。**1要素は1 MSDecResult、すなわち1プリカーサーピークに対応するデコンボリューション済みMS/MS結果**である。件数は同名PAI2のピーク数と一致する設計である。

| キー | 型 | 意味 |
|---|---|---|
| `dcl_index` | int | DCL内の0始まり順序。PAI2のリスト順序/MasterPeakIDに対応する設計 |
| `scan_id` | int | MSDec結果のスキャンID |
| `raw_spec_id` | int | 元のraw spectrum ID。PAI2の `ms2_raw_id` と対応し得る |
| `precursor_mz` | float | プリカーサーイオン m/z |
| `ion_mode` | int | 生のイオンモード列挙値。`0=Positive`, `1=Negative`, `2=Both` |
| `rt` | float | プリカーサーピークRT（min） |
| `model_peak_height` | float | デコンボリューションモデルピーク高さ |
| `signal_to_noise` | float | DCL scoring blockのS/N。PAI2のピーク検出S/Nとは別フィールド |
| `estimated_noise` | float | DCL scoring blockの推定ノイズ |
| `n_msms_peaks` | int | 元スペクトルに格納されたフラグメントピーク数 |
| `msms_spectrum` | list[list] | `[fragment_mz, intensity]` のリスト |

`include_spectrum=false` では `msms_spectrum=[]` だが、`n_msms_peaks` は元の本数を保持する。`top_n_peaks=N` では `msms_spectrum` だけを強度上位N本に縮め、m/z昇順に戻す。したがって **`len(msms_spectrum)` と `n_msms_peaks` は一致しないことがある**。

### 6.2 `summarize_dcl()`

| キー | 意味 |
|---|---|
| `total_results` | MSDecResult総数 |
| `with_msms` | `n_msms_peaks > 0` の件数 |
| `msms_rate_pct` | `with_msms / total_results * 100` |
| `msms_peak_count_median` | MS/MS保有結果におけるフラグメント数中央値。偶数件でも中央2値平均ではなく上側の中央要素 |
| `msms_peak_count_max` | フラグメント数最大値 |
| `precursor_mz_range` | 正の precursor m/z の `(min, max)`。小数4桁丸め |
| `rt_range` | RT の `(min, max)`。小数2桁丸め |
| `error` | 空入力時のメッセージ |

### 6.3 検索とPAI2への付与

`get_msms_by_precursor()` は `abs(result.precursor_mz - query) <= tol`、任意で `abs(result.rt - query_rt) <= rt_tol`、かつ `n_msms_peaks > 0` のDCL辞書をそのまま返す。

`attach_msms_to_features()` は同じリスト位置の PAI2 と DCL を対応付け、m/z差が `mz_tol` 以下ならPAI2辞書へ次を追加/上書きする。

| 追加キー | 意味 |
|---|---|
| `msms_spectrum` | DCL由来フラグメント配列 |
| `n_msms_peaks` | DCLに記録された元フラグメント数 |
| `msms_peak_count` | `n_msms_peaks` と同じ値 |

関数の返り値は、付与に成功したうち `n_msms_peaks > 0` だったピーク数である。

## 7. EIC/AEF (`eic_aef_reader.py`)

### 7.1 `parse_eic_aef_css1()` のスポット出力

型は `list[dict]`。**1要素は1アラインメントスポットのEIC集合**で、`samples` に各測定ファイルのクロマトグラム要約を持つ。

| キー | 型 | 意味 |
|---|---|---|
| `spot_id` | int | ファイル中の0始まりスポット順序。今回のデータではARF/ARF2のID順と対応 |
| `rt` | float | スポット代表RT（min） |
| `ri` | float | スポット代表RI。LCデータでは0のことがある |
| `mz` | float | スポット代表 m/z |
| `drift` | float | スポット代表ドリフト時間。未使用時は `-1` |
| `main_type` | int | 横軸種別の列挙値。`0` はRT主軸を表す |
| `num_samples` | int | このスポットに格納されたサンプル数 |
| `samples` | list[dict] | サンプル別EIC要約。リスト長は通常 `num_samples` |

### 7.2 `samples[]` の全フィールド

**1要素は「1スポット × 1測定ファイル」のEIC**である。

| キー | 型 | 意味 |
|---|---|---|
| `file_id` | int | データセット内の測定ファイルID |
| `peak_top` | float | ピーク頂点の**横軸座標**。今回の `main_type=0` データではRT（min）。**強度ではない** |
| `num_peaks` | int | EICに格納されたクロマトグラム点数。名称はピーク数に見えるがデータ点数 |
| `mean_intensity` | float | 全クロマトグラム点の強度算術平均 |
| `max_intensity` | float | 全クロマトグラム点の最大強度 |
| `chromatogram` | list[tuple] | `include_chromatogram=true` のときだけ追加される `(horizontal_coordinate, intensity)` 点列。長さは `num_peaks` |
注意: バイナリにはピーク左端・右端座標もあるが、現パーサーは読み進めるだけで出力辞書には含めない。

### 7.3 `summarize_eic_data()`

| キー | 意味 |
|---|---|
| `total_spots` | EICスポット総数 |
| `rt_range` | スポット代表RTの `(min, max)` |
| `mz_range` | スポット代表 m/z の `(min, max)` |
| `total_samples` | 全スポットの `num_samples` 合計。ユニークサンプル数ではない |
| `total_peaks` | 全 `samples[].num_peaks` の合計。実際には全クロマトグラム点数 |
| `peak_top_mean` | 全サンプルのピーク頂点横軸座標の平均。強度平均ではない |
| `peak_top_max` | 全サンプルのピーク頂点横軸座標の最大。最大強度ではない |
| `unique_file_ids` | 出現した `file_id` の昇順リスト。長さがユニークサンプル数 |

### 7.4 検索・ランキング出力

`search_eic_by_mz_range()` と `search_eic_by_rt_range()` は条件に合う**元のスポット辞書全体**を返す。境界値を含む。

`top_eic_spots_by_peak_top()` の1行:

| キー | 意味 |
|---|---|
| `spot_id` | スポットID |
| `rt` | スポット代表RT |
| `mz` | スポット代表 m/z |
| `num_samples` | サンプル数 |
| `max_peak_top` | サンプル間で最大のピーク頂点横軸座標 |

**現関数名・MCP表示は強度ランキングのように見えるが、実際には `peak_top` 座標の降順である。今回のLCデータでは概ね遅いRTのスポットが上位になる。強度上位を求める場合は `max_intensity` を使う別ロジックが必要である。**

## 8. MCPツールの外部出力

MCPツール層（`tools_*.py`。`server.py` はそれらを登録・再エクスポートする薄いファサード）が、上記の構造化結果を主にMarkdown/JSON文字列へ整形する。LLMは表示文ではなく、次の意味を基準に解釈する。

### 8.1 `arf_parser()` / `arf_re_pca()`

返り値はテキスト1件を含む `list`。総スポット数、Class ID分布、タグファイル対応数、タグ別件数、総サンプル別レコード数、平均サンプル数/スポット、PCA行列形状、PC1/PC2説明分散比、PCAスコア要約（群別サンプル数・図示note、点列は非同梱）、Loading上位を含む。`class_ids` を指定すると、選択したClass IDに属するサンプル行だけを残してPCAを実行する。複数Class IDはOR条件で、照合は大文字小文字を区別しない。`arf_list_classes()` は `.mddata` のパスとClass ID別サンプル数をJSONで返す。`arf_list_tags()` は現在のARFセッションについてタグ定義、サンプルファイル対応数、タグ付与数をJSONで返す。

PCAスコア要約（散布図の点列は非同梱＝`save_pca_figure` で図示。全点列は
`session.last_pca_plot` に保持され図ツールが参照する）:

| キー/行 | 意味 |
|---|---|
| タイトル行 | 図タイトル |
| `PC1 (x%) × PC2 (y%)` | PC1/PC2 説明分散率 |
| 群別サンプル数 | 群ラベルがあれば `群=件数` を列挙、無ければ総サンプル数 |
| 図示note | `save_pca_figure` で散布図を生成する旨 |

### 8.2 `arf2_parser()`

返り値はテキスト1件を含む `list`。`total_spots`, `annotated_count`, `annotation_rate`, RT/m/z範囲、強度中央値、イオンモード、S/N中央値、Ontology上位を自然言語で表示する。個々の22列は返さず、セッション内部に保持する。

### 8.3 `pai2_parser()` と関連ツール

`pai2_parser()` は `[text_report, Image]` を返す。`text_report` のJSONは 5.3節の `summary`、`Image` はピークPCA散布図PNGである。`pai2_get_top_metabolites()` は5.3節の contributor 配列をJSON文字列で返す。`pai2_inspect_metabolite_details()` は5.4節の辞書をJSON文字列で返す。`pai2_update_analysis_filter()` は適用した `min_intensity`/`min_sn`、フィルタ前後件数、データ損失率、PC1/PC2説明分散率、前回からのPC1説明分散率変化、フィルタ後のPC1スコア絶対値上位5ピークをテキストで返す。

### 8.4 `eicaef_parser()` と関連ツール

`eicaef_parser()` は7.3節の要約をJSON文字列として返す。m/z/RT検索の各表示行は `spot_id`, `rt`, `mz`, `num_samples` を持つ。`eicaef_top_peak_tops()` はさらに `max_peak_top` を表示するが、7.4節のとおり強度ではなく横軸座標である。

### 8.5 `eicaef_plot_chromatograms()` の描画契約

`eicaef_plot_chromatograms()` は画像ではなく、クライアント中立の構造化JSON
`plot_schema="lipidmix.eic.v1"` を返す。主要フィールドは `axes`、スポットの
`rt`/`mz`、および各試料の `series[].x` / `series[].y` / `file_id` /
`peak_left` / `peak_top` / `peak_right` である。Use-LLLMはこれをPlotlyへ変換し、
Claude Desktop等は各クライアントのUI方式で描画できる。通常の描画ではファイルを
作らない。

PNGが必要だとユーザーが明示した場合に限り、先に得たプロット情報を
`save_eic_figure(analysis_id, title=None)` で `reports/figures/` へ保存する。
この保存は対話描画とは別の書き込み操作である。

DCLパーサーは現時点で独立したMCPツールとして公開されていない。

## 9. LLM解釈時の必須注意事項

1. `Name` が存在しても確定同定とは限らない。空文字、`Unknown`、`no MS2:`、`low score:`を区別する。
2. `Ontology` は化合物名ではなく分類クラスである。空文字と文字列 `Unknown` も区別する。
3. ARFの1行はサンプル別ピーク、ARF2の1行は全サンプル統合スポットであり、同じ「1行」でも粒度が違う。
4. ARFの `IsGapFilled=true` は実測ピークではなく補間値である。検出率や存在判定では別扱いする。
5. ARFの `HeightAverage` は現実装ではグループ先頭値であり、名前どおりの再計算平均ではない。統合統計にはARF2側を優先する。
6. ARF要約の `named_compounds` は空文字も数えるため、注釈率には使わない。
7. PAI2のPCAはピークを行とする3変数PCAで、サンプル間比較ではない。`top_contributors` はPC Loadingではなくピークスコア絶対値上位である。
8. PAI2の `has_msms=true` は取得参照があることを示すだけで、PAI2内 `msms_spectrum` が非空とは限らない。実スペクトルはDCLを参照する。
9. DCLの `n_msms_peaks` は元本数であり、`top_n_peaks` 適用後の配列長とは異なり得る。
10. EICの `peak_top` は強度ではなく頂点座標である。強度は `max_intensity`、平均強度は `mean_intensity` を使う。
11. `total_samples` はEIC全スポットにわたるサンプルエントリ総数であり、ユニークサンプル数は `len(unique_file_ids)` である。
12. m/zとRTの微小差はファイル形式の浮動小数精度、代表値の定義、アラインメント処理に由来し得る。厳密一致ではなく許容差を用いる。

## 10. 前処理・QC（P2a）

`preprocessing.py`（MCP非依存の純ロジック層）と `tools_arf.py` の `arf_list_sample_roles()` / `arf_preprocess()` / `arf_pca_preprocessed()` が、ARFロード後のサンプル×特徴量行列に対する前処理・QCを担う。既定では**何も適用されない（opt-in）**。生行列を消費する `arf_parser`/`arf_re_pca` の既定挙動は変えない。

### 10.1 役割検出（sample/qc/blank）

`preprocessing.detect_sample_roles()` が、ファイル名と Class ID を `_` 区切りでトークン化し、大小無視で `qc`/`blank` トークンと照合してサンプルを `sample`/`qc`/`blank` に分類する（`blank` を `qc` より優先評価）。`arf_list_sample_roles()` はこの分類結果と役割別件数を、前処理適用前の確認用にJSONで返す。

### 10.2 前処理レシピ（`arf_preprocess`）

`arf_preprocess(normalize, blank_min_fold, drift_correct, max_qc_rsd, impute, props)` が、`preprocessing.preprocess()` に処理を委譲し、以下の順で適用する。

1. **ブランク除去**（`blank_min_fold` 指定時）: 生体試料平均 が `blank_min_fold` × ブランク平均 未満の特徴量を背景として除去。ブランク/生体試料のどちらかが無ければ未実施（caveat）。
2. **正規化**（`normalize="tic"|"median"|"pqn"|"none"`）: 行（サンプル）ごとのスケーリング。`tic`=行総和、`median`=行中央値、`pqn`=Probabilistic Quotient Normalization（参照はQC中央値、QCが無ければ全サンプル中央値）。**正規化係数が0または非有限のサンプル（未検出=0が過半で行中央値=0 になる疎な試料など）は、行全体をNaN化して破棄せず未正規化のまま残置し、`report["unscaled_samples"]` と caveat で明示する**（`median`/`pqn` で起こりやすい。`tic`=行総和は総和>0のため安全）。旧実装は該当行をNaNで全消去し、疎データで多数の試料を無言で失っていた。
3. **QC-RLSCドリフト補正**（`drift_correct=True` 指定時）: QCを注入順（`analytical_order`、`.mddata` 由来）に並べ移動中央値で平滑化した系統ドリフトで、特徴量ごとに全サンプルを補正する。**注入順が全サンプルで取得できない、またはQCが最小数未満なら未実施**（caveat）。加えて **QC が試料列に挿入されていない設計でも未実施**（`status="skipped"`）: QC-RLSC は QC が試料の前後に散在することを前提とし、QC を全試料の後にまとめて流した設計（実例: 試料 1–48 → Blank 49 → QC 50–56）では `np.interp` が端値で頭打ちになり、補正した外見だけが残る。判定は `report["qc_interspersion"]`（`covered`＝QC注入順区間に入る試料数 / `qc_range` / `sample_range`）。`covered` が試料の半数未満なら適用はするが「外挿補正」caveat を付す。
4. **QC RSDフィルタ**（`max_qc_rsd` 指定時）: QC群での相対標準偏差（SD/mean）が閾値を超える特徴量を除去。QCが無い/不足なら未実施（caveat）。
5. **欠損補完**（`impute="half_min"|"knn"|"column_mean"|"none"`、既定 `half_min`）: 行列生成後に残るNaNを補完。`half_min`=特徴量最小値の半分（既定）、`knn`=sklearn `KNNImputer`、`column_mean`=列平均（旧実装互換）、`none`=補完しない。

処理結果は `session.feature_matrix`（前処理後行列）・`session.pp_sample_names`・`session.pp_feature_names`・`session.sample_meta`・`session.preprocessing_recipe` に保存され、以降の `arf_pca_preprocessed()` や将来の差次的解析（P2b）はこの前処理後行列を消費する。適用したレシピそのものが `session.preprocessing_recipe` に記録され、`arf_pca_preprocessed()` の出力にも「前処理レシピ」として明示される。

### 10.3 caveatの扱い

QC/ブランク/注入順のいずれかが欠けているためにスキップされたステップは、無言で無視されるのではなく `report["caveats"]`（`arf_preprocess()` のJSON応答）に文言として残る（例:「注入順が欠落、または QC が不足のためドリフト補正は未実施。」）。LLMはこれらのcaveatを解釈結果や報告書の注意点として引用すべきである。追加で前景化される caveat:

- **失敗 QC 注入**: 前処理の最初（正規化でスケールが動く前の生強度）に `preprocessing.detect_failed_qc()` が、総強度が QC 中央値の 20% 未満の QC を名指しする（`steps["qc_health"]`）。失敗注入を残したまま `max_qc_rsd` を掛けると QC の RSD が全特徴で跳ね上がり、ほぼ全特徴が除去される（実測: kidney aging NEG で 1345 → 51）。**「閾値が厳しすぎる」ように見える現象の真因は QC 側にあることが多い**ので、`arf_exclude` で除外してから前処理し直す。
- **プールQC の層別**: QC が複数バッチ（日付）に分かれる場合に加え、**QC 試料名の層別**（部位別 QC 等。`preprocessing.detect_qc_strata` が `20240311_QC_Cerebellum_ICR_NEG_1` → `cerebellum_icr` のように日付/`qc`/極性/数字を除いた残りで判定）も検出し、「全 QC を1系列扱いするドリフト補正/RSD は近似」と警告する。
- **正規化での試料脱落**: `normalize` の `unscaled_samples`（係数0/非有限で未正規化残置した試料数）に対応する caveat（§10.2）。
- **過度な特徴量除去**: フィルタ後に残存0件なら「全特徴が除去（閾値が厳しすぎる可能性、解析不能）」、特徴量の90%超が除去なら残存割合を注記する。除去総数は `report["features_removed_total"]`（= before − after）で参照する。**各 `steps[*]["removed"]` はフィルタごとの独立マスク件数で重複し得るため加算しないこと**（blank と qc_rsd の removed 合計が総数を超えることがある）。

なお `load_dataset` の複数バッチ告知は、解析対象である **`.arf`/`.arf2` のバッチ**にのみ基づく（`.mddata`/`.mdproject`/`.msp2`/`.pai2` 等も `AlignmentResult` 形式のタイムスタンプを持つため、拡張子で限定しないと告知バッチが実際に解析する `.arf` とズレる）。

### 10.4 `arf_pca_preprocessed()`

前処理後行列が無い（`session.feature_matrix is None`）場合はエラーメッセージ1件を返す。あれば `arf_reader.run_pca` でPCAを実行し、`arf_parser`/`arf_re_pca` と同じ整形ヘルパー（スコアプロット用JSON、Loadings上位）を使って結果を返す。出力テキストの構造・キー意味は8.1節のスコアプロット用JSONと同一。既定の `arf_parser`/`arf_re_pca` 経路とは完全に独立しており、`arf_preprocess()` を実行しない限り既存の解析結果には影響しない。

### 10.5 手動サンプル/ピーク除外（`arf_exclude`）

PCAスコアプロットで明らかに外れた1サンプルや、特定のピーク（スポット）を**名前/IDで手動除外**するためのツール。`exclusions.py`（MCP非依存の純ロジック層、`prune_spots()` / `roster()`）と `tools_arf.py` の `arf_exclude()` が担う。除外は**可逆・非破壊**で、`session.filtered_features` 自体は変更しない。

`arf_exclude(exclude_samples=None, exclude_spots=None, mode="add")` は JSON を返す。

- **`exclude_samples`**: 除外するサンプル名（`file_name`、完全一致）のリスト。
- **`exclude_spots`**: 除外するスポットの `MasterAlignmentID`（int）のリスト。
- **`mode`**: `add`（既定・追加）/ `remove`（再包含）/ `clear`（全消去）/ `list`（現状表示のみ）。
- 現データに存在しない指定は `unmatched_samples` / `unmatched_spots` として警告に載せ、一致分のみ集合へ反映する（タイプミスに寛容）。
- 応答キー: `status` / `mode` / `excluded_samples` / `excluded_spots` / `samples_before` / `samples_after` / `spots_before` / `spots_after` / `unmatched_samples` / `unmatched_spots` / `caveats`。残サンプルまたは残スポットが0件になる指定には caveat が付く。

除外集合は `session.excluded_samples`（`file_name` 集合）と `session.excluded_spots`（`MasterAlignmentID` 集合）に保持され、新ファイルロード（`load_data` のキャッシュミス経路）でリセットされる。行列を組む直前に `exclusions.prune_spots()` が適用され、**`arf_preprocess`**（→ `arf_pca_preprocessed` / `arf_differential`）と **`arf_re_pca`** が自動的に除外を反映する（初回ロードの `arf_parser` には適用されない）。除外が有効なとき、それぞれの出力に「ユーザ手動除外: サンプル N 件 / スポット M 件」の注記が付く。`arf_list_sample_roles()` は各サンプルに `excluded: true/false` を付して現在の除外状態を示す。

運用フロー: `arf_parser`（全体PCAで外れ俯瞰）→ `arf_exclude(exclude_samples=[...])` → `arf_re_pca` または `arf_preprocess`＋`arf_pca_preprocessed` で除外後PCAを確認 → `arf_differential`。戻したいときは `mode="remove"` / `mode="clear"`。

## 11. 差次的解析（P2b）

`differential.py`（MCP非依存の純ロジック層）と、`tools_arf.py` の `arf_differential()` / `tools_reports.py` の `save_volcano_figure()` が、前処理後のサンプル×特徴量行列に対する群間比較を担う。既定挙動・既存ツールは不変で、明示呼び出し時のみ作用する。

### 11.1 群ラベルの由来

群ラベルは `session.sample_meta[<sample>]["group"]`（ファイル名由来の factor トークン / Class ID 機構、`msdial_classes.assign_sample_groups`）から取得する。バッチは同 `sample_meta` の `batch`（ファイル名中の8桁日付）。`arf_differential()` は `session.feature_matrix`（前処理後行列）を消費し、無ければエラーを返す（先に `arf_preprocess()` が必要）。

`sample_meta[...]["group"]` は常に**完全な Class ID**（例 `24M_GF_F`）である。一方 `group_a` / `group_b` は**因子トークンによるプール指定**を受け付ける（`msdial_classes.expand_class_specs`）:

- `group_a="24M", group_b="9w"` → `24M_*` を全てプールし `9w_*` と比較（多因子デザインで主効果を見る正しい経路）
- `group_a="24M_GF"` のように複数トークンを `_` で繋ぐと AND 絞り込み（`24M` かつ `GF`）
- 完全な Class ID を渡せば従来どおりその1水準のみ
- 応答の `resolved_class_ids` に展開結果、`n_a` / `n_b` に実 n を返す。展開が2水準以上なら「プール群として解決」caveat を付す

**一致ゼロ・両群が同じ Class ID を掴む指定は `status="error"` で落とす**（成功扱いで n=0 を返すと「有意0件＝群間差なし」と誤読されるため）。`24M` と `GF` は `24M_GF_*` を共有するので排他ではなくエラーになる。なお交互作用検定は依然として提供しない。

### 11.1.1 上位ヒットの命名（ARF/ARF2 橋渡し）

`summary.top` の各行には `spot_id` / `name` / `name_source` が付く。`name_source="arf"` は ARF スポットの `Name`、`"arf2"` は ARF が `Unknown` のときに **同一アラインメントの兄弟 `.arf2`** から補った注釈（`ontology` も併記）。

**ARF と ARF2 は同じ `MasterAlignmentID` を指しながら代表 `Name` が食い違うことがある。** 実例（kidney aging, NEG）: Spot 474 は ARF 側 `Unknown`、ARF2 側 `SL 33:0;O|SL 17:0;O/16:0`（Ontology=`SL`）。ARF だけを見ると最大効果量の特徴が無名のまま残り、生物学的解釈に到達できない。

補完元は `AlignmentResult_<timestamp>` の語幹が一致する隣接 `.arf2` に限定する（`MasterAlignmentID` はアラインメント実行ごとに振り直されるため、別バッチの `.arf2` を引くと ID 対応が黙って崩れる）。兄弟が無ければ補完せず `name=null` のままにする。

### 11.2 統計

- **2群比較**（`group_a` と `group_b` を指定）: 特徴量ごとに Welch t 検定（等分散を仮定しない）と log2 fold change を計算する。`log2fc = log2((mean_a + 擬似カウント) / (mean_b + 擬似カウント))`（**正=群Aで高い**、擬似カウント既定1.0でゼロ割回避）。小n・分散0・全欠損は `p=NaN`。
- **一元配置ANOVA**（`group_factor` のみ指定）: その factor の全水準で特徴量ごとに F 統計量と p 値を計算する（3群以上）。
- **多重検定補正**: いずれも Benjamini-Hochberg で `p → q`（FDR）を付与（NaN は補正から除外し位置は保持）。p値は scipy があれば正確（無ければ近似フォールバック）。
- **volcano**: 2群比較のみ。各点は `feature` / `log2fc` / `neg_log10_p` / `sig`（`up`=q≤閾値かつlog2fc≥+閾値 / `down`=q≤閾値かつlog2fc≤−閾値 / `ns`）。全特徴分の点列は `session.last_differential["volcano"]` に保持し、`save_volcano_figure(analysis_id, title=None)` が `reports/figures/<analysis_id>_volcano.png` に描画する。**`arf_differential()` の応答 payload には全量 volcano を同梱せず**、`summary`（`n_tested`/`n_significant`/`n_up`/`n_down`＋有意上位 `top`）中心の要約と `volcano_note` のみを返す（先頭の結論が巨大配列＋文脈切り詰めで埋没し「全て ns」と誤読される退行を避けるため）。

### 11.3 必須caveat

`arf_differential()` の応答 `caveats` には、該当時に以下を前景化する（無言で握りつぶさない）:

1. **交絡（群⟂バッチ）**: 各群が単一バッチに偏る場合、「処理効果と測定バッチを分離できない」旨を警告（`check_confounding`）。例: `2_lipidome_lcms/NEG` は control/LPS=20220901・ILG/G_uralensis=20220902 で交絡。
2. **正規化状態**: `session.preprocessing_recipe` に正規化が含まれなければ「未正規化データの log2FC は測定量差を含み得る」と警告。
3. **群サイズ不足**: いずれかの群が n<2 なら「各群 n>=2 が必要（群名の誤り／前処理での試料脱落の可能性）」と警告。n>=2 かつ n<4 なら小n（検出力の限界）を注記。
4. **退化（検定不能）**: 検定できた特徴が0件なら「全特徴で p=NaN。群が空・分散0・正規化での試料NaN化の可能性。『有意0件』を『群間差なし』と解釈しない」と警告。0件でなくても特徴数の20%未満しか検定できなければ注記する。これにより「本当に有意差が無い（n_tested 健全）」と「そもそも検定できていない（n_tested≈0）」を区別できる。

LLMはこれらを解釈結果・報告書の注意点として必ず引用すること。統計値は「事実」だが、交絡・小nの下での因果的解釈は保留し、人間の判断に委ねる（既存の分業に整合）。

## 12. 同定信頼度・標準化（P2c）

`lipid_identity.py`（MCP非依存の純ロジック層、**完全オフライン**）と `peak_verification.py` の拡張が、脂質同定名の標準化と信頼度レベルの推定を担う。外部識別子の取得はネットワークを一切使わず、`pygoslin`（同梱・純Python）と同梱 TSV 表のみで行う。既存ツール・既定挙動・`verify_peak_annotation` の既存キーは不変で、新データは新ブロックに追加する。

### 12.1 GOSLIN 名正規化（`normalize_lipid_name`）

`pygoslin` で脂質ショートハンド名を正規化し、`normalized`（正規化名）/ `level`（構造レベル: SPECIES/MOLECULAR_SPECIES など）/ `lipid_maps_category` / `parse_ok`（失敗時 False＋`error`）/ `stripped`（下記の前処理をしたか）を返す。`pygoslin` 未導入や解析不能でも例外を投げず `parse_ok=False` を返す（グレースフルデグレード）。

**MS-DIAL 限定子接頭辞の除去**: MS-DIAL は Name に信頼度の限定子（`"no MS2: "` / `"low score: "` / `"w/o MS2:"` 等）を前置し、複数候補を `|` で連結する。解析前にこれら接頭辞と `|` 以降を除去して単一の species 表記に整える（除去した場合 `stripped=True`）。この修正で正規化名の付与が増える。`"RIKEN N-VS1 ID-…"` 等の真の未同定名は接頭辞除去後も解析不能のまま（`parse_ok=False`）で正しい。

**プラズマローゲン注意**: pygoslin は species レベルで `PC P-34:0` を `PC O-34:1` に**同一化**する（P-/O- エーテルの曖昧性）。P- と O- を区別したい場合は正規化名ではなく `peak_verification.ether_caveats()` の caveat（[[pe-p-vs-pe-o-annotation]] / [[plasmalogen-oxidation]] に直結）に依拠すること。

### 12.2 同梱マッピング表（`load_reference_tables` / `map_to_reference`）

`reference/lipidmaps_classes.tsv`（クラス→LIPID MAPS カテゴリ/メインクラス）と `reference/refmet_map.tsv`（クラス→RefMet 名）は**キュレート済みの部分集合**（一般的な脂質クラスを網羅）。クラストークンで写像し、`matched`（bool）/ `lipid_maps_category` / `lipid_maps_main_class` / `refmet_name` / `caveat` を返す。表に無いクラスは `matched=False`＋caveat「同梱マッピング表に無いため ID 未付与」を返し、**推測はしない**。

### 12.3 MSI レベル推定（`msi_level`、ヒューリスティック）

決定論的シグナル（名称の有無・MS/MS取得・精密質量誤差バンド・アダクト整合バンド）を組み合わせて MSI 同定信頼度を推定する。`level`（2/3/4）/ `label` / `rationale` / `heuristic=True` を返す。

- **Level 2**（putative annotated compound）: 名称あり＋MS/MS取得＋精密質量整合（PASS）＋アダクト非FAIL。
- **Level 3**（putative class-level）: クラス（ontology）は判別できるが上記を満たさない。
- **Level 4**（unknown）: 名称・クラスとも無し。
- **Level 1（標準品照合）は決して主張しない**。返り値の `heuristic=True` が示すとおり、これは決定論的推定であって同定の確定ではない。

### 12.4 統合ツール

- `verify_peak_annotation` のドシエに `identity_normalization` ブロック（`goslin` / `reference` / `msi` / `class_token`）を追加。既存の `analytical_checks`（精密質量誤差・アダクト整合）から算出したバンドを MSI 推定に流用する（質量・アダクトロジックの二重化を回避）。
- `arf2_annotate_identities(file_path=None, max_rows=50)`: ARF2 スポットカタログの注釈を一括で正規化・ID/レベル付与し、上位 `max_rows` 件を返す。**ARF2 には MS/MS 取得フラグ・精密質量誤差が無いため MSI は保守的にクラス上限で評価**（`has_msms=False`、バンド UNKNOWN）。より確度の高い MSI 評価は個別ピークの `verify_peak_annotation` を用いること。

### 12.5 アダクト/元素表の拡張（`peak_verification.py`）

`ADDUCT_SHIFTS` を `(sign, shift, charge, n_mol)` の4タプル化し、多量体 `[2M-H]-`・多価 `[M-2H]2-`・`[M+FA-H]-`（`[M+HCOO]-` の別名）を追加。`adduct_mz` は `m/z = (n_mol×neutral + shift) / charge` で多量体・多価に対応する（既存1価アダクトの数値挙動は不変）。元素表に D(²H)/F/Br/¹³C を追加（標識・ハロゲン対応）。CCS/RT 参照照合・同位体パターン照合は参照表未同梱のため v1 対象外。
