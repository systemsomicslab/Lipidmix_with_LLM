# Lipidmix with LLM

Python prototypes for parsing, cleaning, visualizing, and exposing MS-DIAL lipidomics data to an LLM/MCP workflow. The repository currently focuses on MS-DIAL binary outputs such as `.arf`, `.arf2`, `.EIC.aef`, and `.pai2`, plus a Tkinter LC-MS/MS viewer and an MS-CleanP-style feature cleanup module.

## What is included

- `server.py` - FastMCP server named `ms-data-parser`. It exposes tools for listing files, parsing MS-DIAL data, running PCA summaries, querying EIC data by m/z or RT, and applying MS-CleanP filtering.
- `test_arf.py` - `.arf` parser and command-line analysis script. It can export peak properties, run PCA, create PCA plots, group replicates, plot peak-height distributions, and inspect top loading features.
- `test_arf2.py` - `.arf2` parser and text summary generator.
- `test_eic_aef.py` - `.EIC.aef` parser with EIC summaries and m/z or RT search helpers.
- `test_pai2.py` - `.pai2` parser with feature filtering, PCA summaries, top-contributor extraction, and metabolite detail lookup.
- `test_dcl.py` - `.dcl` (MSDecResult) parser. Reads MS-DIAL's custom binary deconvoluted MS/MS spectra (not msgpack/lz4), and can attach those spectra to `.pai2` peaks by index (`attach_msms_to_features`).
- `data_config.py` - Single source of truth for the data search directory. Returns `<project>/data` by default, or the path in the `LIPIDMIX_DATA_DIR` environment variable when set. Used by `server.py` and all `test_*` parsers.
- `MS_CleanP.py` - MS-CleanP-inspired cleanup pipeline for MS-DIAL-like feature tables. It supports blank filtering, RSD filtering, mass-defect checks, isotope/adduct/loss link inference, graph clustering, representative peak selection, and CSV export.
- `lcmsms_viewer.py` - Tkinter desktop viewer for LC-MS/MS data processing, plotting, grid rendering, library building, mzML conversion helpers, and run history.
- `check.py` - Scratch / pseudo-workspace for temporary experiments and quick code verification. Treat it as a throwaway sandbox: write exploratory code here, and once something works, copy the good code into its proper module and clear `check.py` back out. Nothing should depend on `check.py`, and it is not expected to retain content between tasks.
- `docs/HISTRY.md` - Development and fact log. Record the timeline of work, findings from investigations, and design decisions here. Fine-grained development history goes in this file.
- `docs/task.md` - Task management. Track development progress, plans, and task status (`TODO`/`DOING`/`DONE`/`HOLD`) here. Detailed facts behind each task live in `docs/HISTRY.md`.
- `docs/*.md` (schema files) - MS-DIAL C# MessagePack schema definitions used as the authoritative index reference for the binary parsers: `AlignmentSpotProperty.md` (→ `.arf2`), `AlignmentChromPeakFeature.md` (→ `.arf` per-sample rows), `ChromatogramPeakFeature.md` (→ `.pai2`).
- `data/` - Default MS-DIAL data directory used by the parsers and MCP tools. Override with the `LIPIDMIX_DATA_DIR` environment variable to point the parsers and `server.py` at any MS-DIAL output folder (e.g. the folder holding the raw acquisition files). All file discovery goes through `data_config.get_data_dir()`.
- `*.png`, `pca_result.json`, `output_peaks.csv` - Generated analysis artifacts from prior runs.
- `debug_*.py` and `test_*.py` scripts - Development and parser-inspection utilities.
- `homework1_260408.csproj` / `.sln` - A small .NET project scaffold that is separate from the Python MS-DIAL tooling.

## Supported data formats

The current Python workflow targets these MS-DIAL outputs:

- `.arf` - Alignment result peak properties. Used for feature extraction, PCA, plotting, and MS-CleanP filtering.
- `.arf2` - Alignment result format parsed with LZ4/msgpack deserialization.
- `.EIC.aef` - Extracted ion chromatogram data. Used for summary statistics and peak searches by m/z or retention time.
- `.pai2` - Peak annotation/feature data. Used for PCA summaries, top metabolite contributors, and detail inspection.
- `.dcl` - Deconvoluted MS/MS spectra (MSDecResult). Custom binary format (not msgpack/lz4).

