# Lipidmix with LLM

Python prototypes for parsing, analyzing, and exposing MS-DIAL lipidomics data to an LLM/MCP workflow. The repository focuses on MS-DIAL binary outputs such as `.arf`, `.arf2`, `.EIC.aef`, and `.pai2`, and adds a knowledge/workflow accumulation layer (`knowledge/`, `playbook/`) surfaced to the LLM through MCP resources.

## What is included

- `server.py` - FastMCP server named `ms-data-parser`. It exposes tools for listing files, loading a dataset (`load_dataset`), parsing MS-DIAL data, running PCA summaries, and querying EIC data by m/z or RT. It also serves the knowledge/playbook indexes as MCP resources.
- `knowledge_store.py` - Pure-logic layer for the accumulation notes: parses note frontmatter, builds the dynamic `knowledge`/`playbook` indexes, and expands a note with its `[[link]]` neighbors under a structural budget.
- `arf_reader.py` - `.arf` parser and command-line analysis script. It can export peak properties, run PCA, create PCA plots, group replicates, plot peak-height distributions, and inspect top loading features.
- `msdial_tags.py` - Parser and filter engine for MS-DIAL `*_tags.xml` sidecars. It joins per-sample tags by `FileName` + `MasterPeakID` and alignment tags by `MasterAlignmentID`.
- `msdial_classes.py` - Reads user-defined Class ID values (`AnalysisFileClass`) from `.mddata`, resolves `.mddata` from `.mdproject` or the ARF directory, and filters ARF sample rows before PCA.
- `arf2_reader.py` - `.arf2` parser and text summary generator.
- `eic_aef_reader.py` - `.EIC.aef` parser with EIC summaries and m/z or RT search helpers.
- `eic_plot.py` - Builds the renderer-neutral `lipidmix.eic.v1` line-plot payload and renders it with matplotlib only for an explicitly requested PNG save.
- `pai2_reader.py` - `.pai2` parser with feature filtering, PCA summaries, top-contributor extraction, and metabolite detail lookup.
- `dcl_reader.py` - `.dcl` (MSDecResult) parser. Reads MS-DIAL's custom binary deconvoluted MS/MS spectra (not msgpack/lz4), and can attach those spectra to `.pai2` peaks by index (`attach_msms_to_features`).
- `data_config.py` - Single source of truth for the data search directory. Returns `<project>/data` by default, or the path in the `LIPIDMIX_DATA_DIR` environment variable when set. Used by `server.py` and all `*_reader` parsers.
- `check.py` - Scratch / pseudo-workspace for temporary experiments and quick code verification. Treat it as a throwaway sandbox: write exploratory code here, and once something works, copy the good code into its proper module and clear `check.py` back out. Nothing should depend on `check.py`, and it is not expected to retain content between tasks.
- `docs/HISTRY.md` - Development and fact log. Record the timeline of work, findings from investigations, and design decisions here. Fine-grained development history goes in this file.
- `docs/task.md` - Task management. Track development progress, plans, and task status (`TODO`/`DOING`/`DONE`/`HOLD`) here. Detailed facts behind each task live in `docs/HISTRY.md`.
- `docs/*.md` (schema files) - MS-DIAL C# MessagePack schema definitions used as the authoritative index reference for the binary parsers: `AlignmentSpotProperty.md` (→ `.arf2`), `AlignmentChromPeakFeature.md` (→ `.arf` per-sample rows), `ChromatogramPeakFeature.md` (→ `.pai2`).
- `data/` - Default MS-DIAL data directory used by the parsers and MCP tools. Override with the `LIPIDMIX_DATA_DIR` environment variable to point the parsers and `server.py` at any MS-DIAL output folder (e.g. the folder holding the raw acquisition files). All file discovery goes through `data_config.get_data_dir()`.
- `*.png`, `pca_result.json`, `output_peaks.csv` - Generated analysis artifacts from prior runs.
- `knowledge/`, `playbook/`, `analyses/` - Accumulation layer. `knowledge/` holds literature-derived notes (citation required), `playbook/` holds reusable analysis workflows, and `analyses/` holds per-experiment objective records. See `docs/HISTRY.md` (2026-06-13).
- `tests/` - Unit tests (`python -m unittest discover -s tests -t .`). The parser modules `arf_reader.py`/`arf2_reader.py`/`eic_aef_reader.py`/`pai2_reader.py`/`dcl_reader.py` live at the repo root (formerly misleadingly named `test_*.py`).

