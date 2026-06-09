# Requirements for implementing `lcmsms_viewer.py` functionality as MCP tools in `server.py`

## 1. Purpose and scope

`lcmsms_viewer.py` is a Tkinter desktop application for LC-MS/MS target inspection and quantification. It loads a target library, loads one or more mzML files, extracts spectra/EIC traces around target RT and m/z windows, applies optional smoothing, supports class/isotope grid views, supports peak picking for quantification, can build an isotope target library from an MS-DIAL alignment table, and can call ProteoWizard `msconvert.exe` to convert Agilent `.d` folders to mzML.

The MCP implementation should not reproduce the GUI. It should expose the same analytical operations as stateless or session-aware tools that can be driven from chat. The server should return compact, structured data that the LLM can summarize or visualize, while keeping Python-side rendering optional.

## 2. Current files and formats used by `lcmsms_viewer.py`

### Input files

- Target library TSV/TXT, currently called an MRMProbs reference file in the GUI.
  - Loaded with `pd.read_csv(path, sep="\t")`.
  - Required columns:
    - `Compound name`
    - `Precursor mz`
    - `Product mz`
    - `RT min`
  - Optional columns with defaults:
    - `RT begin`, default `RT min`
    - `RT end`, default `RT min`
    - `MS1 tolerance`, default `0.005`
    - `MS2 tolerance`, default `0.01`
    - `MS level`, default `1`
    - `Class`, default empty string
- mzML files:
  - Accepted extensions in the GUI: `.mzML`, `.mzml`, `.mzML.gz`, `.mzml.gz`.
  - Read through `pyteomics.mzml.MzML`.
  - The file reader supports MS1 and MS2 filtering, retention-time filtering, and extraction of m/z and intensity arrays.
- MS-DIAL alignment result text/TSV:
  - Used only by the library builder.
  - The parser scans until a line whose first cell is `Alignment ID`, then reads TSV from that line.
  - Required columns for isotope library generation:
    - `Average Rt(min)`
    - `Average Mz`
    - `Metabolite name`
    - `Formula`
  - Optional column:
    - `Isotope tracking weight number` for stable sorting.
- Agilent raw `.d` folders:
  - Used only by the converter workflow.
  - Conversion requires an external ProteoWizard `msconvert.exe`.
  - The converter searches either a single `.d` directory or nested `.d` directories under a root.

### Output files currently produced by the GUI

- Generated isotope target library TSV/TXT.
- Extracted spectrum/EIC CSV with columns:
  - `type`, `group`, `color`, `file`, `rt_min`, `mz`, `intensity`
- Class grid CSV with columns:
  - `class`, `panel`, `compound`, `group`, `file`, `rt_min`, `intensity`
- Class grid PNG rendered with Pillow.
- Peak quantification CSV with columns:
  - `Class`, `Compound name`, `File`, `Group`, `RT min`, `Precursor mz`, `RT start`, `RT end`, `Apex RT`, `Peak height`, `Peak area`, `Source`, `Enabled`

## 3. Existing functional units to reuse

Prefer extracting or reusing pure/non-GUI functions from `lcmsms_viewer.py`. Avoid instantiating `LCMSMSApp`, `Tk`, or any `Toplevel` class inside MCP tools.

Reusable functions/classes:

- Library generation:
  - `read_msdial_alignment`
  - `carbon_count_from_formula`
  - `is_blank_name`
  - `build_isotope_library_rows`
  - `write_library_rows`
- mzML reading and extraction:
  - `LibraryEntry`
  - `SpectrumSlice`
  - `InputFile`
  - `MzMLReader`
- Smoothing and peak picking:
  - `smooth_eic_points`
  - `summarize_peak`
  - `auto_pick_peak`
  - `trapezoid_area`
  - `points_in_range`
- Grid payload and optional rendering:
  - `safe_filename`
  - `normalize_grid_rows`
  - `flatten_grid_panels`
  - `render_grid_png`
- Conversion helpers:
  - `detect_msconvert`
  - logic from `find_raw_data_folders`
  - logic from `_convert_worker`

