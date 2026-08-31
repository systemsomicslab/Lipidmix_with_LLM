# MS-DIAL Interactive・Lipidmix mzTab-M 連携設計

- 日付: 2026-08-29
- 状態: Draft（実装前レビュー対象）
- 対象リポジトリ:
  - 上流: `C:\Users\yuu18\msdial-interactive-app`
  - 下流: `C:\Users\yuu18\Lipidmix_with_LLM`
  - 将来の参照先: `C:\Users\yuu18\repository_catalog`
  - 将来の化合物・スペクトル照合: `massbank-context`（別システム）
- 主交換形式: mzTab-M 2.0
- 制御契約: `omics-handoff.v2`

## 1. 目的

LLM に生データのリポジトリパスを渡すだけで、次の処理を安全かつ再現可能に連携できる状態を
設計する。

1. `msdial-interactive-app` が MS-DIAL Console を実行し、ピーク検出・アラインメント・同定・
   mzTab-M エクスポートを行う。
2. 出力が今回のジョブに属すること、mzTab-M の構造、定量値の意味を検証する。
3. `Lipidmix_with_LLM` の MCP サーバ `ms-data-parser` が mzTab-M を主入力として読み、前処理、
   QC、PCA、差次的解析、図示、ピーク根拠確認を行う。
4. 将来、公開研究メタデータと化合物・MS/MS データベースを独立 MCP から参照し、統合レポートを
   作成する。

本設計の中心判断は、**mzTab-M を主交換ファイルにするが、mzTab-M だけを唯一の根拠には
しない**ことである。MS/MS、ギャップフィル、クロマトグラム、MS-DIAL 固有の詳細は、検証済みの
sidecar として同じ解析handoffに束ねる。

## 2. 設計原則

1. **データ面と制御面を分離する。** mzTab-M は定量表と同定表を運ぶデータ面、
   `omics-handoff.v2` はファイル選択・定量種別・検証結果・来歴を運ぶ制御面とする。
2. **LLM にファイルの意味を推測させない。** 極性、Peak Height / Peak Area、サンプル因子、
   主ファイルの選択は機械可読な契約で確定する。
3. **ジョブ所有権を優先する。** ディレクトリ内の「更新時刻が最新のファイル」は選ばず、実行前後の
   差分から今回のジョブが生成した成果物だけをhandoffへ収録する。
4. **検証不能は停止する。** 定量値の実体とメタデータが衝突した状態で統計解析を続けない。
5. **MCP は責務ごとに分離する。** MS-DIAL 実行、下流解析、研究メタデータ参照、スペクトル照合を
   1つの巨大サーバへ統合しない。
6. **ローカルファーストを維持する。** 生データ、解析結果、セッション状態はローカルに置く。
   NAS 共有対象は既存方針どおり知識・playbook等に限定し、明示的な設定なしに生データを共有しない。
7. **既存 ARF 系機能を一度に廃止しない。** mzTab-M 経路を追加した後も、ARF/ARF2/PAI2/DCL/EIC
   経路を互換・根拠確認用として残す。

## 3. 現状調査で確認した事実

### 3.1 `msdial-interactive-app`

- WebUI、Python HTTP backend、MS-DIAL Console 実行層を持つ。
- MCP アダプタは実在し、MCP tool はローカル HTTP API を介して backend と MS-DIAL Console を
  操作する。したがって「MCP化」は呼称だけではない。
- 現行の `msdial-interactive.datamining-handoff.v1` は `primary_mztab_file` を1件だけ持ち、
  複数 mzTab-M がある場合は更新時刻が新しいものを選ぶ。
- ジョブ所有成果物の収集機構はあるが、下流で必要な `.arf`、`.arf2`、`.pai2`、`.dcl`、
  `.EIC.aef` を意味別に記録する契約にはなっていない。
- mzTab-M の構造検査とpreviewはあるが、数値が Height / Area のどちらであるかを実データと照合する
  意味検証はない。

