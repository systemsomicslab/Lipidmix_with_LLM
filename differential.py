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


def _one_way_f(groups):
    """一元配置 ANOVA の F 統計量と p 値（scipy があれば使用）。"""
    groups = [g[np.isfinite(g)] for g in groups]
    groups = [g for g in groups if g.size >= 2]
    k = len(groups)
    if k < 2:
        return math.nan, math.nan, k
    grand = np.concatenate(groups)
    n = grand.size
    grand_mean = grand.mean()
    ss_between = sum(g.size * (g.mean() - grand_mean) ** 2 for g in groups)
    ss_within = sum(((g - g.mean()) ** 2).sum() for g in groups)
    df_b, df_w = k - 1, n - k
    if df_w <= 0 or ss_within == 0:
        return math.nan, math.nan, k
    f = (ss_between / df_b) / (ss_within / df_w)
    try:
        from scipy import stats
        p = stats.f.sf(f, df_b, df_w)
    except Exception:
        # 近似フォールバック: F を chi2 近似（df_b * F ~ chi2(df_b)）
        x = df_b * f
        # 生存関数の粗い近似（df_b<=~6 で妥当）。scipy 推奨。
        p = math.exp(-x / 2.0) * sum((x / 2.0) ** i / math.factorial(i)
                                     for i in range(int(df_b // 2) + 1))
        p = min(max(p, 0.0), 1.0)
    return f, p, k


def one_way_anova(matrix, feature_names, group_labels):
    """factor の全水準について特徴量ごとに一元配置 ANOVA を実行する。"""
    matrix = np.asarray(matrix, dtype=float)
    labels = np.asarray(group_labels)
    levels = [lv for lv in sorted(set(labels.tolist())) if lv is not None]
    results = []
    for j, name in enumerate(feature_names):
        col = matrix[:, j]
        groups = [col[labels == lv] for lv in levels]
        f, p, k = _one_way_f(groups)
        results.append({"feature": name, "F": f, "p": p, "n_groups": k})
    return results


def add_fdr(results):
    """results の p 列に BH-FDR を適用し、各要素に q を付与する。"""
    qs = bh_fdr([r["p"] for r in results])
    for r, q in zip(results, qs):
        r["q"] = q
    return results


def volcano_data(results, q_thr=0.05, log2fc_thr=1.0):
    """volcano 用の点列（log2fc, -log10 p, 有意フラグ up/down/ns）を返す。"""
    points = []
    for r in results:
        p = r.get("p")
        q = r.get("q", p)
        fc = r.get("log2fc")
        neg_log10_p = (-math.log10(p)) if (p is not None and math.isfinite(p) and p > 0) else math.nan
        sig = "ns"
        if q is not None and math.isfinite(q) and q <= q_thr and fc is not None and math.isfinite(fc):
            if fc >= log2fc_thr:
                sig = "up"
            elif fc <= -log2fc_thr:
                sig = "down"
        points.append({"feature": r["feature"], "log2fc": fc,
                       "neg_log10_p": neg_log10_p, "sig": sig})
    return points


def check_confounding(group_labels, batch_labels):
    """各群が単一バッチに偏るか（群 ⟂ バッチの交絡）を判定する。"""
    if not batch_labels or all(b is None for b in batch_labels):
        return {"confounded": False, "detail": "バッチ情報が無いため交絡判定不可。"}
    by_group: dict[str, set] = {}
    for g, b in zip(group_labels, batch_labels):
        by_group.setdefault(g, set()).add(b)
    single = {g: next(iter(bs)) for g, bs in by_group.items() if len(bs) == 1}
    confounded = len(single) == len(by_group) and len(set(single.values())) > 1
    if confounded:
        detail = "各群が単一バッチに対応し、処理効果と測定バッチを分離できません: " + \
                 ", ".join(f"{g}->{b}" for g, b in single.items())
    else:
        detail = "群とバッチは交絡していません（または部分的）。"
    return {"confounded": confounded, "detail": detail}


def summarize_two_group(results, q_thr=0.05, log2fc_thr=1.0, top_n=15):
    """2群比較結果の有意 up/down 件数と上位特徴量を要約する。"""
    tested = [r for r in results if r.get("p") is not None and math.isfinite(r["p"])]

    def is_sig(r):
        q = r.get("q", r.get("p"))
        return q is not None and math.isfinite(q) and q <= q_thr and \
            r.get("log2fc") is not None and abs(r["log2fc"]) >= log2fc_thr

    sig = [r for r in tested if is_sig(r)]
    n_up = sum(1 for r in sig if r["log2fc"] >= log2fc_thr)
    n_down = sum(1 for r in sig if r["log2fc"] <= -log2fc_thr)
    top = sorted(sig, key=lambda r: r.get("q", r.get("p", 1.0)))[:top_n]
    return {"n_tested": len(tested), "n_significant": len(sig),
            "n_up": n_up, "n_down": n_down, "top": top}
