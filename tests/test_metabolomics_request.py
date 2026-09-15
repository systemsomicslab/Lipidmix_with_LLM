"""pipeline-request.v2 の解決・更新契約（spec §6, §6.2）を検証する。

request_v2.resolve/validate_statistics/merge_updates はすべて
lipidmix.pipeline.request_v2 にある。既存v1 (lipidmix.pipeline.request) は
schemaディスパッチだけを追加で持ち、v1自身の挙動（既定値・null・更新）は
一切変えない——tests/test_pipeline_request.py の既存assertは変更しない。

profileはlcms-profile.v1のうちrequest_v2が実際に参照するキー（matrix_recipes・
feature_targets・analysis_recipe.statistics）だけを持つ最小dictで足りる
（validate_profileを通す義務はない——request_v2はprofileを検証済みとして受け取る側）。
"""
from __future__ import annotations

import copy

import pytest

from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline import request as request_v1
from lipidmix.pipeline import request_v2
from lipidmix.pipeline.request_v2 import merge_updates, resolve, validate_statistics


def _profile(matrix_recipes=None, feature_targets=None, statistics=None):
    return {
        "matrix_recipes": matrix_recipes if matrix_recipes is not None else {
            "default": {"base": "peak_height", "normalize": "none",
                        "drift_correct": False, "filter": None, "impute": "none"},
        },
        "feature_targets": feature_targets if feature_targets is not None else {},
        "analysis_recipe": {
            "statistics": statistics if statistics is not None else [],
            "internal_standards": [],
        },
    }


def _pca(statistic_id="pca", matrix_recipe_id="default", **extra):
    base = {
        "statistic_id": statistic_id, "kind": "pca", "matrix_recipe_id": matrix_recipe_id,
        "transform": "none", "feature_scope": {"mode": "all_eligible"},
    }
    base.update(extra)
    return base


def _welch(statistic_id="welch1", reference_group="control", test_group="treated", **extra):
    base = {
        "statistic_id": statistic_id, "kind": "welch", "matrix_recipe_id": "default",
        "transform": "log2", "feature_scope": {"mode": "all_eligible"},
        "reference_group": reference_group, "test_group": test_group,
    }
    base.update(extra)
    return base


# ---------- brief記載のRED（pca rejects groups. 手を加えず、原文を保つ） ----------

def test_pca_rejects_groups():
    s = {"statistic_id": "p", "kind": "pca", "matrix_recipe_id": "default",
         "transform": "none", "feature_scope": {"mode": "all_eligible"},
         "groups": ["a", "b"]}
    with pytest.raises(DomainError):
        validate_statistics([s], {"matrix_recipes": {"default": {}}})


# ---------- unknown ----------

def test_unknown_top_level_key_rejected():
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve({"profile_file": "profile.json", "bogus_key": 1}, _profile())


def test_unknown_key_in_statistic_item_rejected():
    profile = _profile()
    stat = _pca()
    stat["bogus"] = "x"
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        validate_statistics([stat], profile)


def test_unknown_statistic_kind_rejected():
    profile = _profile()
    stat = _pca()
    stat["kind"] = "boxplot"
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        validate_statistics([stat], profile)


# ---------- null ----------

def test_explicit_null_target_rejected():
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve({"profile_file": "profile.json", "target": None}, _profile())


def test_explicit_null_statistics_rejected():
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve({"profile_file": "profile.json", "statistics": None}, _profile())


def test_explicit_null_profile_file_rejected():
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve({"profile_file": None}, _profile())


def test_sample_manifest_explicit_null_is_allowed_like_v1():
    resolved = resolve(
        {"profile_file": "profile.json", "sample_manifest": None,
         "statistics": [_pca()]},
        _profile(),
    )
    assert resolved["sample_manifest"] is None
    assert resolved["value_sources"]["sample_manifest"] == "explicit"


# ---------- 旧comparisons ----------

def test_old_comparisons_field_rejected():
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve(
            {"profile_file": "profile.json",
             "comparisons": [{"comparison_id": "a_vs_b",
                               "reference_group": "a", "test_group": "b"}]},
            _profile(),
        )


# ---------- method_file直接指定 ----------

def test_method_file_direct_specification_rejected():
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve({"profile_file": "profile.json", "method_file": "method.txt"}, _profile())


def test_lbm_file_direct_specification_rejected():
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve({"profile_file": "profile.json", "lbm_file": "lib.lbm"}, _profile())


# ---------- 重複statistic_id ----------

def test_duplicate_statistic_id_rejected():
    profile = _profile()
    stats = [_pca(statistic_id="dup"), _pca(statistic_id="dup")]
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        validate_statistics(stats, profile)


# ---------- target不整合 ----------

def test_exploratory_target_with_test_statistic_rejected():
    profile = _profile()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve(
            {"profile_file": "profile.json", "target": "exploratory",
             "statistics": [_welch()]},
            profile,
        )


def test_differential_target_without_test_statistic_rejected():
    profile = _profile()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve(
            {"profile_file": "profile.json", "target": "differential",
             "statistics": [_pca()]},
            profile,
        )