## Supported data formats

The current Python workflow targets these MS-DIAL outputs:

- `.arf` - Alignment result peak properties. Used for feature extraction, PCA, and plotting.
- `.arf2` - Alignment result format parsed with LZ4/msgpack deserialization.
- `.EIC.aef` - Extracted ion chromatogram data. Used for summary statistics and peak searches by m/z or retention time.
- `.pai2` - Peak annotation/feature data. Used for PCA summaries, top metabolite contributors, and detail inspection.
- `.dcl` - Deconvoluted MS/MS spectra (MSDecResult). Custom binary format (not msgpack/lz4).

## 各パーサが取得できる情報（日本語・網羅）

MS-DIAL のアラインメント結果は「**個別測定 → サンプル別ピーク → スポット代表**」の3層に分かれ、
さらに MS/MS スペクトル本体は別ファイル（`.dcl`）に格納される。各パーサが取得できる情報は以下の通り。
各ファイルが対応する C# スキーマ（Key インデックスの正解表）は `docs/*.md` を参照。

### `arf_reader.py` — `.arf`（アライン後・サンプル別ピーク / `AlignmentChromPeakFeature`）
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
- CLI（`python arf_reader.py --pca`）でデシリアライズ結果と PCA 結果を表示。ファイルは `--file`/`--index` で指定。

### `arf2_reader.py` — `.arf2`（アライン後・スポット代表 / `AlignmentSpotProperty`）
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

### `pai2_reader.py` — `.pai2`（個別測定・検出ピーク / `ChromatogramPeakFeature`）
**1ファイル = 1サンプルの全検出ピーク**（アライン前）。サンプル単体の詳細・MS/MS 参照を持つ。

- `deserialize()` / `_convert_to_peakfeature()` … 1ピークあたり:
  - 位置/定量: `time` / `time_left` / `time_right`（保持時間と両端）/ `m/z` /
    `peak_height`（と左右）/ `peak_area` / `peak_area_above_baseline`
  - 品質: `S/N` / `id`
  - アノテ: `ion_mode` / `name` / `formula` / `ontology` / `smiles` / `inchikey` /
    `adduct` / `collision_cross_section`（CCS）/ `comment`
  - MS/MS: `has_msms`（取得有無）/ `ms2_raw_id` / `collision_energies`（衝突エネルギー）/
    `msms_peak_count` / `msms_spectrum`（フラグメント列。**本体は `.pai2` には無く `.dcl` 側**。
    `dcl_reader.attach_msms_to_features()` で充填する）
- `perform_pca_summary()` … ピーク群の PCA（説明分散比・スコア画像など）。
- `filter_features_by_params()` … `min_intensity` / `min_sn` 等でフィルタ。
- `get_top_contributors()` … PCA 主成分への寄与上位ピーク。
- `inspect_metabolite_details()` … ID／名前指定で 1 ピークの強度・S/N・MS/MS 相当情報を取得。

### `dcl_reader.py` — `.dcl`（デコンボリューション済み MS/MS / `MSDecResult`）
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

### `eic_aef_reader.py` — `.EIC.aef`（抽出イオンクロマトグラム / EIC）
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

## FastMCP server

Start the MCP server with:

```bash
python server.py
```

The server looks for example inputs in the repository `data/` directory unless a tool call provides an explicit file path.

