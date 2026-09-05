"""結果 ID・来歴・派生結果の無効化（spec §6）。

前処理をやり直したのに古い PCA / 差次的結果がセッションに残っていると、図と TSV が
**別々の前処理から出た数字**を並べる。しかもどちらも「直近の結果」を名乗るので、
読み手には見分けがつかない。ここで縛るのは 2 つ:

1. **どの入力から出た結果かを結果自身が持つ**（`provenance.result_id` と
   `input_fingerprint`）。あとから「この図はどの行列から出たのか」を answer できる。
2. **入力が変わったら派生結果を消す**。残す（＝古い数字を返し続ける）くらいなら、
   無いことにして計算し直させるほうが安全。

前処理は途中で失敗しうるので、**成功してからまとめて反映する**（トランザクション）。
失敗した前処理が行列だけ差し替えてレシピは古いまま、という半端な状態を作らない。
"""
from __future__ import annotations

import numpy as np
import pytest

from lipidmix.analysis.dataset_service import (
    compare_dataset,
    pca_dataset,
    preprocess_dataset,
)
from lipidmix.analysis.result_state import (
    array_fingerprint,
    assert_current,
    dataset_fingerprint,
    invalidate_results,
    metadata_fingerprints,
)
from lipidmix.core.atomic_io import DomainError
from tests.pipeline_fixtures import make_dataset

_BASE_RECIPE = {"normalize": "none", "impute": "half_min"}


# ---------- fingerprint ----------

def test_array_fingerprint_is_stable_for_equal_arrays():
    a = np.array([[1.0, 2.0], [3.0, 4.0]])
    b = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert array_fingerprint(a) == array_fingerprint(b)


def test_array_fingerprint_separates_nan_from_zero():
    """NaN を JSON 経由で扱うと null になり 0 と混ざる。バイト列で見る。"""
    a = np.array([[np.nan, 1.0]])
    b = np.array([[0.0, 1.0]])
    assert array_fingerprint(a) != array_fingerprint(b)


def test_array_fingerprint_is_equal_for_two_nans():
    """NaN != NaN だが、同じ配列は同じ指紋でなければ再計算の判定が壊れる。"""
    assert array_fingerprint(np.array([np.nan])) == array_fingerprint(np.array([np.nan]))


def test_array_fingerprint_separates_shape_and_orientation():
    """転置は別の行列。要素の並びだけを見ると (n, m) と (m, n) が同一になる。"""
    a = np.arange(6.0).reshape(2, 3)
    assert array_fingerprint(a) != array_fingerprint(a.T)
    assert array_fingerprint(a) != array_fingerprint(a.reshape(3, 2))


def test_array_fingerprint_separates_dtype():
    assert (array_fingerprint(np.array([1, 2], dtype=np.int64))
            != array_fingerprint(np.array([1.0, 2.0], dtype=np.float64)))


def test_dataset_fingerprint_changes_with_the_matrix():
    ds = make_dataset()
    before = dataset_fingerprint(ds)
    ds.feature_matrix = ds.feature_matrix + 1.0
    assert dataset_fingerprint(ds) != before


def test_dataset_fingerprint_changes_with_the_detection_mask():
    """検出マスクは min_detection_rate の入力。変われば前処理の入力が変わる。"""
    ds = make_dataset()
    before = dataset_fingerprint(ds)
    ds.detected_mask = np.ones(ds.feature_matrix.shape, dtype=bool)
    assert dataset_fingerprint(ds) != before


def test_dataset_fingerprint_ignores_result_slots():
    """派生結果は入力ではない。ここが変わるたびに前処理をやり直させない。"""
    ds = make_dataset()
    before = dataset_fingerprint(ds)
    ds.last_pca = {"scores": [[1.0]]}
    assert dataset_fingerprint(ds) == before


def test_metadata_fingerprints_are_per_field():
    rows = [{"sample_id": "S0", "group": "ctrl", "role": "sample", "batch": "1"},
            {"sample_id": "S1", "group": "treat", "role": "sample", "batch": "1"}]
    before = metadata_fingerprints(rows)
    rows[1]["group"] = "ctrl"
    after = metadata_fingerprints(rows)
    assert after["group"] != before["group"]
    assert after["batch"] == before["batch"]
    assert after["role"] == before["role"]


# ---------- 無効化 ----------

def _with_results(ds):
    ds.last_pca = {"scores": []}
    ds.last_differential = {"kind": "two_group"}
    return ds


def test_preprocess_relevant_metadata_change_invalidates_everything():
    ds = _with_results(make_dataset())
    invalidate_results(ds, {"batch"})
    assert ds.last_pca is None
    assert ds.last_differential is None


def test_group_change_invalidates_only_the_comparison():
    """群の付け替えは PCA の入力ではない。無関係な結果まで捨てない。"""
    ds = _with_results(make_dataset())
    invalidate_results(ds, {"group"})
    assert ds.last_pca is not None
    assert ds.last_differential is None


def test_pca_settings_change_invalidates_only_pca():
    ds = _with_results(make_dataset())
    invalidate_results(ds, {"pca_settings"})
    assert ds.last_pca is None
    assert ds.last_differential is not None


def test_unrelated_change_invalidates_nothing():
    ds = _with_results(make_dataset())
    invalidate_results(ds, {"note"})
    assert ds.last_pca is not None
    assert ds.last_differential is not None


def test_preprocess_relevant_change_also_drops_the_preprocessed_matrix():
    """行列を残したまま結果だけ消すと、次の PCA が古い行列で走る。"""
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    invalidate_results(ds, {"batch"})
    assert ds.pp_matrix is None
    assert ds.preprocessing_recipe == {}


# ---------- 来歴 ----------

