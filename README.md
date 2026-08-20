# Lipidmix with LLM

Python prototypes for parsing, analyzing, and exposing MS-DIAL lipidomics data to an LLM/MCP workflow. The repository focuses on MS-DIAL binary outputs such as `.arf`, `.arf2`, `.EIC.aef`, and `.pai2`, and adds a knowledge/workflow accumulation layer (`knowledge/`, `playbook/`) surfaced to the LLM through MCP resources.

## What is included

- `server.py` - FastMCP server named `ms-data-parser`. It exposes tools for listing files, loading a dataset (`load_dataset`), parsing MS-DIAL data, running PCA summaries, and querying EIC data by m/z or RT. It also serves the knowledge/playbook indexes as MCP resources. Implementation lives under `lipidmix/`; `server.py` stays at the repo root because `.mcp.json` and `.vscode/mcp.json` point at it by absolute path.
- `lipidmix/corpus/knowledge_store.py` - Pure-logic layer for the accumulation notes: parses note frontmatter, builds the dynamic `knowledge`/`playbook` indexes, and expands a note with its `[[link]]` neighbors under a structural budget.
- `lipidmix/arf/reader.py` - `.arf` parser and command-line analysis script. It can export peak properties, run PCA, create PCA plots, group replicates, plot peak-height distributions, and inspect top loading features.
- `lipidmix/msdial/tags.py` - Parser and filter engine for MS-DIAL `*_tags.xml` sidecars. It joins per-sample tags by `FileName` + `MasterPeakID` and alignment tags by `MasterAlignmentID`.
- `lipidmix/msdial/classes.py` - Reads user-defined Class ID values (`AnalysisFileClass`) from `.mddata`, resolves `.mddata` from `.mdproject` or the ARF directory, and filters ARF sample rows before PCA.
- `lipidmix/arf2/reader.py` - `.arf2` parser and text summary generator.
- `lipidmix/eic/reader.py` - `.EIC.aef` parser with EIC summaries and m/z or RT search helpers.
- `lipidmix/plots/eic.py` - Builds the renderer-neutral `lipidmix.eic.v1` line-plot payload and renders it with matplotlib only for an explicitly requested PNG save.
- `lipidmix/plots/volcano.py` - Builds the renderer-neutral `lipidmix.volcano.v1` scatter payload from the latest two-group differential result, keeping every `up`/`down` point and thinning only `ns` points.
- `lipidmix/pai2/reader.py` - `.pai2` parser with feature filtering, PCA summaries, top-contributor extraction, and metabolite detail lookup.
- `lipidmix/dcl/reader.py` - `.dcl` (MSDecResult) parser. Reads MS-DIAL's custom binary deconvoluted MS/MS spectra (not msgpack/lz4), and can attach those spectra to `.pai2` peaks by index (`attach_msms_to_features`).
- `lipidmix/core/data_config.py` - Single source of truth for the data search directory. Returns `<project>/data` by default, or the path in the `LIPIDMIX_DATA_DIR` environment variable when set. Used by `server.py` and all `*_reader` parsers.
- `check.py` - Scratch / pseudo-workspace for temporary experiments and quick code verification. Treat it as a throwaway sandbox: write exploratory code here, and once something works, copy the good code into its proper module and clear `check.py` back out. Nothing should depend on `check.py`, and it is not expected to retain content between tasks.
- `docs/HISTRY.md` - Development and fact log. Record the timeline of work, findings from investigations, and design decisions here. Fine-grained development history goes in this file.
- `docs/task.md` - Task management. Track development progress, plans, and task status (`TODO`/`DOING`/`DONE`/`HOLD`) here. Detailed facts behind each task live in `docs/HISTRY.md`.
- `docs/schema/*.md` - MS-DIAL C# MessagePack schema definitions used as the authoritative index reference for the binary parsers: `AlignmentSpotProperty.md` (→ `.arf2`), `AlignmentChromPeakFeature.md` (→ `.arf` per-sample rows), `ChromatogramPeakFeature.md` (→ `.pai2`). These are the only first-hand record of the MessagePack key numbers; consult them before changing any reader's index constants.
- `data/` - Default MS-DIAL data directory used by the parsers and MCP tools. Override with the `LIPIDMIX_DATA_DIR` environment variable to point the parsers and `server.py` at any MS-DIAL output folder (e.g. the folder holding the raw acquisition files). All file discovery goes through `lipidmix.core.data_config.get_data_dir()`.
- `*.png`, `pca_result.json`, `output_peaks.csv` - Generated analysis artifacts from prior runs.
- `knowledge/`, `playbook/`, `analyses/` - Accumulation layer. `knowledge/` holds literature-derived notes (citation required), `playbook/` holds reusable analysis workflows, and `analyses/` holds per-experiment objective records. See `docs/HISTRY.md` (2026-06-13).
- `tests/` - Unit tests (`python -m unittest discover -s tests -t .`, run from the repo root). The parser modules live under `lipidmix/<format>/reader.py`; only `server.py` and `check.py` remain at the repo root.
- `lipidmix/` - 実装本体。入力形式ごと（`arf/` `arf2/` `pai2/` `dcl/` `eic/`）に
  パーサ（`reader.py`）と MCP ツール（`tools.py`）を置き、形式に依存しない数値処理を
  `analysis/`、レンダラ中立の描画 payload を `plots/`、FastMCP インスタンスと
  セッション状態を `core/` に分けている。各ツールがどのファイルのどの関数を
  どの順に呼ぶかは `docs/workflow/` を参照。
