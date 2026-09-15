"""MCP 公開経路（spec §12・plan Task 13）。

MCP 層は数値を持たない。ここで縛るのは「どの前提が欠けているか」を利用者が
**区別できる形で**返すこと:

- DatasetState が無いのか、解析行列が無いのか（前者は `dataset_load`、後者は
  pipeline を回す、と次の一手が違う）。
- 指定した `matrix_result_id` が無いのか（近い行列で代用しない）。
- 統計が計算できなかったのか（結果は返るが `status=not_evaluable`）。

worker はこのツールを呼ばない——pipeline の handler は Python 関数を直接呼ぶ
（MCP を自己呼び出ししない、という CLAUDE.md の規約）。
"""
from __future__ import annotations

import json

import numpy as np

from lipidmix.core import session_state
from lipidmix.mztab.dataset_state import DatasetState
from lipidmix.pipeline.metabolomics_handlers import build_handlers
from lipidmix.tools.dataset_analysis_tools import dataset_statistic


# ---------- brief記載のRED ----------

def test_worker_handlers_cover_new_stages():
    assert {"load_assay_evidence", "resolve_feature_bindings", "statistics"} \
        <= set(build_handlers())


# ---------- fixture ----------

def _matrix(matrix_id="mat_x") -> dict:
    values = np.array([[10., 5.], [11., 5.], [9., 5.],
                       [40., 5.], [42., 5.], [38., 5.]])
    return {
        "schema": "analysis-matrix.v1", "matrix_id": matrix_id,
        "parent_id": None, "stage": "finalized", "recipe_id": "default",
        "assay_ids": [f"assay[{i + 1}]" for i in range(6)],
        "feature_ids": ["1", "2"],
        "values": values, "units": ["peak_height", "peak_height"],
        "eligibility_mask": np.ones(2, dtype=bool), "detected_mask": None,
        "imputed_mask": np.zeros(values.shape, dtype=bool),
        "locked_mask": np.zeros(values.shape, dtype=bool),
        "missing_reasons": {}, "support_feature_ids": [],
        "correction_history": [], "caveats": [],
    }


def _rows():
    groups = ["control"] * 3 + ["treated"] * 3
    return [{"sample_id": f"s{i}", "source_file": f"S{i}.wiff", "role": "sample",
             "group": group, "batch": "B1", "injection_order": i + 1,
             "qc_pool": None, "include": True, "biological_sample_id": f"bio{i}"}
            for i, group in enumerate(groups)]


def _session_dataset(with_matrix=True) -> DatasetState:
    ds = DatasetState()
    ds.feature_ids = ["1", "2"]
    ds.sample_assay_ids = [f"assay[{i + 1}]" for i in range(6)]
    ds.sample_names = [f"S{i}" for i in range(6)]
    ds.sample_metadata_rows = _rows()
    if with_matrix:
        matrix = _matrix()
        ds.analysis_matrices[matrix["matrix_id"]] = matrix
    session_state.session.dataset = ds
    return ds


def _spec(**overrides) -> dict:
    spec = {"statistic_id": "w1", "kind": "welch", "matrix_recipe_id": "default",
            "transform": "none", "feature_scope": {"mode": "all_eligible"},
            "reference_group": "control", "test_group": "treated",
            "q_threshold": 0.05, "log2fc_threshold": 1.0}
    spec.update(overrides)
    return spec


def _payload(text: str) -> dict:
    return json.loads(text)


# ---------- registration ----------

def test_the_tool_is_registered_and_read_only():
    import asyncio

    import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert "dataset_statistic" in tools
    assert tools["dataset_statistic"].annotations.readOnlyHint is True


# ---------- 欠けている前提の区別 ----------

def test_without_a_dataset_it_says_to_load_one():
    session_state.session.dataset = None
    payload = _payload(dataset_statistic(_spec(), "mat_x"))
    assert payload["error"]["code"] == "missing_state"
    assert payload["error"]["state"] == "dataset"
    assert "dataset_load" in payload["error"]["required_tools"]


def test_without_a_matrix_it_points_at_the_pipeline_not_at_itself():
    _session_dataset(with_matrix=False)
    payload = _payload(dataset_statistic(_spec(), "mat_x"))
    assert payload["error"]["state"] == "analysis_matrix"
    assert "pipeline_run" in payload["error"]["required_tools"]
    assert "dataset_statistic" not in payload["error"]["required_tools"]


def test_an_unknown_matrix_id_is_not_silently_substituted():
    _session_dataset()
    payload = _payload(dataset_statistic(_spec(), "mat_does_not_exist"))
    assert payload["error"]["code"] == "ANALYSIS_RESULT_NOT_FOUND"
    assert payload["error"]["details"]["available_matrix_ids"] == ["mat_x"]


# ---------- 正常系 ----------

def test_a_successful_statistic_returns_a_summary_not_every_feature():
    _session_dataset()
    payload = _payload(dataset_statistic(_spec(), "mat_x"))
    assert payload["status"] == "success"
    assert payload["matrix_id"] == "mat_x"
    assert payload["effect_size_definition"] == "log2_arithmetic_mean_ratio"
    assert "features" not in payload
    assert payload["n_features"] == 2
    # 全量はセッションに残る（戻り値を肥大させない）。
    stored = session_state.session.dataset.results["stat_w1"]
    assert len(stored["features"]) == 2


def test_a_group_that_is_too_small_is_reported_as_a_machine_readable_error():
    ds = _session_dataset()
    ds.sample_metadata_rows[0]["group"] = "other"
    ds.sample_metadata_rows[1]["group"] = "other"
    payload = _payload(dataset_statistic(_spec(), "mat_x"))
    assert payload["error"]["code"] == "STATISTIC_GROUP_TOO_SMALL"


def test_repeated_injections_are_reported_not_averaged():
    ds = _session_dataset()
    ds.sample_metadata_rows[0]["biological_sample_id"] = "bio1"
    payload = _payload(dataset_statistic(_spec(), "mat_x"))
    assert payload["error"]["code"] == "REPEATED_MEASURES_UNSUPPORTED"


def test_an_unknown_kind_is_refused_with_the_supported_list():
    _session_dataset()
    payload = _payload(dataset_statistic(_spec(kind="mann_whitney"), "mat_x"))
    assert payload["error"]["code"] == "STATISTIC_SPECIFICATION_INVALID"
    assert "welch" in payload["error"]["details"]["supported"]


# ---------- v1 の既定値を変えない ----------

def test_v1_dataset_differential_defaults_are_unchanged():
    import inspect

    from lipidmix.tools.dataset_analysis_tools import dataset_differential

    signature = inspect.signature(dataset_differential)
    assert signature.parameters["q_threshold"].default == 0.05
    assert signature.parameters["log2fc_threshold"].default == 1.0
