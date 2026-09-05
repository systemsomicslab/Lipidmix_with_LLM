"""pipeline-request.v1 の厳密な解決・更新契約（spec §10.1）を検証する。

resolve_request/merge_updates/validate_request/request_fingerprint はすべて
lipidmix.pipeline.request にある。実rawや既存成果物は使わない
（tmp_path上に空のsource_rootを作るだけ）。
"""
import math

import pytest

from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline.request import (
    UPDATABLE,
    merge_updates,
    request_fingerprint,
    resolve_request,
    validate_request,
)


# ---------- brief記載のRED ----------

def test_null_disable_is_distinct_from_omitted(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    original = resolve_request(root)
    assert original["preprocess"]["blank_min_fold"] == "auto"
    changed = merge_updates(original, {"preprocess": {"blank_min_fold": None}})
    assert changed["preprocess"]["blank_min_fold"] is None
    assert changed["preprocess"]["normalize"] == "auto"


def test_resume_cannot_change_upstream(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="NEW_PIPELINE_REQUIRED"):
        merge_updates(resolve_request(root), {"method_file": "other.txt"})


# ---------- 既定値・出所 ----------

def test_defaults_are_fully_populated(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)

    assert req["schema"] == "pipeline-request.v1"
    assert req["target"] == "auto"
    assert req["effective_target"] == "exploratory"  # comparisons空 => exploratory
    assert req["method_file"] is None
    assert req["lbm_file"] is None
    assert req["polarity"] is None
    assert req["measure"] == "peak_height"
    assert req["keep_extension"] is None
    assert req["timeout_s"] == 21600
    assert req["save_project"] is True
    assert req["output_root"] is None
    assert req["sample_manifest"] is None
    assert req["comparisons"] == []
    assert req["preprocess"] == {
        "policy": "conservative-v1", "normalize": "auto", "blank_min_fold": "auto",
        "drift_correct": "auto", "max_qc_rsd": "auto", "impute": "half_min",
        "min_detection_rate": 0.0,
    }
    # 未指定は全フィールドdefault
    assert req["value_sources"]["target"] == "default"
    assert req["value_sources"]["sample_manifest"] == "default"
    assert all(v == "default" for v in req["value_sources"]["preprocess"].values())
    assert req["value_sources"]["comparisons"] == "default"


def test_explicit_overrides_default_and_source_is_explicit(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"polarity": "negative", "preprocess": {"impute": "knn"}})
    assert req["polarity"] == "negative"
    assert req["value_sources"]["polarity"] == "explicit"
    # 未指定の他preprocessキーはdefaultのまま
    assert req["preprocess"]["impute"] == "knn"
    assert req["preprocess"]["normalize"] == "auto"
    assert req["value_sources"]["preprocess"]["impute"] == "explicit"
    assert req["value_sources"]["preprocess"]["normalize"] == "default"


def test_explicit_gt_file_gt_default_resolution_order(tmp_path):
    """明示値 > (前revisionの)ファイル的な既存値 > 既定、の3段の優先順位。"""
    root = tmp_path / "source"
    root.mkdir()
    base = resolve_request(root, {"preprocess": {"normalize": "tic"}})
    assert base["preprocess"]["normalize"] == "tic"  # 既定autoではなく明示値

    # merge_updatesの`request`引数は「保存済みファイルから読み戻した値」を表す。
    # ここでの明示updatesがそれをさらに上書きすることを確認する。
    updated = merge_updates(base, {"preprocess": {"normalize": "median"}})
    assert updated["preprocess"]["normalize"] == "median"
    assert updated["value_sources"]["preprocess"]["normalize"] == "explicit_update"
    # 更新していない他のpreprocessキーの出所は維持される
    assert updated["value_sources"]["preprocess"]["impute"] == "default"


# ---------- bool/NaN/Infinityの拒否 ----------

def test_timeout_s_rejects_bool(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"timeout_s": True})


def test_preprocess_threshold_rejects_bool(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"preprocess": {"blank_min_fold": True}})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_preprocess_rejects_nan_and_infinity(tmp_path, bad):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"preprocess": {"max_qc_rsd": bad}})