- `docs/workflow/*.md` - パーサ系・プロット系 28 ツールの呼び出し連鎖。
  行番号は持たず、参照の実在は `tests/test_workflow_docs.py` が AST で検証している。

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

### `lipidmix/arf/reader.py` — `.arf`（アライン後・サンプル別ピーク / `AlignmentChromPeakFeature`）

> 呼び出し順は [docs/workflow/arf.md](docs/workflow/arf.md) を参照。

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
- CLI（`python -m lipidmix.arf.reader --pca`）でデシリアライズ結果と PCA 結果を表示。ファイルは `--file`/`--index` で指定。

### `lipidmix/arf2/reader.py` — `.arf2`（アライン後・スポット代表 / `AlignmentSpotProperty`）

> 呼び出し順は [docs/workflow/arf2.md](docs/workflow/arf2.md) を参照。

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

### `lipidmix/pai2/reader.py` — `.pai2`（個別測定・検出ピーク / `ChromatogramPeakFeature`）

> 呼び出し順は [docs/workflow/pai2.md](docs/workflow/pai2.md) を参照。

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
- `filter_features_by_params()` … `min_intensity` / `min_sn` 等でフィルタ。
- `inspect_peak_details()` … ID／名前指定で 1 ピークの強度・S/N・MS/MS 相当情報を取得。
- `summarize_pai2_inventory()` … 注釈状況・m/z・RT・強度・S/N の分布と強度上位ピークの在庫要約。

（`perform_pca_summary()` / `get_top_contributors()` は撤去済み。PAI2 は単一測定
ファイルでサンプル間比較ができず、`[RT, m/z, Height]` の3変数PCAは生物学的仮説を
検定しないため。詳細は `docs/output_format/pai2.md` の 5.3 節。）

### `lipidmix/dcl/reader.py` — `.dcl`（デコンボリューション済み MS/MS / `MSDecResult`）

> 呼び出し順は [docs/workflow/dcl.md](docs/workflow/dcl.md) を参照。

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

### `lipidmix/eic/reader.py` — `.EIC.aef`（抽出イオンクロマトグラム / EIC）

> 呼び出し順は [docs/workflow/eic.md](docs/workflow/eic.md) を参照。

スポットごとの **EIC（クロマトグラム）と各サンプルのピークトップ**を持つ。

- `parse_eic_aef_css1(path, include_chromatogram)` … 1スポット: `spot_id` / `rt` / `ri` /
  `mz` / `drift` / `main_type` / `num_samples` / `samples`（各サンプル: `file_id` /
  `peak_top`（ピークトップ強度）/ `num_peaks` / `mean_intensity` / `max_intensity` /
  任意で `chromatogram`（生クロマト点列））。