Implementation requirement: if these functions are imported from `lcmsms_viewer.py`, importing the module must remain safe and must not launch the GUI. The current file is safe on import because `main()` is guarded by `if __name__ == "__main__"`.

## 4. Dependencies and environment

Required Python packages:

- `pandas`
- `Pillow`
- `pyteomics`
- `psims`

Already required elsewhere in this repository or useful for server integration:

- `fastmcp`
- `numpy`

External executable:

- ProteoWizard `msconvert.exe` is needed only for raw `.d` to mzML conversion. MCP tools must treat it as an optional external dependency and return a clear error if not found.

Runtime requirement:

- Use the same Python environment used for `server.py`.
- If running with `uv`, use the environment where `lcmsms_viewer.py` imports successfully:
  - `uv run --python .venv-1\Scripts\python.exe python server.py`

## 5. MCP design goals

- Convert GUI actions into explicit tool calls with JSON arguments.
- Keep a lightweight session cache for repeated mzML extraction.
- Return structured, bounded data instead of GUI images by default.
- Let the LLM produce final charts from returned series data when practical.
- Provide optional CSV/PNG export tools for large results or reproducibility.
- Avoid blocking the MCP server indefinitely on expensive conversions or all-library quantification.

## 6. Recommended session state

The GUI stores mutable state in `LCMSMSApp`. MCP should replace this with a plain session object, for example `LCMSMSSession`.

Suggested fields:

- `session_id`: string.
- `library_path`: string.
- `library_entries`: list of parsed `LibraryEntry` or serializable dicts.
- `data_files`: list of dicts containing:
  - `path`
  - `file_name`
  - `group`
  - `color`
  - `reader`
- `settings`:
  - `rt_window`, default `0.05`
  - `mz_window`, default `0.005`
  - `target_mz`, optional
  - `target_rt`, optional
  - `ms_level`, default `1`
  - `smoothing_method`, default `Linear weighted moving average`
  - `smoothing_level`, default `3`
  - `minimum_peak_width`, default `5`
- `peak_selections`: mapping keyed by `(compound_name, file_path)`.
- `quant_points`: cached smoothed EIC points keyed by `(compound_name, file_path)`.
- `reader_cache`: use `MzMLReader._cache`, but expose a way to clear it.
- `last_results`: optional compact result summaries.
- `run_history`: optional list of settings/data-file snapshots.

Session lifecycle tools should include create, inspect, update, and clear operations. If server-level memory is used, implement max session count, max cache size, and expiry.

## 7. Recommended MCP tools

### 7.1 `lcmsms_create_session`

Purpose:

- Initialize a session with optional library, mzML files, groups, colors, and processing settings.

Arguments:

- `library_path: str | None`
- `mzml_paths: list[str] | None`
- `file_groups: dict[str, str] | None`
- `file_colors: dict[str, str] | None`
- `settings: dict | None`

Return:

- `session_id`
- loaded target count
- loaded file count
- warnings/errors
- normalized settings

### 7.2 `lcmsms_load_library`

Purpose:

- Load or replace a target library.

Arguments:

- `session_id`
- `library_path`

Validation:

- Check file exists.
- Check required columns.
- Convert numeric columns.
- Normalize class name: `Class` or prefix before trailing isotope suffix in `Compound name`.

Return:

- total targets
- total classes
- preview of first N class entries:
  - class name
  - representative target
  - RT
  - isotope count

### 7.3 `lcmsms_search_targets`

Purpose:

- Replace the GUI search box and class list.

Arguments:

- `session_id`
- `query: str | None`
- `limit: int = 50`
- `offset: int = 0`

Behavior:

- Match query against class name and compound name, case-insensitive.
- Return one representative target per class, sorted by isotope index/m/z as in `filter_entries`.

Return:

- list of target classes:
  - `class_name`
  - `representative_compound`
  - `rt_min`
  - `isotope_count`
  - `isotopes` optionally truncated

### 7.4 `lcmsms_add_mzml_files`

Purpose:

- Replace the "Add mzML" file dialog.

Arguments:

- `session_id`
- `paths: list[str]`
- optional `groups: dict[str, str]`
- optional `colors: dict[str, str]`

Validation:

- Accept `.mzML`, `.mzml`, `.mzML.gz`, `.mzml.gz`.
- Check each path exists.
- Avoid duplicates.
- Guess group from the second underscore-separated file-name segment if not supplied.
- Assign default colors from `PlotCanvas.COLORS` or a copied constant.

Return:

- added files
- skipped duplicates
- missing/invalid files

### 7.5 `lcmsms_update_file_groups`

Purpose:

- Replace the group settings window.

Arguments:

- `session_id`
- `updates: list[{path, group, color}]`

Return:

- updated file metadata.

Chat behavior:

- The user can say "treat A1, B1, C1 as control and D1, E1, F1 as treated"; the LLM should map this into group updates by matching file names.
- If ambiguous, the tool should return matching candidates and ask for confirmation instead of guessing silently.

### 7.6 `lcmsms_update_settings`

Purpose:

- Replace the processing settings window.

Arguments:

- `session_id`
- `rt_window: float | None`
- `mz_window: float | None`
- `target_mz: float | None`
- `target_rt: float | None`
- `ms_level: 1 | 2 | None`
- `smoothing_method: str | None`
- `smoothing_level: int | None`
- `minimum_peak_width: int | None`

Validation:

- Numeric values must be finite and non-negative where applicable.
- `ms_level` must be 1 or 2.
- `smoothing_method` must be one of:
  - `None`
  - `Simple moving average`
  - `Linear weighted moving average`
  - `Savitzky-Golay filter`
  - `Binomial filter`
  - `Lowess filter`
  - `Loess filter`
  - `Time-based linear weighted moving average`

Return:

- normalized current settings.

### 7.7 `lcmsms_build_isotope_library`

Purpose:

- Replace the library builder window.

Arguments:

- `alignment_path`
- `output_path: str | None`
- `rt_tolerance: float = 0.5`
- `ms1_tolerance: float = 0.05`
- `write_file: bool = false`

Behavior:

- Use MS-DIAL alignment columns to generate isotope library rows.
- For each unique nonblank metabolite with a valid formula and carbon count, generate `M+0` through `M+n`.
- `Precursor mz` and `Product mz` are both `Average Mz + isotope_index * 1.003354835`.
- `RT begin/end` are `RT min +/- rt_tolerance`.
- `MS level` is 1.
- MRM mode is currently not implemented in the GUI and should remain unsupported unless a separate spec is added.

Return:

- generated row count
- class count
- preview rows
- output path if written
- warnings for skipped rows if collected

### 7.8 `lcmsms_extract_target`

Purpose:

- Replace "Plot selected" for one target/class representative.

Arguments:

- `session_id`
- `compound_name: str | None`
- `class_name: str | None`
- `file_paths: list[str] | None`
- optional override settings:
  - `rt_window`
  - `mz_window`
  - `target_mz`
  - `target_rt`
  - `ms_level`
  - `smoothing_method`
  - `smoothing_level`
- `max_points_per_series: int | None`
- `include_spectrum: bool = true`
- `include_eic: bool = true`

Behavior:

- Resolve a target by exact compound name, or representative target for a class.
- Use `target_mz` override if provided, otherwise precursor m/z for MS1 and product m/z for MS2.
- Use `target_rt` override if provided, otherwise target `RT min`.
- Extract each mzML file through `MzMLReader.extract`.
- EIC intensity at each scan is the sum of intensities within `[target_mz - mz_window, target_mz + mz_window]`.
- Spectrum data is the list of mz/intensity points found across scans in the RT window and m/z window.
- Smooth only EIC points for display, not raw spectrum points.

Return:

- metadata:
  - target class, compound, target RT, target m/z, windows, MS level
- `spectrum_series`: list of `{label, file, group, color, style: "sticks", points: [[mz, intensity], ...]}`
- `eic_series`: list of `{label, file, group, color, style: "line", points: [[rt_min, intensity], ...]}`
- summaries:
  - total files
  - total spectrum points
  - EIC apex per file
  - warnings