### 3.2 `Lipidmix_with_LLM`

- `server.py` は FastMCP `ms-data-parser` の薄いファサードで、現行実装は40 tools、4 resources、
  3 resource templatesを公開する。
- `load_dataset(directory)` は `.arf2` で全体を概観し、`.arf` でサンプル別PCAを実行する入口であり、
  mzTab-M は未対応である。
- 解析状態は `session.arf` / `session.arf2` / `session.pai2` / `session.eic` に分離され、
  ARF の行構造に依存する `feature_matrix` を他形式で置き換えない設計になっている。
- 統計行列に用いている ARF の既定値は Peak Height である。
- 実 MS/MS スペクトルは `.dcl` にあり、`.pai2` の `has_msms` は取得フラグでしかない。
- クロマトグラムは `.EIC.aef` にある。mzTab-M 単体では現行の根拠確認機能をすべて代替できない。

### 3.3 `repository_catalog` と `massbank-context`

- `repository_catalog` は約4 GBのSQLiteを持つdata-onlyリポジトリで、現時点ではAPI/MCPではない。
  研究、サンプル、測定法、公開raw fileの文脈検索には使えるが、化合物・スペクトル同定DBではない。
- `massbank-context` は別責務のシステムであり、現時点の設計・実装進捗を前提に本連携の必須依存へ
  しない。
- 将来の照合では、スペクトル類似度等による同定順位と、研究・組織・生物学的文脈を分離する。
  文脈情報でスペクトル同定順位を上書きしてはならない。

### 3.4 MS-DIAL mzTab-M の Height / Area 不整合

