"""lipidmix.pipeline.service の単体テスト。

`tests/test_pipeline_engine.py` / `test_pipeline_recovery.py` は合成handlerで
engine自体（stage順序・owner lock・冪等性）を検証しており、`build_handlers()`が
組み立てる実handler一式そのものは別の場所で検証されていなかった。ここでは
そのうち、carry-forwardルーリングとして明示された振る舞いに絞って検証する。

- R18: PCA不成立(`PreconditionError`)がhandler境界で`needs_input`へ変換されること。
- Console用workerを二重起動しないこと（`upstream` handlerが `console.worker` を
  一切呼ばず `console.execution.supervise` だけを呼ぶこと）。
- `launch_pipeline_worker` が `sys.executable` を使い、cwdをこのcheckout自身へ
  固定すること（Task2: 利用者のcwdや別checkoutを暗黙に使わない）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lipidmix.analysis.dataset_analysis import PreconditionError
from lipidmix.pipeline import service


def _pca_context(pipeline_root: Path) -> dict:
    return {
        "runtime": {"dataset": object()},  # pca_dataset自体をpatchするので中身は使わない
        "pipeline_root": pipeline_root,
        "request_meta": {"revision": 1},
    }


def test_pca_precondition_error_becomes_needs_input_stage_result(tmp_path, monkeypatch):
    """R18: PreconditionErrorはhandler境界でneeds_inputへ変換され、failedにならない。"""
    def raise_precondition(*a, **kw):
        raise PreconditionError("missing_state", "検出状態が無くPCAを実行できません。",
                                {"detail": "no-detection-state"}, state="preprocessed_matrix")

    monkeypatch.setattr(service.dataset_service, "pca_dataset", raise_precondition)

    outcome = service._handle_pca(_pca_context(tmp_path))

    assert outcome["status"] == "needs_input"
    assert outcome["error"]["code"] == "PCA_PRECONDITION_MISSING"
    assert outcome["result_refs"] == []


def test_pca_success_persists_data_and_figure_refs(tmp_path, monkeypatch):
    """成功時はpca/pca_figureの2件のoutput_name付きrefをresult_refsへ積む。"""
    fake_result = {
        "provenance": {"result_id": "res_pca_fake", "input_fingerprint": "f" * 40,
                      "parent_ids": [], "warnings": []},
        "scores": [[0.1, 0.2]], "loadings": [[0.3, 0.4]], "explained_variance": [0.9],
    }
    def fake_save_figure(ds, result, figure_path, *, kind):
        Path(figure_path).parent.mkdir(parents=True, exist_ok=True)
        Path(figure_path).write_bytes(b"fake-png")

    monkeypatch.setattr(service.dataset_service, "pca_dataset", lambda *a, **kw: fake_result)
    monkeypatch.setattr(service.result_output, "save_result_figure", fake_save_figure)

    outcome = service._handle_pca(_pca_context(tmp_path))

    assert outcome["status"] == "succeeded"
    names = {ref["output_name"] for ref in outcome["result_refs"]}
    assert names == {"pca", "pca_figure"}


def test_upstream_handler_never_calls_console_worker_module(tmp_path, monkeypatch):
    """Console用workerを二重起動しない: `lipidmix.console.worker`を一切呼ばない。"""
    import lipidmix.console.worker as console_worker_mod

    def _forbidden(*a, **kw):
        raise AssertionError("upstream handlerがlipidmix.console.workerを呼び出した"
                            "（Console用workerの二重起動）")

    for name in ("run_job", "launch_console_worker"):
        if hasattr(console_worker_mod, name):
            monkeypatch.setattr(console_worker_mod, name, _forbidden)

    supervise_calls = []

    def fake_supervise(job_path, *, cancel_path):
        supervise_calls.append((job_path, cancel_path))
        return {"execution_id": "exec-1", "termination": "exited", "exit_code": 0}

    class _FakeJob:
        status = "completed"
        warnings: list[str] = []
        error = None

    monkeypatch.setattr(service.console_execution, "supervise", fake_supervise)
    monkeypatch.setattr(service.job_manager, "load_job", lambda job_path: _FakeJob())
    monkeypatch.setattr(service.job_manager, "count_raw_inputs", lambda root: 1)
    monkeypatch.setattr(service.job_manager, "list_raw_inputs", lambda root: [])
    monkeypatch.setattr(service.store, "register_job_owner", lambda job_path, root: None)
    monkeypatch.setattr(service.console_execution, "write_supervision_inputs",
                        lambda run_dir, payload: None)

    pipeline_root = tmp_path / "pipeline"
    pipeline_root.mkdir()
    (pipeline_root / "input").mkdir()
    context = {
        "pipeline_root": pipeline_root,
        "pipeline_id": "pipe1",
        "request": {"measure": "peak_height", "save_project": False, "timeout_s": 60},
        "inputs": {"method": {"effective_relative_path": None, "source_path": "C:/m.txt"},
                  "polarity": {"value": "negative"}, "exe": {"path": "C:/fake.exe"}},
    }

    outcome = service._handle_upstream(context)

    assert len(supervise_calls) == 1
    assert outcome["status"] == "succeeded"


def test_launch_pipeline_worker_uses_sys_executable_and_repo_root_cwd(tmp_path, monkeypatch):
    """Task2: launchはsys.executableで、cwdは呼び出し元のcwdでなくこのcheckout自身。"""
    captured = {}

    def fake_launch_detached(command, *, cwd, log_path):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["log_path"] = log_path
        return {"launched": True, "pid": 4321}

    monkeypatch.setattr(service, "launch_detached", fake_launch_detached)
    pipeline_path = tmp_path / "pipeline"
    (pipeline_path / "control").mkdir(parents=True)

    result = service.launch_pipeline_worker(pipeline_path)

    assert result == {"launched": True, "pid": 4321}
    assert captured["command"][0] == sys.executable
    assert captured["command"][1:4] == ["-m", "lipidmix.pipeline.worker", "--pipeline"]
    assert captured["command"][4] == str(pipeline_path)
    assert captured["cwd"] == service._REPO_ROOT
