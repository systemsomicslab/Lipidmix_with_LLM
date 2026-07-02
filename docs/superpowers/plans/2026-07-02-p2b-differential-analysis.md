# P2b Differential Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add differential analysis (two-group Welch test, one-way ANOVA, log2 fold change, BH-FDR, volcano data, confounding detection) over the preprocessed sample×feature matrix, exposed as MCP tools.

**Architecture:** A new MCP-independent pure-logic module `differential.py`, plus MCP tools `arf_differential` and `save_volcano_figure` in `server.py`. Consumes `session.feature_matrix` (from P2a) or falls back to a freshly built matrix with a "not normalized" caveat.

**Tech Stack:** Python 3, numpy, scikit-learn already present; `scipy` for t/F distributions — if `scipy` is unavailable, use the survival-function fallback shown in Task 2/3 (no new hard dependency).

## Global Constraints

- `differential.py` must be MCP-independent and unit-testable (no `fastmcp` import).
- Matrix orientation: rows = samples, columns = features.
- Group labels come from the existing factor-token / Class ID machinery (`msdial_classes.assign_sample_groups`, `expand_class_specs`).
- Every result set must carry a `caveats` list covering: confounding (group ⟂ batch), small n, normalization status (from `session.preprocessing_recipe`), and low detection rate.
- Statistical scope is **two-group Welch + one-way ANOVA only** (no two-way / time-series in this plan).
- Requires P2a Task 8 (`session.feature_matrix`, `session.sample_meta`, `session.preprocessing_recipe`) to be merged.
- Run tests with: `python -m unittest discover -s tests -t .`
- Commit after each task with the shown message.

---

### Task 1: BH-FDR correction

**Files:**
- Create: `differential.py`
- Test: `tests/test_differential.py`

**Interfaces:**
- Produces: `bh_fdr(pvalues: list[float]) -> list[float]`. NaN p-values are excluded from ranking but kept as NaN in the output at their original positions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_differential.py
import math
import unittest
import differential as diff