2026-08-29時点の公式最新安定版は
[MSDIAL-v5.5.260817](https://github.com/systemsomicslab/MsdialWorkbench/releases/tag/MSDIAL-v5.5.260817)、
Console向けpre-releaseは
[MSDIAL-v5.5.260820](https://github.com/systemsomicslab/MsdialWorkbench/releases/tag/MSDIAL-v5.5.260820)
である。安定版の「Fixed GC-MS mzTab export」はFormulaのnull処理に関する修正であり、以下の
LC-MS定量種別問題は改善していない。

ソース調査で確認した挙動は次のとおり。

| 経路 | 選択 | 実際の値 | mzTab-M metadata | 判定 |
| --- | --- | --- | --- | --- |
| GUI | Height | `PeakHeightTop` | グローバルexport flag依存 | 通常は一致 |
| GUI | Area | `PeakAreaAboveZero` | 現在処理中のaccessorではなくグローバルexport flag依存 | 不一致し得る |
| GUI | Height + Area | `Height_*.mzTab` と `Area_*.mzTab` の2ファイル | 各ファイル固有ではない | 曖昧 |
| Console LC-MS | Height相当 | Height accessor固定 | Height/Area flag依存 | Area指定でも実値がAreaにならない |

GUIでは選択した `ExportType` ごとに別ファイルを生成し、値は各
`QuantValueAccessor` から取得する。一方、quantification unit metadataは現在処理中のaccessorではなく、
`DataExportParam.IsHeightMatrixExport` / `IsPeakAreaMatrixExport` を参照する。そのためArea値の
ファイルにHeight metadataが付く、または両方のunit行が付く可能性がある。

Console LC-MSのmzTab-M出力はHeight accessorが固定であり、Area flagを切り替えても値の取得元は
変わらない。したがって、**現行Consoleを使う自動パイプラインでArea出力を要求してはならない**。

### 3.5 実ファイルで確認した意味論

MS-DIAL v5.5.260323で生成された実ファイル
`Area_AlignmentResult_2026_07_09_17_14_19_2026_08_28_12_31_34.mzTab` と、対応するARFを比較した。

- assay 56、study variable 18、SML 1,345、SMF 1,345、SME 942。
- mzTab-MのSMF行列は 56 samples × 1,345 featuresで、ARFと同じ次元だった。
- abundanceはARFの `PeakAreaAboveZero` と整数丸め誤差内で一致した。
  最大絶対差0.5、平均絶対差約0.24。
- abundanceは `PeakHeight`、`PeakAreaAboveBaseline` とは一致しなかった。
- それにもかかわらずMTDには `precursor intensity (peak height)` が記録されていた。
- 全75,320セル中47,568セルがgap-filledで、うち45,070セルは非ゼロAreaだった。
  mzTab-M abundanceだけではgap-filledか実ピークかを区別できない。
- SME行にはheaderより末尾空欄1列多い行があり、構造validatorはwarningとした。パーサは末尾空セルを
  正規化して読み込める必要があるが、入力warningは来歴に残す。

この実測により、ファイル名やMTDだけでは定量値の意味を確定できないこと、また現行の検出率・
gap-fill QCを維持するにはsidecarが必要であることが分かる。

## 4. 比較した方式

### A. mzTab-Mだけに統一

長所は標準形式だけで連携できる単純さである。一方、現行MS-DIALのHeight/Area metadata不整合、
gap-fill情報の欠落、MS/MS・EICの詳細不足を吸収できないため採用しない。

### B. 現行の独自バイナリだけを継続

既存Lipidmix機能をそのまま利用できるが、上流と下流がMS-DIAL内部形式へ強く結合し、他ツールとの
交換性、検証可能性、将来の標準化を得られないため主経路にはしない。

### C. mzTab-M主形式 + 検証handoff + evidence sidecar

mzTab-Mを統計・同定の主交換形式とし、制御契約で定量値の意味を確定し、独自形式または標準化TSVを
根拠sidecarとして添付する。標準性と現行解析の科学的完全性を両立できるため、本設計で採用する。

## 5. 全体アーキテクチャ

```text
raw data repository path
          |
          v
LLM / durable orchestrator
          |
          +---- MCP: msdial-interactive
          |       plan -> run -> collect -> validate -> handoff
          |                       |
          |                       +-- *.mzTab          primary data
          |                       +-- feature-qc.tsv   QC sidecar
          |                       +-- *.arf/*.arf2     fallback/provenance
          |                       +-- *.pai2/*.dcl     peak/MS-MS evidence
          |                       +-- *.EIC.aef        chromatogram evidence
          |                       +-- omics-handoff.v2.json
          |
          +---- MCP: ms-data-parser
          |       load -> normalize -> preprocess/QC -> PCA/differential
          |       -> peak/MS-MS/EIC verification -> report
          |
          +---- future MCP: repository-context   study/sample/method context
          |
          +---- future MCP: massbank-context     compound/MS-MS matching
          |
          v
integrated local report + machine-readable provenance
```

オーケストレータは各MCPの公開toolだけを呼び、各サーバのプロセス内状態へ直接依存しない。長時間処理の
状態はhandoffとジョブ記録に永続化し、MCP再起動後も再開可能にする。

## 6. 成果物bundle

1解析・1極性・1定量種別を最小単位とし、POS/NEGは別mzTab-Mとして扱う。HeightとAreaを両方出す
場合も別ファイルであり、同じファイル内に2つの行列が入るとは解釈しない。

```text
<run-directory>/
  omics-handoff.v2.json
  mztab/
    neg-height.mzTab
    neg-area.mzTab
  sidecars/
    feature-qc.tsv
    sample-manifest.tsv
  msdial/
    AlignmentResult_*.arf
    AlignmentResult_*.arf2
    *.pai2
    *.dcl
    *.EIC.aef
  reports/
    mztab-validation.json
    quantification-verification.json
```

物理的なファイル移動は必須ではない。既存run directory内のファイルを参照してよいが、handoff内の
全パスはhandoff自身を基準に解決可能で、各ファイルにSHA-256を持つことを必須とする。

## 7. `omics-handoff.v2` 契約

### 7.1 例

```json
{
  "schema": "omics-handoff.v2",
  "analysis_id": "ana_20260829_001",
  "job_id": "msdial_job_001",
  "status": "completed",
  "created_at": "2026-08-29T12:00:00+09:00",
  "source": {
    "dataset_root": "D:/lab/raw/study-001",
    "input_count": 56
  },
  "software": {
    "name": "MS-DIAL",
    "version": "5.5.260820",
    "commit": "9c4712c",
    "execution_mode": "console"
  },
  "project": {
    "project_type": "lcms",
    "target_omics": "lipidomics",
    "polarity": "negative"
  },
  "primary_mztab_files": [
    {
      "path": "mztab/neg-height.mzTab",
      "polarity": "negative",
      "measure": "peak_height",
      "sha256": "<sha256>",
      "validation": {
        "structure": "passed_with_warnings",
        "quantification": "verified",
        "warnings": ["SME_TRAILING_EMPTY_COLUMN"]
      }
    }
  ],
  "artifacts": [
    {
      "path": "sidecars/feature-qc.tsv",
      "role": "feature_qc",
      "format": "tsv",
      "sha256": "<sha256>"
    },
    {
      "path": "msdial/AlignmentResult_001.arf",
      "role": "peak_matrix_source",
      "format": "arf",
      "sha256": "<sha256>"
    },
    {
      "path": "msdial/sample01.dcl",
      "role": "msms_evidence",
      "format": "dcl",
      "sha256": "<sha256>"
    }
  ],
  "sample_manifest": {
    "path": "sidecars/sample-manifest.tsv",
    "sha256": "<sha256>",
    "status": "verified"
  },
  "warnings": []
}
```

### 7.2 必須フィールドと不変条件

| フィールド | 契約 |
| --- | --- |
| `schema` | 完全一致で `omics-handoff.v2` |
| `analysis_id` | 上流・下流・将来のDB照合を束ねる不変ID |
| `job_id` | MS-DIAL実行ジョブの所有権確認に使用 |
| `status` | `planned / running / needs_input / completed / failed` |
| `software` | version、可能ならcommit、GUI/Consoleを記録 |
| `project.polarity` | `positive / negative`。混合ファイルは初期対応外 |
| `primary_mztab_files` | 単数ではなく配列。各要素が極性とmeasureを明示 |
| `validation.quantification` | `verified` 以外は統計解析を開始しない |
| `artifacts` | role、format、path、SHA-256を記録 |
| `sample_manifest` | 解析群・QC・Blank・共変量を明示。LLM推測は禁止 |

`primary_mztab_file` のような暗黙の単数選択と `newest_modified_time` はv2で廃止する。同一の
`polarity + measure` に候補が複数ある場合、ジョブ所有権だけで一意にできなければ
`AMBIGUOUS_PRIMARY_MZTAB` として停止する。

## 8. 定量種別契約

### 8.1 正準語彙

初期対応では次の2種類に限定する。

| `measure` | MS-DIAL値 | 用途 |
| --- | --- | --- |
| `peak_height` | `PeakHeightTop` | 現行ARF/Lipidmix既定と互換 |
| `peak_area_above_zero` | `PeakAreaAboveZero` | GUI Area exportの実体 |

`peak_area_above_baseline`、normalized value等は、実装と実データ検証を追加するまで未対応とする。
単に `area` と呼ばず、`above_zero` を含む名称で意味を固定する。

### 8.2 検証規則

定量種別は次の独立した証拠を収集して判定する。

1. 実行時に要求したexport type。
2. GUI生成ファイルの `Height_` / `Area_` prefix。
3. mzTab-M MTDのquantification unit。
4. MS-DIAL versionと実行mode。
5. 対応ARFがある場合、SMF abundanceと `PeakHeightTop` / `PeakAreaAboveZero` の数値照合。

証拠を単純多数決にはしない。既知の実装挙動をversion別adapterとして評価する。

| 条件 | 結果 |
| --- | --- |
| Console LC-MS v5.5でHeight要求、構造検査合格 | `peak_height / verified` |
| Console LC-MS v5.5でArea要求 | `UNSUPPORTED_AREA_CONSOLE` で実行前停止 |
| GUI Area prefix、ARF Areaと一致、MTDはHeight | `peak_area_above_zero / verified` + metadata conflict warning |
| GUI Height prefix、ARF Heightと一致 | `peak_height / verified` |
| prefix・MTD・ARF数値が相互に説明不能 | `QUANTIFICATION_CONFLICT` で停止 |
| ARFなしでprefixとMTDが衝突 | `QUANTIFICATION_CONFLICT` で停止。LLMに決めさせない |

ARFとの照合は assay/feature ID対応を先に検証し、丸め前後の差として `abs(diff) <= 0.5` を既定許容値
とする。全セル比較を原則とし、巨大データ向けにsamplingを導入する場合はseed、件数、対象範囲を
verification reportへ記録する。

### 8.3 上流修正後の扱い

将来MS-DIAL側でmetadata生成が修正されても、version番号だけで検証を省略しない。Height-only、
Area-only、Height+Area、GUI、Consoleのconformance fixtureを実出力で通過したversion adapterだけを
信頼対象に昇格する。

## 9. mzTab-M読み込み契約

### 9.1 対応範囲

- 初期対象は mzTab-M `2.0.0-M`。
- versionを解析し、2.1等を黙って2.0として読まない。
- 統計行列の正準はSMFの assay abundance列とする。SMLをfeature行列として扱わない。
- SML、SMF、SME間の参照整合性を検査する。
- `null`、空欄、数値0、NaNを区別して保持する。
- headerより末尾空欄が1列多い既知SME行は正規化して読めるようにするが、warningを消さない。
- `ms_run.location` の絶対URIを無条件には参照せず、handoffのdataset rootと許可済みrootに対して
  解決・検証する。

### 9.2 サンプル設計

mzTab-Mのassayとstudy variableは読み込むが、`9w_GF_F` のようにflattenされた名前から群、時点、
性別等をLLMが再推定してはならない。`sample-manifest.tsv` を正準とし、assay IDで結合する。

推奨列:

```text
assay_id  source_file  sample_name  sample_type  group  batch  subject_id  covariates_json
```

`sample_type` は最低限 `sample / qc / blank` を区別する。差次的解析に必要な群が未定義なら
`SAMPLE_DESIGN_MISSING` として `needs_input` へ移行する。

## 10. evidence sidecar

### 10.1 `feature-qc.tsv`

mzTab-Mだけで失われる検出・補完状態を運ぶ。1行は `SMF_ID × assay_id` とする。

```text
smf_id  assay_id  is_gap_filled  detected_peak  signal_to_noise  msdial_peak_id  msms_available
```

最低限 `is_gap_filled` と `detected_peak` を必須にする。これにより、非ゼロ値であってもgap-filledの
セルを検出率計算から区別できる。初期実装ではARFから生成してよい。

### 10.2 MS/MSとクロマトグラム

- `.dcl`: 実MS/MSスペクトル。ピーク同定・MassBank照合の一次根拠。
- `.pai2`: 測定ごとのピークと `.dcl` への索引対応。
- `.EIC.aef`: EIC描画とピーク形状確認。
- `.arf/.arf2`: 定量種別の照合、gap-fill、MS-DIAL固有属性、互換fallback。

各sidecarはhandoffの `artifacts[].role` で意味を宣言し、拡張子だけで用途を推定しない。不足時は
統計全体を常に失敗させるのではなく、依存機能だけを機械可読に無効化する。例えば `.dcl` が無い場合、
PCAは可能だがMS/MSによる同定確認は不可とする。

## 11. Lipidmix内部モデル

mzTab-Mを偽のARFとして `session.arf` に格納しない。形式固有readerの上に、解析に必要な正準モデルを
新設する。

```text
DatasetState
  source_format                arf | mztab
  source_files                 paths + hashes
  quantification_measure       peak_height | peak_area_above_zero
  feature_matrix               samples x features
  assay_metadata               sample design and file mapping
  feature_metadata             mz, rt, names, identifiers, SML/SMF/SME links
  feature_qc                   detected/gap-filled flags
  evidence_index               feature/sample -> DCL/PAI2/EIC references
  preprocessing_state          parameters + transformed matrix
  analysis_results             PCA/differential/QC
  provenance                   handoff + validators + warnings
```

`session.mztab` を単独追加するだけではARF専用の前処理・PCA・差次的解析が二重化するため、
`DatasetState` を共通解析層の正準とする。既存 `session.arf` 等は形式固有の詳細・後方互換用途として
残し、共通解析関数は `DatasetState` の行列とmetadataを受け取る。

## 12. 下流MCP公開面

初期移行では既存toolを壊さず、次の汎用入口を追加する。

| tool | 責務 |
| --- | --- |
| `dataset_load` | directory、mzTab-M、handoff v2のいずれかを読み、正準状態を作る |
| `dataset_status` | source、measure、極性、検証結果、利用可能なsidecarを返す |
| `dataset_preprocess` | 欠損、QC/Blank、gap-fill、変換、正規化を明示条件で実行 |
| `dataset_pca` | 正準行列からPCAを実行 |
| `dataset_differential` | sample manifestで指定した比較を実行 |
| `dataset_feature_evidence` | featureに対応するMS/MS・EIC・MS-DIAL根拠を集約 |

`load_dataset(directory)` は第1段階では互換wrapperとして残す。handoff v2が存在するdirectoryでは
`dataset_load(handoff)` を優先し、存在しない場合は現行ARF経路へfallbackする。ただし、複数候補から
更新時刻だけでmzTab-Mを自動選択してはならない。

既存 `arf_preprocess`、`arf_pca_preprocessed`、`arf_differential` は当面維持し、ARF入力時には
共通処理へ委譲する。mzTab-M入力に対してARF tool名を推奨しない。

## 13. 上流MCP公開面の変更

現行toolを保ちつつ、次の責務を追加・強化する。

1. MS-DIAL実行計画時に `polarity` と `measure` を明示する。
2. Console LC-MSのArea要求を実行前に拒否する。
3. ジョブ所有成果物へ `.arf/.arf2/.pai2/.dcl/.EIC.aef` を含め、roleを付ける。
4. mzTab-M構造検証後に定量種別検証を行う。
5. `feature-qc.tsv` と `sample-manifest.tsv` を生成または収集する。
6. `omics-handoff.v2.json` を生成する。
7. downstreamに単一 `primary_mztab_file` ではなく、検証済みhandoff pathを返す。

既存 `datamining-handoff.json` は移行期間中v1として読み取り可能にするが、v1からは定量種別を安全に
確定できない。v1入力でMTDとファイル名が衝突した場合は自動解析へ進まない。

## 14. 自動連携フロー

1. LLMが生データrootをオーケストレータへ渡す。
2. オーケストレータがファイル形式、極性候補、sample manifestの有無をread-onlyで調査する。
3. 不可逆な選択または実験デザインが不足する場合だけ `needs_input` としてユーザへ確認する。
4. `msdial-interactive` が解析計画を作り、設定とMS-DIAL versionを固定する。
5. MS-DIAL Consoleを実行し、今回のジョブに属する成果物を収集する。
6. mzTab-Mの構造、参照、次元、定量種別を検証する。
7. `feature-qc.tsv` とhandoff v2を生成し、全hashを確定する。
8. `ms-data-parser.dataset_load(handoff_path)` が正準状態を作る。
9. sample manifestに従い、QC/Blank処理、前処理、PCA、差次的解析を実行する。
10. 必要なfeatureだけ `.dcl` と `.EIC.aef` で根拠確認する。
11. 将来は `repository-context` と `massbank-context` を呼び、文脈とスペクトル照合を別々に追記する。
12. 実行条件、入力hash、warning、失敗、生成物を統合レポートへ記録する。

## 15. 状態・エラー契約

| code | 意味 | 既定動作 |
| --- | --- | --- |
| `MZTAB_NOT_FOUND` | ジョブ所有のmzTab-Mが無い | failed |
| `MZTAB_STRUCTURE_INVALID` | 必須section・参照・列構造が不正 | failed |
| `QUANTIFICATION_CONFLICT` | 値、MTD、prefix、要求種別が衝突 | needs_inputまたはfailed |
| `AMBIGUOUS_PRIMARY_MZTAB` | polarity + measureで一意に選べない | needs_input |
| `UNSUPPORTED_AREA_CONSOLE` | 現行ConsoleでAreaを要求 | 実行前failed |
| `SAMPLE_DESIGN_MISSING` | 統計比較に必要な因子が無い | needs_input |
| `COMPANION_ARTIFACT_MISSING` | 必要なsidecarが無い | 依存機能のみ無効化 |
| `ARTIFACT_HASH_MISMATCH` | handoff後にファイルが変化 | failed |
| `POLARITY_MISMATCH` | handoff、mzTab-M、設定の極性が不一致 | failed |

MCP toolの失敗は、既存の `missing_state` と同様に機械可読なエンベロープで返す。LLM向けmessageだけで
成否を表現しない。

## 16. 将来のDB連携境界

### 16.1 `repository-context`

`repository_catalog` をread-onlyで開き、研究、サンプル、測定法、公開raw fileの検索を提供する。
このDBへ解析結果を書き込まない。検索結果は生物学的・実験的文脈であり、化合物同定の一次根拠には
使わない。

### 16.2 `massbank-context`

`.dcl` または将来の標準化スペクトルsidecarを入力し、候補化合物、スペクトル類似度、matched peak、
library provenanceを返す。候補順位はスペクトル証拠に基づき、repository contextは表示・解釈の補助に
限定する。

### 16.3 初期実装から除外するもの

- `repository_catalog` のschema変更または書き込みAPI。
- MassBank indexer/matcherの完成。
- 複数MCPを1プロセスへ統合すること。
- LLMによる化合物同定順位の恣意的な並べ替え。

## 17. セキュリティとデータ境界

- 生データroot、run directory、handoff pathは許可されたローカルroot配下に限定する。
- handoff内の相対パス解決後にpath traversalを検査する。
- 外部から渡されたabsolute pathや `file://` URIをそのまま信用しない。
- raw dataをNAS、knowledge directory、外部DBへ自動コピーしない。
- SQLite参照はread-only modeを用いる。
- 解析設定、software version、commit、hashをレポートへ残す。
- MCP serverへのネットワーク公開は既定で行わず、stdioまたはloopbackを基本とする。

## 18. 移行段階

### Phase 1: mzTab-M readerと意味検証

- mzTab-M 2.0 parser、validator、fixtureをLipidmixへ追加する。
- 実ファイルとARFのHeight/Area照合を自動テスト化する。
- `DatasetState` と `dataset_load/status` を追加する。

### Phase 2: handoff v2とsidecar

- 上流の成果物分類を拡張する。
- sample manifest、feature QC、hash、明示的なprimary配列を実装する。
- Console Area guardとGUI出力検証を追加する。

### Phase 3: 形式非依存解析

- preprocess、PCA、differential、plotを `DatasetState` に移す。
- ARF toolを互換wrapperにする。
- DCL/EIC evidence indexを接続する。

### Phase 4: durable orchestration

- 生データrootからhandoff、下流解析、レポートまでの再開可能な状態機械を作る。
- `needs_input`、再試行、MCP再接続、部分失敗を扱う。

### Phase 5: context services

- `repository-context` をread-only MCPとして追加する。
- `massbank-context` のスペクトル照合を接続する。
- 同定根拠と生物学的文脈を分離した統合レポートを作る。

各Phaseは独立して検証・運用可能にする。Phase 1から3の完了を、Phase 4の自動運転開始条件とする。
Phase 5は上流・下流連携の必須条件にしない。

## 19. 受入条件

### 19.1 mzTab-Mと定量値

- 実ファイルの56 assays × 1,345 SMF matrixを欠落なく読み込める。
- SMFを統計feature行列とし、SML/SME参照を保持できる。
- 実Areaファイルの値が `PeakAreaAboveZero` と最大絶対差0.5以内で一致することを検証できる。
- 同じファイルのHeight MTDとの衝突を検出し、warningと実値根拠をhandoffへ記録できる。
- Console LC-MSでArea要求を実行前に拒否できる。
- Height + Area選択を2つの明示的なprimary entryとして扱い、更新時刻で片方を選ばない。
- POSとNEGを別ファイル・別entryとして扱う。

### 19.2 QCと根拠

- 75,320セル中47,568 gap-filledというfixtureの状態をsidecar経由で復元できる。
- gap-filled非ゼロ値を実検出ピークと区別して検出率を計算できる。
- `.dcl` がある場合だけ実MS/MS根拠を提示し、`.pai2.has_msms` だけでスペクトル確認済みとしない。
- `.EIC.aef` があるfeatureは既存EIC確認経路へ解決できる。
- sidecar欠落時は依存機能だけを無効化し、その理由を機械可読に返す。

### 19.3 再現性と互換性

- handoff内の全成果物hashを検証できる。
- 同じhandoffからMCP再起動後にも同じ入力・measure・sample designを復元できる。
- handoff v2が無い既存ARF directoryでは現行 `load_dataset` 経路が退行しない。
- 既存Lipidmix test suiteと上流test suiteが通過する。
- raw data、解析成果物、`repository_catalog` に意図しない書き込みを行わない。

## 20. テスト戦略

1. **unit**: MTD/SMF/SML/SME parser、末尾空欄、null/0、path解決、hash、error code。
2. **contract**: handoff JSON Schema、極性・measure一意性、artifact role、sample manifest結合。
3. **conformance**: MS-DIAL version別にGUI Height、GUI Area、GUI両方、Console Height、
   Console Area拒否を実出力で検査。
4. **golden data**: 56 × 1,345の実データから匿名化・最小化したfixtureと期待統計を保持。
5. **integration**: 上流の完了jobからhandoffを作り、別プロセスの `ms-data-parser` が読み込む。
6. **restart**: 上流・下流MCPを途中で再起動し、handoffから再開する。
7. **regression**: 既存ARF/ARF2/PAI2/DCL/EIC解析、plot、missing-state契約を維持する。

## 21. 非目標

- MS-DIAL本体の不具合修正を本連携の開始条件にすること。
- mzTab-MへMS-DIAL独自情報をすべて埋め込むこと。
- GUIとConsoleの出力を検証なしに同一視すること。
- sample nameから実験デザインをLLMが確定すること。
- 初期段階でmzTab-M 2.1、GC-MS、imaging、DIA proteomics等へ範囲を広げること。
- 既存のMassBank設計specを本specへ統合・変更すること。

## 22. 未決事項

実装計画へ進む前に、次の2点だけユーザ判断を得る。

1. 自動運転の既定定量値を、現行Lipidmix互換の `peak_height` とするか、実験系で重視する
   `peak_area_above_zero` とするか。現行Consoleを使う間は前者だけが自動運転可能である。
2. sample manifestをMS-DIAL実行前の必須入力にするか、初回はmzTab-Mから候補を生成して
   `needs_input` で承認するか。

この2点以外は本specの契約として固定し、決定後に別ファイルの実装計画へ分解する。