## 各パーサが取得できる情報（日本語・網羅）

MS-DIAL のアラインメント結果は「**個別測定 → サンプル別ピーク → スポット代表**」の3層に分かれ、
さらに MS/MS スペクトル本体は別ファイル（`.dcl`）に格納される。各パーサが取得できる情報は以下の通り。
各ファイルが対応する C# スキーマ（Key インデックスの正解表）は `docs/*.md` を参照。

### `test_arf.py` — `.arf`（アライン後・サンプル別ピーク / `AlignmentChromPeakFeature`）
**1スポット = 全サンプル分の検出ピーク行**を持つ。サンプル間比較・PCA の主データ源。

- `deserialize()` … スポット辞書のリスト。各スポット: `MasterAlignmentID` / `AlignmentID` /
  `RT`（保持時間）/ `MassCenter`（代表 m/z）/ `IonMode`（極性）/ `Name`（代表アノテ名）/
  `HeightAverage`（平均ピーク高）/ `AlignedPeakProperties`（サンプル別の生ピーク行）。
- `extract_peak_properties()` … サンプル×スポットを展開した DataFrame。1行 = 1サンプルの1ピーク:
  - 識別: `MasterAlignmentID` / `AlignmentID` / `SampleIndex` / `FileName`（サンプル名）/
    `FileID`（サンプル番号 0..N）/ `PeakID` / `MasterPeakID`
  - 定量: `PeakHeight`（ピーク高）/ `PeakArea`（面積・ゼロ基準）/ `PeakAreaAboveBaseline`（面積・ベースライン基準）
  - 位置: `PeakMZ`（m/z）/ `PeakRT`（保持時間）/ `SpotMassCenter` / `SpotRT`
  - 品質/状態: `SignalToNoise`（S/N）/ `IsMsms`（MS/MS取得有無）/ `IsGapFilled`（補間値=未検出を埋めた値か）
  - アノテ: `IonMode` / `CompoundName`
  - ※ `MasterPeakID = -2` かつ `IsGapFilled = True` のサンプルはギャップフィル（そのサンプルで未検出）。
- `build_pca_matrix()` + `run_pca()` … サンプル×特徴量の PCA。**説明分散比 / サンプル別スコア(PC1,PC2) /
  Loadings 寄与上位（正負, アノテ・m/z・RT付き）** を取得。
- CLI（`python test_arf.py --pca`）でデシリアライズ結果と PCA 結果を表示。ファイルは `--file`/`--index` で指定。

### `test_arf2.py` — `.arf2`（アライン後・スポット代表 / `AlignmentSpotProperty`）
**サンプル別の生データを持たない軽量なメタ層**（= LLM に渡しやすい層）。PCA は不可。

- `deserialize()` / `extract_arf2_data()` … 1スポット = 22項目:
  - 識別/位置: `MasterAlignmentID` / `AlignmentID` / `RT` / `MassCenter` / `IonMode` /
    `MassMin` / `MassMax`
  - 同定: `Name`（代表名）/ `Formula`（組成式）/ `Ontology`（**脂質クラス**: FA, PC, TG…）/
    `SMILES` / `InChIKey` / `AdductType`（付加体 `[M-H]-` 等）
  - 強度: `HeightAverage` / `HeightMin` / `HeightMax`
  - 品質: `SignalToNoiseAve` / `SignalToNoiseMax` / `SignalToNoiseMin` / `PeakWidthAverage` /
    `FillPercentage`（検出されたサンプルの割合）/ `MonoIsotopicPercentage`
- `summarize_arf2_data()` / `generate_text_summary()` … 総スポット数 / アノテーション率 /
  RT・m/z 範囲 / 強度中央値 / イオンモード分布 / **S/N 中央値 / 脂質クラス分布(上位)**。
- `format_spots_as_table(delimiter)` … 全スポットを CSV/TSV 化（LLM へ渡す軽量符号化。整形 JSON より大幅に低トークン）。

### `test_pai2.py` — `.pai2`（個別測定・検出ピーク / `ChromatogramPeakFeature`）
**1ファイル = 1サンプルの全検出ピーク**（アライン前）。サンプル単体の詳細・MS/MS 参照を持つ。

