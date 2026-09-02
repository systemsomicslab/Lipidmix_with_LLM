import numpy as np
import pytest

from lipidmix.mztab.dataset_state import DatasetState


def _make_ds(n_features=20, n_samples=8, with_blank=False):
    """最小限の DatasetState。ロールは dataset_analysis がサンプル名から推定する。"""
    rng = np.random.default_rng(42)
    ds = DatasetState()
    names = [f"ctrl_{i}" for i in range(n_samples // 2)]
    names += [f"treat_{i}" for i in range(n_samples - len(names))]
    if with_blank:
        names[-1] = "blank_1"
    ds.feature_matrix = rng.random((n_features, n_samples)) * 1000.0
    ds.sample_names = names
    ds.feature_ids = [f"f{i}" for i in range(n_features)]
    return ds


def _preprocessed(ds, recipe=None):
    from lipidmix.analysis.dataset_analysis import run_dataset_preprocess
    (ds.pp_matrix, ds.pp_sample_names, ds.pp_feature_names,
     ds.roles, ds.sample_meta, report) = run_dataset_preprocess(ds, recipe or {})
    return report


# ---------- build_dataset_pp_inputs ----------

def test_build_dataset_pp_inputs_transposes():
    from lipidmix.analysis.dataset_analysis import build_dataset_pp_inputs
    ds = _make_ds(n_features=20, n_samples=8)
    matrix, sample_names, feature_names, roles, sample_meta = build_dataset_pp_inputs(ds)
    assert matrix.shape == (8, 20)          # (n_samples, n_features)
    assert sample_names == ds.sample_names
    assert feature_names == ds.feature_ids
    assert set(roles) == set(ds.sample_names)
    assert set(sample_meta) == set(ds.sample_names)


def test_build_dataset_pp_inputs_detects_blank_role():
    from lipidmix.analysis.dataset_analysis import build_dataset_pp_inputs
    ds = _make_ds(with_blank=True)
    _, _, _, roles, _ = build_dataset_pp_inputs(ds)
    assert roles["blank_1"] == "blank"


def test_build_dataset_pp_inputs_rejects_empty_matrix():
    from lipidmix.analysis.dataset_analysis import (
        PreconditionError, build_dataset_pp_inputs,
    )
    ds = DatasetState()
    with pytest.raises(PreconditionError):
        build_dataset_pp_inputs(ds)


# ---------- run_dataset_preprocess ----------

def test_run_dataset_preprocess_keeps_feature_count():
    ds = _make_ds(n_features=20, n_samples=8)
    report = _preprocessed(ds)
    assert ds.pp_matrix.shape == (8, 20)
    assert len(ds.pp_feature_names) == 20
    assert report["features_before"] == 20


def test_run_dataset_preprocess_drops_blank_samples():
    """ブランクは背景除去の参照として使った後、解析行列から外す（ARF と同じ）。"""
    ds = _make_ds(n_features=20, n_samples=8, with_blank=True)
    report = _preprocessed(ds)
    assert "blank_1" not in ds.pp_sample_names
    assert report["excluded_from_matrix"]["blank"] == ["blank_1"]


def test_run_dataset_preprocess_warns_no_run_order():
    """mzTab-M に注入順が無いため drift_correct は必ず skipped になる。"""
    ds = _make_ds()
    report = _preprocessed(ds, {"drift_correct": True})
    assert report["steps"]["drift_correct"]["status"] == "skipped"
    assert any("注入順" in c for c in report["caveats"])


def test_run_dataset_preprocess_rejects_unknown_normalize():
    from lipidmix.analysis.dataset_analysis import PreconditionError
    ds = _make_ds()
    with pytest.raises(PreconditionError):
        _preprocessed(ds, {"normalize": "not_a_method"})


# ---------- run_dataset_pca ----------

def test_run_dataset_pca_requires_preprocess():
    from lipidmix.analysis.dataset_analysis import PreconditionError, run_dataset_pca
    ds = _make_ds()
    with pytest.raises(PreconditionError):
        run_dataset_pca(ds, n_components=2)


def test_run_dataset_pca_returns_scores_per_sample():
    from lipidmix.analysis.dataset_analysis import run_dataset_pca
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    result = run_dataset_pca(ds, n_components=2)
    assert len(result["explained_variance_ratio"]) == 2
    assert [s["name"] for s in result["scores"]] == ds.pp_sample_names
    assert "PC1" in result["scores"][0]


# ---------- run_dataset_differential ----------

def test_run_dataset_differential_summarizes():
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    result = run_dataset_differential(
        ds,
        group_a_samples=[n for n in ds.pp_sample_names if n.startswith("ctrl")],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )
    assert result["summary"]["n_tested"] > 0
    assert len(result["results"]) == 20
    assert "q" in result["results"][0]
    assert result["contract_version"] == 1
    assert result["log2fc_sign"] == "positive means group_b is higher"


def test_run_dataset_differential_reports_unknown_samples():
    """存在しないサンプル名を黙って捨てず、caveat で名指しする。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    result = run_dataset_differential(
        ds,
        group_a_samples=["ctrl_0", "ctrl_1", "ctrl_2", "nope_1"],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )
    assert any("nope_1" in c for c in result["caveats"])
    assert result["n_a"] == 3


def test_run_dataset_differential_rejects_small_groups():
    from lipidmix.analysis.dataset_analysis import (
        PreconditionError, run_dataset_differential,
    )
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    with pytest.raises(PreconditionError):
        run_dataset_differential(ds, group_a_samples=["ctrl_0"],
                                 group_b_samples=["treat_0"])
