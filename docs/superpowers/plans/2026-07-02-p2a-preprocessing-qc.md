# P2a Preprocessing / QC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a preprocessing/QC layer (sample-role detection, blank filtering, normalization, QC drift correction, feature filtering, imputation) that transforms the ARF sample×feature matrix before PCA and differential analysis, recording the applied recipe.

**Architecture:** A new MCP-independent pure-logic module `preprocessing.py` (mirroring `knowledge_store.py` / `peak_verification.py`), a small `AnalysisSession` enhancement holding a canonical feature matrix + per-sample metadata + preprocessing recipe, and two MCP tools (`arf_list_sample_roles`, `arf_preprocess`) in `server.py`.

**Tech Stack:** Python 3, numpy, scikit-learn (KNNImputer), pandas; existing modules `test_arf` (build_pca_matrix), `msdial_classes` (analytical_order / class_index).

## Global Constraints

- All new logic in `preprocessing.py` must be MCP-independent and unit-testable (no `fastmcp` import), matching `knowledge_store.py`.
- Matrix orientation is **rows = samples, columns = features** (as returned by `test_arf.build_pca_matrix`).
- Every processing step is opt-in; defaults must leave existing `arf_parser` / `run_pca` behavior unchanged (backward compatible).
- Every step returns a `report` dict; when a step cannot run (no QC / no blank / no run order) it is **skipped with an explicit caveat string**, never a silent pass.
- Sample-role tokens are matched case-insensitively on underscore-delimited segments of both the sample filename and the Class ID. Default tokens: `qc → {"qc"}`, `blank → {"blank"}`.
- Run tests with: `python -m unittest discover -s tests -t .`
- Commit after each task with the shown message.

---

### Task 1: Sample-role detection

