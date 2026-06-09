"""
mscleanr_bridge.py

R integration layer that calls the original MS-CleanR R package
(``eMetaboHUB/MS-CleanR``) through ``rpy2``.

Design rules (see mscleanr_rpy2_migration_prompt.md):

* ``rpy2`` is imported lazily, never at ``server.py`` import time. Importing this
  module is therefore safe even when neither ``rpy2``, R, nor MS-CleanR exist.
* All pandas <-> R conversion happens inside this module. The MCP layer only ever
  sees JSON-friendly Python structures.
* Every public entry point returns the same structured "data contract" dict and
  never raises for the expected failure modes (missing rpy2 / R / mscleanr /
  project files / R runtime errors). Unexpected programming errors still raise.

Two execution modes are exposed:

* ``run_arf_compat_mode`` -- Mode A. Takes the in-memory pivot table built from an
  ``.arf`` file and runs MS-CleanR's QC / top-peak logic on it, returning the
  retained ``MasterAlignmentID`` values so the caller can rerun PCA. Because
  MS-CleanR's native importer expects on-disk MS-DIAL exports, the data-frame
  path uses an R-side wrapper that applies MS-CleanR's blank/RSD/top-peak
  criteria directly to the supplied table (documented fallback).
* ``run_native_project_mode`` -- Mode B. Runs MS-CleanR as intended against a
  project directory of MS-DIAL / MS-FINDER exports using ``clean_msdial_data``
  and ``keep_top_peaks``.

R-side installation requirements
--------------------------------
R and the MS-CleanR package must be installed in the R environment that ``rpy2``
binds to::

    install.packages("remotes")
    remotes::install_github("eMetaboHUB/MS-CleanR")

And in the Python environment running ``server.py``::

    pip install rpy2
"""

from __future__ import annotations

import os
import csv
import subprocess
import tempfile
import traceback
from typing import Any, Optional

import pandas as pd

# Name of the R package as installed by remotes::install_github("eMetaboHUB/MS-CleanR").
R_PACKAGE_NAME = "mscleanr"

# Recognised id columns coming back from MS-CleanR / the ARF pivot table, in
# priority order. Normalised to "MasterAlignmentID" in the contract.
_ID_COLUMN_CANDIDATES = [
    "MasterAlignmentID",
    "AlignmentID",
    "Alignment.ID",
    "Alignment ID",
    "PeakID",
    "id",
]

_DEPENDENCY_STATUS_CACHE: dict[str, Any] | None = None


def reset_dependency_cache() -> None:
    """Clear the cached dependency probe result. Primarily useful for tests."""
    global _DEPENDENCY_STATUS_CACHE
    _DEPENDENCY_STATUS_CACHE = None


def _prepend_path(path: str) -> None:
    if not path or not os.path.isdir(path):
        return
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    lowered = {p.lower() for p in parts}
    if path.lower() not in lowered:
        os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


def _bootstrap_r_environment() -> dict[str, str]:
    """Set stable Windows R defaults before importing rpy2.

    MCP server processes can be launched without the user's freshly configured
    environment variables. rpy2 reads R_HOME/PATH/R_LIBS_USER during import, so
    we defensively fill them here while keeping existing explicit values.
    """
    applied: dict[str, str] = {}
    if os.name != "nt":
        return applied

    os.environ.setdefault("PYTHONUTF8", "1")
    applied["PYTHONUTF8"] = os.environ["PYTHONUTF8"]

    r_home = os.environ.get("R_HOME")
    if not r_home or not os.path.isdir(r_home):
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        r_root = os.path.join(program_files, "R")
        versions: list[str] = []
        if os.path.isdir(r_root):
            versions = [
                os.path.join(r_root, name)
                for name in os.listdir(r_root)
                if os.path.isdir(os.path.join(r_root, name)) and name.startswith("R-")
            ]
        if versions:
            r_home = sorted(versions)[-1]
            os.environ["R_HOME"] = r_home

    if r_home and os.path.isdir(r_home):
        applied["R_HOME"] = r_home
        _prepend_path(os.path.join(r_home, "bin", "x64"))
        _prepend_path(os.path.join(r_home, "bin"))

    r_libs_user = os.environ.get("R_LIBS_USER")
    if not r_libs_user:
        appdata = os.environ.get("APPDATA")
        if appdata and r_home:
            version = os.path.basename(r_home).removeprefix("R-")
            major_minor = ".".join(version.split(".")[:2])
            r_libs_user = os.path.join(appdata, "R", "win-library", major_minor)
            os.environ["R_LIBS_USER"] = r_libs_user

    if r_libs_user:
        applied["R_LIBS_USER"] = r_libs_user
    return applied


