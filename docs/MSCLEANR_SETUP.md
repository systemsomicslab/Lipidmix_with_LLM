# MS-CleanR integration (via rpy2)

The `mscleanp_filter` MCP tool calls the original R package
[`eMetaboHUB/MS-CleanR`](https://rdrr.io/github/eMetaboHUB/MS-CleanR/) through
[`rpy2`](https://rpy2.github.io/). The Python mimic in `MS_CleanP.py` is no
longer used by the server (it remains only as an offline reference).

> The MCP server (`server.py`) **starts and runs every other tool without R,
> rpy2, or MS-CleanR installed.** These dependencies are imported lazily and are
> only required when `mscleanp_filter` is actually called.

## 1. Python side

In the Python environment that runs `server.py`:

```bash
python -m pip install -r requirements-mscleanr.txt
```

## 2. R side

Install R (https://cran.r-project.org/), then install MS-CleanR into the R
library that `rpy2` binds to:

```r
install.packages("remotes")
remotes::install_github("eMetaboHUB/MS-CleanR")
```

`rpy2` locates R via the `R_HOME` environment variable (or the `R` executable on
`PATH`). On Windows you may need to set `R_HOME`, e.g.:

```powershell
$env:R_HOME = "C:\Program Files\R\R-4.4.1"
```

## 3. Verify

```bash
python -c "import mscleanr_bridge as b; import json; print(json.dumps(b.check_dependencies(), indent=2))"
```

`check_dependencies()` reports, without raising, whether `rpy2`, R, and the
`mscleanr` package are each available, and returns clear messages for whatever is
missing.

## Modes

`mscleanp_filter` supports two modes:

### Mode A — ARF compatibility (default)

Preserves the existing workflow `arf_parser -> mscleanp_filter -> PCA`. The ARF
peak table is pivoted in Python (`MasterAlignmentID`, metadata, and
`<sample>_Intensity` columns) and handed to the R bridge, which applies
MS-CleanR's blank-ratio / RSD / top-peak criteria. Retained `MasterAlignmentID`
values update `session.filtered_features` and PCA is rerun.

> MS-CleanR's native importer (`import_msdial_data`) expects on-disk MS-DIAL
> exports, so the in-memory data-frame path runs an R-side wrapper that applies
> MS-CleanR's filtering criteria directly to the supplied table (a documented
> fallback). The `mscleanr` package is still required and loaded.

### Mode B — native MS-CleanR project mode

Set `use_native_project_mode=True` (or pass `mscleanr_project_dir`) to run
MS-CleanR as intended against a directory of MS-DIAL / MS-FINDER exports, using
`clean_msdial_data(...)` and `keep_top_peaks(...)`. Generated output paths and
summary stats are returned.

## Error handling

The bridge never returns raw tracebacks as the primary message. Expected failure
modes each yield a concise, user-facing message (tracebacks, if any, go in a
`debug` field):

- `rpy2 is not installed. Install it in the Python environment used by server.py.`
- `R is not available or is misconfigured for rpy2.`
- `R package 'mscleanr' is not installed in the R library visible to rpy2.`
- `MS-CleanR project mode requires mscleanr_project_dir.`
- `MS-CleanR output did not contain a recognizable feature ID column.`