- `deserialize()` / `_convert_to_peakfeature()` … 1ピークあたり:
  - 位置/定量: `time` / `time_left` / `time_right`（保持時間と両端）/ `m/z` /
    `peak_height`（と左右）/ `peak_area` / `peak_area_above_baseline`
  - 品質: `S/N` / `id`
  - アノテ: `ion_mode` / `name` / `formula` / `ontology` / `smiles` / `inchikey` /
    `adduct` / `collision_cross_section`（CCS）/ `comment`
  - MS/MS: `has_msms`（取得有無）/ `ms2_raw_id` / `collision_energies`（衝突エネルギー）/
    `msms_peak_count` / `msms_spectrum`（フラグメント列。**本体は `.pai2` には無く `.dcl` 側**。
    `test_dcl.attach_msms_to_features()` で充填する）
- `perform_pca_summary()` … ピーク群の PCA（説明分散比・スコア画像など）。
- `filter_features_by_params()` … `min_intensity` / `min_sn` 等でフィルタ。
- `get_top_contributors()` … PCA 主成分への寄与上位ピーク。
- `inspect_metabolite_details()` … ID／名前指定で 1 ピークの強度・S/N・MS/MS 相当情報を取得。

### `test_dcl.py` — `.dcl`（デコンボリューション済み MS/MS / `MSDecResult`）
**MS/MS スペクトル本体**。独自バイナリ（msgpack/lz4 ではない）。`.pai2` とインデックス完全一致。

- `deserialize_dcl(path, include_spectrum, top_n_peaks)` … 1結果あたり:
  - 識別/対応: `dcl_index`（= `.pai2` の `MasterPeakID`）/ `scan_id` / `raw_spec_id`
  - 位置/定量: `precursor_mz`（プリカーサ m/z）/ `ion_mode` / `rt` / `model_peak_height`
  - 品質: `signal_to_noise` / `estimated_noise`
  - **MS/MS**: `n_msms_peaks` / `msms_spectrum`（`[m/z, intensity]` のリスト。`top_n_peaks` で間引き可）
- `summarize_dcl()` … 総結果数 / MS/MS 保有率 / フラグメント数の中央値・最大 / m/z・RT 範囲。
- `get_msms_by_precursor()` … プリカーサ m/z（任意で RT）で MS/MS を検索。
- `find_dcl_for_pai2()` … `.pai2` と同名の `.dcl` を探索。
- `attach_msms_to_features()` … `.pai2` ピークに実フラグメントを索引対応で付与（m/z 差で安全弁）。

### `test_eic_aef.py` — `.EIC.aef`（抽出イオンクロマトグラム / EIC）
スポットごとの **EIC（クロマトグラム）と各サンプルのピークトップ**を持つ。

- `parse_eic_aef_css1(path, include_chromatogram)` … 1スポット: `spot_id` / `rt` / `ri` /
  `mz` / `drift` / `main_type` / `num_samples` / `samples`（各サンプル: `file_id` /
  `peak_top`（ピークトップ強度）/ `num_peaks` / `mean_intensity` / `max_intensity` /
  任意で `chromatogram`（生クロマト点列））。
- `summarize_eic_data()` … スポット数 / RT・m/z 範囲 / ピークトップ統計。
- `search_eic_by_mz_range()` / `search_eic_by_rt_range()` … m/z・RT 範囲検索。
- `top_eic_spots_by_peak_top()` … ピークトップ強度の上位スポット抽出。

### 共通: データ探索先の指定
全パーサと `server.py` は探索ディレクトリを `data_config.get_data_dir()` から取得する。
環境変数 `LIPIDMIX_DATA_DIR` を設定するとそのフォルダ（MS-DIAL の出力一式）を対象にできる
（未設定時は `<project>/data`）。

## Setup

