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


def _rows_for_role(matrix, roles, sample_names, role):
    """指定ロールに該当する行のインデックスを抽出し、対応する行部分行列を返す。"""
    idx = [i for i, n in enumerate(sample_names) if roles.get(n) == role]
    return matrix[idx] if idx else None


def blank_filter(matrix, roles, sample_names, min_fold=3.0):
    """生体試料平均 < min_fold × ブランク平均 の特徴量を背景として除去する。"""
    matrix = np.asarray(matrix, dtype=float)
    n_features = matrix.shape[1]
    blanks = _rows_for_role(matrix, roles, sample_names, "blank")
    samples = _rows_for_role(matrix, roles, sample_names, "sample")
    if blanks is None or samples is None:
        return np.ones(n_features, dtype=bool), {
            "removed": 0,
            "caveat": "ブランクまたは生体試料が無いため背景除去は未実施。",
        }
    blank_mean = np.nanmean(blanks, axis=0)
    sample_mean = np.nanmean(samples, axis=0)
    # ブランクがゼロの特徴量は常に keep（除算回避）
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(blank_mean > 0, sample_mean / blank_mean, np.inf)
    keep = ratio >= min_fold
    return keep, {"removed": int((~keep).sum()), "min_fold": min_fold}


def normalize(
    matrix,
    method: str,
    roles: dict[str, str] | None = None,
    sample_names: list[str] | None = None,
):
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
    # スケール後の全体水準を保つため、TIC/median は除数そのものを参照係数の
    # 中央値で正規化する（factors が実際に適用した除数と一致するようにする）。
    if method in ("tic", "median"):
        factors = factors / np.nanmedian(factors)
    scaled = matrix / factors[:, None]
    report["factors_finite"] = int(np.isfinite(factors).sum())
    return scaled, factors, report


def _moving_median(values, window=5):
    """奇数窓の移動中央値（端は縮小窓）。numpy のみで LOESS 相当の平滑化。"""
    n = len(values)
    half = window // 2
    smoothed = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        smoothed[i] = np.nanmedian(values[lo:hi])
    return smoothed


def qc_drift_correct(matrix, roles, sample_names, run_order, min_qc=4, window=5):
    """QC を注入順に平滑化した系統ドリフトで、特徴量ごとに全サンプルを補正する。

    注入順が取れない、または QC が min_qc 未満なら未実施（skipped）。
    プールQC が層別と疑われる場合の警告は呼び出し側（server）で付す。
    """
    matrix = np.asarray(matrix, dtype=float)
    orders = np.array([run_order.get(n) for n in sample_names], dtype=object)
    have_order = np.array([o is not None for o in orders])
    qc_mask = np.array([roles.get(n) == "qc" and run_order.get(n) is not None
                        for n in sample_names])
    if not have_order.all() or qc_mask.sum() < min_qc:
        return matrix, {
            "status": "skipped",
            "caveat": "注入順が欠落、または QC が不足のためドリフト補正は未実施。",
            "qc_used": int(qc_mask.sum()),
        }

    order_int = np.array([int(o) for o in orders])
    qc_order = order_int[qc_mask]
    sort = np.argsort(qc_order)
    qc_order_sorted = qc_order[sort]
    n_qc = qc_mask.sum()
    # 窓サイズをQCサンプル数以下に制限し、奇数を保証
    eff_window = min(window, int(n_qc))
    if eff_window % 2 == 0:
        eff_window = max(1, eff_window - 1)
    corrected = matrix.copy()
    for j in range(matrix.shape[1]):
        qc_vals = matrix[qc_mask, j][sort]
        trend = _moving_median(qc_vals, eff_window)
        global_level = np.nanmedian(qc_vals)
        if not np.isfinite(global_level) or global_level == 0:
            continue
        # 全サンプル注入順に対しトレンドを内挿し、補正係数=global_level/trend を適用
        interp_trend = np.interp(order_int, qc_order_sorted, trend)
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(interp_trend > 0, global_level / interp_trend, 1.0)
        corrected[:, j] = matrix[:, j] * factor
    return corrected, {"status": "applied", "qc_used": int(qc_mask.sum())}


