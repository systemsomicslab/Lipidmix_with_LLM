"""前処理後のサンプル×特徴量行列に対する差次的解析（純ロジック層、MCP 非依存）。

2群 Welch t 検定・一元配置 ANOVA・log2 fold change・BH-FDR・volcano・交絡検出を提供する。
行列は行=サンプル、列=特徴量。
"""

from __future__ import annotations

import math

import numpy as np


def bh_fdr(pvalues):
    """Benjamini-Hochberg で p 値を q 値に補正する（NaN は位置保持で除外）。"""
    p = np.asarray(pvalues, dtype=float)
    q = np.full(p.shape, np.nan)
    finite_idx = np.where(np.isfinite(p))[0]
    if finite_idx.size == 0:
        return q.tolist()
    pv = p[finite_idx]
    order = np.argsort(pv)
    ranked = pv[order]
    m = ranked.size
    adj = ranked * m / (np.arange(1, m + 1))
    # 単調化（後ろから最小を累積）
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    adj = np.clip(adj, 0.0, 1.0)
    out = np.empty(m)
    out[order] = adj
    q[finite_idx] = out
    return q.tolist()


def _welch_t(a, b):
    """Welch t 統計量と両側 p 値。scipy があれば使い、無ければ正規近似。"""
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if a.size < 2 or b.size < 2:
        return math.nan, math.nan
    va, vb = a.var(ddof=1), b.var(ddof=1)
    na, nb = a.size, b.size
    denom = va / na + vb / nb
    if denom <= 0:
        return math.nan, math.nan
    t = (a.mean() - b.mean()) / math.sqrt(denom)
    df = denom ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    try:
        from scipy import stats
        p = 2.0 * stats.t.sf(abs(t), df)
    except Exception:
        # 正規近似フォールバック（df 大で妥当、小 df では保守的）
        p = math.erfc(abs(t) / math.sqrt(2.0))
    return t, p


def _log2fc(mean_a, mean_b, pseudo_count):
    num = max(mean_a, 0.0) + pseudo_count
    den = max(mean_b, 0.0) + pseudo_count
    return math.log2(num / den)


def two_group_test(matrix, feature_names, group_labels, group_a, group_b,
                   *, log2=True, pseudo_count=1.0):
    """群 a/b について特徴量ごとに Welch t 検定と log2 fold change を計算する。"""
    matrix = np.asarray(matrix, dtype=float)
    labels = np.asarray(group_labels)
    idx_a = np.where(labels == group_a)[0]
    idx_b = np.where(labels == group_b)[0]
    results = []
    for j, name in enumerate(feature_names):
        col = matrix[:, j]
        a, b = col[idx_a], col[idx_b]
        mean_a = float(np.nanmean(a)) if a.size else math.nan
        mean_b = float(np.nanmean(b)) if b.size else math.nan
        t, p = _welch_t(a, b)
        fc = _log2fc(mean_a, mean_b, pseudo_count) if (log2 and math.isfinite(mean_a) and math.isfinite(mean_b)) else math.nan
        results.append({
            "feature": name, "mean_a": mean_a, "mean_b": mean_b,
            "log2fc": fc, "t": t, "p": p,
        })
    return results
