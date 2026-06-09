# Claude Implementation Prompt: Replace MS-CleanP With MS-CleanR via rpy2

You are working in `C:\Users\yuu18\Lipidmix_with_LLM`.

## Goal

Replace the current Python-only `MS_CleanP.py` implementation, which mimics MS-CleanR behavior, with an integration that calls the original R package `eMetaboHUB/MS-CleanR` through `rpy2`.

Keep the existing MCP server design intact:

- Existing ARF, ARF2, PAI2, AEF, and LC-MS/MS tools must remain available.
- The current MCP tool name `mscleanp_filter` may remain for backward compatibility, but its implementation should call MS-CleanR instead of the Python mimic.
- Do not make mzML LC-MS/MS tools depend on ARF, PAI2, AEF, or MS-CleanR.
- Keep `server.py` import-safe and usable as an MCP server.

## Current Design Summary

`server.py` has a global `AnalysisSession` named `session`.

The current workflow is:

1. `arf_parser(...)` reads an `.arf` file and stores parsed feature data in `session.features`.
2. `mscleanp_filter(...)` requires `session.features`.
3. `mscleanp_filter(...)` imports `MS_CleanP as msc`.
4. It calls `test_arf.extract_peak_properties(session.features)`.
5. It pivots ARF peak properties into a table:
   - index columns:
     - `MasterAlignmentID`
     - `CompoundName`
     - `SpotMassCenter`
     - `SpotRT`
     - `IonMode`
   - columns:
     - `FileName`
   - values:
     - `PeakHeight`
   - sample columns are renamed to `<sample>_Intensity`
   - metadata columns are renamed:
     - `CompoundName` -> `Name`
     - `SpotMassCenter` -> `MassCenter`
     - `SpotRT` -> `RT`
6. It calls `MS_CleanP.process_cleanup(...)`.
7. It takes `filtered_df["MasterAlignmentID"]` and filters `session.features`.
8. It reruns PCA using `test_arf.build_pca_matrix` and `test_arf.run_pca`.
9. It returns a list containing a Markdown/text report with PCA JSON.

`MS_CleanP.py` currently exposes:

- `CleanPConfig`
- `process_cleanup(...) -> tuple[pd.DataFrame, dict]`
- `process_cleanup_enhanced(...)`
- helper functions for blank/RSD/filtering/cluster selection

These are Python approximations of MS-CleanR and should no longer be the source of truth.

## MS-CleanR Context

MS-CleanR is the original R package from `eMetaboHUB/MS-CleanR`.

Relevant public documentation:

- Package README / install: `remotes::install_github("eMetaboHUB/MS-CleanR")`
  - https://rdrr.io/github/eMetaboHUB/MS-CleanR/f/README.md
- Package function index:
  - https://rdrr.io/github/eMetaboHUB/MS-CleanR/
- Main package docs state important functions include `clean_msdial_data`, `keep_top_peaks`, and `launch_msfinder_annotation`.
  - https://rdrr.io/github/eMetaboHUB/MS-CleanR/man/mscleanr.html
- `import_msdial_data(...)` parameters include blank filtering, RSD filtering, RMD filtering, m/z filtering, and thresholds.
  - https://rdrr.io/github/eMetaboHUB/MS-CleanR/man/import_msdial_data.html
- `keep_top_peaks(selection_criterion, n = 1, export_filtered_peaks = TRUE)` keeps top peaks per cluster by criterion.
  - https://rdrr.io/github/eMetaboHUB/MS-CleanR/man/keep_top_peaks.html

Do not assume all MS-CleanR functions are pure in-memory functions. The package is project-directory oriented and may write/read files.

## Required Implementation

### 1. Add an R integration layer

Create a new module, for example:

```text
mscleanr_bridge.py
```

Responsibilities:

- Import `rpy2` lazily, not at `server.py` import time.
- Check that R is available.
- Check that the R package `mscleanr` is installed.
- Return clear structured errors if:
  - `rpy2` is missing
  - R is missing/misconfigured
  - `mscleanr` is not installed
  - required MS-CleanR project files/directories are missing
  - MS-CleanR execution fails
- Convert pandas DataFrames to R data frames only inside this bridge.
- Convert R outputs back to pandas/JSON-friendly Python structures.

Do not require `rpy2` merely to start the MCP server. Only require it when the MS-CleanR tool is called.

### 2. Preserve backward-compatible MCP behavior

Keep `mscleanp_filter(...)` callable from MCP with its current user-facing parameters:

```python
def mscleanp_filter(
    do_blank_sub: bool = True,
    blank_ratio: float = 3.0,
    do_rsd_filter: bool = True,
    rsd_threshold: float = 30.0,
    components: int | None = None
) -> list:
```

But internally it should call the new bridge. You may add optional parameters if needed, but do not break existing calls.

Recommended optional parameters:

```python
mscleanr_project_dir: str | None = None
selection_criterion: str = "both"  # "intensity", "degree", or "both"
top_n: int = 1
export_filtered_peaks: bool = False
use_native_project_mode: bool = False
```

### 3. Support two modes

#### Mode A: ARF compatibility mode

Purpose: preserve the existing user workflow:

```text
arf_parser -> mscleanp_filter -> PCA
```

In this mode:

- Use `session.features` and `test_arf.extract_peak_properties`.
- Build the same pivot table currently passed to `MS_CleanP.process_cleanup`.
- Send this table into the R bridge.
- If MS-CleanR cannot operate directly on this in-memory table because it expects MS-DIAL project files, implement one of:
  - a temporary MS-DIAL-like project/export folder accepted by MS-CleanR, or
  - a clearly documented fallback to an R-side wrapper that applies MS-CleanR functions that can operate on data frames.
- Do not silently use the old Python `MS_CleanP.py` mimic unless explicitly requested by a fallback flag.

The output must still:

- Update `session.filtered_features` using retained `MasterAlignmentID`.
- Rerun PCA using existing `build_pca_matrix` and `run_pca`.
- Return a report compatible with the current MCP client behavior.

#### Mode B: native MS-CleanR project mode

Purpose: run MS-CleanR as intended on MS-DIAL/MS-FINDER-style project directories/files.

In this mode:

- Require `mscleanr_project_dir`.
- Use MS-CleanR's native project-directory-oriented functions.
- Expose at minimum:
  - `clean_msdial_data(...)`
  - `keep_top_peaks(...)`
- Return generated paths and summary stats.
- Do not mutate `session.features` unless the output can be mapped back to ARF `MasterAlignmentID`.

### 4. Data contract

The bridge should return a structure like:

```python
{
    "ok": True,
    "message": "...",
    "stats": {
        "initial_peaks": 1234,
        "final_peaks": 456,
        "retention_rate": 36.9,
        "removed_peaks": 778
    },
    "retained_master_ids": [1, 2, 3],
    "output_paths": {
        "cleaned": "...",
        "annotated": "...",
        "links": "..."
    },
    "warnings": [],
    "errors": []
}
```

If MS-CleanR uses different column names, normalize them in the bridge. The MCP layer should not need to know R-specific internals.

### 5. Do not break other tools

Keep these existing tools working:

- `arf_parser`
- `arf_re_pca`
- `arf2_parser`
- `eicaef_parser`
- `eicaef_top_peak_tops`
- `eicaef_search_by_mz_range`
- `eicaef_search_by_rt_range`
- `pai2_parser`
- `pai2_get_top_metabolites`
- `pai2_inspect_metabolite_details`
- `pai2_update_analysis_filter`
- all `lcmsms_*` tools

The LC-MS/MS mzML tools are separate and must not depend on MS-CleanR.

### 6. Environment and dependencies

Update `requirements.txt` only if needed:

```text
rpy2
```

Do not import `rpy2` in `server.py` top-level. Import it lazily inside `mscleanr_bridge.py`.

Document R-side installation requirements in a Markdown file or README section:

```r
install.packages("remotes")
remotes::install_github("eMetaboHUB/MS-CleanR")
```

Also document that R and MS-CleanR must be installed in the R environment visible to `rpy2`.

### 7. Error handling

Do not return raw tracebacks as the main result. Return concise user-facing errors and include tracebacks only in a debug field or warning if useful.

Examples:

- `rpy2 is not installed. Install it in the Python environment used by server.py.`
- `R package 'mscleanr' is not installed in the R library visible to rpy2.`
- `MS-CleanR project mode requires mscleanr_project_dir.`
- `MS-CleanR output did not contain a recognizable feature ID column.`

### 8. Tests / verification

At minimum:

- `python -m py_compile server.py mscleanr_bridge.py`
- Import smoke test: `python -c "import server"`
- Unit/smoke test for bridge dependency check when `rpy2` is unavailable or MS-CleanR is unavailable.
- If no real MS-CleanR environment exists, implement the bridge so missing R dependencies fail cleanly rather than crashing the MCP server.

If possible, add a small test fixture for the pandas-to-R input conversion using a monkeypatched bridge function, not requiring R.

## Acceptance Criteria

- `server.py` starts without requiring `rpy2` or R.
- Existing non-MS-CleanP tools remain registered and usable.
- Calling `mscleanp_filter` before `arf_parser` still returns the current style of "load ARF first" error.
- Calling `mscleanp_filter` after `arf_parser` calls MS-CleanR through rpy2, not the Python mimic.
- The output updates `session.filtered_features` and reruns PCA as before when retained IDs are available.
- Missing R/rpy2/MS-CleanR returns a clear MCP response instead of crashing.
- `MS_CleanP.py` is no longer used as the default cleanup implementation. It may remain as a deprecated fallback only if explicitly requested.