For team use, keep this server local on each member's machine and point only
`LIPIDMIX_KNOWLEDGE_DIR` at the shared NAS knowledge folder. See
[`DEPLOY.md`](DEPLOY.md) and
[`docs/local_shared_knowledge_setup.md`](docs/local_shared_knowledge_setup.md).

Available MCP tools include:

- `load_dataset(directory=None)` - Entry point. Given an MS-DIAL output folder, runs the standard initial analysis (arf2 overview -> arf PCA), auto-selecting the latest `*PeakProperties.arf` when duplicates exist, and primes the session. When the folder mixes files from several MS-DIAL runs (multiple dates/batches), every resolver auto-selects the **latest batch** by the `AlignmentResult_<timestamp>` embedded in the filenames (across `.arf`/`.arf2`/`.pai2`/`.aef`); `load_dataset` reports which batch it chose and skips older ones.
- `list_data_files(extension=None, directory=None)` - List files in the given `directory` (defaults to the configured data directory: `LIPIDMIX_DATA_DIR` or `<project>/data`), optionally filtered by extension.
- `pai2_parser(file_path, filter_threshold=None)` - Parse one `.pai2` (a single measurement file's peak list) and return a peak-inventory summary: annotation counts, m/z / RT / height / S/N distributions, and top peaks by height. No PCA — PAI2 is a single sample, so cross-sample (omics) PCA is not meaningful here; use ARF/ARF2 for multi-sample multivariate analysis. MS/MS lives in the sibling `.dcl` (`dcl_index` matches list order).
- `pai2_inspect_metabolite_details(metabolite_id=None, metabolite_name=None)` - Inspect a cached `.pai2` metabolite by ID or name.
- `arf_parser(..., class_ids=None, group_levels=None, tag_labels=None, ...)` - Parse `.arf`, auto-load adjacent `.mddata` and tag sidecars, optionally filter samples by Class ID (partial factor specs allowed), color PCA points by group, and build a PCA matrix.
- `arf_list_classes()` - List Class ID values, sample counts, and the per-position factor-value vocabulary (`factors_by_position`) discovered from the cached ARF dataset's `.mddata`.
- `arf_list_tags()` - List discovered tag definitions, matched sample files, and tag assignment counts for the cached `.arf` session.
- `arf_re_pca(..., class_ids=None, group_levels=None, tag_labels=None, ...)` - Rerun PCA with intensity, annotation, Class ID (partial factor specs), and MS-DIAL tag filters, coloring points by group.
- `arf_list_sample_roles()` - Classify the cached ARF's samples into `sample`/`qc`/`blank` roles (from filename and Class ID tokens) before applying any preprocessing.
- `arf_preprocess(normalize="none", blank_min_fold=None, drift_correct=False, max_qc_rsd=None, impute="half_min", props=None)` - Apply an opt-in QC/normalization/imputation recipe to the loaded ARF matrix and cache the result as `session.feature_matrix` for downstream PCA/differential analysis.
- `arf_pca_preprocessed(components=None, top_features=10, log_transform=False, group_levels=None)` - Run PCA on the preprocessed matrix produced by `arf_preprocess` (independent of the raw-matrix `arf_parser`/`arf_re_pca` path).
- `arf_differential(group_a=None, group_b=None, q_threshold=0.05, log2fc_threshold=1.0)` - Two-group differential analysis over the preprocessed matrix: Welch t-test + log2 fold change. Both `group_a` and `group_b` are required (full Class IDs or partial factor-token pools, e.g. `group_a="24M", group_b="9w"`). Multi-group one-way ANOVA is currently disabled: MS-DIAL metadata carries no factor→level mapping, so selecting a factor cannot be done safely — carve out the two groups of interest instead. Adds BH-FDR, volcano points, and mandatory caveats (group⟂batch confounding, small n, normalization status).
- `arf2_parser(file_path=None)` - Parse and summarize `.arf2`.
- `arf2_annotate_identities(file_path=None, max_rows=50)` - Offline identity standardization for the ARF2 spot catalog: GOSLIN-normalized name, bundled RefMet name / LIPID MAPS category, and a conservative MSI level per spot. (`verify_peak_annotation` also gains an `identity_normalization` block combining GOSLIN + reference mapping + an MSI-level heuristic that never asserts Level 1.)
- `eic_parser(file_path=None)` - Parse and summarize `.EIC.aef`.
- `eic_rank_by_max_intensity(file_path=None, top_n=20)` - Return EIC spots ranked by intensity (each sample's max chromatogram intensity, taken across samples). Replaces the old `eicaef_top_peak_tops`, which ranked by `peak_top` (a retention-time coordinate, not intensity).
- `eic_search_by_mz_range(file_path=None, min_mz=0.0, max_mz=1000.0, max_results=20)` - Search EIC spots by m/z range.
- `eic_search_by_rt_range(file_path=None, min_rt=0.0, max_rt=20.0, max_results=20)` - Search EIC spots by retention-time range.

Objective lifecycle and gap-driven literature discovery (see `docs/HISTRY.md`):

- `record_objective(analysis_id, dataset, polarity, groups, comparison, sub_questions, biological_context="", ...)` - Record the user-confirmed experimental objective and its sub-questions under `analyses/`.
- `update_objective(analysis_id, confirmed_objective=None, biological_context=None, status=None, add_subquestions=None)` - Update the objective or append an emergent sub-question.
- `knowledge_coverage(analysis_id)` - Classify each sub-question as COVERED / WEAK / GAP against `knowledge/` (annotates ones already searched).
- `paper_search(query, max_results=10)` - Search Europe PMC (peer-reviewed, abstracts), filtering retractions and duplicates. Queries must be user-confirmed first.
- `ingest_stage(title, abstract, source, found_for, query, ...)` - Quarantine a relevant hit as a speculative note under `knowledge/_inbox`.
- `log_search(analysis_id, subquestion, query, hits, promoted=0)` - Record a search attempt (prevents re-searching the same gap).
- `ingest_review_queue()` / `ingest_promote(slug, claim_strength, links=None)` / `ingest_reject(slug)` - Human review gate; promotion is the only way an `_inbox` note becomes trusted knowledge.

Analysis/interpretation report recording:

- `write_report(analysis_id, dataset, body, status="draft", knowledge_refs=None)` - Overwrite the analysis/interpretation report at `<analysis-folder>/reports/<analysis_id>.md` (falls back to `LIPIDMIX_REPORTS_DIR`, default `<project>/reports`, when the data folder is read-only). `body` is the Markdown body; recommended sections are `## 目的` / `## 実施した解析` / `## 主要な所見` / `## 解釈` / `## 注意点・コンフリクト` / `## 結論`. Keyed by `analysis_id` to the objective record.
- `read_report(analysis_id)` - Read back a past report (analysis folder then fallback) for session continuity.
- `list_reports()` - One-line index (analysis_id / date / status) of existing reports.
- `save_pca_figure(analysis_id, title=None)` - Render the latest session PCA result to `reports/figures/<analysis_id>_pca.png` and return a relative path to embed in the report body as `![PCA](figures/<analysis_id>_pca.png)`.
- `save_volcano_figure(analysis_id, title=None)` - Render the latest two-group differential result (`arf_differential`) as a volcano plot to `reports/figures/<analysis_id>_volcano.png` and return a relative path to embed as `![volcano](figures/<analysis_id>_volcano.png)`.
- `eic_plot_chromatograms(spot_id, file_path=None, file_ids=None, normalize="none", title=None)` - Read selected traces for one CSS1 EIC spot by direct pointer-table access and return structured `lipidmix.eic.v1` plot information. The tool does not render or write an image; each MCP client chooses its own UI renderer.
- `save_eic_figure(analysis_id, title=None)` - Only when the user explicitly requests PNG output, render the latest EIC plot payload to `reports/figures/<analysis_id>_eic.png`. This is a separate write operation from interactive plotting.

The server also exposes MCP **resources**: `lipidmix://docs/output-format` (authoritative parser output reference), `lipidmix://knowledge/index` and `lipidmix://playbook/index` (dynamic, one line per note), `lipidmix://{knowledge,playbook}/expand/{slug}` (a note plus its 1-hop `[[link]]` neighbors within a structural budget), and `lipidmix://knowledge/inbox` (pending discovery notes awaiting review).

MS-DIAL tag filtering supports `any`, `all`, `none`, and `not_all`. Use
`tag_scope="sample_peak"` for the per-sample `*_tags.xml` files generated next
to `.pai2` files, or `tag_scope="alignment_spot"` for the alignment result
sidecar. Sample tag files are matched to ARF `FileName` values after removing
the processing timestamp suffix such as `_202605151012`.
Exact sample-name matches take precedence. Timestamp-stripped matching is used
only when it resolves to exactly one ARF sample; ambiguous or duplicate matches
raise an error. When a sample tag file is missing, the default `error` policy
stops analysis. Set `missing_sample_policy="exclude"` to remove those samples,
or `"untagged"` to treat them explicitly as having no selected tags.

Class ID filtering uses the user-defined values from MS-DIAL's
`Option > File property setting > Class ID`. Call `arf_list_classes()` after
loading an ARF to inspect available values (it also returns `factors_by_position`,
the per-position factor-value vocabulary, to help compose partial specs).

Class IDs are treated as compound, underscore-delimited factors (e.g.
`Cerebellum_gf_AIN` → tokens `Cerebellum`, `gf`, `AIN`). Each entry in `class_ids`
may be a **partial spec**: it matches any Class ID that contains all of the spec's
tokens (AND within a spec, order-independent). Entries are combined with OR, and an
exact full Class ID still matches only itself. Examples: `class_ids=["gf"]` selects
every `*_gf_*` class, `class_ids=["Cerebellum_gf"]` selects `Cerebellum_gf_*`, and
`class_ids=["Cerebellum_gf", "Hippocampus_gf"]` ORs the two. Matching is
case-insensitive; a spec that matches nothing raises an error.

For between-group comparison, PCA points are colored by group. By default the group
is the sample's full Class ID; pass `group_levels=["gf", "spf"]` to collapse the
coloring onto a single factor (samples matching none of the levels become `other`;
matching two or more raises an error). Selection (`class_ids`) and grouping
(`group_levels`) are independent, so e.g. `class_ids=["Cerebellum"],
group_levels=["gf", "spf"]` compares gf vs spf within the cerebellum subset.

## Command-line examples

Parse an `.arf` file and export peak properties:

```bash
python arf_reader.py --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --export output_peaks.csv
```

Run PCA from an `.arf` file and save outputs:

```bash
python arf_reader.py --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --pca --output-pca pca_result.json --output-plot pca_plot.png --output-sample-scores sample_scores.png
```

Show top PCA loading features:

```bash
python arf_reader.py --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --pca --top-features 10 --props height
```

Run the `.arf2` summary script:

```bash
python arf2_reader.py
```

Run the unit tests:

```bash
python -m unittest discover -s tests -t .
```

## Data and output conventions

- Put MS-DIAL example files in `data/` when using the MCP server defaults.
- The parser scripts can also accept explicit paths where implemented.
- Generated plots and CSV/JSON outputs are written to the current working directory unless an output path is provided.
- The repository already contains several generated artifacts, including `pca_plot.png`, `sample_scores.png`, `ether_pe_grouped.png`, `pca_result.json`, and `output_peaks.csv`.

## Current caveats

- `server.py` is the single MCP entry point. (The former `fastmcp_msdial_cli.py` stub, which imported a non-existent `msdial_reader` module, has been removed.)
- Some scripts are development utilities and may assume files exist in `data/` or use hard-coded example search paths.