**Files:**
- Create: `preprocessing.py`
- Test: `tests/test_preprocessing.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `detect_sample_roles(sample_names: list[str], class_ids: dict[str, str] | None = None, config: dict | None = None) -> dict[str, str]` returning `{sample_name: "sample"|"qc"|"blank"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preprocessing.py
import unittest
import preprocessing as pp


class TestDetectSampleRoles(unittest.TestCase):
    def test_qc_and_blank_detected_from_filename(self):
        names = [
            "20240311_QC_Cerebellum_ICR_NEG_1",
            "20240311_blank_Cerebellum_NEG_1",
            "20240311_Cerebellum_gf_AIN_1_NEG",
        ]
        roles = pp.detect_sample_roles(names)
        self.assertEqual(roles[names[0]], "qc")
        self.assertEqual(roles[names[1]], "blank")
        self.assertEqual(roles[names[2]], "sample")

    def test_role_from_class_id_token(self):
        names = ["s1"]
        roles = pp.detect_sample_roles(names, class_ids={"s1": "QC_pool"})
        self.assertEqual(roles["s1"], "qc")

    def test_config_overrides_tokens(self):
        names = ["ctrl_pooledqc_1"]
        roles = pp.detect_sample_roles(names, config={"qc_tokens": ["pooledqc"]})
        self.assertEqual(roles["ctrl_pooledqc_1"], "qc")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocessing -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'preprocessing'`.

- [ ] **Step 3: Write minimal implementation**

```python
# preprocessing.py
"""MS-DIAL ロード後のサンプル×特徴量行列に対する前処理・QC（純ロジック層、MCP 非依存）。

役割検出・ブランク処理・正規化・QCドリフト補正・特徴量フィルタ・欠損補完を提供する。
knowledge_store.py / peak_verification.py と同じく MCP に依存しない純関数群。
行列は行=サンプル、列=特徴量（test_arf.build_pca_matrix の向き）。
"""

from __future__ import annotations

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocessing -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add preprocessing.py tests/test_preprocessing.py
git commit -m "feat(p2a): add sample-role detection (qc/blank/sample)"
```

---

### Task 2: Normalization (tic / median / pqn)

**Files:**
- Modify: `preprocessing.py`
- Test: `tests/test_preprocessing.py`

**Interfaces:**
- Consumes: `detect_sample_roles` output for the optional `roles` arg.
- Produces: `normalize(matrix: "np.ndarray", method: str, roles: dict | None = None, sample_names: list[str] | None = None) -> tuple[np.ndarray, np.ndarray, dict]` returning `(matrix2, factors, report)`. `factors` is a per-sample 1-D array of the divisor applied.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_preprocessing.py
import numpy as np


class TestNormalize(unittest.TestCase):
    def test_none_is_identity(self):
        m = np.array([[1.0, 2.0], [3.0, 4.0]])
        out, factors, report = pp.normalize(m, "none")
        np.testing.assert_array_equal(out, m)
        self.assertEqual(report["method"], "none")

    def test_tic_divides_by_row_sum(self):
        m = np.array([[1.0, 1.0], [2.0, 2.0]])
        out, factors, report = pp.normalize(m, "tic")
        # each row sums to 2 and 4 -> scaled so row sums equal the mean row sum (3)
        self.assertAlmostEqual(out[0, 0] / out[1, 0], (1 / 2) / (2 / 4))

    def test_median_uses_row_median(self):
        m = np.array([[2.0, 4.0], [10.0, 20.0]])
        out, factors, report = pp.normalize(m, "median")
        self.assertAlmostEqual(factors[0], 3.0)   # median(2,4)=3
        self.assertAlmostEqual(factors[1], 15.0)  # median(10,20)=15

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            pp.normalize(np.zeros((2, 2)), "bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocessing.TestNormalize -v`
Expected: FAIL with `AttributeError: module 'preprocessing' has no attribute 'normalize'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to preprocessing.py (top: add `import numpy as np`)
import numpy as np


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocessing.TestNormalize -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add preprocessing.py tests/test_preprocessing.py
git commit -m "feat(p2a): add tic/median/pqn normalization"
```

---

### Task 3: Blank filtering

**Files:**
- Modify: `preprocessing.py`
- Test: `tests/test_preprocessing.py`

**Interfaces:**
- Consumes: `detect_sample_roles` output.
- Produces: `blank_filter(matrix, roles, sample_names, min_fold=3.0) -> tuple[np.ndarray, dict]` returning `(keep_mask, report)`. `keep_mask` is a 1-D bool array over columns (True = keep feature).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_preprocessing.py
class TestBlankFilter(unittest.TestCase):
    def test_background_feature_removed(self):
        # col0: sample >> blank (keep). col1: sample ~ blank (remove).
        m = np.array([
            [100.0, 10.0],  # sample
            [120.0, 11.0],  # sample
            [5.0, 9.0],     # blank
        ])
        roles = {"s1": "sample", "s2": "sample", "b1": "blank"}
        names = ["s1", "s2", "b1"]
        mask, report = pp.blank_filter(m, roles, names, min_fold=3.0)
        self.assertTrue(mask[0])
        self.assertFalse(mask[1])
        self.assertEqual(report["removed"], 1)

    def test_no_blank_keeps_all_with_caveat(self):
        m = np.array([[1.0, 2.0]])
        mask, report = pp.blank_filter(m, {"s1": "sample"}, ["s1"])
        self.assertTrue(mask.all())
        self.assertIn("caveat", report)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocessing.TestBlankFilter -v`
Expected: FAIL with `AttributeError: ... has no attribute 'blank_filter'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to preprocessing.py
def _rows_for_role(matrix, roles, sample_names, role):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocessing.TestBlankFilter -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add preprocessing.py tests/test_preprocessing.py
git commit -m "feat(p2a): add blank-based background feature filter"
```

---

### Task 4: QC drift correction

**Files:**
- Modify: `preprocessing.py`
- Test: `tests/test_preprocessing.py`

**Interfaces:**
- Consumes: `detect_sample_roles` output.
- Produces: `qc_drift_correct(matrix, roles, sample_names, run_order, min_qc=4) -> tuple[np.ndarray, dict]` returning `(matrix2, report)`. `run_order` is `dict[sample_name -> int|None]`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_preprocessing.py
class TestDriftCorrect(unittest.TestCase):
    def test_linear_drift_flattened(self):
        # feature drifts linearly with run order across QC; correction should
        # bring QC intensities close to a constant level.
        order = [1, 2, 3, 4, 5, 6]
        names = [f"n{i}" for i in order]
        roles = {n: ("qc" if i % 2 == 1 else "sample") for n, i in zip(names, order)}
        run_order = dict(zip(names, order))
        base = np.array([o * 10.0 for o in order])  # 10,20,30,40,50,60
        m = base.reshape(-1, 1)
        out, report = pp.qc_drift_correct(m, roles, names, run_order, min_qc=3)
        qc_vals = [out[i, 0] for i, n in enumerate(names) if roles[n] == "qc"]
        self.assertLess(np.std(qc_vals), np.std([10, 30, 50]))
        self.assertEqual(report["status"], "applied")

    def test_no_run_order_skips(self):
        m = np.array([[1.0], [2.0]])
        names = ["a", "b"]
        roles = {"a": "qc", "b": "sample"}
        out, report = pp.qc_drift_correct(m, roles, names, {"a": None, "b": None})
        np.testing.assert_array_equal(out, m)
        self.assertEqual(report["status"], "skipped")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocessing.TestDriftCorrect -v`
Expected: FAIL with `AttributeError: ... has no attribute 'qc_drift_correct'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to preprocessing.py
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
    corrected = matrix.copy()
    for j in range(matrix.shape[1]):
        qc_vals = matrix[qc_mask, j][sort]
        trend = _moving_median(qc_vals, window)
        global_level = np.nanmedian(qc_vals)
        if not np.isfinite(global_level) or global_level == 0:
            continue
        # 全サンプル注入順に対しトレンドを内挿し、補正係数=global_level/trend を適用
        interp_trend = np.interp(order_int, qc_order_sorted, trend)
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(interp_trend > 0, global_level / interp_trend, 1.0)
        corrected[:, j] = matrix[:, j] * factor
    return corrected, {"status": "applied", "qc_used": int(qc_mask.sum())}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocessing.TestDriftCorrect -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add preprocessing.py tests/test_preprocessing.py
git commit -m "feat(p2a): add QC-RLSC-style drift correction (numpy moving median)"
```

---

### Task 5: Feature filtering (QC RSD)

**Files:**
- Modify: `preprocessing.py`
- Test: `tests/test_preprocessing.py`

**Interfaces:**
- Consumes: `detect_sample_roles` output.
- Produces: `qc_rsd_filter(matrix, roles, sample_names, max_rsd=0.30) -> tuple[np.ndarray, dict]` returning `(keep_mask, report)` over columns.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_preprocessing.py
class TestQcRsdFilter(unittest.TestCase):
    def test_high_rsd_feature_removed(self):
        # col0: stable in QC (keep). col1: highly variable in QC (remove).
        m = np.array([
            [100.0, 10.0],   # qc
            [101.0, 90.0],   # qc
            [ 99.0, 50.0],   # qc
            [ 50.0, 40.0],   # sample (ignored for RSD)
        ])
        names = ["q1", "q2", "q3", "s1"]
        roles = {"q1": "qc", "q2": "qc", "q3": "qc", "s1": "sample"}
        mask, report = pp.qc_rsd_filter(m, roles, names, max_rsd=0.30)
        self.assertTrue(mask[0])
        self.assertFalse(mask[1])

    def test_no_qc_keeps_all_with_caveat(self):
        m = np.array([[1.0, 2.0]])
        mask, report = pp.qc_rsd_filter(m, {"s1": "sample"}, ["s1"])
        self.assertTrue(mask.all())
        self.assertIn("caveat", report)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocessing.TestQcRsdFilter -v`
Expected: FAIL with `AttributeError: ... has no attribute 'qc_rsd_filter'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to preprocessing.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocessing.TestQcRsdFilter -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add preprocessing.py tests/test_preprocessing.py
git commit -m "feat(p2a): add QC RSD feature filter"
```

---

### Task 6: Imputation (half_min / knn / column_mean / none)

**Files:**
- Modify: `preprocessing.py`
- Test: `tests/test_preprocessing.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `impute(matrix, method="half_min") -> tuple[np.ndarray, dict]`. Missing = `np.nan`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_preprocessing.py
class TestImpute(unittest.TestCase):
    def test_half_min_fills_per_feature(self):
        m = np.array([[10.0, np.nan], [20.0, 4.0], [30.0, 8.0]])
        out, report = pp.impute(m, "half_min")
        self.assertAlmostEqual(out[0, 1], 2.0)  # half of column-1 min (4) = 2
        self.assertFalse(np.isnan(out).any())

    def test_none_keeps_nan(self):
        m = np.array([[np.nan, 1.0]])
        out, report = pp.impute(m, "none")
        self.assertTrue(np.isnan(out[0, 0]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocessing.TestImpute -v`
Expected: FAIL with `AttributeError: ... has no attribute 'impute'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to preprocessing.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocessing.TestImpute -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add preprocessing.py tests/test_preprocessing.py
git commit -m "feat(p2a): add imputation (half_min/knn/column_mean/none)"
```

---

### Task 7: Orchestration (`preprocess`) with recipe recording

**Files:**
- Modify: `preprocessing.py`
- Test: `tests/test_preprocessing.py`

**Interfaces:**
- Consumes: all functions above.
- Produces: `preprocess(matrix, sample_names, roles, run_order, recipe) -> tuple[np.ndarray, list[str], dict]` returning `(matrix2, kept_feature_mask_as_index, report)`. `recipe` keys: `blank_min_fold` (float|None), `normalize` (str, default "none"), `drift_correct` (bool), `max_qc_rsd` (float|None), `impute` (str, default "half_min"). `report` includes `recipe_applied` (ordered list) and per-step reports and `caveats` (list[str]).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_preprocessing.py
class TestPreprocessOrchestration(unittest.TestCase):
    def test_recipe_applied_order_and_caveats(self):
        m = np.array([
            [100.0, 10.0, 5.0],  # sample
            [120.0, 90.0, 5.0],  # sample
            [110.0, 50.0, 5.0],  # qc
        ])
        names = ["s1", "s2", "q1"]
        roles = {"s1": "sample", "s2": "sample", "q1": "qc"}
        run_order = {"s1": 1, "s2": 2, "q1": 3}
        recipe = {"normalize": "median", "impute": "half_min",
                  "drift_correct": True, "max_qc_rsd": 0.30}
        out, kept_idx, report = pp.preprocess(m, names, roles, run_order, recipe)
        self.assertIn("normalize", report["recipe_applied"])
        # drift correction has only 1 QC -> skipped caveat present
        self.assertTrue(any("ドリフト" in c for c in report["caveats"]))
        self.assertEqual(out.shape[0], 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocessing.TestPreprocessOrchestration -v`
Expected: FAIL with `AttributeError: ... has no attribute 'preprocess'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to preprocessing.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocessing -v`
Expected: PASS (all preprocessing tests).

- [ ] **Step 5: Commit**

```bash
git add preprocessing.py tests/test_preprocessing.py
git commit -m "feat(p2a): add preprocess orchestration with recipe + caveats"
```

---

### Task 8: Session enhancement (canonical matrix + sample_meta + recipe)

**Files:**
- Modify: `server.py:679-692` (AnalysisSession.__init__)
- Modify: `server.py` (add helper `_build_sample_meta` near AnalysisSession)
- Test: `tests/test_preprocessing_session.py`

**Interfaces:**
- Consumes: `msdial_classes.discover_arf_class_index` records (which include `analytical_order`, `class_id`, `file_name`).
- Produces: `AnalysisSession` gains attributes `feature_matrix`, `pp_sample_names`, `pp_feature_names`, `sample_meta`, `preprocessing_recipe`. Helper `_build_sample_meta(sample_names, class_index) -> dict[str, dict]` where each value has keys `role`, `group`, `run_order`, `batch`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preprocessing_session.py
import unittest
import server


class TestSampleMeta(unittest.TestCase):
    def test_build_sample_meta_roles_and_run_order(self):
        class_index = {
            "records": [
                {"file_id": 0, "file_name": "20240311_QC_Cerebellum_ICR_NEG_1",
                 "class_id": "QC", "analytical_order": 5},
                {"file_id": 1, "file_name": "20240311_Cerebellum_gf_AIN_1_NEG",
                 "class_id": "Cerebellum_gf_AIN", "analytical_order": 6},
            ],
            "by_file_id": {}, "by_file_name": {},
        }
        names = ["20240311_QC_Cerebellum_ICR_NEG_1",
                 "20240311_Cerebellum_gf_AIN_1_NEG"]
        meta = server._build_sample_meta(names, class_index)
        self.assertEqual(meta[names[0]]["role"], "qc")
        self.assertEqual(meta[names[1]]["role"], "sample")
        self.assertEqual(meta[names[1]]["run_order"], 6)
        self.assertEqual(meta[names[0]]["batch"], "20240311")

    def test_session_has_new_attrs(self):
        s = server.AnalysisSession()
        self.assertIsNone(s.feature_matrix)
        self.assertEqual(s.preprocessing_recipe, {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocessing_session -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute '_build_sample_meta'` (or missing session attrs).

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add `import preprocessing` and `import re` (already imported) at the top import block. Extend `AnalysisSession.__init__` (after `self.last_pca_plot = None`):

```python
        # --- P2a 前処理用の正準行列とサンプルメタ ---
        self.feature_matrix = None       # 前処理後のサンプル×特徴量行列
        self.pp_sample_names = None
        self.pp_feature_names = None
        self.sample_meta = {}            # {sample_name: {role, group, run_order, batch}}
        self.preprocessing_recipe = {}   # 直近適用した前処理レシピ（空=未適用）
```

Add module-level helper near `AnalysisSession` (after the class or before it, module scope):

```python
_BATCH_DATE_RE = re.compile(r"(\d{8})")


def _build_sample_meta(sample_names, class_index):
    """サンプル名から role/group/run_order/batch を組み立てる。"""
    class_ids = {}
    orders = {}
    if class_index:
        by_name = {}
        for rec in class_index.get("records", []):
            key = normalize_sample_name(rec.get("file_name"), strip_processing_timestamp=False)
            if key:
                by_name[key] = rec
        for name in sample_names:
            rec = by_name.get(normalize_sample_name(name, strip_processing_timestamp=False))
            if rec:
                class_ids[name] = rec.get("class_id") or ""
                orders[name] = rec.get("analytical_order")
    roles = preprocessing.detect_sample_roles(sample_names, class_ids)
    groups = assign_sample_groups(sample_names, class_index, None)
    meta = {}
    for name in sample_names:
        m = _BATCH_DATE_RE.search(name)
        meta[name] = {
            "role": roles.get(name, "sample"),
            "group": groups.get(name),
            "run_order": orders.get(name),
            "batch": m.group(1) if m else None,
        }
    return meta
```

Ensure `normalize_sample_name` is importable in `server.py` (it is imported via `from msdial_tags import ...`; if not, add `from msdial_tags import normalize_sample_name`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocessing_session -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_preprocessing_session.py
git commit -m "feat(p2a): add canonical matrix + sample_meta to AnalysisSession"
```

---

### Task 9: MCP tools `arf_list_sample_roles` and `arf_preprocess`

**Files:**
- Modify: `server.py` (add two `@mcp.tool()` functions after `arf_list_classes`, near line 1512)
- Test: `tests/test_preprocess_tools.py`

**Interfaces:**
- Consumes: `session.filtered_features` (ARF spot dicts), `test_arf.build_pca_matrix`, `preprocessing.preprocess`, `_build_sample_meta`.
- Produces: MCP tools `arf_list_sample_roles() -> str` (JSON) and `arf_preprocess(normalize="none", blank_min_fold=None, drift_correct=False, max_qc_rsd=None, impute="half_min", props=None) -> str` (JSON report). Side effect: sets `session.feature_matrix`, `session.pp_sample_names`, `session.pp_feature_names`, `session.preprocessing_recipe`, `session.sample_meta`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preprocess_tools.py
import json
import unittest
import numpy as np
import server


class TestPreprocessTools(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_list_sample_roles_requires_data(self):
        out = json.loads(server.arf_list_sample_roles())
        self.assertEqual(out["status"], "error")

    def test_preprocess_sets_session_matrix(self):
        # minimal fake ARF matrix path: inject via monkeypatch of build helper
        server.session.filtered_features = [{"AlignedPeakProperties": []}]
        server.session.arf_class_index = None

        def fake_matrix(*a, **k):
            m = np.array([[10.0, 1.0], [20.0, 2.0], [15.0, 1.5]])
            return m, ["s1", "s2", "q1"], ["Spot_0_height", "Spot_1_height"]

        server._pp_build_matrix = fake_matrix  # helper indirection (see Step 3)
        out = json.loads(server.arf_preprocess(normalize="median", impute="half_min"))
        self.assertEqual(out["status"], "success")
        self.assertIsNotNone(server.session.feature_matrix)
        self.assertEqual(server.session.preprocessing_recipe["normalize"], "median")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocess_tools -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute 'arf_list_sample_roles'`.

- [ ] **Step 3: Write minimal implementation**

Add a small indirection helper and the two tools in `server.py`:

```python
def _pp_build_matrix(features, props):
    from test_arf import build_pca_matrix
    return build_pca_matrix(features, use_properties=props)


@mcp.tool()
def arf_list_sample_roles() -> str:
    """ロード済み ARF のサンプルを sample/qc/blank に分類して返す（前処理の適用前確認）。"""
    if session.filtered_features is None:
        return json.dumps({"status": "error",
                           "message": "先に arf_parser で ARF を読み込んでください。"},
                          ensure_ascii=False, indent=2)
    _, sample_names, _ = _pp_build_matrix(session.filtered_features, ["height"])
    meta = _build_sample_meta(sample_names, session.arf_class_index)
    counts = {"sample": 0, "qc": 0, "blank": 0}
    for m in meta.values():
        counts[m["role"]] = counts.get(m["role"], 0) + 1
    return json.dumps({"status": "success", "counts": counts, "samples": meta},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def arf_preprocess(
    normalize: str = "none",
    blank_min_fold: float | None = None,
    drift_correct: bool = False,
    max_qc_rsd: float | None = None,
    impute: str = "half_min",
    props: list[str] | None = None,
) -> str:
    """ロード済み ARF 行列に前処理レシピを適用し、session を更新して報告を返す。

    以降の PCA/差次的解析は session.feature_matrix（前処理後）を消費する。
    """
    if session.filtered_features is None:
        return json.dumps({"status": "error",
                           "message": "先に arf_parser で ARF を読み込んでください。"},
                          ensure_ascii=False, indent=2)
    props = props or ["height"]
    matrix, sample_names, feature_names = _pp_build_matrix(session.filtered_features, props)
    meta = _build_sample_meta(sample_names, session.arf_class_index)
    roles = {n: meta[n]["role"] for n in sample_names}
    run_order = {n: meta[n]["run_order"] for n in sample_names}

    # プールQC が層別（複数 QC サブグループ）かの簡易警告材料
    qc_batches = {meta[n]["batch"] for n in sample_names if meta[n]["role"] == "qc"}

    recipe = {
        "normalize": normalize,
        "blank_min_fold": blank_min_fold,
        "drift_correct": drift_correct,
        "max_qc_rsd": max_qc_rsd,
        "impute": impute,
        "props": props,
    }
    import preprocessing
    matrix2, kept_idx, report = preprocessing.preprocess(
        matrix, sample_names, roles, run_order, recipe,
    )
    kept_feature_names = [feature_names[i] for i in kept_idx]
    session.feature_matrix = matrix2
    session.pp_sample_names = sample_names
    session.pp_feature_names = kept_feature_names
    session.sample_meta = meta
    session.preprocessing_recipe = recipe
    if len(qc_batches) > 1:
        report.setdefault("caveats", []).append(
            "プールQC が複数バッチ/層に分かれています。全体一律のドリフト補正は近似です。"
        )
    report["status"] = "success"
    report["matrix_shape"] = list(matrix2.shape)
    report["recipe"] = recipe
    return json.dumps(report, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_preprocess_tools -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_preprocess_tools.py
git commit -m "feat(p2a): add arf_list_sample_roles and arf_preprocess MCP tools"
```

---

### Task 10: Wire preprocessed matrix into `arf_re_pca` + docs

**Files:**
- Modify: `server.py` (`arf_re_pca`, near line 1664) — consume `session.feature_matrix` when present.
- Modify: `README.md` (MCP tools list), `docs/output_format.md` (add §11 preprocessing), `docs/HISTRY.md`, `docs/task.md`.
- Test: `tests/test_preprocess_tools.py` (extend).

**Interfaces:**
- Consumes: `session.feature_matrix`, `session.pp_feature_names`, `session.pp_sample_names`, `session.arf_class_index`; existing helpers `test_arf.run_pca`, `test_arf.get_pca_loading_features`, `_format_pca_plot_block`, `_remember_arf_pca_plot`, `_format_pca_loadings_md`, `assign_sample_groups`.
- Produces: helper `_pp_has_preprocessed() -> bool` and a new MCP tool `arf_pca_preprocessed(components=None, top_features=10, log_transform=False, group_levels=None) -> list` that runs PCA on the preprocessed matrix and returns a text report (mirroring `arf_parser`'s output block). A dedicated tool avoids editing `arf_re_pca`'s existing body and keeps the default path untouched.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_preprocess_tools.py
class TestPcaPreprocessed(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_requires_preprocessed_matrix(self):
        out = server.arf_pca_preprocessed()
        self.assertIn("前処理", out[0])

    def test_runs_pca_on_preprocessed_matrix(self):
        import numpy as np
        server.session.feature_matrix = np.array(
            [[1.0, 2.0, 3.0], [2.0, 1.0, 0.0], [3.0, 3.0, 3.0], [0.0, 1.0, 2.0]])
        server.session.pp_sample_names = ["a", "b", "c", "d"]
        server.session.pp_feature_names = ["Spot_0_height", "Spot_1_height", "Spot_2_height"]
        server.session.arf_class_index = None
        server.session.features = []  # get_pca_loading_features tolerates empty spots
        server.session.preprocessing_recipe = {"normalize": "median"}
        self.assertTrue(server._pp_has_preprocessed())
        out = server.arf_pca_preprocessed()
        self.assertIn("PCA", out[0])
        self.assertIn("前処理レシピ", out[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_preprocess_tools.TestPcaPreprocessed -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute '_pp_has_preprocessed'`.

- [ ] **Step 3: Write minimal implementation**

Add the helper and the new tool in `server.py` (place after `arf_preprocess`):

```python
def _pp_has_preprocessed() -> bool:
    return getattr(session, "feature_matrix", None) is not None


@mcp.tool()
def arf_pca_preprocessed(
    components: int | None = None,
    top_features: int = 10,
    log_transform: bool = False,
    group_levels: list[str] | None = None,
) -> list:
    """arf_preprocess で用意した前処理後行列で PCA を実行する。

    通常の arf_parser 経路（生行列）とは独立で、既定挙動を変えない。
    """
    if not _pp_has_preprocessed():
        return ["前処理後の行列がありません。先に arf_preprocess を実行してください。"]
    from test_arf import run_pca, get_pca_loading_features
    matrix = session.feature_matrix
    sample_names = session.pp_sample_names
    feature_names = session.pp_feature_names
    try:
        pca_result = run_pca(matrix, n_components=components, log_transform=log_transform)
    except Exception as exc:
        return [f"PCA 実行に失敗しました: {exc}"]

    sample_groups = assign_sample_groups(sample_names, session.arf_class_index, group_levels)
    plot_block = _format_pca_plot_block(
        pca_result, sample_names,
        title="PCA (preprocessed ARF)",
        intro="\n#### 📊 PCA スコアプロット用データ（前処理後）\n",
        groups=sample_groups,
    )
    _remember_arf_pca_plot(pca_result, sample_names,
                           title="PCA (preprocessed ARF)", groups=sample_groups)
    loading_features = get_pca_loading_features(
        pca_result, session.features or [], feature_names, top_n=top_features,
    )
    loadings_block = _format_pca_loadings_md(
        loading_features, header="#### 📊 PCA Loadings 寄与度分析（前処理後）\n",
    )
    text = (
        f"### 📈 前処理後 ARF PCA 解析\n"
        f"- **前処理レシピ**: {session.preprocessing_recipe}\n"
        f"- **PCA入力行列の形状**: {tuple(matrix.shape)} (サンプル数 x 特徴量数)\n"
        f"- **PC1 説明分散比**: {pca_result['explained_variance_ratio'][0]*100:.2f}%\n"
        f"- **PC2 説明分散比**: {pca_result['explained_variance_ratio'][1]*100:.2f}%\n"
        f"{plot_block}{loadings_block}"
    )
    return [text]
```

Then update docs:
- `README.md`: add `arf_list_sample_roles()`, `arf_preprocess(...)`, and `arf_pca_preprocessed(...)` to the tool list with one-line descriptions.
- `docs/output_format.md`: add a new section `## 11. 前処理・QC（P2a）` describing role detection, the recipe steps, and that preprocessing is opt-in and recorded in `session.preprocessing_recipe`.
- `docs/HISTRY.md`: prepend a dated entry summarizing P2a.
- `docs/task.md`: mark a new `T14. 前処理・QC層（P2a）` as DONE with a one-line summary.

- [ ] **Step 4: Run the full test suite**

Run: `python -m unittest discover -s tests -t .`
Expected: PASS (all existing tests + new preprocessing tests).

- [ ] **Step 5: Commit**

```bash
git add server.py README.md docs/output_format.md docs/HISTRY.md docs/task.md tests/test_preprocess_tools.py
git commit -m "feat(p2a): consume preprocessed matrix in arf_re_pca; docs"
```

---

## Self-Review Notes

- **Spec coverage:** role detection (§3.1, Task 1), blank (§3.2, Task 3), normalization (§3.3, Task 2), drift correction (§3.4, Task 4), filtering (§3.5, Task 5), imputation (§3.6, Task 6), orchestration+recipe (§3.7, Task 7), session refactor (§2.1, Task 8), MCP tools (§3.7, Task 9), preprocessed-matrix PCA tool + docs (§2.2/§6, Task 10). Run-order source = `analytical_order` from `msdial_classes.parse_analysis_file_classes` (confirmed present).
- **PCA integration choice:** Task 10 adds a dedicated `arf_pca_preprocessed` tool rather than editing `arf_re_pca`'s existing body, keeping the default (raw-matrix) path byte-for-byte unchanged and avoiding a risky in-place edit.
- **Deferred to P2b/P2c:** detection-rate filter reuse (already exists in `build_pca_matrix(min_detection_rate=...)`); differential analysis consumes `session.feature_matrix`.
- **Caveat surfacing:** every skip path returns a `caveat`; `arf_preprocess` adds the stratified-QC caveat. Matches the spec's "surface caveats" principle.