def test_target_auto_resolves_to_differential_with_welch():
    profile = _profile()
    resolved = resolve(
        {"profile_file": "profile.json", "statistics": [_welch()]}, profile,
    )
    assert resolved["target"] == "auto"
    assert resolved["effective_target"] == "differential"


def test_target_auto_resolves_to_exploratory_with_only_pca():
    profile = _profile()
    resolved = resolve(
        {"profile_file": "profile.json", "statistics": [_pca()]}, profile,
    )
    assert resolved["effective_target"] == "exploratory"


# ---------- schema省略はv1 ----------

def test_schema_omission_defaults_to_v1(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    resolved = request_v1.resolve_request(root, {})
    assert resolved["schema"] == "pipeline-request.v1"


def test_schema_omission_in_explicit_dict_still_dispatches_v1_even_with_profile(tmp_path):
    """profileを渡しても、schemaを明示しない限りv2を推測しない(A01)。"""
    root = tmp_path / "source"
    root.mkdir()
    resolved = request_v1.resolve_request(root, {}, profile=_profile())
    assert resolved["schema"] == "pipeline-request.v1"


# ---------- routine範囲外override ----------

def _profile_with_two_recipes():
    return _profile(matrix_recipes={
        "default": {"base": "peak_height", "normalize": "none",
                    "drift_correct": False, "filter": None, "impute": "none"},
        "alt": {"base": "peak_height", "normalize": "tic",
                "drift_correct": True, "filter": None, "impute": "half_min"},
    })


def test_routine_preprocess_override_outside_certified_scope_rejected():
    profile = _profile_with_two_recipes()
    with pytest.raises(DomainError, match="PROFILE_SCOPE_MISMATCH"):
        resolve(
            {"profile_file": "profile.json",
             "preprocess": {"default": {"normalize": "median"}},
             "statistics": [_pca()]},
            profile,
        )


def test_routine_preprocess_override_within_certified_scope_allowed():
    profile = _profile_with_two_recipes()
    resolved = resolve(
        {"profile_file": "profile.json",
         "preprocess": {"default": {"normalize": "tic"}},
         "statistics": [_pca()]},
        profile,
    )
    assert resolved["preprocess"]["default"]["normalize"] == "tic"


def test_validation_purpose_allows_out_of_scope_override():
    profile = _profile_with_two_recipes()
    resolved = resolve(
        {"profile_file": "profile.json", "execution_purpose": "validation",
         "preprocess": {"default": {"normalize": "median"}},
         "statistics": [_pca()]},
        profile,
    )
    assert resolved["preprocess"]["default"]["normalize"] == "median"


def test_preprocess_override_unknown_recipe_rejected():
    profile = _profile()
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve(
            {"profile_file": "profile.json",
             "preprocess": {"nonexistent": {"normalize": "none"}},
             "statistics": [_pca()]},
            profile,
        )


def test_preprocess_override_double_normalization_rejected():
    profile = _profile(matrix_recipes={
        "default": {"base": "internal_standard_ratio", "normalize": "none",
                    "drift_correct": False, "filter": None, "impute": "none"},
    })
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve(
            {"profile_file": "profile.json",
             "preprocess": {"default": {"normalize": "tic"}},
             "statistics": [_pca()]},
            profile,
        )


# ---------- 既定値 ----------

def test_statistics_omitted_falls_back_to_profile_default():
    profile = _profile(statistics=[_welch(statistic_id="welch1")])
    resolved = resolve({"profile_file": "profile.json"}, profile)
    assert resolved["value_sources"]["statistics"] == "profile_default"
    assert [s["statistic_id"] for s in resolved["statistics"]] == ["welch1"]
    assert resolved["effective_target"] == "differential"


def test_statistics_omitted_and_profile_has_none_uses_global_pca_default():
    profile = _profile()
    resolved = resolve({"profile_file": "profile.json"}, profile)
    assert resolved["value_sources"]["statistics"] == "v2_default"
    assert resolved["statistics"] == [{
        "statistic_id": "pca", "kind": "pca", "matrix_recipe_id": "default",
        "transform": "none", "feature_scope": {"mode": "all_eligible"},
        "scaling": "autoscale", "n_components": 2,
    }]
    assert resolved["effective_target"] == "exploratory"


def test_pca_defaults_scaling_and_n_components():
    profile = _profile()
    stat = {"statistic_id": "p", "kind": "pca", "matrix_recipe_id": "default",
            "transform": "none", "feature_scope": {"mode": "all_eligible"}}
    normalized = validate_statistics([stat], profile)
    assert normalized[0]["scaling"] == "autoscale"
    assert normalized[0]["n_components"] == 2


def test_welch_defaults_thresholds():
    profile = _profile()
    stat = {"statistic_id": "w", "kind": "welch", "matrix_recipe_id": "default",
            "transform": "log2", "feature_scope": {"mode": "all_eligible"},
            "reference_group": "a", "test_group": "b"}
    normalized = validate_statistics([stat], profile)
    assert normalized[0]["q_threshold"] == 0.05
    assert normalized[0]["log2fc_threshold"] == 1.0


def test_anova_requires_three_or_more_groups():
    profile = _profile()
    stat = {"statistic_id": "an", "kind": "anova_tukey", "matrix_recipe_id": "default",
            "transform": "none", "feature_scope": {"mode": "all_eligible"},
            "groups": ["a", "b"]}
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        validate_statistics([stat], profile)


def test_feature_scope_targets_must_reference_existing_target_id():
    profile = _profile(feature_targets={"gaba": {}})
    stat = _pca()
    stat["feature_scope"] = {"mode": "targets", "target_ids": ["not_gaba"]}
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        validate_statistics([stat], profile)


def test_matrix_recipe_id_must_exist_in_profile():
    profile = _profile()
    stat = _pca(matrix_recipe_id="nonexistent")
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        validate_statistics([stat], profile)


# ---------- standard_assays ----------

def test_standard_assays_unknown_target_id_rejected():
    profile = _profile(feature_targets={"gaba": {}})
    with pytest.raises(DomainError, match="PIPELINE_REQUEST_INVALID"):
        resolve(
            {"profile_file": "profile.json", "statistics": [_pca()],
             "standard_assays": {"not_gaba": ["sample-1"]}},
            profile,
        )


def test_standard_assays_valid_structure_accepted():
    profile = _profile(feature_targets={"gaba": {}})
    resolved = resolve(
        {"profile_file": "profile.json", "statistics": [_pca()],
         "standard_assays": {"gaba": ["sample-1", "sample-2"]}},
        profile,
    )
    assert resolved["standard_assays"] == {"gaba": ["sample-1", "sample-2"]}


def test_standard_assays_omitted_defaults_to_empty():
    profile = _profile()
    resolved = resolve({"profile_file": "profile.json", "statistics": [_pca()]}, profile)
    assert resolved["standard_assays"] == {}


# ---------- 更新不可キー ----------

def test_update_profile_file_requires_new_pipeline():
    profile = _profile()
    resolved = resolve({"profile_file": "profile.json", "statistics": [_pca()]}, profile)
    with pytest.raises(DomainError, match="NEW_PIPELINE_REQUIRED"):
        merge_updates(resolved, {"profile_file": "other.json"}, profile)


def test_update_execution_purpose_requires_new_pipeline():
    profile = _profile()
    resolved = resolve({"profile_file": "profile.json", "statistics": [_pca()]}, profile)
    with pytest.raises(DomainError, match="NEW_PIPELINE_REQUIRED"):
        merge_updates(resolved, {"execution_purpose": "validation"}, profile)


def test_update_statistics_is_allowed_and_revalidated():
    profile = _profile()
    resolved = resolve({"profile_file": "profile.json", "statistics": [_pca()]}, profile)
    updated = merge_updates(resolved, {"statistics": [_welch()]}, profile)
    assert [s["statistic_id"] for s in updated["statistics"]] == ["welch1"]
    assert updated["effective_target"] == "differential"
    assert updated["value_sources"]["statistics"] == "explicit_update"


def test_update_preserves_value_sources_for_untouched_fields():
    profile = _profile()
    resolved = resolve(
        {"profile_file": "profile.json", "statistics": [_pca()],
         "sample_manifest": "manifest.tsv"},
        profile,
    )
    assert resolved["value_sources"]["sample_manifest"] == "explicit"
    updated = merge_updates(resolved, {"target": "differential", "statistics": [_welch()]}, profile)
    # targetを更新してもsample_manifestの出所は変わらない
    assert updated["value_sources"]["sample_manifest"] == "explicit"
    assert updated["value_sources"]["target"] == "explicit_update"


def test_merge_updates_dispatch_from_v1_module(tmp_path):
    """既存request.merge_updatesがschema="pipeline-request.v2"のrequestを
    request_v2.merge_updatesへ委譲することを確認する。"""
    profile = _profile()
    resolved = resolve({"profile_file": "profile.json", "statistics": [_pca()]}, profile)
    updated = request_v1.merge_updates(resolved, {"target": "exploratory"}, profile=profile)
    assert updated["schema"] == request_v2.SCHEMA
    assert updated["target"] == "exploratory"


def test_resolve_request_dispatch_from_v1_module(tmp_path):
    """既存request.resolve_requestがexplicit["schema"]=="pipeline-request.v2"を
    検出してrequest_v2.resolveへ委譲することを確認する。"""
    root = tmp_path / "source"
    root.mkdir()
    profile = _profile()
    resolved = request_v1.resolve_request(
        root, {"schema": "pipeline-request.v2", "profile_file": "profile.json",
               "statistics": [_pca()]},
        profile=profile,
    )
    assert resolved["schema"] == "pipeline-request.v2"
    assert resolved["profile_file"] == "profile.json"


# ---------- resolveは入力を書き換えない ----------

def test_resolve_does_not_mutate_input_profile_or_data():
    profile = _profile(statistics=[_welch()])
    profile_copy = copy.deepcopy(profile)
    data = {"profile_file": "profile.json"}
    data_copy = copy.deepcopy(data)
    resolve(data, profile)
    assert profile == profile_copy
    assert data == data_copy
