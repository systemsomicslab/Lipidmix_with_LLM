"""再開・再構築・取消・中断の読取表示のテスト（Task16）。

engineの`stage_inputs_unchanged`/`make_context`の拡張はここで、注入handler
（test_pipeline_engine.pyと同じ流儀）を使って検証する——本番handler一式
（`build_handlers`）はTask18が実装する。fixtureは合成dictビルダーとして
本ファイル内に閉じる（controller裁定: 既存`tests.pipeline_fixtures`の
契約は壊さない範囲でしか触らない）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lipidmix.core.atomic_io import DomainError, atomic_write_json
from lipidmix.core.process_control import process_identity
from lipidmix.pipeline.engine import cancel_request_path, run_engine
from lipidmix.pipeline.recovery import prepare_resume, read_status, request_cancel
from lipidmix.pipeline.request import resolve_request
from lipidmix.pipeline.store import create_run, load_run, save_run
import lipidmix.pipeline.recovery as recovery_module
import lipidmix.pipeline.store as store_module

from tests.pipeline_fixtures import make_source

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- 合成fixture ----------

def _minimal_inputs(root: Path, *, fingerprint: str = "f" * 64) -> dict:
    return {"source_root": str(root), "fingerprint": fingerprint, "raw_inventory": []}


def _recorder(calls: list[str], *, status: str = "succeeded",
              result_refs: list | None = None, error: dict | None = None):
    def handler(context: dict) -> dict:
        calls.append(context["stage_id"])
        return {"status": status, "result_refs": list(result_refs or []),
                "warnings": [], "error": error}
    return handler


_HANDLER_KEYS = (
    "prepare_input", "upstream", "validate_outputs", "load_dataset",
    "resolve_metadata", "preprocess", "pca", "resolve_comparisons",
    "differential", "export", "report",
)


def _build_pipeline(tmp_path: Path, *, target: str, comparisons: list | None = None):
    source_root = tmp_path / "source"
    source_root.mkdir()
    request = resolve_request(source_root, {"target": target, "comparisons": comparisons or []})
    inputs = _minimal_inputs(source_root)
    pipeline_path = create_run(source_root, request, inputs)

    calls: list[str] = []
    handlers = {key: _recorder(calls) for key in _HANDLER_KEYS}
    handlers["pca"] = _recorder(calls, result_refs=["pca-result"])
    return pipeline_path, handlers, calls


@pytest.fixture
def run_with_lost_worker(tmp_path):
    """`create_run`/`load_run`/`save_run`だけでstatus=running・worker identity
    設定済みのrunを作る（実PIDを終了させない——自分自身の生きたidentityを使う）。
    """
    pipeline_path, _handlers, _calls = _build_pipeline(tmp_path, target="exploratory")
    record = load_run(pipeline_path)
    record["status"] = "running"
    record["worker"] = {"identity": process_identity_of_self(), "started_at": "2026-09-05T00:00:00+00:00"}
    save_run(pipeline_path, record, expected_revision=record["state_revision"])
    return pipeline_path / store_module.RUN_FILENAME


def process_identity_of_self():
    import os
    return process_identity(os.getpid())


# ---------- brief記載のRED（そのまま） ----------

def test_status_does_not_finalize_or_rewrite(run_with_lost_worker, monkeypatch):
    path = run_with_lost_worker
    before = path.read_bytes()
    monkeypatch.setattr("lipidmix.pipeline.recovery.same_process", lambda identity: False)
    status = read_status(path)
    assert status["observed_health"] == "worker_missing"
    assert path.read_bytes() == before
    assert status["status"] != "completed"


# ---------- read_status: 判定不能はdeadと解釈しない / 生存確認 ----------

def test_unknown_worker_identity_is_unknown_not_dead(tmp_path):
    """worker identity自体が記録されていないrunning ——判定不能はdeadと断定しない。"""
    pipeline_path, _handlers, _calls = _build_pipeline(tmp_path, target="exploratory")
    record = load_run(pipeline_path)
    record["status"] = "running"
    record["worker"] = {"identity": None, "started_at": None}
    save_run(pipeline_path, record, expected_revision=record["state_revision"])

    status = read_status(pipeline_path)
    assert status["observed_health"] == "unknown"
    assert status["status"] == "running"
    assert status["recovery_hint"]["code"] == "SUPERVISION_UNKNOWN"


def test_real_dead_pid_is_worker_missing_without_mocking_same_process(tmp_path):
    """PID再利用/実プロセス終了: モックせず本物のsame_processで判定する。"""
    pipeline_path, _handlers, _calls = _build_pipeline(tmp_path, target="exploratory")
    completed = subprocess.run([sys.executable, "-c", "pass"], timeout=30)
    assert completed.returncode == 0
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    identity = process_identity(proc.pid)
    proc.wait(timeout=30)

    record = load_run(pipeline_path)
    record["status"] = "running"
    record["worker"] = {"identity": identity, "started_at": "2026-09-05T00:00:00+00:00"}
    save_run(pipeline_path, record, expected_revision=record["state_revision"])

    status = read_status(pipeline_path)
    assert status["observed_health"] == "worker_missing"


def test_status_readable_on_old_completed_run_with_only_results(tmp_path):
    """旧completed job: control/等が無くてもresultsとstatusだけで読める（E04）。"""
    pipeline_path, handlers, _calls = _build_pipeline(tmp_path, target="exploratory")
    result = run_engine(pipeline_path, handlers)
    assert result["status"] == "completed"

    status = read_status(pipeline_path)
    assert status["status"] == "completed"
    assert status["observed_health"] == "ok"
    assert status["stage_statuses"]["pca"] == "succeeded"


# ---------- request_cancel: 受理のみ・再送は冪等 ----------

def test_request_cancel_accepts_without_killing_and_is_idempotent(tmp_path):
    pipeline_path, _handlers, _calls = _build_pipeline(tmp_path, target="exploratory")

    first = request_cancel(pipeline_path)
    assert first["accepted"] is True
    assert first["status"] != "cancelled"  # 受理≠確定

    second = request_cancel(pipeline_path)
    assert second["accepted"] is True

    flag = json.loads(cancel_request_path(pipeline_path).read_text(encoding="utf-8"))
    assert flag["cancel_requested"] is True


def test_request_cancel_then_engine_stops_between_stages(tmp_path):
    pipeline_path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    original_preprocess = handlers["preprocess"]

    def preprocess_then_cancel(context):
        outcome = original_preprocess(context)
        request_cancel(pipeline_path)
        return outcome

    handlers["preprocess"] = preprocess_then_cancel
    result = run_engine(pipeline_path, handlers)
    assert result["status"] == "cancelled"
    assert "pca" not in calls


# ---------- prepare_resume: EXECUTION_UNRESOLVED / UPSTREAM_RERUN_REQUIRED ----------

def test_upstream_not_succeeded_requires_explicit_rerun_upstream(tmp_path):
    """D09: 取消後・timeout後のresumeは上流再実行の明示指定が無いと進めない。"""
    pipeline_path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")

    def failing_upstream(context):
        calls.append(context["stage_id"])
        raise RuntimeError("boom-during-upstream")

    handlers["upstream"] = failing_upstream
    result = run_engine(pipeline_path, handlers)
    assert result["status"] == "failed"  # 有効な出力が無いのでfailed

    with pytest.raises(DomainError, match="UPSTREAM_RERUN_REQUIRED"):
        prepare_resume(pipeline_path)

    # 明示すれば進める(upstream stageがpendingへ戻る)。
    accepted = prepare_resume(pipeline_path, rerun_upstream=True)
    assert accepted["reused"] is False
    assert "upstream" in accepted["reset_stage_ids"]
    record = load_run(pipeline_path)
    assert record["stages"]["upstream"]["status"] == "pending"
    assert record["status"] == "planned"


def test_execution_unresolved_when_console_alive_but_unverified(tmp_path, monkeypatch):
    pipeline_path, _handlers, _calls = _build_pipeline(tmp_path, target="exploratory")
    job_dir = tmp_path / "console_job"
    job_dir.mkdir()
    receipt = {
        "schema": "console-execution.v1", "process_identity": {"pid": 4242, "creation_time": 7},
    }
    (job_dir / "execution-result.json").write_text(json.dumps(receipt), encoding="utf-8")
    # console_job_pathはanalysis-job.jsonそのものへのパス(run_dirはその親)という
    # コードベース全体の流儀(store.register_job_owner等)に合わせる。
    job_json_path = job_dir / "analysis-job.json"
    job_json_path.write_text("{}", encoding="utf-8")

    record = load_run(pipeline_path)
    record["upstream"]["console_job_path"] = str(job_json_path)
    save_run(pipeline_path, record, expected_revision=record["state_revision"])

    monkeypatch.setattr(recovery_module, "same_process", lambda identity: True)
    with pytest.raises(DomainError, match="EXECUTION_UNRESOLVED"):
        prepare_resume(pipeline_path)


# ---------- D03: 上流を再実行せずcomparisonを解決して完走する ----------

def test_resume_reuses_upstream_and_completes_after_adding_comparisons(tmp_path):
    comparisons = [{"comparison_id": "treated_vs_control",
                    "reference_group": "control", "test_group": "treated"}]
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=[])
    handlers["resolve_comparisons"] = _recorder(
        calls, status="needs_input",
        error={"code": "COMPARISON_REQUIRED", "message": "比較を指定してください。", "details": {}})

    waiting = run_engine(path, handlers)
    assert waiting["status"] == "needs_input"

    accepted = prepare_resume(path, updates={"comparisons": comparisons})
    assert accepted["reused"] is False
    assert "resolve_comparisons" in accepted["reset_stage_ids"]

    # resolve_comparisonsは今度こそ本来の(needs_inputを返さない)handlerへ戻す。
    handlers["resolve_comparisons"] = _recorder(calls)

    calls.clear()
    completed = run_engine(path, handlers)
    assert completed["status"] == "completed"
    # upstream/prepare_input/validate_outputsは再実行されない(R20)。
    assert "upstream" not in calls
    assert "prepare_input" not in calls
    assert "validate_outputs" not in calls
    # load_dataset~pcaは再構築のため再度呼ばれる。
    assert "load_dataset" in calls
    assert "preprocess" in calls
    assert "pca" in calls
    assert calls.count("differential:treated_vs_control") == 1
    assert calls.count("export:treated_vs_control") == 1

    record = load_run(path)
    # 探索段階の旧resultは消えていない(append-only)。
    old_results = [r for r in record["results"] if r == "pca-result"]
    assert old_results  # 前回attemptのpca-resultが保持されている


# ---------- group-only更新: 変更した比較だけreset ----------

def test_group_only_update_resets_only_the_changed_comparison(tmp_path):
    comparisons = [
        {"comparison_id": "cmp1", "reference_group": "control", "test_group": "treated"},
        {"comparison_id": "cmp2", "reference_group": "control", "test_group": "other"},
    ]
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=comparisons)
    result = run_engine(path, handlers)
    assert result["status"] == "completed"

    changed = [
        {"comparison_id": "cmp1", "reference_group": "control", "test_group": "recovered"},
        {"comparison_id": "cmp2", "reference_group": "control", "test_group": "other"},
    ]
    accepted = prepare_resume(path, updates={"comparisons": changed})
    assert set(accepted["reset_stage_ids"]) == {
        "resolve_comparisons", "report", "differential:cmp1", "export:cmp1",
    }
    record = load_run(path)
    assert record["stages"]["differential:cmp2"]["status"] == "succeeded"  # 無関係は不変
    assert record["stages"]["differential:cmp1"]["status"] == "pending"


# ---------- 冪等性: 同一request_id再送 / request_id無し同一更新再送 ----------

def test_same_request_id_resend_does_not_create_new_revision(tmp_path):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    result = run_engine(path, handlers)
    assert result["status"] == "completed"

    first = prepare_resume(path, updates={"preprocess": {"normalize": "tic"}},
                           request_id="resume-1")
    assert first["reused"] is False
    second = prepare_resume(path, updates={"preprocess": {"normalize": "tic"}},
                            request_id="resume-1")
    assert second["reused"] is True
    assert second["request_revision"] == first["request_revision"]

    record = load_run(path)
    assert record["request"]["revision"] == first["request_revision"]


def test_same_request_id_resend_with_different_updates_conflicts(tmp_path):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    run_engine(path, handlers)

    prepare_resume(path, updates={"preprocess": {"normalize": "tic"}}, request_id="resume-1")
    with pytest.raises(DomainError, match="IDEMPOTENCY_CONFLICT"):
        prepare_resume(path, updates={"preprocess": {"normalize": "pqn"}}, request_id="resume-1")


def test_same_updates_resent_without_request_id_is_a_no_op(tmp_path):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    run_engine(path, handlers)

    first = prepare_resume(path, updates={"preprocess": {"normalize": "tic"}})
    second = prepare_resume(path, updates={"preprocess": {"normalize": "tic"}})
    assert second["request_revision"] == first["request_revision"]
    record = load_run(path)
    assert record["request"]["revision"] == first["request_revision"]


def test_no_op_resume_does_not_disturb_a_completed_run(tmp_path):
    """完全に無変化なresume呼出しはcompletedを崩さない(no-opはno-opのまま)。"""
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    result = run_engine(path, handlers)
    assert result["status"] == "completed"

    accepted = prepare_resume(path)  # updates無し・rerun_upstream無し
    assert accepted["reused"] is False
    assert accepted["reset_stage_ids"] == []
    record = load_run(path)
    assert record["status"] == "completed"


# ---------- 二重呼出し: 状態競合を有限回で吸収する ----------

def test_prepare_resume_absorbs_concurrent_state_bump(tmp_path, monkeypatch):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    run_engine(path, handlers)

    real_load_run = store_module.load_run
    calls_n = {"n": 0}

    def flaky_load_run(p):
        record = real_load_run(p)
        calls_n["n"] += 1
        if calls_n["n"] == 1:
            concurrent = real_load_run(p)
            concurrent["status"] = "planned"
            store_module.save_run(p, concurrent, expected_revision=concurrent["state_revision"])
        return record

    monkeypatch.setattr(store_module, "load_run", flaky_load_run)

    accepted = prepare_resume(path, updates={"preprocess": {"normalize": "tic"}})
    assert accepted["reused"] is False
    assert calls_n["n"] >= 2


# ---------- D05: 固定入力・実効メソッドの改変を検出する ----------

def _build_pipeline_with_real_inputs(tmp_path, monkeypatch, *, target="exploratory"):
    """Task13の`inspect_inputs`/`stage_inputs`で実際に入力を固定したrunを作る。

    `stage_inputs`は確定済みのpipeline_rootへしか書けないため、まず仮の
    fingerprintで`create_run`し、得られた実際のpipeline_rootへ配置してから
    `record["inputs"]`を実snapshotへ差し替える。upstream stageはこのfixtureの
    時点で（Task18のhandlerを経由せず）直接`succeeded`にする——D05は「上流は
    既に検証済み」という前提のもとでの再検証なので、ここでは
    UPSTREAM_RERUN_REQUIREDゲートを通過させるためだけに必要。
    """
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    from lipidmix.pipeline.inputs import inspect_inputs, stage_inputs
    from lipidmix.core.atomic_io import canonical_hash

    source = make_source(tmp_path / "source")
    request = resolve_request(source["root"], {"target": target})
    plan = inspect_inputs(source["root"], request, exe_path=source["exe"])

    placeholder_inputs = _minimal_inputs(source["root"], fingerprint="0" * 64)
    pipeline_path = create_run(source["root"], request, placeholder_inputs)

    snapshot = stage_inputs(plan, pipeline_path)
    snapshot["fingerprint"] = canonical_hash({"raw_stat": snapshot["raw_stat"]})

    record = load_run(pipeline_path)
    record["inputs"] = snapshot
    record["stages"]["upstream"]["status"] = "succeeded"
    save_run(pipeline_path, record, expected_revision=record["state_revision"])
    return pipeline_path, source


def test_resume_detects_tampered_effective_method(tmp_path, monkeypatch):
    pipeline_path, _source = _build_pipeline_with_real_inputs(tmp_path, monkeypatch)
    effective = pipeline_path / "inputs" / "effective-method.txt"
    effective.write_text(effective.read_text(encoding="ascii") + "\ntampered", encoding="ascii")

    with pytest.raises(DomainError, match="STAGED_INPUT_MISMATCH"):
        prepare_resume(pipeline_path)


def test_resume_detects_changed_raw_input(tmp_path, monkeypatch):
    pipeline_path, source = _build_pipeline_with_real_inputs(tmp_path, monkeypatch)
    (source["root"] / "S0.wiff").write_text("raw-0-changed", encoding="ascii")

    with pytest.raises(DomainError, match="INPUT_CHANGED"):
        prepare_resume(pipeline_path)


def test_resume_with_rerun_upstream_skips_staged_input_check(tmp_path, monkeypatch):
    """rerun_upstream=Trueのときは再検証しない(上流が自分で再検証・再配置する)。"""
    pipeline_path, source = _build_pipeline_with_real_inputs(tmp_path, monkeypatch)
    (source["root"] / "S0.wiff").write_text("raw-0-changed", encoding="ascii")

    accepted = prepare_resume(pipeline_path, rerun_upstream=True)
    assert accepted["reused"] is False


# ---------- hash不一致: 成果物改変は無条件再利用しない(engine側) ----------

def test_tampered_result_artifact_forces_recompute(tmp_path):
    comparisons = [{"comparison_id": "cmp1", "reference_group": "control", "test_group": "treated"}]
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=comparisons)

    output_file = path / "report.txt"

    def report_handler(context):
        calls.append(context["stage_id"])
        output_file.write_text("report-v1", encoding="utf-8")
        import hashlib
        digest = hashlib.sha256(output_file.read_bytes()).hexdigest()
        return {"status": "succeeded",
                "result_refs": [{"relative_path": "report.txt", "hash": digest}],
                "warnings": [], "error": None}

    handlers["report"] = report_handler
    result = run_engine(path, handlers)
    assert result["status"] == "completed"

    # 成果物を改変(hash不一致)してから再度run_engineを呼ぶ。
    output_file.write_text("tampered", encoding="utf-8")
    calls.clear()
    result2 = run_engine(path, handlers)
    assert result2["status"] == "completed"
    assert "report" in calls  # hash不一致を検出し再実行された


def test_untampered_result_artifact_is_skipped_on_second_run(tmp_path):
    comparisons = [{"comparison_id": "cmp1", "reference_group": "control", "test_group": "treated"}]
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=comparisons)
    result = run_engine(path, handlers)
    assert result["status"] == "completed"

    calls.clear()
    # 何も変えずにもう一度run_engineを呼ぶ(resumeの二重呼出し相当)。
    result2 = run_engine(path, handlers)
    assert result2["status"] == "completed"
    assert "upstream" not in calls
    assert "differential:cmp1" not in calls
    assert "export:cmp1" not in calls
    assert "report" not in calls
    # runtime再構築対象は再度呼ばれる。
    assert "load_dataset" in calls
    assert "pca" in calls


# ---------- make_context (R19) ----------

def test_make_context_carries_full_request_and_upstream_identity(tmp_path):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    seen = {}

    def capturing_upstream(context):
        seen["context"] = context
        calls.append(context["stage_id"])
        return {"status": "succeeded", "result_refs": [], "warnings": [], "error": None}

    handlers["upstream"] = capturing_upstream
    run_engine(path, handlers)

    context = seen["context"]
    assert context["request"]["schema"] == "pipeline-request.v1"
    assert "value_sources" in context["request"]
    assert "inputs" in context and "upstream" in context and "identity" in context
    assert context["identity"]["pipeline_root"]
    assert context["request_meta"]["revision"] == 1