def test_comparison_threshold_rejects_nan(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"comparisons": [{
            "comparison_id": "a_vs_b", "reference_group": "a", "test_group": "b",
            "q_threshold": math.nan,
        }]})


# ---------- target 固定/更新 ----------

def test_target_auto_resolves_to_differential_when_comparisons_present(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"comparisons": [{
        "comparison_id": "t_vs_c", "reference_group": "control", "test_group": "treated",
    }]})
    assert req["target"] == "auto"
    assert req["effective_target"] == "differential"


def test_target_can_be_updated_via_resume(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    assert req["effective_target"] == "exploratory"
    updated = merge_updates(req, {"target": "differential"})
    assert updated["target"] == "differential"
    assert updated["effective_target"] == "differential"
    assert updated["value_sources"]["target"] == "explicit_update"


# ---------- sample_manifest: null(明示解除) と 未指定(既定探索) の区別 ----------

def test_sample_manifest_omitted_means_default_search(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    assert req["sample_manifest"] is None
    assert req["value_sources"]["sample_manifest"] == "default"


def test_sample_manifest_explicit_path_then_explicit_null_release(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"sample_manifest": "sample-manifest.tsv"})
    assert req["sample_manifest"] == "sample-manifest.tsv"
    assert req["value_sources"]["sample_manifest"] == "explicit"

    released = merge_updates(req, {"sample_manifest": None})
    assert released["sample_manifest"] is None
    assert released["value_sources"]["sample_manifest"] == "explicit_update"


# ---------- preprocessの深い更新 ----------

def test_merge_updates_preprocess_updates_only_named_child_keys(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"preprocess": {
        "normalize": "tic", "impute": "knn", "min_detection_rate": 0.1,
    }})
    updated = merge_updates(req, {"preprocess": {"impute": "column_mean"}})
    assert updated["preprocess"]["impute"] == "column_mean"
    # 指定していないキーは前の値のまま(既定ではなく、resolve_request時の明示値)
    assert updated["preprocess"]["normalize"] == "tic"
    assert updated["preprocess"]["min_detection_rate"] == 0.1
    assert updated["value_sources"]["preprocess"]["impute"] == "explicit_update"
    assert updated["value_sources"]["preprocess"]["normalize"] == "explicit"


def test_merge_updates_preprocess_value_must_be_dict(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        merge_updates(req, {"preprocess": "tic"})


def test_merge_updates_preprocess_rejects_unknown_child_key(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        merge_updates(req, {"preprocess": {"unknown_field": 1}})


# ---------- comparisons: 方向・重複ID・path脱出 ----------

def test_comparison_reference_and_test_must_differ(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"comparisons": [{
            "comparison_id": "x", "reference_group": "control", "test_group": "control",
        }]})


def test_comparison_duplicate_id_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"comparisons": [
            {"comparison_id": "a", "reference_group": "control", "test_group": "treated"},
            {"comparison_id": "a", "reference_group": "control", "test_group": "treated2"},
        ]})


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "a\\b", "..", ""])
def test_comparison_id_path_escape_rejected(tmp_path, bad_id):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"comparisons": [
            {"comparison_id": bad_id, "reference_group": "control", "test_group": "treated"},
        ]})


def test_comparison_defaults_are_filled(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"comparisons": [{
        "comparison_id": "t_vs_c", "reference_group": "control", "test_group": "treated",
    }]})
    comparison = req["comparisons"][0]
    assert comparison["q_threshold"] == 0.05
    assert comparison["log2fc_threshold"] == 1.0
    assert comparison["log_transform"] is True
    assert comparison["allow_confounded"] is False


def test_comparisons_replace_whole_array_on_resume(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"comparisons": [
        {"comparison_id": "a", "reference_group": "control", "test_group": "treated"},
    ]})
    updated = merge_updates(req, {"comparisons": [
        {"comparison_id": "b", "reference_group": "control", "test_group": "treated2"},
    ]})
    ids = [c["comparison_id"] for c in updated["comparisons"]]
    assert ids == ["b"]


# ---------- 未知キー・内部キーの拒否 ----------

def test_resolve_request_rejects_unknown_top_level_key(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"not_a_real_key": 1})


@pytest.mark.parametrize("internal_key", ["effective_target", "value_sources"])
def test_resolve_request_rejects_internal_keys_from_external_input(tmp_path, internal_key):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {internal_key: "x"})


