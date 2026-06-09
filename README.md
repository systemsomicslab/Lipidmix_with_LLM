# Lipidmix with LLM

Python prototypes for parsing, cleaning, visualizing, and exposing MS-DIAL lipidomics data to an LLM/MCP workflow. The repository currently focuses on MS-DIAL binary outputs such as `.arf`, `.arf2`, `.EIC.aef`, and `.pai2`, plus a Tkinter LC-MS/MS viewer and an MS-CleanP-style feature cleanup module.

## What is included

- `server.py` - FastMCP server named `ms-data-parser`. It exposes tools for listing files, parsing MS-DIAL data, running PCA summaries, querying EIC data by m/z or RT, and applying MS-CleanP filtering.
- `test_arf.py` - `.arf` parser and command-line analysis script. It can export peak properties, run PCA, create PCA plots, group replicates, plot peak-height distributions, and inspect top loading features.
- `test_arf2.py` - `.arf2` parser and text summary generator.
- `test_eic_aef.py` - `.EIC.aef` parser with EIC summaries and m/z or RT search helpers.
- `test_pai2.py` - `.pai2` parser with feature filtering, PCA summaries, top-contributor extraction, and metabolite detail lookup.
- `MS_CleanP.py` - MS-CleanP-inspired cleanup pipeline for MS-DIAL-like feature tables. It supports blank filtering, RSD filtering, mass-defect checks, isotope/adduct/loss link inference, graph clustering, representative peak selection, and CSV export.
- `lcmsms_viewer.py` - Tkinter desktop viewer for LC-MS/MS data processing, plotting, grid rendering, library building, mzML conversion helpers, and run history.
- `data/` - Example MS-DIAL data files used by the parsers and MCP tools.
- `*.png`, `pca_result.json`, `output_peaks.csv` - Generated analysis artifacts from prior runs.
- `debug_*.py` and `test_*.py` scripts - Development and parser-inspection utilities.
- `homework1_260408.csproj` / `.sln` - A small .NET project scaffold that is separate from the Python MS-DIAL tooling.

## Supported data formats

The current Python workflow targets these MS-DIAL outputs:

- `.arf` - Alignment result peak properties. Used for feature extraction, PCA, plotting, and MS-CleanP filtering.
- `.arf2` - Alignment result format parsed with LZ4/msgpack deserialization.
- `.EIC.aef` - Extracted ion chromatogram data. Used for summary statistics and peak searches by m/z or retention time.
- `.pai2` - Peak annotation/feature data. Used for PCA summaries, top metabolite contributors, and detail inspection.

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

- `list_data_files(extension=None)` - List files under `data/`, optionally filtered by extension.
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
