"""
MS_CleanP_enhanced.py

Python implementation inspired by MS-CleanR for MS-DIAL-derived feature tables.
It is designed to be usable from the existing MCP server code as a drop-in
replacement for the simple MS_CleanP.process_cleanup(), while exposing richer
outputs for LLM-assisted interpretation.

Implemented layers:
  1. QC filtering: blank ratio, blank ghost peaks, RSD, mass defect, RMD.
  2. Feature relation inference: isotope, adduct, neutral loss / in-source loss,
     optional dimer links, RT-window links.
  3. Feature graph clustering via union-find connected components.
  4. Representative peak selection per cluster using intensity, degree, annotation,
     and QC penalties.
  5. Audit columns: keep/drop flags, exclusion reasons, cluster_id, representative.

Input expectations:
  - One row per aligned feature.
  - m/z column: one of ['m/z', 'mz', 'MassCenter', 'SpotMassCenter', 'PeakMZ']
  - RT column: one of ['rt', 'RT', 'SpotRT', 'PeakRT']
  - intensity columns: automatically detected by suffix '_Intensity' or supplied.
  - optional annotation column: ['CompoundName', 'Name', 'annotation']
  - optional ID column: ['MasterAlignmentID', 'AlignmentID', 'id']

Limitations vs. original MS-CleanR:
  - Does not call MS-FINDER or rank structure candidates by database/biological
    source. It only prepares cluster/representative information for downstream use.
  - Ionization/adduct rules are heuristic and should be configured per method.
  - It uses connected components rather than MS-CleanR's exact graph/community
    implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional
import math
import re
import sys

import numpy as np
import pandas as pd

PROTON = 1.007276466812
NEUTRON = 1.0033548378
ELECTRON = 0.000548579909

DEFAULT_ADDUCTS_POS = {
    "[M+H]+": (1, PROTON),
    "[M+Na]+": (1, 22.989218),
    "[M+K]+": (1, 38.963158),
    "[M+NH4]+": (1, 18.033823),
    "[M+ACN+H]+": (1, 42.033823),
    "[M+2H]2+": (2, 2 * PROTON),
}

DEFAULT_ADDUCTS_NEG = {
    "[M-H]-": (1, -PROTON),
    "[M+Cl]-": (1, 34.969402),
    "[M+FA-H]-": (1, 44.998201 - PROTON),
    "[M+Hac-H]-": (1, 60.021129 - PROTON),
    "[M-2H]2-": (2, -2 * PROTON),
}

DEFAULT_NEUTRAL_LOSSES = {
    "H2O": 18.010565,
    "NH3": 17.026549,
    "CO2": 43.989830,
    "CH2O2": 46.005480,
    "H3PO4": 97.976896,
    "C6H10O5": 162.052824,
}


@dataclass
class CleanPConfig:
    blank_keyword: str = "Blank"
    qc_keyword: str = "QC"
    intensity_suffix: str = "_Intensity"
    id_col: Optional[str] = None
    mz_col: Optional[str] = None
    rt_col: Optional[str] = None
    annotation_col: Optional[str] = None
    sample_cols: Optional[list[str]] = None
    blank_cols: Optional[list[str]] = None
    qc_cols: Optional[list[str]] = None

    # QC filters
    do_blank_filter: bool = True
    blank_sample_ratio_threshold: float = 3.0  # keep if sample_mean >= blank_mean * this value
    blank_absolute_threshold: float = 0.0      # drop ghost if sample_mean <= this and blank_mean > 0
    do_rsd_filter: bool = True
    rsd_threshold: float = 30.0
    rsd_use_qc_if_available: bool = True
    do_mass_defect_filter: bool = True
    allowed_mass_defect_range: tuple[float, float] = (0.0, 0.8)
    do_rmd_filter: bool = True
    allowed_rmd_range: tuple[float, float] = (50.0, 3000.0)
    min_mean_intensity: float = 0.0

    # relation inference
    do_isotope_links: bool = True
    do_adduct_links: bool = True
    do_neutral_loss_links: bool = True
    do_dimer_links: bool = False
    mz_tolerance: float = 0.01          # Da. For high-res data, 0.005-0.01 is often safer.
    mz_tolerance_ppm: Optional[float] = 10.0
    rt_tolerance: float = 0.10          # min
    ion_mode: Literal["positive", "negative", "both", "unknown"] = "unknown"
    adducts_positive: dict[str, tuple[int, float]] = field(default_factory=lambda: DEFAULT_ADDUCTS_POS.copy())
    adducts_negative: dict[str, tuple[int, float]] = field(default_factory=lambda: DEFAULT_ADDUCTS_NEG.copy())
    neutral_losses: dict[str, float] = field(default_factory=lambda: DEFAULT_NEUTRAL_LOSSES.copy())

    # representative selection
    representative_strategy: Literal["intensity", "degree", "both"] = "both"
    keep_representatives_only: bool = True
    prefer_annotated: bool = True

    # output
    verbose: bool = True


class UnionFind:
    def __init__(self, items: Iterable[Any]):
        self.parent = {x: x for x in items}
        self.rank = {x: 0 for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


def _log(msg: str, config: CleanPConfig):
    if config.verbose:
        print(msg, file=sys.stderr)


def _first_existing(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def _resolve_columns(df: pd.DataFrame, config: CleanPConfig) -> CleanPConfig:
    cfg = CleanPConfig(**config.__dict__)
    cfg.id_col = cfg.id_col or _first_existing(df, ["MasterAlignmentID", "AlignmentID", "id", "feature_id"])
    cfg.mz_col = cfg.mz_col or _first_existing(df, ["m/z", "mz", "MassCenter", "SpotMassCenter", "PeakMZ"])
    cfg.rt_col = cfg.rt_col or _first_existing(df, ["rt", "RT", "SpotRT", "PeakRT"])
    cfg.annotation_col = cfg.annotation_col or _first_existing(df, ["CompoundName", "Name", "annotation", "Annotation"])

    if cfg.sample_cols is None:
        cfg.blank_cols = cfg.blank_cols or [c for c in df.columns if cfg.blank_keyword.lower() in str(c).lower() and cfg.intensity_suffix in str(c)]
        cfg.qc_cols = cfg.qc_cols or [c for c in df.columns if cfg.qc_keyword.lower() in str(c).lower() and cfg.intensity_suffix in str(c)]
        exclude = set(cfg.blank_cols or [])
        cfg.sample_cols = [c for c in df.columns if cfg.intensity_suffix in str(c) and c not in exclude]
        # Treat QC as sample for mean intensity, but use it specifically for RSD when available.
    else:
        cfg.blank_cols = cfg.blank_cols or []
        cfg.qc_cols = cfg.qc_cols or [c for c in cfg.sample_cols if cfg.qc_keyword.lower() in str(c).lower()]

    if cfg.id_col is None:
        df["__feature_id"] = np.arange(len(df))
        cfg.id_col = "__feature_id"
    if cfg.mz_col is None:
        raise ValueError("m/z列が見つかりません。mz_colを指定してください。")
    if cfg.rt_col is None:
        raise ValueError("RT列が見つかりません。rt_colを指定してください。")
    if not cfg.sample_cols:
        raise ValueError("強度列が見つかりません。sample_colsまたは '_Intensity' 接尾辞の列を指定してください。")
    return cfg


def _tol_da(mz: float, config: CleanPConfig) -> float:
    tol = config.mz_tolerance
    if config.mz_tolerance_ppm is not None and np.isfinite(mz):
        tol = max(tol, abs(mz) * config.mz_tolerance_ppm * 1e-6)
    return tol


def _mass_defect(mz: pd.Series) -> pd.Series:
    return mz - np.floor(mz)


def _relative_mass_defect_ppm(mz: pd.Series) -> pd.Series:
    nominal = np.round(mz).replace(0, np.nan)
    return ((mz - nominal) / nominal).abs() * 1e6


def add_qc_metrics(df: pd.DataFrame, config: CleanPConfig) -> tuple[pd.DataFrame, CleanPConfig]:
    cfg = _resolve_columns(df, config)
    out = df.copy()
    intensity = out[cfg.sample_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    out["cleanp_sample_mean"] = intensity.mean(axis=1)
    out["cleanp_sample_max"] = intensity.max(axis=1)
    out["cleanp_sample_rsd"] = np.where(
        out["cleanp_sample_mean"] > 0,
        intensity.std(axis=1, ddof=1) / out["cleanp_sample_mean"] * 100.0,
        100.0,
    )

    if cfg.blank_cols:
        blank = out[cfg.blank_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        out["cleanp_blank_mean"] = blank.mean(axis=1)
        out["cleanp_blank_ratio_sample_over_blank"] = np.where(
            out["cleanp_blank_mean"] > 0,
            out["cleanp_sample_mean"] / out["cleanp_blank_mean"],
            np.inf,
        )
    else:
        out["cleanp_blank_mean"] = 0.0
        out["cleanp_blank_ratio_sample_over_blank"] = np.inf

    rsd_cols = cfg.qc_cols if cfg.rsd_use_qc_if_available and cfg.qc_cols and len(cfg.qc_cols) >= 2 else cfg.sample_cols
    rsd_vals = out[rsd_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    rsd_mean = rsd_vals.mean(axis=1)
    out["cleanp_rsd"] = np.where(rsd_mean > 0, rsd_vals.std(axis=1, ddof=1) / rsd_mean * 100.0, 100.0)

    mz = pd.to_numeric(out[cfg.mz_col], errors="coerce")
    out["cleanp_mass_defect"] = _mass_defect(mz)
    out["cleanp_rmd_ppm"] = _relative_mass_defect_ppm(mz)
    return out, cfg


def apply_qc_filters(df: pd.DataFrame, config: CleanPConfig) -> tuple[pd.DataFrame, dict]:
    out, cfg = add_qc_metrics(df, config)
    reasons = [[] for _ in range(len(out))]
    keep = np.ones(len(out), dtype=bool)

    def mark(mask, reason):
        nonlocal keep
        mask_arr = np.asarray(mask, dtype=bool)
        for i in np.where(mask_arr)[0]:
            reasons[i].append(reason)
        keep &= ~mask_arr

    if cfg.min_mean_intensity > 0:
        mark(out["cleanp_sample_mean"] < cfg.min_mean_intensity, "low_mean_intensity")

    if cfg.do_blank_filter and cfg.blank_cols:
        mark(
            (out["cleanp_blank_mean"] > 0) &
            (out["cleanp_sample_mean"] < out["cleanp_blank_mean"] * cfg.blank_sample_ratio_threshold),
            "blank_dominant",
        )
        if cfg.blank_absolute_threshold > 0:
            mark(
                (out["cleanp_blank_mean"] > 0) & (out["cleanp_sample_mean"] <= cfg.blank_absolute_threshold),
                "blank_ghost_peak",
            )

    if cfg.do_rsd_filter:
        mark(out["cleanp_rsd"] > cfg.rsd_threshold, "high_rsd")

    if cfg.do_mass_defect_filter:
        lo, hi = cfg.allowed_mass_defect_range
        md = out["cleanp_mass_defect"]
        mark(md.notna() & ((md < lo) | (md > hi)), "unusual_mass_defect")

    if cfg.do_rmd_filter:
        lo, hi = cfg.allowed_rmd_range
        rmd = out["cleanp_rmd_ppm"]
        mark(rmd.notna() & ((rmd < lo) | (rmd > hi)), "rmd_out_of_range")

    out["cleanp_qc_keep"] = keep
    out["cleanp_exclusion_reasons"] = [";".join(x) if x else "" for x in reasons]

    stats = {
        "initial_peaks": int(len(out)),
        "after_qc_filtering": int(out["cleanp_qc_keep"].sum()),
        "removed_by_qc": int((~out["cleanp_qc_keep"]).sum()),
        "reason_counts": pd.Series([r for sub in reasons for r in sub]).value_counts().to_dict(),
    }
    return out, stats


def _candidate_pairs_by_rt(df: pd.DataFrame, cfg: CleanPConfig) -> list[tuple[int, int]]:
    mz = pd.to_numeric(df[cfg.mz_col], errors="coerce").to_numpy()
    rt = pd.to_numeric(df[cfg.rt_col], errors="coerce").to_numpy()
    valid = np.where(np.isfinite(mz) & np.isfinite(rt))[0]
    order = valid[np.argsort(rt[valid])]
    pairs: list[tuple[int, int]] = []
    for pos, i in enumerate(order):
        jpos = pos + 1
        while jpos < len(order) and rt[order[jpos]] - rt[i] <= cfg.rt_tolerance:
            pairs.append((i, int(order[jpos])))
            jpos += 1
    return pairs


def infer_feature_links(df: pd.DataFrame, config: CleanPConfig) -> pd.DataFrame:
    cfg = _resolve_columns(df, config)
    mzs = pd.to_numeric(df[cfg.mz_col], errors="coerce").to_numpy()
    ids = df[cfg.id_col].tolist()
    links = []

    def add_link(i, j, link_type, label, delta_observed, delta_expected, score=1.0):
        links.append({
            "source_id": ids[i],
            "target_id": ids[j],
            "source_index": int(i),
            "target_index": int(j),
            "link_type": link_type,
            "label": label,
            "mz_source": float(mzs[i]),
            "mz_target": float(mzs[j]),
            "delta_observed": float(delta_observed),
            "delta_expected": float(delta_expected),
            "abs_error_da": float(abs(delta_observed - delta_expected)),
            "score": float(score),
        })

    adducts = {}
    if cfg.ion_mode in ("positive", "both", "unknown"):
        adducts.update(cfg.adducts_positive)
    if cfg.ion_mode in ("negative", "both", "unknown"):
        adducts.update(cfg.adducts_negative)

    adduct_deltas = []
    names = list(adducts.items())
    for a_name, (a_z, a_mass) in names:
        for b_name, (b_z, b_mass) in names:
            if a_name >= b_name:
                continue
            # Currently only compare same-charge singly charged adducts exactly.
            if a_z == 1 and b_z == 1:
                adduct_deltas.append((f"{a_name}<->{b_name}", abs(a_mass - b_mass)))

    for i, j in _candidate_pairs_by_rt(df, cfg):
        if not np.isfinite(mzs[i]) or not np.isfinite(mzs[j]):
            continue
        delta = abs(mzs[j] - mzs[i])
        tol = max(_tol_da(mzs[i], cfg), _tol_da(mzs[j], cfg))

        if cfg.do_isotope_links:
            # z=1 and z=2 isotope spacings
            for z in (1, 2):
                expected = NEUTRON / z
                if abs(delta - expected) <= tol:
                    add_link(i, j, "isotope", f"M+1 z={z}", delta, expected, score=1.0)
                    break

        if cfg.do_adduct_links:
            for label, expected in adduct_deltas:
                if abs(delta - expected) <= tol:
                    add_link(i, j, "adduct", label, delta, expected, score=0.9)
                    break

        if cfg.do_neutral_loss_links:
            for label, expected in cfg.neutral_losses.items():
                if abs(delta - expected) <= tol:
                    add_link(i, j, "neutral_loss", label, delta, expected, score=0.8)
                    break

        if cfg.do_dimer_links:
            lo, hi = sorted((mzs[i], mzs[j]))
            expected = 2 * lo - PROTON
            if abs(hi - expected) <= tol:
                add_link(i, j, "dimer", "[2M+H]+ approx", hi - lo, expected - lo, score=0.6)

    return pd.DataFrame(links)


def cluster_features(df: pd.DataFrame, links: pd.DataFrame, config: CleanPConfig) -> pd.DataFrame:
    cfg = _resolve_columns(df, config)
    out = df.copy()
    ids = out[cfg.id_col].tolist()
    uf = UnionFind(ids)
    if not links.empty:
        for row in links.itertuples(index=False):
            uf.union(row.source_id, row.target_id)

    roots = [uf.find(x) for x in ids]
    root_to_cluster = {root: k for k, root in enumerate(sorted(set(roots), key=lambda x: str(x)), start=1)}
    out["cleanp_cluster_id"] = [root_to_cluster[r] for r in roots]
    if links.empty:
        degree = pd.Series(0, index=ids)
    else:
        degree = pd.concat([links["source_id"], links["target_id"]]).value_counts()
    out["cleanp_graph_degree"] = [int(degree.get(x, 0)) for x in ids]
    return out


def select_representatives(df: pd.DataFrame, config: CleanPConfig) -> pd.DataFrame:
    cfg = _resolve_columns(df, config)
    out = df.copy()
    if "cleanp_cluster_id" not in out.columns:
        out["cleanp_cluster_id"] = np.arange(1, len(out) + 1)
    if "cleanp_graph_degree" not in out.columns:
        out["cleanp_graph_degree"] = 0
    if "cleanp_qc_keep" not in out.columns:
        out["cleanp_qc_keep"] = True
    if "cleanp_sample_mean" not in out.columns:
        out, _ = add_qc_metrics(out, cfg)

    ann_score = np.zeros(len(out), dtype=float)
    if cfg.prefer_annotated and cfg.annotation_col and cfg.annotation_col in out.columns:
        ann = out[cfg.annotation_col].fillna("").astype(str)
        ann_score = (~ann.str.lower().isin(["", "unknown", "nan", "null"])).astype(float).to_numpy()

    intensity = np.log10(out["cleanp_sample_mean"].astype(float).clip(lower=0) + 1.0).to_numpy()
    degree = out["cleanp_graph_degree"].astype(float).to_numpy()
    qc_penalty = (~out["cleanp_qc_keep"].astype(bool)).astype(float).to_numpy() * 10.0

    if cfg.representative_strategy == "intensity":
        score = intensity + 0.5 * ann_score - qc_penalty
    elif cfg.representative_strategy == "degree":
        score = degree + 0.5 * ann_score - qc_penalty
    else:
        score = intensity + degree + 0.5 * ann_score - qc_penalty

    out["cleanp_representative_score"] = score
    out["cleanp_is_representative"] = False
    out["cleanp_representative_id"] = None

    for cluster_id, g in out.groupby("cleanp_cluster_id", sort=False):
        idx = g["cleanp_representative_score"].astype(float).idxmax()
        rep_id = out.loc[idx, cfg.id_col]
        out.loc[idx, "cleanp_is_representative"] = True
        out.loc[g.index, "cleanp_representative_id"] = rep_id

    out["cleanp_final_keep"] = out["cleanp_qc_keep"].astype(bool)
    if cfg.keep_representatives_only:
        out["cleanp_final_keep"] &= out["cleanp_is_representative"].astype(bool)
    return out


def process_cleanup_enhanced(df: pd.DataFrame, config: Optional[CleanPConfig] = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Full MS-CleanP enhanced workflow.

    Returns:
        cleaned_df: rows kept for downstream analysis.
        annotated_df: all rows with audit/cluster columns.
        stats: dictionary for reporting.
    """
    cfg = config or CleanPConfig()
    qc_df, qc_stats = apply_qc_filters(df.copy(), cfg)
    cfg = _resolve_columns(qc_df, cfg)

    # Link only QC-kept rows. Dropped rows remain annotated but do not define clusters.
    link_input = qc_df[qc_df["cleanp_qc_keep"]].copy()
    links = infer_feature_links(link_input, cfg) if len(link_input) else pd.DataFrame()
    clustered_kept = cluster_features(link_input, links, cfg) if len(link_input) else link_input

    # Merge cluster annotations back to all rows.
    merge_cols = [cfg.id_col, "cleanp_cluster_id", "cleanp_graph_degree"]
    annotated = qc_df.merge(clustered_kept[merge_cols], on=cfg.id_col, how="left")
    max_cluster = int(annotated["cleanp_cluster_id"].max()) if annotated["cleanp_cluster_id"].notna().any() else 0
    missing = annotated["cleanp_cluster_id"].isna()
    annotated.loc[missing, "cleanp_cluster_id"] = np.arange(max_cluster + 1, max_cluster + 1 + missing.sum())
    annotated["cleanp_cluster_id"] = annotated["cleanp_cluster_id"].astype(int)
    annotated["cleanp_graph_degree"] = annotated["cleanp_graph_degree"].fillna(0).astype(int)

    annotated = select_representatives(annotated, cfg)
    cleaned = annotated[annotated["cleanp_final_keep"]].copy()

    stats = {
        **qc_stats,
        "links_total": int(len(links)),
        "links_by_type": links["link_type"].value_counts().to_dict() if not links.empty else {},
        "clusters_total": int(annotated["cleanp_cluster_id"].nunique()),
        "representatives_total": int(annotated["cleanp_is_representative"].sum()),
        "final_peaks": int(len(cleaned)),
        "removed_peaks": int(len(annotated) - len(cleaned)),
        "retention_rate": float(len(cleaned) / len(annotated) * 100.0) if len(annotated) else 0.0,
    }
    annotated.attrs["cleanp_links"] = links
    return cleaned, annotated, stats


