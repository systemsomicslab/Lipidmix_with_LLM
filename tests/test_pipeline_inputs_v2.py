"""v2 の入力配置計画は profile だけを情報源にする。

v1 の `inspect_inputs` はフォルダから method を推定し LBM を必須にし、実行体を
環境設定から取る。v2 でそこへ落ちると、profile が固定したのとは別の条件で
Console が走る——`PIPELINE_V2_UPSTREAM_UNAVAILABLE` で公開経路を塞いでいたのは
これを避けるためだった。ここで縛るのは「落ちないこと」そのもの。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lipidmix.console.profile_schema import validate_profile
from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline import inputs as inputs_mod
from tests.metabolomics_fixtures import write_profile


def _setup(tmp_path, monkeypatch) -> tuple[Path, dict, dict]:
    # fixture の実行体は placeholder なので、Console 判定（実際に --help を
    # 起動する）は既存テストと同じ流儀で差し替える。
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    source_root = tmp_path / "source"
    source_root.mkdir()
    for name in ("A", "B"):
        (source_root / f"{name}.wiff").write_bytes(b"synthetic raw")
    profile_path = write_profile(source_root / "profile.json")
    profile = validate_profile(json.loads(profile_path.read_text(encoding="utf-8")))
    # draft profile なので purpose は validation。省略すると既定の routine になり、
    # 証明書が要る経路（R2 未接続）へ入ってしまう。
    request = {"schema": "pipeline-request.v2", "profile_file": str(profile_path),
               "execution_purpose": "validation"}
    return source_root, request, profile


def test_plan_has_the_same_shape_as_the_v1_plan(tmp_path, monkeypatch):
    source_root, request, profile = _setup(tmp_path, monkeypatch)
    plan = inputs_mod.plan_from_profile(source_root, request, profile)
    assert set(plan) == {"source_root", "selected_format", "entries", "raw_stat",
                         "companions", "method", "lbm", "exe", "polarity", "unverified"}
    assert plan["selected_format"] == "wiff"
    assert len(plan["raw_stat"]) == 2


def test_method_and_executable_come_from_the_profile(tmp_path, monkeypatch):
    source_root, request, profile = _setup(tmp_path, monkeypatch)
    plan = inputs_mod.plan_from_profile(source_root, request, profile)
    assert plan["method"]["sha256"] == profile["processing"]["method_sha256"]
    assert plan["exe"]["sha256"] == profile["software"]["executable_sha256"]
    assert plan["polarity"]["source"] == "profile"
    assert plan["polarity"]["value"] == profile["acquisition"]["polarity"]


def test_every_declared_dependency_becomes_a_method_override(tmp_path, monkeypatch):
    source_root, request, profile = _setup(tmp_path, monkeypatch)
    plan = inputs_mod.plan_from_profile(source_root, request, profile)
    for dependency in profile["processing"]["dependencies"]:
        assert dependency["method_key"] in plan["method"]["overrides"]


def test_a_changed_dependency_stops_before_any_plan_is_returned(tmp_path, monkeypatch):
    source_root, request, profile = _setup(tmp_path, monkeypatch)
    target = source_root / profile["processing"]["dependencies"][0]["path"]
    target.write_text("tampered", encoding="utf-8")
    with pytest.raises(DomainError) as excinfo:
        inputs_mod.plan_from_profile(source_root, request, profile)
    assert excinfo.value.code == "INPUT_CHANGED"


# ---------- 受付が v1 経路へ落ちないこと ----------

def test_v2_request_does_not_go_through_the_v1_inspection(tmp_path, monkeypatch):
    from lipidmix.pipeline import service

    source_root, request, _profile = _setup(tmp_path, monkeypatch)

    def _fail(*args, **kwargs):
        raise AssertionError("v2 要求が v1 の inspect_inputs へ落ちた")

    monkeypatch.setattr(inputs_mod, "inspect_inputs", _fail)
    result = service.plan_pipeline(source_root, request)
    assert result["status"] in {"planned", "needs_input"}, result


def test_v2_request_does_not_touch_the_environment_executable(tmp_path, monkeypatch):
    from lipidmix.pipeline import service

    source_root, request, _profile = _setup(tmp_path, monkeypatch)

    def _fail():
        raise AssertionError("v2 要求が環境設定の実行体を参照した")

    monkeypatch.setattr(service, "_resolve_exe_path", _fail)
    service.plan_pipeline(source_root, request)


def test_a_v2_request_without_a_profile_stops_instead_of_falling_back(tmp_path, monkeypatch):
    from lipidmix.pipeline import service

    source_root, request, _profile = _setup(tmp_path, monkeypatch)
    Path(request["profile_file"]).unlink()

    def _fail(*args, **kwargs):
        raise AssertionError("profile が無い v2 要求が v1 の inspect_inputs へ落ちた")

    monkeypatch.setattr(inputs_mod, "inspect_inputs", _fail)
    with pytest.raises(DomainError) as excinfo:
        service.plan_pipeline(source_root, request)
    assert excinfo.value.code != "PIPELINE_V2_UPSTREAM_UNAVAILABLE"
    assert excinfo.value.code in {"PIPELINE_REQUEST_INVALID", "PROFILE_NOT_FOUND"}