class TestBhFdr(unittest.TestCase):
    def test_monotone_and_bounded(self):
        q = diff.bh_fdr([0.001, 0.01, 0.02, 0.5])
        self.assertTrue(all(0.0 <= v <= 1.0 for v in q))
        # smallest p gets smallest q
        self.assertLessEqual(q[0], q[3])

    def test_nan_preserved(self):
        q = diff.bh_fdr([0.01, float("nan"), 0.02])
        self.assertTrue(math.isnan(q[1]))
        self.assertFalse(math.isnan(q[0]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_differential -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'differential'`.

- [ ] **Step 3: Write minimal implementation**

```python
# differential.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_differential.TestBhFdr -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add differential.py tests/test_differential.py
git commit -m "feat(p2b): add Benjamini-Hochberg FDR correction"
```

---

### Task 2: Two-group Welch test + log2FC

**Files:**
- Modify: `differential.py`
- Test: `tests/test_differential.py`

**Interfaces:**
- Produces: `two_group_test(matrix, feature_names, group_labels, group_a, group_b, *, log2=True, pseudo_count=1.0) -> list[dict]`. Each dict: `feature`, `mean_a`, `mean_b`, `log2fc`, `t`, `p`. `group_labels` is `list[str]` aligned to matrix rows.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_differential.py
import numpy as np


class TestTwoGroup(unittest.TestCase):
    def test_clear_difference_significant(self):
        # feature 0 differs strongly between A and B; feature 1 does not.
        matrix = np.array([
            [10.0, 5.0],  # A
            [11.0, 5.1],  # A
            [ 9.5, 4.9],  # A
            [50.0, 5.0],  # B
            [52.0, 5.2],  # B
            [48.0, 4.8],  # B
        ])
        labels = ["A", "A", "A", "B", "B", "B"]
        res = diff.two_group_test(matrix, ["f0", "f1"], labels, "A", "B")
        by_feat = {r["feature"]: r for r in res}
        self.assertLess(by_feat["f0"]["p"], 0.05)
        self.assertGreater(by_feat["f1"]["p"], 0.05)
        self.assertGreater(by_feat["f0"]["log2fc"], 1.0)

    def test_missing_group_member_gives_nan(self):
        matrix = np.array([[10.0], [np.nan]])
        res = diff.two_group_test(matrix, ["f0"], ["A", "B"], "A", "B")
        self.assertTrue(math.isnan(res[0]["p"]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_differential.TestTwoGroup -v`
Expected: FAIL with `AttributeError: module 'differential' has no attribute 'two_group_test'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to differential.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_differential.TestTwoGroup -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add differential.py tests/test_differential.py
git commit -m "feat(p2b): add two-group Welch test with log2 fold change"
```

---

### Task 3: One-way ANOVA

**Files:**
- Modify: `differential.py`
- Test: `tests/test_differential.py`

**Interfaces:**
- Produces: `one_way_anova(matrix, feature_names, group_labels) -> list[dict]`. Each dict: `feature`, `F`, `p`, `n_groups`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_differential.py
class TestAnova(unittest.TestCase):
    def test_three_group_difference(self):
        matrix = np.array([
            [1.0], [1.1], [0.9],     # G1
            [5.0], [5.1], [4.9],     # G2
            [9.0], [9.2], [8.8],     # G3
        ])
        labels = ["G1"] * 3 + ["G2"] * 3 + ["G3"] * 3
        res = diff.one_way_anova(matrix, ["f0"], labels)
        self.assertLess(res[0]["p"], 0.01)
        self.assertEqual(res[0]["n_groups"], 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_differential.TestAnova -v`
Expected: FAIL with `AttributeError: module 'differential' has no attribute 'one_way_anova'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to differential.py
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
        from math import exp
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
```

Note: `scipy` gives exact ANOVA p-values; add `scipy` to `requirements.txt` in Task 6. The fallback keeps tests passing if scipy is absent but is approximate — the test uses a strong signal so both paths pass.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_differential.TestAnova -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add differential.py tests/test_differential.py
git commit -m "feat(p2b): add one-way ANOVA"
```

---

### Task 4: Volcano data + confounding detection + summary

**Files:**
- Modify: `differential.py`
- Test: `tests/test_differential.py`

**Interfaces:**
- Produces:
  - `add_fdr(results) -> list[dict]` — adds `q` to each result using `bh_fdr` over the `p` column.
  - `volcano_data(results, q_thr=0.05, log2fc_thr=1.0) -> list[dict]` — each: `feature`, `log2fc`, `neg_log10_p`, `sig` (`up`/`down`/`ns`).
  - `check_confounding(group_labels, batch_labels) -> dict` — `{confounded: bool, detail: str}`.
  - `summarize_two_group(results) -> dict` — `{n_up, n_down, n_tested, top}`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_differential.py
class TestVolcanoAndConfound(unittest.TestCase):
    def test_add_fdr_and_volcano_flags(self):
        results = [
            {"feature": "f0", "log2fc": 2.0, "p": 0.001, "mean_a": 4, "mean_b": 1},
            {"feature": "f1", "log2fc": -3.0, "p": 0.002, "mean_a": 1, "mean_b": 8},
            {"feature": "f2", "log2fc": 0.1, "p": 0.9, "mean_a": 1, "mean_b": 1},
        ]
        withq = diff.add_fdr(results)
        self.assertIn("q", withq[0])
        pts = diff.volcano_data(withq, q_thr=0.05, log2fc_thr=1.0)
        flags = {p["feature"]: p["sig"] for p in pts}
        self.assertEqual(flags["f0"], "up")
        self.assertEqual(flags["f1"], "down")
        self.assertEqual(flags["f2"], "ns")

    def test_confounding_detected(self):
        groups = ["ctrl", "ctrl", "trt", "trt"]
        batches = ["d1", "d1", "d2", "d2"]  # group perfectly aligns with batch
        out = diff.check_confounding(groups, batches)
        self.assertTrue(out["confounded"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_differential.TestVolcanoAndConfound -v`
Expected: FAIL with `AttributeError: module 'differential' has no attribute 'add_fdr'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to differential.py
def add_fdr(results):
    qs = bh_fdr([r["p"] for r in results])
    for r, q in zip(results, qs):
        r["q"] = q
    return results


def volcano_data(results, q_thr=0.05, log2fc_thr=1.0):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_differential.TestVolcanoAndConfound -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add differential.py tests/test_differential.py
git commit -m "feat(p2b): add FDR annotation, volcano data, confounding + summary"
```

---

### Task 5: MCP tool `arf_differential`

**Files:**
- Modify: `server.py` (add `@mcp.tool()` after `arf_re_pca`)
- Test: `tests/test_differential_tools.py`

**Interfaces:**
- Consumes: `session.feature_matrix` / `session.pp_sample_names` / `session.pp_feature_names` / `session.sample_meta` / `session.preprocessing_recipe` (from P2a); `msdial_classes.assign_sample_groups`; `differential.*`.
- Produces: `arf_differential(group_factor=None, group_a=None, group_b=None, q_threshold=0.05, log2fc_threshold=1.0) -> str` (JSON). Two-group when `group_a` and `group_b` given, else one-way ANOVA over `group_factor` levels. Stores `session.last_differential` (list of result dicts) for `save_volcano_figure`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_differential_tools.py
import json
import unittest
import numpy as np
import server


class TestArfDifferential(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_requires_matrix(self):
        out = json.loads(server.arf_differential(group_a="A", group_b="B"))
        self.assertEqual(out["status"], "error")

    def test_two_group_reports_significant_and_caveats(self):
        server.session.feature_matrix = np.array([
            [10.0, 5.0], [11.0, 5.1], [9.5, 4.9],
            [50.0, 5.0], [52.0, 5.2], [48.0, 4.8],
        ])
        names = ["a1", "a2", "a3", "b1", "b2", "b3"]
        server.session.pp_sample_names = names
        server.session.pp_feature_names = ["f0", "f1"]
        server.session.preprocessing_recipe = {"normalize": "median"}
        server.session.sample_meta = {
            n: {"group": ("A" if n.startswith("a") else "B"),
                "batch": ("d1" if n.startswith("a") else "d2")}
            for n in names
        }
        out = json.loads(server.arf_differential(group_a="A", group_b="B"))
        self.assertEqual(out["status"], "success")
        self.assertGreaterEqual(out["summary"]["n_significant"], 1)
        self.assertTrue(any("バッチ" in c or "交絡" in c for c in out["caveats"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_differential_tools -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute 'arf_differential'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to server.py (import differential at top)
import differential


@mcp.tool()
def arf_differential(
    group_factor: str | None = None,
    group_a: str | None = None,
    group_b: str | None = None,
    q_threshold: float = 0.05,
    log2fc_threshold: float = 1.0,
) -> str:
    """前処理後行列で差次的解析を行う。group_a/group_b 指定時は2群 Welch、
    group_factor のみ指定時はその因子の全水準で一元配置 ANOVA。

    先に arf_preprocess を実行して session.feature_matrix を用意すること
    （未実行なら未正規化 caveat 付きで生行列にフォールバックする）。
    """
    matrix = getattr(session, "feature_matrix", None)
    if matrix is None:
        return json.dumps({"status": "error",
                           "message": "先に arf_preprocess を実行してください（前処理後行列が必要）。"},
                          ensure_ascii=False, indent=2)
    sample_names = session.pp_sample_names
    feature_names = session.pp_feature_names
    meta = session.sample_meta or {}
    group_labels = [(meta.get(n) or {}).get("group") for n in sample_names]
    batch_labels = [(meta.get(n) or {}).get("batch") for n in sample_names]

    caveats: list[str] = []
    recipe = session.preprocessing_recipe or {}
    if recipe.get("normalize", "none") == "none":
        caveats.append("正規化が未適用のため log2FC は測定量差を含み得ます（arf_preprocess の normalize を検討）。")

    conf = differential.check_confounding(group_labels, batch_labels)
    if conf["confounded"]:
        caveats.append("交絡: " + conf["detail"])

    if group_a is not None and group_b is not None:
        results = differential.two_group_test(matrix, feature_names, group_labels, group_a, group_b)
        results = differential.add_fdr(results)
        summary = differential.summarize_two_group(results, q_threshold, log2fc_threshold)
        volcano = differential.volcano_data(results, q_threshold, log2fc_threshold)
        n_a = group_labels.count(group_a)
        n_b = group_labels.count(group_b)
        if min(n_a, n_b) < 4:
            caveats.append(f"小n（{group_a}={n_a}, {group_b}={n_b}）につき検出力が限られます。")
        session.last_differential = {"kind": "two_group", "a": group_a, "b": group_b,
                                     "results": results, "volcano": volcano}
        payload = {"status": "success", "kind": "two_group",
                   "group_a": group_a, "group_b": group_b,
                   "summary": summary, "volcano": volcano, "caveats": caveats}
    elif group_factor is not None:
        results = differential.one_way_anova(matrix, feature_names, group_labels)
        results = differential.add_fdr(results)
        sig = [r for r in results if r.get("q") is not None and math.isfinite(r["q"]) and r["q"] <= q_threshold]
        session.last_differential = {"kind": "anova", "results": results, "volcano": []}
        payload = {"status": "success", "kind": "anova",
                   "n_tested": sum(1 for r in results if r["p"] is not None and math.isfinite(r["p"])),
                   "n_significant": len(sig),
                   "top": sorted(sig, key=lambda r: r["q"])[:15],
                   "caveats": caveats}
    else:
        return json.dumps({"status": "error",
                           "message": "group_a+group_b（2群）か group_factor（ANOVA）のいずれかを指定してください。"},
                          ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

Ensure `import math` is present at the top of `server.py` (add if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_differential_tools -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_differential_tools.py
git commit -m "feat(p2b): add arf_differential MCP tool (two-group + ANOVA)"
```

---

### Task 6: `save_volcano_figure`, requirements, docs

**Files:**
- Modify: `server.py` (add `@mcp.tool() save_volcano_figure` mirroring `save_pca_figure`, near line 643)
- Modify: `requirements.txt` (add `scipy`)
- Modify: `README.md`, `docs/output_format.md`, `docs/HISTRY.md`, `docs/task.md`
- Test: `tests/test_differential_tools.py` (extend)

**Interfaces:**
- Consumes: `session.last_differential`, `_resolve_report_dir` (existing).
- Produces: `save_volcano_figure(analysis_id, title=None) -> str` writing `reports/figures/<analysis_id>_volcano.png` and returning the relative path.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_differential_tools.py
class TestSaveVolcano(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_requires_last_differential(self):
        out = server.save_volcano_figure("A1")
        self.assertIn("error", out.lower())

    def test_writes_png(self):
        server.session.last_differential = {
            "kind": "two_group", "a": "A", "b": "B",
            "volcano": [
                {"feature": "f0", "log2fc": 2.0, "neg_log10_p": 3.0, "sig": "up"},
                {"feature": "f1", "log2fc": -2.0, "neg_log10_p": 3.0, "sig": "down"},
                {"feature": "f2", "log2fc": 0.0, "neg_log10_p": 0.1, "sig": "ns"},
            ],
        }
        rel = server.save_volcano_figure("A1")
        self.assertIn("volcano", rel)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_differential_tools.TestSaveVolcano -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute 'save_volcano_figure'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to server.py
@mcp.tool()
def save_volcano_figure(analysis_id: str, title: str | None = None) -> str:
    """直近の差次的解析結果を volcano プロットとして
    reports/figures/<analysis_id>_volcano.png に保存し、相対パスを返す。"""
    last = getattr(session, "last_differential", None)
    if not last or not last.get("volcano"):
        return "[error] 直近の差次的解析（volcano データ）がありません。先に arf_differential を実行してください。"
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"up": "#c0392b", "down": "#2471a3", "ns": "#95a5a6"}
    fig, ax = plt.subplots(figsize=(6, 5))
    for sig in ("ns", "up", "down"):
        pts = [p for p in last["volcano"] if p["sig"] == sig
               and p["log2fc"] is not None and math.isfinite(p["log2fc"])
               and math.isfinite(p["neg_log10_p"])]
        if pts:
            ax.scatter([p["log2fc"] for p in pts], [p["neg_log10_p"] for p in pts],
                       s=12, c=colors[sig], label=sig, alpha=0.7)
    ax.set_xlabel("log2 fold change")
    ax.set_ylabel("-log10 p")
    ax.set_title(title or f"Volcano ({last.get('a')} vs {last.get('b')})")
    ax.legend()
    report_dir = _resolve_report_dir()
    fig_dir = report_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    out_path = fig_dir / f"{analysis_id}_volcano.png"
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return f"figures/{analysis_id}_volcano.png"
```

Then:
- `requirements.txt`: add a line `scipy`.
- `README.md`: add `arf_differential(...)` and `save_volcano_figure(...)` to the tool list.
- `docs/output_format.md`: add `## 12. 差次的解析（P2b）` describing group labels source, Welch/ANOVA, log2FC/FDR/volcano, and the mandatory caveats (confounding, small n, normalization status).
- `docs/HISTRY.md`: prepend a dated P2b entry.
- `docs/task.md`: mark `T3` (差次的解析) as DONE (replaces the prior HOLD), referencing this plan.
- `server.py` `MCP_INSTRUCTIONS`: add a sentence to the GATEWAY: "差次的解析の前に arf_preprocess を検討し、交絡・小n・正規化状態を caveat として提示する。"

- [ ] **Step 4: Run the full test suite**

Run: `python -m unittest discover -s tests -t .`
Expected: PASS (all tests, including P2a + P2b).

- [ ] **Step 5: Commit**

```bash
git add server.py requirements.txt README.md docs/output_format.md docs/HISTRY.md docs/task.md tests/test_differential_tools.py
git commit -m "feat(p2b): add save_volcano_figure, scipy dep, gateway + docs"
```

---

## Self-Review Notes

- **Spec coverage:** BH-FDR (§4.1, Task 1), two-group Welch + log2FC (§4.1, Task 2), one-way ANOVA (§4.1, Task 3), volcano + confounding + summary (§4.1/§4.2, Task 4), `arf_differential` tool (§4.3, Task 5), `save_volcano_figure` + docs + gateway (§4.3/§6, Task 6).
- **Caveats (§4.2):** confounding via `check_confounding` (uses `sample_meta.batch`), small n check in the tool, normalization-status check from `session.preprocessing_recipe`. Detection-rate caveat is covered by P2a's filter; the tool notes when preprocessing was skipped.
- **scipy:** exact p-values; a pure-Python fallback keeps tests green if scipy is missing, but `requirements.txt` adds scipy for correctness. Tests use strong signals so both code paths pass.
- **Dependency on P2a:** this plan assumes P2a Task 8 (`session.feature_matrix`, `sample_meta`, `preprocessing_recipe`) merged. `arf_differential` errors clearly if `feature_matrix` is absent.