Data-size requirement:

- For large traces, return downsampled points and include `point_count_raw`.
- Offer `export_csv` or `result_file` for full data.

### 7.9 `lcmsms_build_class_grid`

Purpose:

- Replace "Class grid" and "Export class".

Arguments:

- `session_id`
- `class_names: list[str]`
- optional settings overrides
- `include_all_isotopes: bool = true`
- `max_classes`
- `max_isotopes_per_class`
- `max_points_per_series`
- `export_csv_path: str | None`
- `export_png_path: str | None`

Behavior:

- For each class, load isotope entries sorted by isotope index/m/z.
- Build one panel per isotope target.
- Each panel contains one smoothed EIC series per mzML file.
- If `target_mz` override is supplied and only one class is requested, use only the selected representative target as the GUI does.
- Return `grid_rows` in the existing structure:
  - `[{class_name, panels: [{title, series, marker_rt, class_name, compound_name}]}]`
- Return `file_infos`:
  - `[{name, group, color}]`
- If export paths are supplied, use `write_grid_csv` equivalent and `render_grid_png`.

Recommended LLM visualization payload:

```json
{
  "kind": "lcmsms_class_grid",
  "title": "PC 34:1 EIC overlay",
  "x_label": "RT min",
  "y_label": "Extracted intensity",
  "file_infos": [{"name": "sample_A.mzML", "group": "A", "color": "#2563eb"}],
  "rows": [
    {
      "class_name": "PC 34:1",
      "panels": [
        {
          "title": "M+0 m/z 760.58500 RT 5.120",
          "marker_rt": 5.12,
          "compound_name": "PC 34:1_0",
          "series": [
            {
              "label": "A: sample_A.mzML",
              "file": "sample_A.mzML",
              "group": "A",
              "color": "#2563eb",
              "points": [[5.07, 1234.0], [5.08, 4567.0]]
            }
          ]
        }
      ]
    }
  ]
}
```

### 7.10 `lcmsms_autopick_peaks`

Purpose:

- Replace "Auto pick all" or auto-pick for selected targets.

Arguments:

- `session_id`
- `scope`: `"all_library" | "classes" | "compounds"`
- `class_names: list[str] | None`
- `compound_names: list[str] | None`
- `file_paths: list[str] | None`
- `minimum_peak_width: int | None`
- `preserve_manual: bool = true`

Behavior:

- For each target/file, ensure smoothed EIC data exists.
- Use target-specific RT window from `max(abs(rt_min - rt_begin), abs(rt_end - rt_min), 0.001)`.
- Use target-specific m/z tolerance based on `MS level`.
- Auto-pick chooses the apex in the local RT window, estimates a 10 percent above-baseline threshold, expands left/right while above threshold, enforces minimum point width, then reports height, area, apex RT, range, source, and enabled flag.
- If `preserve_manual` is true, do not overwrite selections whose source is `manual`.

Return:

- count of processed traces
- list or file path of peak selections
- warnings/errors

### 7.11 `lcmsms_set_peak_range`

Purpose:

- Replace manual left-drag peak selection.

Arguments:

- `session_id`
- `compound_name`
- `file_path`
- `rt_start`
- `rt_end`

Behavior:

- Ensure quant EIC points exist for the compound/file.
- Use `summarize_peak(points, rt_start, rt_end, source="manual", enabled=True)`.

Return:

- updated peak selection.

Chat behavior:

- User examples:
  - "For PC 34:1_0 in sample A, integrate 5.10 to 5.18 min."
  - "Use 3.42-3.50 minutes for all control files."
- The LLM maps natural-language file/group references to explicit file paths and calls this tool once per affected trace.

### 7.12 `lcmsms_clear_peak_range`

Purpose:

- Replace double right-click clear action.

Arguments:

- `session_id`
- `compound_name`
- `file_path`

Behavior:

- Mark the selection as disabled, source `cleared`, height/area `0.0`.

Return:

- updated peak selection.

### 7.13 `lcmsms_export_peak_quant`

