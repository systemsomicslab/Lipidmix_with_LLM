"""DatasetState: mzTab-M を読んだ後の正準モデル。spec §11 参照。

session.arf の ARF 専用構造とは完全に独立している。session.dataset スロットへ格納する。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from lipidmix.mztab.identity import derive_inchikey
from lipidmix.mztab.reader import extract_abundance_matrix
from lipidmix.mztab.validator import detect_quantification_measure, validate_mztab


class DatasetState:
    """mzTab-M 読み込み後の正準モデル。多変量解析の共通入口。"""

    def __init__(self):
        self.source_format: str = "mztab"
        self.source_files: dict[str, str] = {}          # path -> sha256
        self.quantification_measure: str | None = None  # peak_height | peak_area_above_zero
        self.quantification_confidence: str | None = None
        self.feature_matrix: np.ndarray | None = None   # shape (n_features, n_samples)
        self.sample_names: list[str] = []               # abundance 列名（= assay 識別子）
        self.feature_ids: list[str] = []                # SMF_ID 列
        self.assay_metadata: dict = {}                  # assay_id -> MTD 情報
        self.feature_metadata: dict = {}                # smf_id -> {name, mz, rt, inchikey, ...}
        self.sml_rows: list[dict] | None = None
        self.sme_rows: list[dict] | None = None
        self.validation_result: dict = {}
        self.inchikey_coverage: dict = {}

        # --- ジョブ由来フィールド（dataset_load(job_path=...) で設定） ---
        # job_path: analysis-job.json の絶対パス。mzTab-M を直接指定した場合は None。
        self.job_path: str | None = None
        # artifact_paths: role -> [絶対パス, ...] のマップ。
        # Console 出力の ARF / DCL / EIC へのルックアップに使う。
        self.artifact_paths: dict[str, list[str]] = {}


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_dataset_state(
    parse_result: dict,
    filename: str,
    source_path: str | Path,
) -> DatasetState:
    """parse_mztab() の戻り値から DatasetState を構築する。"""
    ds = DatasetState()

    # ファイルハッシュ
    p = Path(source_path)
    if p.is_file():
        ds.source_files[str(p)] = _sha256(p)

    # バリデーション
    ds.validation_result = validate_mztab(parse_result)

    # 定量種別
    measure, confidence = detect_quantification_measure(parse_result, filename)
    ds.quantification_measure = measure
    ds.quantification_confidence = confidence

    # abundance 行列
    matrix, sample_names, feature_ids = extract_abundance_matrix(parse_result)
    ds.feature_matrix = matrix
    ds.sample_names = sample_names
    ds.feature_ids = feature_ids

    # SMF メタデータ + InChIKey 導出
    smf_rows = parse_result["sections"].get("SMF", {}).get("rows", [])
    by_source: dict[str, int] = {"database_identifier": 0, "inchi_derived": 0, "smiles_derived": 0, "none": 0}
    for row in smf_rows:
        fid = row.get("SMF_ID", "")
        ik, src = derive_inchikey(
            row.get("database_identifier"),
            row.get("inchi"),
            row.get("smiles"),
        )
        ds.feature_metadata[fid] = {
            "name": row.get("chemical_name"),
            "mz": _to_float(row.get("mz_exp")),
            "rt": _to_float(row.get("rt_mean")),
            "inchikey": ik,
            "inchikey_source": src,
            "smiles": row.get("smiles"),
            "inchi": row.get("inchi"),
        }
        by_source[src] = by_source.get(src, 0) + 1

    with_ik = sum(v for k, v in by_source.items() if k != "none")
    ds.inchikey_coverage = {
        "total_features": len(smf_rows),
        "with_inchikey": with_ik,
        "by_source": by_source,
    }

    # SML / SME 行
    ds.sml_rows = parse_result["sections"].get("SML", {}).get("rows")
    ds.sme_rows = parse_result["sections"].get("SME", {}).get("rows")

    # assay メタデータ
    meta = parse_result.get("metadata", {})
    for k, v in meta.items():
        m = re.match(r"^assay\[(\d+)\]-(.+)$", k)
        if m:
            aid = f"assay[{m.group(1)}]"
            ds.assay_metadata.setdefault(aid, {})[m.group(2)] = v

    return ds


def _to_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
