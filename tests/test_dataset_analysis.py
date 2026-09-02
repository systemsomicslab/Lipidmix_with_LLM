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


def test_run_dataset_preprocess_warns_multi_batch_qc():
    """QC が複数バッチ（ファイル名日付）に分かれる場合、arf_preprocess と同じ caveat を出す。"""
    rng = np.random.default_rng(7)
    ds = DatasetState()
    names = [f"ctrl_{i}" for i in range(3)] + [f"treat_{i}" for i in range(3)]
    names += ["20260901_QC_1", "20260902_QC_2"]
    ds.feature_matrix = rng.random((20, len(names))) * 1000.0
    ds.sample_names = names
    ds.feature_ids = [f"f{i}" for i in range(20)]
    report = _preprocessed(ds)
    assert any("複数バッチ" in c for c in report["caveats"])


def test_run_dataset_preprocess_no_multi_batch_caveat_without_dates():
    """既定フィクスチャ（サンプル名に日付なし）では複数バッチ caveat は出ない。"""
    ds = _make_ds()
    report = _preprocessed(ds)
    assert not any("複数バッチ" in c for c in report["caveats"])


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


def test_run_dataset_differential_deduplicates_requested_samples():
    """group_a=["S1","S1"] は同じ行を2回数えるだけで、分散0の縮退比較を
    len(idx_a)<2 の拒否をすり抜けて通してしまう。重複は1回に丸め、
    その旨を caveat で名指しする。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    result = run_dataset_differential(
        ds,
        group_a_samples=["ctrl_0", "ctrl_0", "ctrl_1"],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )
    assert result["n_a"] == 2
    assert result["samples_a"] == ["ctrl_0", "ctrl_1"]
    assert any("ctrl_0" in c and "重複" in c for c in result["caveats"])


def test_run_dataset_differential_rejects_group_that_is_all_duplicates():
    """重複除去後に n<2 まで縮む場合、水増しされた見かけの n ではなく
    実際のサイズ不足として拒否されなければならない。"""
    from lipidmix.analysis.dataset_analysis import (
        PreconditionError, run_dataset_differential,
    )
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    with pytest.raises(PreconditionError):
        run_dataset_differential(
            ds,
            group_a_samples=["ctrl_0", "ctrl_0"],
            group_b_samples=["treat_0", "treat_1"],
        )


# ---------- run_dataset_differential: ARF parity caveats (fix round 1) ----------

def _make_batch_ds(names, n_features=20):
    """バッチ判定テスト用。サンプル名の8桁日付から sample_meta.batch が決まる。"""
    rng = np.random.default_rng(11)
    ds = DatasetState()
    ds.feature_matrix = rng.random((n_features, len(names))) * 1000.0
    ds.sample_names = names
    ds.feature_ids = [f"f{i}" for i in range(n_features)]
    return ds


def test_run_dataset_differential_warns_when_not_normalized():
    """arf_differential:846-848 と同じ「正規化未適用」警告。既定 recipe は normalize=none。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)  # ds.preprocessing_recipe は既定の {} のまま
    result = run_dataset_differential(
        ds,
        group_a_samples=[n for n in ds.pp_sample_names if n.startswith("ctrl")],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )
    assert any("正規化が未適用" in c for c in result["caveats"])


def test_run_dataset_differential_no_normalize_caveat_when_normalized():
    """normalize が none 以外なら「正規化未適用」警告は出ない。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    ds.preprocessing_recipe = {"normalize": "median"}
    result = run_dataset_differential(
        ds,
        group_a_samples=[n for n in ds.pp_sample_names if n.startswith("ctrl")],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )
    assert not any("正規化が未適用" in c for c in result["caveats"])


def test_run_dataset_differential_unassessable_confounding_without_dates():
    """既定フィクスチャ（サンプル名に日付なし）は全 batch=None → 交絡評価不可。

    check_confounding のドキュメント（lipidmix/analysis/differential.py:256）どおり、
    「バッチが1つ（この場合は判明ゼロ）＝交絡なし」と誤読させないための assessable=False 経路。
    """
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    result = run_dataset_differential(
        ds,
        group_a_samples=[n for n in ds.pp_sample_names if n.startswith("ctrl")],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )
    assert any("交絡評価不可" in c for c in result["caveats"])
    # バッチ情報自体が無い（filename_date 由来ではない）ので、出所注記は付かない。
    assert not any("バッチはファイル名の日付から推定" in c for c in result["caveats"])


def test_run_dataset_differential_confounded_caveat_when_groups_are_single_batch():
    """group_a が丸ごとバッチA、group_b が丸ごとバッチBなら交絡と判定する。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    names = ([f"20260901_ctrl_{i}" for i in range(3)]
             + [f"20260902_treat_{i}" for i in range(3)])
    ds = _make_batch_ds(names)
    _preprocessed(ds)
    result = run_dataset_differential(
        ds,
        group_a_samples=[n for n in ds.pp_sample_names if "ctrl" in n],
        group_b_samples=[n for n in ds.pp_sample_names if "treat" in n],
    )
    assert any("交絡:" in c for c in result["caveats"])
    assert any("バッチはファイル名の日付から推定" in c for c in result["caveats"])


def test_run_dataset_differential_no_confounding_caveat_when_batches_mixed():
    """両群にバッチA・バッチBが混在していれば、交絡系の caveat はどちらも出ない。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    names = ["20260901_ctrl_0", "20260902_ctrl_1", "20260901_ctrl_2",
             "20260901_treat_0", "20260902_treat_1", "20260902_treat_2"]
    ds = _make_batch_ds(names)
    _preprocessed(ds)
    result = run_dataset_differential(
        ds,
        group_a_samples=[n for n in ds.pp_sample_names if "ctrl" in n],
        group_b_samples=[n for n in ds.pp_sample_names if "treat" in n],
    )
    assert not any(c.startswith("交絡") for c in result["caveats"])
