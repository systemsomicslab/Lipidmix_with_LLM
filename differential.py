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
