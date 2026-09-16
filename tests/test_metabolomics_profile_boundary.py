"""Profile resolution must agree across file requests and worker processes."""
import json

import pytest

from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline import request as request_mod, service
from tests.test_metabolomics_engine import _profile


def test_request_file_validation_purpose_is_used_when_loading_profile(tmp_path):
    profile_path = _profile(tmp_path)
    (tmp_path / "analysis-request.json").write_text(json.dumps({
        "schema": "pipeline-request.v2", "profile_file": str(profile_path),
        "execution_purpose": "validation",
    }), encoding="utf-8")
    arguments = service._profile_arguments(tmp_path, None)
    resolved = request_mod.resolve_request(tmp_path, None, **arguments)
    assert resolved["execution_purpose"] == "validation"
    assert arguments["profile"]["validation"]["status"] == "draft"


def test_relative_profile_is_resolved_against_dataset_root(tmp_path, monkeypatch):
    _profile(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    explicit = {"schema": "pipeline-request.v2", "profile_file": "profile.json",
                "execution_purpose": "validation"}
    arguments = service._profile_arguments(tmp_path, explicit)
    resolved = request_mod.resolve_request(tmp_path, explicit, **arguments)
    assert resolved["profile_file"] == str(tmp_path / "profile.json")
    assert resolved["value_sources"]["profile_file"] == "explicit"
    assert explicit["profile_file"] == "profile.json"


@pytest.mark.parametrize("value", [None, "", 3, []])
def test_invalid_explicit_profile_does_not_fall_back_to_request_file(tmp_path, value):
    profile_path = _profile(tmp_path)
    (tmp_path / "analysis-request.json").write_text(json.dumps({
        "schema": "pipeline-request.v2", "profile_file": str(profile_path),
        "execution_purpose": "validation",
    }), encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        service._profile_arguments(tmp_path, {"schema": "pipeline-request.v2",
                                              "profile_file": value})
    assert exc.value.code == "PIPELINE_REQUEST_INVALID"


def test_explicit_routine_overrides_file_validation(tmp_path):
    profile_path = _profile(tmp_path)
    (tmp_path / "analysis-request.json").write_text(json.dumps({
        "schema": "pipeline-request.v2", "profile_file": str(profile_path),
        "execution_purpose": "validation",
    }), encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        service._profile_arguments(tmp_path, {"execution_purpose": "routine"})
    assert exc.value.code == "PROFILE_NOT_VALIDATED"


@pytest.mark.parametrize("entrypoint", [service.plan_pipeline, service.start_pipeline])
def test_unwired_v2_upstream_cannot_fall_through_to_lipidomics(tmp_path, entrypoint):
    profile_path = _profile(tmp_path)
    with pytest.raises(DomainError) as exc:
        entrypoint(tmp_path, {"schema": "pipeline-request.v2",
                             "profile_file": str(profile_path),
                             "execution_purpose": "validation"})
    assert exc.value.code == "PIPELINE_V2_UPSTREAM_UNAVAILABLE"
    assert not list(tmp_path.rglob("pipeline-run.json"))


def test_snapshot_uses_profile_directory_and_persists_raw_hashes(tmp_path):
    from lipidmix.pipeline import metabolomics_handlers
    from tests.test_lcms_profile_inputs import _build_profile, _write_fake_exe, _write, _msp_dependency

    profile_root = tmp_path / "configuration"
    profile_root.mkdir()
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    _write(raw_root / "sample.mzML", "synthetic raw")
    exe = _write_fake_exe(profile_root)
    library = _write(profile_root / "lib.msp", "synthetic library")
    profile = _build_profile(profile_root, method_lines="Ion mode: Positive\n",
                             dependencies=[_msp_dependency(library)], exe=exe)
    profile["processing"]["method_path"] = "method/params.txt"
    path = profile_root / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    context = {"request": {"profile_file": str(path), "execution_purpose": "validation"},
               "identity": {"source_root": str(raw_root)}, "runtime": {},
               "pipeline_root": tmp_path / "run", "pipeline_id": "boundary"}
    outcome = metabolomics_handlers.snapshot_profile_outcome(context, {"status": "succeeded"})
    plan = context["runtime"]["profile_plan"]
    assert plan["raw_files"]["files"][0]["relative_path"] == "sample.mzML"
    ref = next(r for r in outcome["result_refs"] if r["output_name"] == "execution_manifest")
    # Read the actual persisted artifact, not a mocked persistence callback.
    artifact = json.loads((context["pipeline_root"] / ref["relative_path"]).read_text(encoding="utf-8"))
    assert artifact["data"]["raw"]["files"][0]["sha256"]