@pytest.mark.parametrize("internal_key", ["effective_target", "value_sources"])
def test_merge_updates_rejects_internal_keys_in_updates(tmp_path, internal_key):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    with pytest.raises(DomainError, match="NEW_PIPELINE_REQUIRED"):
        merge_updates(req, {internal_key: "x"})


def test_merge_updates_rejects_keys_outside_updatable(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    assert UPDATABLE == {"target", "sample_manifest", "preprocess", "comparisons"}
    with pytest.raises(DomainError, match="NEW_PIPELINE_REQUIRED"):
        merge_updates(req, {"polarity": "positive"})


# ---------- request_fingerprint ----------

def test_request_fingerprint_stable_for_same_content(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req1 = resolve_request(root, {"polarity": "negative"})
    req2 = resolve_request(root, {"polarity": "negative"})
    assert request_fingerprint(req1) == request_fingerprint(req2)


def test_request_fingerprint_differs_for_different_content(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req1 = resolve_request(root, {"polarity": "negative"})
    req2 = resolve_request(root, {"polarity": "positive"})
    assert request_fingerprint(req1) != request_fingerprint(req2)


def test_request_fingerprint_ignores_provenance_only_differences(tmp_path):
    """value_sources/effective_targetのような由来情報だけが違っても内容が同じなら同一hash。"""
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"polarity": "negative"})
    updated = merge_updates(req, {"target": "auto"})  # 値は変わらないが出所はexplicit_updateへ
    assert request_fingerprint(req) == request_fingerprint(updated)


# ---------- validate_request 単体 ----------

def test_validate_request_rejects_unknown_key_directly():
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        validate_request({"not_a_real_key": 1})


# ---------- 明示無効化(auto/既定と同値でも出所で区別) ----------

def test_normalize_none_and_drift_correct_false_are_explicit_not_default(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"preprocess": {"normalize": "none", "drift_correct": False}})
    assert req["preprocess"]["normalize"] == "none"
    assert req["preprocess"]["drift_correct"] is False
    assert req["value_sources"]["preprocess"]["normalize"] == "explicit"
    assert req["value_sources"]["preprocess"]["drift_correct"] == "explicit"


def test_min_detection_rate_explicit_zero_distinct_from_default_zero(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    default_req = resolve_request(root)
    explicit_req = resolve_request(root, {"preprocess": {"min_detection_rate": 0.0}})
    assert default_req["preprocess"]["min_detection_rate"] == 0.0
    assert explicit_req["preprocess"]["min_detection_rate"] == 0.0
    assert default_req["value_sources"]["preprocess"]["min_detection_rate"] == "default"
    assert explicit_req["value_sources"]["preprocess"]["min_detection_rate"] == "explicit"


def test_blank_min_fold_explicit_null_is_disable_not_auto(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"preprocess": {"blank_min_fold": None}})
    assert req["preprocess"]["blank_min_fold"] is None
    assert req["value_sources"]["preprocess"]["blank_min_fold"] == "explicit"


# ---------- 個別フィールドのenum/型 ----------

def test_polarity_rejects_unknown_value(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"polarity": "both"})


def test_measure_rejects_value_other_than_peak_height(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"measure": "peak_area"})


def test_keep_extension_rejects_empty_string(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"keep_extension": ""})


def test_output_root_accepts_absolute_path_outside_source_root(tmp_path):
    """output_rootは書込先の変更用途があるためsource_root外の絶対パスも許可する。"""
    root = tmp_path / "source"
    root.mkdir()
    outside = str(tmp_path / "elsewhere")
    req = resolve_request(root, {"output_root": outside})
    assert req["output_root"] == outside


def test_method_file_accepts_absolute_path_outside_source_root(tmp_path):
    """spec §10.1の例に合わせ、method_fileはラボ共有フォルダの絶対パスを許可する。"""
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {"method_file": "C:/lab/methods/lipid_NEG.txt"})
    assert req["method_file"] == "C:/lab/methods/lipid_NEG.txt"


def test_save_project_rejects_non_bool(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"save_project": "true"})


def test_timeout_s_rejects_non_positive(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve_request(root, {"timeout_s": 0})