# Backward-compatible API for existing server.py.
def run_blank_subtraction(df: pd.DataFrame, blank_keyword: str = "Blank", ratio_threshold: float = 3.0) -> pd.DataFrame:
    cfg = CleanPConfig(blank_keyword=blank_keyword, do_rsd_filter=False, do_mass_defect_filter=False, do_rmd_filter=False,
                       blank_sample_ratio_threshold=ratio_threshold, keep_representatives_only=False)
    qc, _ = apply_qc_filters(df, cfg)
    return qc[qc["cleanp_qc_keep"]].copy()


def run_rsd_filtering(df: pd.DataFrame, group_prefix: str | None = None, rsd_threshold: float = 30.0) -> pd.DataFrame:
    sample_cols = None
    if group_prefix:
        sample_cols = [c for c in df.columns if group_prefix.lower() in str(c).lower() and "_Intensity" in str(c)]
    cfg = CleanPConfig(sample_cols=sample_cols, do_blank_filter=False, do_mass_defect_filter=False, do_rmd_filter=False,
                       rsd_threshold=rsd_threshold, keep_representatives_only=False)
    qc, _ = apply_qc_filters(df, cfg)
    return qc[qc["cleanp_qc_keep"]].copy()


def process_cleanup(
    df: pd.DataFrame,
    do_blank_sub: bool = True,
    blank_ratio: float = 3.0,
    do_rsd_filter: bool = True,
    rsd_threshold: float = 30.0,
    enable_ms_cleanr_like: bool = True,
    keep_representatives_only: bool = True,
    mz_tolerance: float = 0.01,
    rt_tolerance: float = 0.10,
    ion_mode: Literal["positive", "negative", "both", "unknown"] = "unknown",
) -> tuple[pd.DataFrame, dict]:
    """
    Compatibility wrapper returning (filtered_df, stats), matching the original MS_CleanP.py.
    Additional audit/cluster columns are preserved in filtered_df.
    """
    cfg = CleanPConfig(
        do_blank_filter=do_blank_sub,
        blank_sample_ratio_threshold=blank_ratio,
        do_rsd_filter=do_rsd_filter,
        rsd_threshold=rsd_threshold,
        do_mass_defect_filter=enable_ms_cleanr_like,
        do_rmd_filter=enable_ms_cleanr_like,
        do_isotope_links=enable_ms_cleanr_like,
        do_adduct_links=enable_ms_cleanr_like,
        do_neutral_loss_links=enable_ms_cleanr_like,
        keep_representatives_only=keep_representatives_only,
        mz_tolerance=mz_tolerance,
        rt_tolerance=rt_tolerance,
        ion_mode=ion_mode,
    )
    cleaned, annotated, stats = process_cleanup_enhanced(df, cfg)
    # Expose full audit table via attrs for callers that know to inspect it.
    cleaned.attrs["cleanp_annotated_table"] = annotated
    cleaned.attrs["cleanp_links"] = annotated.attrs.get("cleanp_links", pd.DataFrame())
    return cleaned, stats