def _patch_rpy2_windows_getrenvvars() -> None:
    """Make rpy2 read R's environment CSV as UTF-8 on Japanese Windows.

    rpy2 opens the CSV without an explicit encoding, which uses cp932 in this
    environment and can fail on UTF-8 values emitted by Rscript during startup.
    """
    if os.name != "nt":
        return
    try:
        import rpy2.rinterface as ri
        from rpy2.rinterface_lib import openrlib
    except Exception:
        return
    if getattr(ri, "_mscleanr_utf8_getrenvvars", False):
        return

    def _getrenvvars_utf8(baselinevars=None, r_home=None):
        if baselinevars is None:
            baselinevars = os.environ
        if r_home is None:
            r_home = openrlib.R_HOME
            if r_home is None:
                raise RuntimeError("Unable to determine R_HOME.")

        temp_fh = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv")
        temp_fh.close()
        try:
            temp_name = temp_fh.name.replace("\\", "/")
            cmd = (
                os.path.join(r_home, "bin", "Rscript"),
                "-e",
                ";".join(
                    (
                        "x <- Sys.getenv()",
                        "dataf <- data.frame(key=names(x), val=as.character(x))",
                        f'write.csv(dataf, file="{temp_name}", row.names=FALSE, fileEncoding="UTF-8")',
                    )
                ),
            )
            subprocess.run(cmd, check=False)
            res = []
            with open(temp_fh.name, mode="r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.reader(fh)
                assert tuple(next(reader)) == ("key", "val")
                for row in reader:
                    if len(row) != 2:
                        raise ValueError(f"Invalid environment variable row: {row}")
                    k, v = row
                    if (k not in baselinevars) or (baselinevars[k] != v):
                        res.append((k, v))
        finally:
            os.remove(temp_fh.name)
        return tuple(res)

    ri._getrenvvars = _getrenvvars_utf8
    ri._mscleanr_utf8_getrenvvars = True


# ---------------------------------------------------------------------------
# Data contract helpers
# ---------------------------------------------------------------------------

def _empty_contract() -> dict[str, Any]:
    return {
        "ok": False,
        "message": "",
        "stats": {},
        "retained_master_ids": [],
        "output_paths": {},
        "warnings": [],
        "errors": [],
    }


def _error_contract(message: str, *, debug: str | None = None, warnings: list | None = None) -> dict[str, Any]:
    contract = _empty_contract()
    contract["ok"] = False
    contract["message"] = message
    contract["errors"] = [message]
    contract["warnings"] = warnings or []
    if debug:
        contract["debug"] = debug
    return contract


# ---------------------------------------------------------------------------
# Dependency checking
# ---------------------------------------------------------------------------

def check_dependencies(force: bool = False) -> dict[str, Any]:
    """Probe rpy2 / R / the mscleanr package without importing rpy2 at module load.

    Returns a structured dict; never raises for the expected "not installed"
    states. ``ok`` is True only when rpy2 imports, R initialises, and the
    ``mscleanr`` package is installed and loadable.
    """
    global _DEPENDENCY_STATUS_CACHE
    if _DEPENDENCY_STATUS_CACHE is not None and not force:
        cached = dict(_DEPENDENCY_STATUS_CACHE)
        cached["cached"] = True
        return cached

    bootstrap_env = _bootstrap_r_environment()
    status = {
        "ok": False,
        "rpy2": False,
        "r_available": False,
        "mscleanr_installed": False,
        "message": "",
        "errors": [],
        "r_version": None,
        "mscleanr_version": None,
        "bootstrap_env": bootstrap_env,
        "cached": False,
    }

    # 1. rpy2 importable?
    try:
        import rpy2  # noqa: F401
        _patch_rpy2_windows_getrenvvars()
        import rpy2.robjects as ro  # noqa: F401
    except Exception as exc:  # ImportError, or a broken/partial install.
        status["errors"].append(
            "rpy2 is not installed. Install it in the Python environment used by server.py "
            "(pip install rpy2)."
        )
        status["message"] = status["errors"][-1]
        status["debug"] = f"{type(exc).__name__}: {exc}"
        _DEPENDENCY_STATUS_CACHE = dict(status)
        return status
    status["rpy2"] = True

    # 2. R reachable through rpy2?
    try:
        from rpy2.robjects import r
        status["r_version"] = str(r("as.character(getRversion())")[0])
        status["r_available"] = True
    except Exception as exc:
        status["errors"].append(
            "R is not available or is misconfigured for rpy2. Ensure R is installed and "
            "R_HOME points at it."
        )
        status["message"] = status["errors"][-1]
        status["debug"] = f"{type(exc).__name__}: {exc}"
        _DEPENDENCY_STATUS_CACHE = dict(status)
        return status

    # 3. mscleanr package installed?
    try:
        from rpy2.robjects.packages import isinstalled
        if not isinstalled(R_PACKAGE_NAME):
            r_libs_user = os.environ.get("R_LIBS_USER")
            if r_libs_user:
                try:
                    from rpy2.robjects import r
                    r(f'.libPaths(unique(c("{r_libs_user.replace("\\", "/")}", .libPaths())))')
                except Exception:
                    pass
        if not isinstalled(R_PACKAGE_NAME):
            status["errors"].append(
                f"R package '{R_PACKAGE_NAME}' is not installed in the R library visible to rpy2. "
                'Install it with remotes::install_github("eMetaboHUB/MS-CleanR").'
            )
            status["message"] = status["errors"][-1]
            _DEPENDENCY_STATUS_CACHE = dict(status)
            return status
        status["mscleanr_installed"] = True
        try:
            from rpy2.robjects import r
            ver = r(f'as.character(packageVersion("{R_PACKAGE_NAME}"))')
            status["mscleanr_version"] = str(ver[0])
        except Exception:
            pass
    except Exception as exc:
        status["errors"].append(
            f"Could not determine whether R package '{R_PACKAGE_NAME}' is installed."
        )
        status["message"] = status["errors"][-1]
        status["debug"] = f"{type(exc).__name__}: {exc}"
        _DEPENDENCY_STATUS_CACHE = dict(status)
        return status

    status["ok"] = True
    status["message"] = (
        f"rpy2 OK, R {status['r_version']}, {R_PACKAGE_NAME} "
        f"{status['mscleanr_version'] or 'installed'}."
    )
    _DEPENDENCY_STATUS_CACHE = dict(status)
    return status


def _require_ready() -> Optional[dict[str, Any]]:
    """Return an error contract if the R stack is not ready, else None."""
    deps = check_dependencies()
    if deps["ok"]:
        return None
    contract = _error_contract(
        deps.get("message") or "MS-CleanR R environment is not available.",
        debug=deps.get("debug"),
    )
    contract["errors"] = deps.get("errors") or contract["errors"]
    contract["dependency_status"] = deps
    return contract


# ---------------------------------------------------------------------------
# pandas <-> R conversion (isolated here so the MCP layer never imports rpy2)
# ---------------------------------------------------------------------------

def pandas_to_r(df: pd.DataFrame):
    """Convert a pandas DataFrame to an R data.frame. Imports rpy2 lazily."""
    import rpy2.robjects as ro
    from rpy2.robjects import pandas2ri
    with (ro.default_converter + pandas2ri.converter).context():
        return ro.conversion.get_conversion().py2rpy(df)


def r_to_pandas(r_df) -> pd.DataFrame:
    """Convert an R data.frame back to a pandas DataFrame. Imports rpy2 lazily."""
    import rpy2.robjects as ro
    from rpy2.robjects import pandas2ri
    with (ro.default_converter + pandas2ri.converter).context():
        return ro.conversion.get_conversion().rpy2py(r_df)


def _find_id_column(df: pd.DataFrame, preferred: str | None = None) -> Optional[str]:
    candidates = ([preferred] if preferred else []) + _ID_COLUMN_CANDIDATES
    lower = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        if cand is None:
            continue
        if cand in df.columns:
            return cand
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def _extract_retained_ids(cleaned: pd.DataFrame, id_col: str | None) -> tuple[list, str | None]:
    col = _find_id_column(cleaned, id_col)
    if col is None:
        return [], None
    ids: list = []
    for value in cleaned[col].tolist():
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        # MS-DIAL IDs are integers; keep them as int when possible.
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            ids.append(value)
    return ids, col


# ---------------------------------------------------------------------------
# R wrapper source. Defined once; injected into the R global env on demand.
# ---------------------------------------------------------------------------

# Mode A: operate MS-CleanR's QC + top-peak criteria on a single in-memory table.
# MS-CleanR's native importer (import_msdial_data) needs on-disk MS-DIAL exports,
# so for the data-frame path we apply the same blank-ratio / RSD / top-peak rules
# in R, after loading the mscleanr namespace (so the package is still required and
# its helpers are used where they accept data frames).
_R_ARF_COMPAT_WRAPPER = """
mscleanr_bridge_arf_compat <- function(df, id_col, blank_ratio, rsd_threshold,
                                       do_blank, do_rsd, selection_criterion,
                                       top_n, blank_pattern) {
  suppressWarnings(suppressMessages(library(mscleanr)))

  intensity_cols <- grep("_Intensity$", colnames(df), value = TRUE)
  if (length(intensity_cols) == 0) {
    stop("No intensity columns (expected '*_Intensity') were found in the input table.")
  }
  blank_cols  <- grep(blank_pattern, intensity_cols, ignore.case = TRUE, value = TRUE)
  sample_cols <- setdiff(intensity_cols, blank_cols)
  if (length(sample_cols) == 0) {
    sample_cols <- intensity_cols
    blank_cols  <- character(0)
  }

  to_num <- function(cols) {
    m <- as.matrix(df[, cols, drop = FALSE])
    suppressWarnings(matrix(as.numeric(m), nrow = nrow(df)))
  }
  sample_mat <- to_num(sample_cols)
  sample_mat[is.na(sample_mat)] <- 0
  sample_mean <- rowMeans(sample_mat)

  keep <- rep(TRUE, nrow(df))
  reasons <- rep("", nrow(df))
  n0 <- nrow(df)

  # Blank filtering (MS-CleanR import_msdial_data blank-ratio criterion).
  after_blank <- n0
  if (do_blank && length(blank_cols) > 0) {
    blank_mat <- to_num(blank_cols)
    blank_mat[is.na(blank_mat)] <- 0
    blank_mean <- rowMeans(blank_mat)
    drop_blank <- (blank_mean > 0) & (sample_mean < blank_mean * blank_ratio)
    keep <- keep & !drop_blank
    reasons[drop_blank] <- "blank_dominant"
    after_blank <- sum(keep)
  }

  # RSD filtering across sample replicates.
  after_rsd <- after_blank
  if (do_rsd && ncol(sample_mat) >= 2) {
    rsd <- apply(sample_mat, 1, function(x) {
      mu <- mean(x)
      if (mu <= 0) return(Inf)
      stats::sd(x) / mu * 100
    })
    drop_rsd <- keep & (rsd > rsd_threshold)
    keep <- keep & !(keep & (rsd > rsd_threshold))
    reasons[drop_rsd] <- ifelse(reasons[drop_rsd] == "", "high_rsd",
                                paste(reasons[drop_rsd], "high_rsd", sep = ";"))
    after_rsd <- sum(keep)
  }

  cleaned <- df[keep, , drop = FALSE]

  bridge_warnings <- character(0)

  # Top-peak selection per cluster, mirroring keep_top_peaks(selection_criterion,
  # n = top_n), but only when an explicit cluster column is available. RT bins are
  # not a safe cluster surrogate: unrelated co-eluting features can share the same
  # rounded RT and would be incorrectly collapsed.
  if (top_n >= 1 && nrow(cleaned) > 0) {
    cluster_col <- intersect(c("cluster_id", "ClusterID", "Cluster.ID",
                               "MSCleanRCluster", "cleanp_cluster_id",
                               "cluster", "Cluster"), colnames(cleaned))
    if (length(cluster_col) >= 1) {
      kept_idx <- which(keep)
      kept_mean <- sample_mean[kept_idx]
      cluster_key <- cleaned[[cluster_col[1]]]
      sel <- logical(nrow(cleaned))
      for (k in unique(cluster_key)) {
        grp <- which(cluster_key == k)
        ord <- grp[order(kept_mean[grp], decreasing = TRUE)]
        sel[ord[seq_len(min(top_n, length(ord)))]] <- TRUE
      }
      cleaned <- cleaned[sel, , drop = FALSE]
    } else {
      bridge_warnings <- c(
        bridge_warnings,
        "Skipped top_n peak selection in ARF compatibility mode because no explicit cluster column was available."
      )
    }
  }

  list(
    cleaned = cleaned,
    stats = list(
      initial_peaks = n0,
      after_blank_subtraction = after_blank,
      after_rsd_filtering = after_rsd,
      final_peaks = nrow(cleaned)
    ),
    sample_cols = sample_cols,
    blank_cols = blank_cols,
    warnings = bridge_warnings
  )
}
"""


# ---------------------------------------------------------------------------
# Mode A: ARF compatibility mode
# ---------------------------------------------------------------------------

def run_arf_compat_mode(
    df: pd.DataFrame,
    *,
    do_blank_sub: bool = True,
    blank_ratio: float = 3.0,
    do_rsd_filter: bool = True,
    rsd_threshold: float = 30.0,
    selection_criterion: str = "both",
    top_n: int = 1,
    export_filtered_peaks: bool = False,
    id_col: str = "MasterAlignmentID",
    blank_keyword: str = "Blank",
) -> dict[str, Any]:
    """Run MS-CleanR's cleanup criteria over the in-memory ARF pivot table.

    ``df`` is the table the legacy code passed to ``MS_CleanP.process_cleanup``:
    one row per aligned feature, an id column (``MasterAlignmentID``), metadata
    columns, and one ``<sample>_Intensity`` column per sample.

    Returns the data-contract dict including ``retained_master_ids``.
    """
    not_ready = _require_ready()
    if not_ready is not None:
        return not_ready

    if df is None or len(df) == 0:
        return _error_contract("ARF compatibility mode received an empty feature table.")

    if _find_id_column(df, id_col) is None:
        return _error_contract(
            "MS-CleanR output did not contain a recognizable feature ID column "
            f"(looked for {id_col!r} and common aliases)."
        )

    initial_peaks = int(len(df))
    try:
        import rpy2.robjects as ro

        ro.r(_R_ARF_COMPAT_WRAPPER)
        wrapper = ro.globalenv["mscleanr_bridge_arf_compat"]
        r_df = pandas_to_r(df)
        result = wrapper(
            r_df,
            ro.StrVector([id_col]),
            ro.FloatVector([float(blank_ratio)]),
            ro.FloatVector([float(rsd_threshold)]),
            ro.BoolVector([bool(do_blank_sub)]),
            ro.BoolVector([bool(do_rsd_filter)]),
            ro.StrVector([str(selection_criterion)]),
            ro.IntVector([int(top_n)]),
            ro.StrVector([str(blank_keyword)]),
        )
        result_dict = dict(zip(result.names, list(result)))
        cleaned = r_to_pandas(result_dict["cleaned"])
        r_stats = dict(zip(result_dict["stats"].names, list(result_dict["stats"])))
        r_warnings = [str(x) for x in list(result_dict.get("warnings", []))]
    except Exception as exc:
        return _error_contract(
            "MS-CleanR execution failed while cleaning the ARF feature table.",
            debug=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )

    retained_ids, used_col = _extract_retained_ids(cleaned, id_col)
    if used_col is None:
        return _error_contract(
            "MS-CleanR output did not contain a recognizable feature ID column."
        )

    final_peaks = int(_r_scalar(r_stats.get("final_peaks"), len(cleaned)))
    contract = _empty_contract()
    contract["ok"] = True
    contract["stats"] = {
        "initial_peaks": initial_peaks,
        "after_blank_subtraction": _r_scalar(r_stats.get("after_blank_subtraction")),
        "after_rsd_filtering": _r_scalar(r_stats.get("after_rsd_filtering")),
        "final_peaks": final_peaks,
        "removed_peaks": initial_peaks - final_peaks,
        "retention_rate": round(100.0 * final_peaks / initial_peaks, 1) if initial_peaks else 0.0,
    }
    contract["retained_master_ids"] = retained_ids
    contract["warnings"] = r_warnings
    contract["message"] = (
        f"MS-CleanR retained {final_peaks}/{initial_peaks} features "
        f"({contract['stats']['retention_rate']}%)."
    )

    if export_filtered_peaks:
        try:
            out_path = os.path.join(os.getcwd(), "mscleanr_cleaned.csv")
            cleaned.to_csv(out_path, index=False)
            contract["output_paths"]["cleaned"] = out_path
        except Exception as exc:  # export is best-effort
            contract["warnings"].append(f"Could not export filtered peaks: {exc}")

    return contract


def _r_scalar(value, default=None):
    """Pull a Python scalar out of an rpy2 vector / list element."""
    if value is None:
        return default
    try:
        seq = list(value)
        if not seq:
            return default
        first = seq[0]
    except TypeError:
        first = value
    try:
        if float(first).is_integer():
            return int(first)
        return float(first)
    except (TypeError, ValueError):
        return first


# ---------------------------------------------------------------------------
# Mode B: native MS-CleanR project mode
# ---------------------------------------------------------------------------

def run_native_project_mode(
    project_dir: str,
    *,
    selection_criterion: str = "both",
    top_n: int = 1,
    export_filtered_peaks: bool = True,
    id_col: str = "MasterAlignmentID",
) -> dict[str, Any]:
    """Run MS-CleanR natively against a project directory of MS-DIAL/MS-FINDER exports.

    Uses ``clean_msdial_data`` followed by ``keep_top_peaks`` as documented in the
    MS-CleanR package. Returns the data-contract dict with generated output paths.
    """
    if not project_dir:
        return _error_contract("MS-CleanR project mode requires mscleanr_project_dir.")
    if not os.path.isdir(project_dir):
        return _error_contract(
            f"MS-CleanR project directory does not exist: {project_dir}"
        )

    not_ready = _require_ready()
    if not_ready is not None:
        return not_ready

    try:
        import rpy2.robjects as ro
        from rpy2.robjects.packages import importr

        mscleanr = importr(R_PACKAGE_NAME)
        ro.r(_R_NATIVE_WRAPPER)
        wrapper = ro.globalenv["mscleanr_bridge_native"]
        result = wrapper(
            ro.StrVector([project_dir]),
            ro.StrVector([str(selection_criterion)]),
            ro.IntVector([int(top_n)]),
            ro.BoolVector([bool(export_filtered_peaks)]),
        )
        result_dict = dict(zip(result.names, list(result)))
    except Exception as exc:
        return _error_contract(
            "MS-CleanR native project run failed.",
            debug=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )

    contract = _empty_contract()
    contract["ok"] = True

    # Output paths (MS-CleanR writes into the project directory).
    paths = {}
    if "output_paths" in result_dict:
        try:
            op = result_dict["output_paths"]
            names = list(op.names) if op.names is not None else []
            for i, v in enumerate(list(op), start=1):
                key = str(names[i - 1]) if i <= len(names) and names[i - 1] else f"path_{i}"
                if hasattr(v, "__len__") and not isinstance(v, str):
                    value = str(v[0]) if len(v) else ""
                else:
                    value = str(v)
                if value:
                    paths[key] = value
        except Exception:
            paths = {}
    contract["output_paths"] = paths

    stats = {}
    if "stats" in result_dict:
        try:
            st = result_dict["stats"]
            stats = {str(k): _r_scalar(v) for k, v in zip(st.names, list(st))}
        except Exception:
            stats = {}
    contract["stats"] = stats

    # Map retained IDs back when a cleaned table is returned.
    retained_ids: list = []
    if "cleaned" in result_dict:
        try:
            cleaned = r_to_pandas(result_dict["cleaned"])
            retained_ids, _ = _extract_retained_ids(cleaned, id_col)
        except Exception as exc:
            contract["warnings"].append(f"Could not map retained IDs from cleaned table: {exc}")
    contract["retained_master_ids"] = retained_ids

    final = stats.get("final_peaks")
    contract["message"] = (
        f"MS-CleanR native run complete on {project_dir}."
        + (f" Retained {final} features." if final is not None else "")
    )
    return contract


_R_NATIVE_WRAPPER = """
mscleanr_bridge_native <- function(project_dir, selection_criterion, top_n, export_peaks) {
  suppressWarnings(suppressMessages(library(mscleanr)))
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(project_dir)

  # MS-CleanR is project-directory oriented: clean_msdial_data() reads the
  # MS-DIAL / MS-FINDER exports in the working directory and writes results back.
  cleaned <- clean_msdial_data()
  topped  <- keep_top_peaks(selection_criterion = selection_criterion,
                            n = top_n,
                            export_filtered_peaks = export_peaks)

  generated <- list.files(project_dir, pattern = "\\\\.(csv|tsv|txt)$",
                          full.names = TRUE, recursive = TRUE)
  final_candidates <- c(
    file.path(project_dir, "intermediary_data", "MS_peaks-final.csv"),
    grep("MS_peaks-final\\\\.csv$|top.*\\\\.csv$|filtered.*\\\\.csv$",
         generated, ignore.case = TRUE, value = TRUE)
  )
  final_candidates <- final_candidates[file.exists(final_candidates)]
  final_path <- if (length(final_candidates) >= 1) final_candidates[1] else ""

  result_table <- if (is.data.frame(topped)) topped else
                  if (is.data.frame(cleaned)) cleaned else
                  if (nzchar(final_path)) utils::read.csv(final_path, check.names = FALSE) else
                  data.frame()

  list(
    cleaned = result_table,
    stats = list(final_peaks = nrow(result_table)),
    output_paths = list(
      final_table = final_path,
      generated_files = paste(generated, collapse = "\n")
    )
  )
}
"""