- `summarize_eic_data()` … スポット数 / RT・m/z 範囲 / ピークトップ統計。
- `search_eic_by_mz_range()` / `search_eic_by_rt_range()` … m/z・RT 範囲検索。
- `top_eic_spots_by_peak_top()` … ピークトップ強度の上位スポット抽出。

### 共通: データ探索先の指定
全パーサと `server.py` は探索ディレクトリを `lipidmix.core.data_config.get_data_dir()` から取得する。
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
- `pai2_parser(file_path, filter_threshold=None)` - Parse one `.pai2` (a single measurement file's peak list) and return a peak-inventory summary: annotation counts, m/z / RT / height / S/N distributions, and top peaks by height. No PCA — PAI2 is a single sample, so cross-sample (omics) PCA is not meaningful here; use ARF/ARF2 for multi-sample multivariate analysis. MS/MS lives in the sibling `.dcl` (`dcl_index` matches list order) and is **attached automatically** — the result reports it under `summary.msms_attachment`.
- `dcl_parser(file_path=None, top_n_peaks=None, preview=5)` - Parse one `.dcl` (MSDecResult: deconvoluted MS/MS) and return an inventory summary plus the leading results with their top fragments. `top_n_peaks` defaults to 10; pass `0` for every fragment.
- `dcl_find_msms(precursor_mz, file_path=None, rt=None, mz_tol=0.01, rt_tol=0.2, top_n_peaks=None)` - Look up the MS/MS for a precursor m/z (optionally disambiguated by RT) to check whether the fragments an annotation predicts are actually present. A `not_found` result means "no MS/MS was acquired here", **not** "the fragments are absent".
- `pai2_inspect_peak(peak_id=None, peak_name=None)` - Inspect a cached `.pai2` peak by ID or name.
- `arf_parser(..., min_intensity=0.0, annotation_keyword=None, class_ids=None, group_levels=None, tag_labels=None, ...)` - Parse `.arf`, auto-load adjacent `.mddata` and tag sidecars, apply filters (intensity / annotation keyword / Class ID / MS-DIAL tags), honor manual exclusions (`arf_exclude`), color PCA points by group, and run PCA. The single entry point for PCA on the raw-spot matrix: **to re-run PCA with different filters, call this again** (the file is session-cached, so no re-parse). For PCA on the normalized/QC/imputed matrix, use `arf_preprocess` → `arf_pca_preprocessed`.
- `arf_list_classes()` - List Class ID values, sample counts, the per-position factor-value vocabulary (`factors_by_position`), and `sample_token_vocabulary` — the factor tokens discovered from **sample names as well as Class IDs**, with per-token sample counts and role breakdown. Use it to find what you can pass to `class_ids` / `group_levels` / `group_factors` / `group_a` / `group_b`.
- `sample_search(specs=None, directory=None, extensions=None, include_roles=None)` - Search samples by factor token (e.g. `["ILG_6h"]`) and return each match's `file_id` plus the real `.pai2` / `.dcl` paths, so pai2 / dcl / EIC tools can be pointed at a condition rather than a hand-picked filename. Omit `specs` to get the full token vocabulary. Works before any ARF is loaded.
- `arf_list_tags()` - List discovered tag definitions, matched sample files, and tag assignment counts for the cached `.arf` session.
- `arf_list_sample_roles()` - Classify the cached ARF's samples into `sample`/`qc`/`blank` roles (from filename and Class ID tokens) before applying any preprocessing.
- `arf_preprocess(normalize="none", blank_min_fold=None, drift_correct=False, max_qc_rsd=None, impute="half_min", props=None)` - Apply an opt-in QC/normalization/imputation recipe to the loaded ARF matrix and cache the result as `session.feature_matrix` for downstream PCA/differential analysis. Blank samples are used as the background reference and then **dropped from the analysis matrix** (they would otherwise dominate PC1); QC samples are kept so PCA can still show QC clustering. The removal is reported in `excluded_from_matrix` and as a caveat.
- `arf_pca_preprocessed(components=None, top_features=10, log_transform=False, group_levels=None)` - Run PCA on the preprocessed matrix produced by `arf_preprocess` (the "post-preprocessing" PCA entry point, independent of the raw-spot `arf_parser` path).
- `arf_differential(group_a=None, group_b=None, q_threshold=0.05, log2fc_threshold=1.0)` - Two-group differential analysis over the preprocessed matrix: Welch t-test + log2 fold change. Both `group_a` and `group_b` are required (full Class IDs or partial factor-token pools, e.g. `group_a="24M", group_b="9w"`). Multi-group one-way ANOVA is currently disabled: MS-DIAL metadata carries no factor→level mapping, so selecting a factor cannot be done safely — carve out the two groups of interest instead. QC and blank samples are excluded from both compared groups (a QC whose Class ID contains the requested factor token would otherwise be pooled in silently). Adds BH-FDR, volcano points, and mandatory caveats (group⟂batch confounding — assessed on the **pooled** groups actually compared, not on the finer per-Class-ID labels — small n, normalization status).
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
- `arf_plot_volcano(max_points=3000, title=None)` - Return the latest two-group differential result as a renderer-neutral `lipidmix.volcano.v1` payload. Read-only, writes no file. `up`/`down` points are always complete; only `ns` points are thinned, and every count is reported in `selection`.
- `save_pca_figure(analysis_id, title=None)` - Only when the user explicitly requests PNG output, render the latest session PCA result to `reports/figures/<analysis_id>_pca.png` and return a relative path to embed as `![PCA](figures/<analysis_id>_pca.png)`.
- `save_volcano_figure(analysis_id, title=None)` - Only when the user explicitly requests PNG output, render the latest two-group differential result as a volcano plot to `reports/figures/<analysis_id>_volcano.png`. Unlike `arf_plot_volcano`, this draws every feature without thinning.
- `eic_plot_chromatograms(spot_id, file_path=None, file_ids=None, normalize="none", title=None)` - Read selected traces for one CSS1 EIC spot by direct pointer-table access and return structured `lipidmix.eic.v1` plot information. The tool does not render or write an image; each MCP client chooses its own UI renderer.
- `eic_plot_compounds(file_id, names=None, ontologies=None, file_path=None, arf2_path=None, normalize="none", top_n=24, title=None)` - Overlay several identified compounds' EIC traces for ONE sample and return structured `lipidmix.eic.multi.v1` plot information. Compounds are selected from ARF2 `Name` (case-insensitive substring) and `Ontology` (exact), and each `AlignmentID` is verified against the EIC spot RT and m/z; anything excluded is listed with a reason in `selection.dropped`. The tool renders no image and writes no file.
- `save_eic_figure(analysis_id, title=None)` - Only when the user explicitly requests PNG output, render the latest EIC plot payload to `reports/figures/<analysis_id>_eic.png`. This is a separate write operation from interactive plotting.

The server also exposes MCP **resources**: `lipidmix://docs/output-format` (the shared ontology core) and `lipidmix://docs/output-format/{topic}` for `arf`, `arf2`, `pai2`, `dcl`, `eic`, `identity` (per-parser field definitions, fetched on demand — parser output carries a pointer to its own topic until it has been read), `lipidmix://knowledge/index` and `lipidmix://playbook/index` (dynamic, one line per note), `lipidmix://{knowledge,playbook}/expand/{slug}` (a note plus its 1-hop `[[link]]` neighbors within a structural budget), and `lipidmix://knowledge/inbox` (pending discovery notes awaiting review).

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
python -m lipidmix.arf.reader --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --export output_peaks.csv
```

Run PCA from an `.arf` file and save outputs:

```bash
python -m lipidmix.arf.reader --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --pca --output-pca pca_result.json --output-plot pca_plot.png --output-sample-scores sample_scores.png
```

Show top PCA loading features:

```bash
python -m lipidmix.arf.reader --file "data/AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf" --pca --top-features 10 --props height
```

Run the `.arf2` summary script:

```bash
python -m lipidmix.arf2.reader
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
