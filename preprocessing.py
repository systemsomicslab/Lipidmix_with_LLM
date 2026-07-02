"""MS-DIAL ロード後のサンプル×特徴量行列に対する前処理・QC（純ロジック層、MCP 非依存）。

役割検出・ブランク処理・正規化・QCドリフト補正・特徴量フィルタ・欠損補完を提供する。
knowledge_store.py / peak_verification.py と同じく MCP に依存しない純関数群。
行列は行=サンプル、列=特徴量（test_arf.build_pca_matrix の向き）。
"""

from __future__ import annotations

import numpy as np

DEFAULT_ROLE_TOKENS: dict[str, set[str]] = {
    "qc": {"qc"},
    "blank": {"blank"},
}


def _segments(text: str) -> set[str]:
    return {seg for seg in str(text).casefold().split("_") if seg}


def detect_sample_roles(
    sample_names: list[str],
    class_ids: dict[str, str] | None = None,
    config: dict | None = None,
) -> dict[str, str]:
    """各サンプルを sample/qc/blank に分類する。

    ファイル名と Class ID の `_` 区切りセグメントを大小無視でトークン照合する。
    blank を qc より優先評価する（曖昧語衝突を避けるため決定的順序）。
    """
    cfg = config or {}
    tokens = {
        "qc": {t.casefold() for t in cfg.get("qc_tokens", DEFAULT_ROLE_TOKENS["qc"])},
        "blank": {t.casefold() for t in cfg.get("blank_tokens", DEFAULT_ROLE_TOKENS["blank"])},
    }
    class_ids = class_ids or {}
    roles: dict[str, str] = {}
    for name in sample_names:
        segs = _segments(name) | _segments(class_ids.get(name, ""))
        if segs & tokens["blank"]:
            roles[name] = "blank"
        elif segs & tokens["qc"]:
            roles[name] = "qc"
        else:
            roles[name] = "sample"
    return roles


def _reference_rows(matrix, roles, sample_names):
    """QC 行が特定できれば QC のみ、無ければ全行を参照集合として返す。"""
    if roles and sample_names:
        qc_idx = [i for i, n in enumerate(sample_names) if roles.get(n) == "qc"]
        if qc_idx:
            return matrix[qc_idx], True
    return matrix, False


def normalize(matrix, method, roles=None, sample_names=None):
    """行（サンプル）ごとにスケーリングして測定量の系統差を補正する。

    tic=行総和, median=行中央値, pqn=Probabilistic Quotient Normalization
    （参照は QC 中央値、無ければ全サンプル中央値）。none は恒等。
    """
    matrix = np.asarray(matrix, dtype=float)
    report = {"method": method}
    n = matrix.shape[0]
    if method == "none":
        return matrix, np.ones(n), report

    if method == "tic":
        factors = np.nansum(matrix, axis=1)
    elif method == "median":
        factors = np.nanmedian(matrix, axis=1)
    elif method == "pqn":
        ref_rows, used_qc = _reference_rows(matrix, roles, sample_names)
        reference = np.nanmedian(ref_rows, axis=0)  # 参照スペクトル
        safe_ref = np.where(reference == 0, np.nan, reference)
        quotients = matrix / safe_ref
        factors = np.nanmedian(quotients, axis=1)
        report["pqn_reference"] = "qc_median" if used_qc else "all_sample_median"
    else:
        raise ValueError(f"unknown normalization method: {method!r}")

    factors = np.where((factors == 0) | ~np.isfinite(factors), np.nan, factors)
    scaled = matrix / factors[:, None]
    # スケール後の全体水準を保つため参照係数の中央値を掛け戻す（TIC/median 用）
    if method in ("tic", "median"):
        scaled = scaled * np.nanmedian(factors)
    report["factors_finite"] = int(np.isfinite(factors).sum())
    return scaled, factors, report