def qc_rsd_filter(matrix, roles, sample_names, max_rsd=0.30):
    """QC 群での相対標準偏差 (SD/mean) が max_rsd を超える特徴量を除去する。"""
    matrix = np.asarray(matrix, dtype=float)
    n_features = matrix.shape[1]
    qc = _rows_for_role(matrix, roles, sample_names, "qc")
    if qc is None or qc.shape[0] < 2:
        return np.ones(n_features, dtype=bool), {
            "removed": 0,
            "caveat": "QC が無い/不足のため RSD フィルタは未実施。",
        }
    mean = np.nanmean(qc, axis=0)
    sd = np.nanstd(qc, axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsd = np.where(mean > 0, sd / mean, np.inf)
    keep = rsd <= max_rsd
    return keep, {"removed": int((~keep).sum()), "max_rsd": max_rsd}


def impute(matrix, method="half_min"):
    """行列生成後に残る欠損 (NaN) を補完する。

    half_min=特徴量最小値の半分（既定）/ knn=sklearn KNNImputer /
    column_mean=列平均（現行互換）/ none=補完しない。
    """
    matrix = np.asarray(matrix, dtype=float)
    report = {"method": method, "missing": int(np.isnan(matrix).sum())}
    if method == "none" or report["missing"] == 0:
        return matrix, report
    out = matrix.copy()
    if method == "half_min":
        col_min = np.nanmin(np.where(np.isnan(out), np.inf, out), axis=0)
        col_min = np.where(np.isfinite(col_min), col_min, 0.0)
        fill = col_min / 2.0
        idx = np.where(np.isnan(out))
        out[idx] = np.take(fill, idx[1])
    elif method == "column_mean":
        col_mean = np.nanmean(out, axis=0)
        col_mean = np.where(np.isfinite(col_mean), col_mean, 0.0)
        idx = np.where(np.isnan(out))
        out[idx] = np.take(col_mean, idx[1])
    elif method == "knn":
        from sklearn.impute import KNNImputer
        out = KNNImputer(n_neighbors=min(5, max(1, out.shape[0] - 1))).fit_transform(out)
    else:
        raise ValueError(f"unknown imputation method: {method!r}")
    return out, report


def preprocess(matrix, sample_names, roles, run_order, recipe):
    """順序: ブランク除去 → 正規化 → ドリフト補正 → QC RSD フィルタ → 補完。

    列（特徴量）を落とすステップはマスクを蓄積し、最後にまとめて適用する。
    """
    matrix = np.asarray(matrix, dtype=float)
    n_features = matrix.shape[1]
    keep = np.ones(n_features, dtype=bool)
    applied: list[str] = []
    caveats: list[str] = []
    steps: dict = {}

    if recipe.get("blank_min_fold") is not None:
        mask, rep = blank_filter(matrix, roles, sample_names, recipe["blank_min_fold"])
        keep &= mask
        applied.append("blank_filter")
        steps["blank_filter"] = rep
        if "caveat" in rep:
            caveats.append(rep["caveat"])

    method = recipe.get("normalize", "none")
    if method != "none":
        matrix, _, rep = normalize(matrix, method, roles, sample_names)
        applied.append("normalize")
        steps["normalize"] = rep

    if recipe.get("drift_correct"):
        matrix, rep = qc_drift_correct(matrix, roles, sample_names, run_order)
        applied.append("drift_correct")
        steps["drift_correct"] = rep
        if "caveat" in rep:
            caveats.append(rep["caveat"])

    if recipe.get("max_qc_rsd") is not None:
        mask, rep = qc_rsd_filter(matrix, roles, sample_names, recipe["max_qc_rsd"])
        keep &= mask
        applied.append("qc_rsd_filter")
        steps["qc_rsd_filter"] = rep
        if "caveat" in rep:
            caveats.append(rep["caveat"])

    matrix = matrix[:, keep]
    kept_idx = list(np.where(keep)[0])

    impute_method = recipe.get("impute", "half_min")
    matrix, rep = impute(matrix, impute_method)
    applied.append("impute")
    steps["impute"] = rep

    report = {
        "recipe_applied": applied,
        "steps": steps,
        "caveats": caveats,
        "features_before": n_features,
        "features_after": int(keep.sum()),
    }
    return matrix, kept_idx, report