def test_preprocess_result_carries_provenance():
    ds = make_dataset()
    result = preprocess_dataset(ds, _BASE_RECIPE)
    prov = result["provenance"]
    assert prov["result_id"]
    assert prov["kind"] == "preprocess"
    assert prov["dataset_id"] == ds.dataset_id
    assert prov["input_fingerprint"]
    assert prov["effective_parameters"]["normalize"] == "none"
    assert prov["code_version"]


def test_derived_results_point_at_the_preprocessing_they_used():
    ds = make_dataset()
    pp = preprocess_dataset(ds, _BASE_RECIPE)
    pca = pca_dataset(ds)
    diff = compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])
    assert pca["provenance"]["parent_ids"] == [pp["provenance"]["result_id"]]
    assert diff["provenance"]["parent_ids"] == [pp["provenance"]["result_id"]]


def test_result_ids_are_unique_per_computation():
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    first = pca_dataset(ds)["provenance"]["result_id"]
    second = pca_dataset(ds)["provenance"]["result_id"]
    assert first != second


def test_results_are_registered_by_id():
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    result = compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])
    rid = result["provenance"]["result_id"]
    assert ds.results[rid]["provenance"]["result_id"] == rid


def test_existing_result_keys_are_not_renamed():
    """provenance を足すだけ。既存の消費者（export・図）が読むキーは動かさない。"""
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    diff = compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])
    for key in ("kind", "results", "volcano", "contract_version", "a", "b",
                "n_a", "n_b", "q_threshold", "log2fc_threshold", "summary"):
        assert key in diff
    assert "scores" in pca_dataset(ds)


# ---------- 有効性の検査 ----------

def test_assert_current_passes_for_a_fresh_result():
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    assert_current(ds, pca_dataset(ds))  # 例外が出ないこと


def test_assert_current_rejects_a_result_from_a_superseded_preprocessing():
    """新しい前処理のあとで古い結果を使うと、図と表の前処理がずれる。"""
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    stale = pca_dataset(ds)
    preprocess_dataset(ds, {"normalize": "tic", "impute": "half_min"})
    with pytest.raises(DomainError) as exc:
        assert_current(ds, stale)
    assert exc.value.code == "RESULT_STALE"


def test_assert_current_rejects_a_result_from_another_dataset():
    ds = make_dataset()
    other = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    preprocess_dataset(other, _BASE_RECIPE)
    foreign = pca_dataset(other)
    with pytest.raises(DomainError):
        assert_current(ds, foreign)


# ---------- トランザクション ----------

def test_new_preprocess_invalidates_existing_comparison():
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    result = compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])
    assert result["provenance"]["result_id"]
    preprocess_dataset(ds, {"normalize": "tic", "impute": "half_min"})
    assert ds.last_differential is None
    assert ds.last_pca is None


def test_failed_preprocess_leaves_the_previous_state_untouched(monkeypatch):
    """途中で落ちた前処理が、行列だけ新しくレシピは古いという状態を作らない。"""
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    diff = compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])
    before_matrix = ds.pp_matrix.copy()
    before_recipe = dict(ds.preprocessing_recipe)
    before_preprocess_id = ds.preprocess_id

    with pytest.raises(Exception):
        preprocess_dataset(ds, {"normalize": "no_such_method", "impute": "half_min"})

    assert np.array_equal(ds.pp_matrix, before_matrix)
    assert ds.preprocessing_recipe == before_recipe
    assert ds.preprocess_id == before_preprocess_id
    assert ds.last_differential["provenance"]["result_id"] == diff["provenance"]["result_id"]


def test_reapplying_the_same_recipe_keeps_the_derived_results():
    """同値のレシピを再適用しただけで結果を捨てると、無駄な再計算を強いる。"""
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    diff = compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])
    preprocess_id = ds.preprocess_id

    preprocess_dataset(ds, dict(_BASE_RECIPE))

    assert ds.preprocess_id == preprocess_id
    assert ds.last_differential["provenance"]["result_id"] == diff["provenance"]["result_id"]


def test_reapplying_the_same_recipe_does_not_mint_a_new_result_id():
    """同じ計算に別の ID を付けると、来歴が「別の実行」に見える。"""
    ds = make_dataset()
    first = preprocess_dataset(ds, _BASE_RECIPE)
    second = preprocess_dataset(ds, dict(_BASE_RECIPE))
    assert second["provenance"]["result_id"] == first["provenance"]["result_id"]


def test_changing_only_the_group_membership_keeps_the_preprocessing():
    """群は前処理の入力ではない。付け替えのたびに再前処理させない。"""
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    preprocess_id = ds.preprocess_id
    pca = pca_dataset(ds)

    invalidate_results(ds, {"group"})

    assert ds.preprocess_id == preprocess_id
    assert ds.last_pca["provenance"]["result_id"] == pca["provenance"]["result_id"]


def test_detection_mask_change_invalidates_the_preprocessing():
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    pca_dataset(ds)

    invalidate_results(ds, {"detection"})

    assert ds.pp_matrix is None
    assert ds.last_pca is None


def test_compare_dataset_updates_the_state_only_on_success():
    ds = make_dataset()
    preprocess_dataset(ds, _BASE_RECIPE)
    good = compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])

    with pytest.raises(Exception):
        compare_dataset(ds, ["missing_a"], ["missing_b"])

    assert ds.last_differential["provenance"]["result_id"] == good["provenance"]["result_id"]


def test_a_result_without_provenance_is_not_called_stale():
    """来歴が無いのは「古い」の証拠ではない。旧経路の結果を一律に殺さない。"""
    from lipidmix.analysis.result_state import is_current
    ds = make_dataset()
    assert is_current(ds, {"scores": []}) is True
    assert_current(ds, {"scores": []})