Purpose:

- Replace "Export CSV" in the quantification tab.

Arguments:

- `session_id`
- `output_path: str | None`
- `scope`: `"all_library" | "classes" | "compounds"`
- optional class/compound/file filters
- `auto_pick_missing: bool = true`

Return:

- CSV path if written
- row count
- optionally inline rows if small

### 7.14 `lcmsms_convert_raw_to_mzml`

Purpose:

- Replace the mzML converter window.

Arguments:

- `raw_root_path`
- `output_dir_path: str | None`
- `msconvert_path: str | None`
- `centroid: bool = true`
- `add_to_session_id: str | None`

Behavior:

- Find `.d` folders.
- Build command:
  - `msconvert raw_dir --mzML --64 --zlib --outdir output_dir`
  - add `--filter "peakPicking true 1-"` when centroid is true.
- Return converted mzML paths and errors.

Safety requirement:

- Because this invokes an external executable and writes output files, require explicit user confirmation or run only when the server policy allows it.
- Do not allow arbitrary command arguments from chat; expose only controlled parameters.

## 8. Chat equivalents for GUI operations

### Load files

GUI:

- Browse library.
- Add mzML.
- Browse raw folder.

Chat/MCP:

- User provides paths in text.
- LLM calls:
  - `lcmsms_create_session`
  - `lcmsms_load_library`
  - `lcmsms_add_mzml_files`
  - optionally `lcmsms_convert_raw_to_mzml`

### Filter target list

GUI:

- Type into search box.

Chat/MCP:

- User says "show targets containing PE" or "find class TG".
- LLM calls `lcmsms_search_targets(query="PE")`.

### Select one or multiple classes

GUI:

- Select rows in listbox.

Chat/MCP:

- User names one or more classes/compounds.
- LLM resolves candidates through `lcmsms_search_targets`.
- Ambiguity should be returned to the user with candidate names.

### Change extraction parameters

GUI:

- Edit RT window, m/z window, target m/z, target RT, MS level.

Chat/MCP:

- User says "use MS2 with 0.01 m/z tolerance and +/-0.1 min RT".
- LLM calls `lcmsms_update_settings`.

### Change smoothing

GUI:

- Select method and level.

Chat/MCP:

- User says "use Savitzky-Golay smoothing level 4" or "turn smoothing off".
- LLM calls `lcmsms_update_settings`.

### Plot selected target

GUI:

- Click "Plot selected".

Chat/MCP:

- LLM calls `lcmsms_extract_target`.
- LLM renders a compact chart from returned `spectrum_series` and `eic_series` or summarizes apex intensity by group.

### Class grid

GUI:

- Click "Class grid".

Chat/MCP:

- User says "make an isotope grid for PE 36:2".
- LLM calls `lcmsms_build_class_grid`.
- The tool returns grid JSON; the LLM creates the final visualization or asks the tool to export PNG only when needed.

### Manual peak picking

GUI:

- Drag a peak range in a mini plot.

Chat/MCP:

- User gives RT ranges in text.
- LLM calls `lcmsms_set_peak_range`.
- If the user says "adjust the left boundary a little earlier", the LLM should first inspect current selection, then apply a numeric shift if inferable; otherwise ask for a specific RT.

### Clear peak

GUI:

- Double right-click a panel.

Chat/MCP:

- User says "clear the peak for sample A" or "exclude this trace".
- LLM calls `lcmsms_clear_peak_range`.

### Export data

GUI:

- Save CSV/PNG through file dialogs.

Chat/MCP:

- LLM passes explicit `output_path` or returns inline data when small.
- The server should not open file dialogs.

## 9. Visualization strategy

Default should be data-first, image-last.

Preferred outputs:

- For single target:
  - `spectrum_series` as stick data.
  - `eic_series` as line data.
  - apex summary table.
- For class grid:
  - `grid_rows` and `file_infos`.
  - optional `export_png_path` only when the user needs a persisted image.
- For quantification:
  - peak selection records and CSV-ready rows.

Reasons:

- Large Pillow PNG rendering is heavier than returning numeric series.
- The LLM/chat client can render simple line/stick charts from structured points.
- Returning compact JSON makes it easier for the LLM to compare groups, explain peak behavior, and request follow-up extraction.

Data reduction requirements:

- Limit inline points by default, for example 500 to 2000 points per series.
- Preserve raw point count and indicate downsampling method.
- Downsample by min/max bucket or largest-triangle-three-buckets, not by naive first-N truncation.
- Provide export-to-CSV tools for full data.

Suggested summary fields:

- `apex_rt`
- `apex_intensity`
- `area`
- `point_count`
- `rt_range`
- `mz_range`
- `group`
- `file`
- `warnings`

## 10. Error handling requirements

Return structured errors instead of GUI message boxes.

Common errors:

- Missing dependency: `pyteomics`/`psims`.
- Missing library file.
- Library missing required columns.
- mzML file path missing or unsupported extension.
- mzML has unsupported compression/encoding if fallback XML reader is ever used.
- `msconvert.exe` not found.
- No `.d` folders found.
- No target matches query.
- Multiple target matches when exact selection is required.
- Invalid numeric setting.
- Extraction returns no points.
- Result too large for inline response.

Each tool response should include:

- `ok: bool`
- `message`
- `errors: list[str]`
- `warnings: list[str]`
- result-specific payload

## 11. Performance and caching considerations

- mzML parsing can be expensive. Cache extraction by:
  - file path
  - compound name
  - target m/z
  - target RT
  - RT window
  - m/z window
  - MS level
- Clear cache when:
  - data files change
  - settings change in a way that affects extraction
  - explicit `lcmsms_clear_cache` is called
- Smoothing can be applied after extraction and cached separately by smoothing method/level.
- All-library auto-picking may be long-running. Consider:
  - limiting scope by default
  - returning progress handles/jobs if FastMCP supports async/background execution
  - writing large outputs to files instead of returning all rows inline
- Avoid retaining full spectra for every target/file if memory becomes large; keep only EIC/spectrum slices and summaries.

## 12. Security and path handling

- Validate every user-provided path.
- Restrict reads/writes to expected project/data/output directories if the deployment requires sandboxing.
- Never interpolate user input into shell commands.
- For `msconvert`, build a fixed argument list and pass it to `subprocess.run` without `shell=True`.
- For output files, reject directories that do not exist unless explicitly allowed to create them.
- Do not expose arbitrary file deletion or moving.

## 13. Suggested implementation structure in `server.py`

Recommended minimum implementation order:

1. Add import-safe helper layer:
   - import `LibraryEntry`, `MzMLReader`, `build_isotope_library_rows`, `write_library_rows`, `smooth_eic_points`, `auto_pick_peak`, `summarize_peak`, `render_grid_png`.
2. Add session state:
   - global `lcmsms_sessions: dict[str, LCMSMSSession]`.
3. Add library and mzML file tools:
   - create session
   - load library
   - add mzML files
   - search targets
4. Add extraction tools:
   - extract target
   - build class grid
5. Add quantification tools:
   - auto-pick
   - set/clear peak range
   - export peak CSV
6. Add optional tools:
   - build isotope library
   - convert raw `.d` to mzML
   - export PNG

Avoid coupling MCP tools to Tkinter widgets, `StringVar`, `Listbox`, `messagebox`, or file dialogs.

## 14. Acceptance criteria

- `server.py` can expose LC-MS/MS tools without launching any GUI.
- A user can, through chat, load a library, add mzML files, search targets, extract one target, build a class grid, auto-pick peaks, manually override a peak range, clear a peak, and export CSV.
- The server can return visualization-ready JSON for spectrum/EIC plots.
- The final visualization can be delegated to the LLM/client for routine plots.
- Optional PNG generation remains available for reproducible saved figures.
- Missing files, missing dependencies, invalid columns, and invalid parameter values return clear structured errors.
- Large results are bounded inline and exportable in full.
- Existing `lcmsms_viewer.py` can remain as the GUI version; MCP should reuse algorithms but not GUI state.