Create or activate a Python environment, then install the checked-in dependencies:

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` currently lists:

```text
fastmcp
msgpack
lz4
numpy
matplotlib
pandas
scikit-learn
```

`lcmsms_viewer.py` also imports Pillow (`PIL`) and may use optional mzML/conversion tooling such as `pyteomics` or ProteoWizard `msconvert`, depending on the workflow. Install those separately if you use the viewer features that require them.

The original MS-CleanR bridge is optional. Install its Python dependency only
when you plan to call `mscleanp_filter` with the R-backed workflow:

```bash
python -m pip install -r requirements-mscleanr.txt
```

## FastMCP server

Start the MCP server with:

```bash
python server.py
```

The server looks for example inputs in the repository `data/` directory unless a tool call provides an explicit file path.

Available MCP tools include:

- `list_data_files(extension=None, directory=None)` - List files in the given `directory` (defaults to the configured data directory: `LIPIDMIX_DATA_DIR` or `<project>/data`), optionally filtered by extension.
- `pai2_parser(file_path, filter_threshold=None)` - Parse `.pai2`, run PCA summary generation, and cache the parsed session.
- `pai2_get_top_metabolites(top_n=10)` - Return top PCA contributors from the latest `.pai2` analysis.
- `pai2_inspect_metabolite_details(metabolite_id=None, metabolite_name=None)` - Inspect a cached `.pai2` metabolite by ID or name.
- `pai2_update_analysis_filter(min_intensity=0.0, min_sn=0.0)` - Re-filter cached `.pai2` features and rerun PCA.
- `arf_parser(file_path=None, props=["height"], components=None, top_features=10)` - Parse `.arf`, build a PCA matrix, and return PCA summary/plot data.
- `arf_re_pca(...)` - Rerun PCA against the cached `.arf` session with updated settings.
- `arf2_parser(file_path=None)` - Parse and summarize `.arf2`.
- `eicaef_parser(file_path=None)` - Parse and summarize `.EIC.aef`.
- `eicaef_top_peak_tops(file_path=None, top_n=20)` - Return EIC spots ranked by peak-top intensity.
- `eicaef_search_by_mz_range(file_path=None, min_mz=0.0, max_mz=1000.0, max_results=20)` - Search EIC spots by m/z range.
- `eicaef_search_by_rt_range(file_path=None, min_rt=0.0, max_rt=20.0, max_results=20)` - Search EIC spots by retention-time range.
- `mscleanp_filter(do_blank_sub=True, blank_ratio=3.0, do_rsd_filter=True, rsd_threshold=30.0, components=None)` - Apply MS-CleanP filtering to the cached `.arf` session and rerun PCA.

`mscleanp_filter` expects `.arf` data to have already been loaded with `arf_parser`.

## Command-line examples

Parse an `.arf` file and export peak properties:

```bash
python test_arf.py --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --export output_peaks.csv
```

Run PCA from an `.arf` file and save outputs:

```bash
python test_arf.py --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --pca --output-pca pca_result.json --output-plot pca_plot.png --output-sample-scores sample_scores.png
```

Show top PCA loading features:

```bash
python test_arf.py --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --pca --top-features 10 --props height
```

Run the `.arf2` summary script:

```bash
python test_arf2.py
```

Run MS-CleanP cleanup on a CSV feature table:

```bash
python MS_CleanP.py output_peaks.csv --output-prefix cleanp --blank-ratio 3.0 --rsd 30.0 --ion-mode unknown
```

Launch the LC-MS/MS desktop viewer:

```bash
python lcmsms_viewer.py
```

## Data and output conventions

- Put MS-DIAL example files in `data/` when using the MCP server defaults.
- The parser scripts can also accept explicit paths where implemented.
- Generated plots and CSV/JSON outputs are written to the current working directory unless an output path is provided.
- The repository already contains several generated artifacts, including `pca_plot.png`, `sample_scores.png`, `ether_pe_grouped.png`, `pca_result.json`, and `output_peaks.csv`.

## Current caveats

- Several source comments and strings contain mojibake, but many user-facing Japanese strings in parser arguments are still readable in context.
- `fastmcp_msdial_cli.py` imports `msdial_reader`, but no `msdial_reader.py` source file is present in the current directory. Treat `server.py` as the active MCP entry point unless that module is restored.
- Some scripts are development utilities and may assume files exist in `data/` or use hard-coded example search paths.
- The checked-in `requirements.txt` covers the core parser/server dependencies, but the Tkinter viewer may need extra packages or external conversion tools for specific workflows.