def export_cleanp_results(cleaned: pd.DataFrame, annotated: pd.DataFrame, output_prefix: str) -> dict[str, str]:
    paths = {
        "cleaned": f"{output_prefix}_cleaned.csv",
        "annotated": f"{output_prefix}_annotated.csv",
        "links": f"{output_prefix}_links.csv",
    }
    cleaned.to_csv(paths["cleaned"], index=False)
    annotated.to_csv(paths["annotated"], index=False)
    links = annotated.attrs.get("cleanp_links", pd.DataFrame())
    links.to_csv(paths["links"], index=False)
    return paths


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MS-CleanP enhanced feature cleanup for MS-DIAL-like tables")
    parser.add_argument("input_csv")
    parser.add_argument("--output-prefix", default="cleanp")
    parser.add_argument("--blank-ratio", type=float, default=3.0)
    parser.add_argument("--rsd", type=float, default=30.0)
    parser.add_argument("--mz-tol", type=float, default=0.01)
    parser.add_argument("--rt-tol", type=float, default=0.10)
    parser.add_argument("--ion-mode", choices=["positive", "negative", "both", "unknown"], default="unknown")
    parser.add_argument("--keep-all-cluster-members", action="store_true")
    args = parser.parse_args()

    table = pd.read_csv(args.input_csv)
    config = CleanPConfig(
        blank_sample_ratio_threshold=args.blank_ratio,
        rsd_threshold=args.rsd,
        mz_tolerance=args.mz_tol,
        rt_tolerance=args.rt_tol,
        ion_mode=args.ion_mode,
        keep_representatives_only=not args.keep_all_cluster_members,
    )
    cleaned_df, annotated_df, summary = process_cleanup_enhanced(table, config)
    paths = export_cleanp_results(cleaned_df, annotated_df, args.output_prefix)
    print({"stats": summary, "paths": paths})
