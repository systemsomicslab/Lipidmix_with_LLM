"""工程エンジン（`lipidmix.pipeline.engine`）とsessionを持たないworkerのテスト（Task15）。

engineは注入handlerだけで検証する（本番handler一式`build_handlers`はTask18）。
fixtureは合成dictビルダーとして本ファイル内に閉じる（Controller裁定: 共有
`tests.pipeline_fixtures` は既存helperの契約を壊さない範囲でしか触らない）。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import lipidmix.pipeline.store as store_module
from lipidmix.core.atomic_io import DomainError, atomic_write_json
from lipidmix.pipeline.engine import cancel_request_path, run_engine
from lipidmix.pipeline.request import resolve_request
from lipidmix.pipeline.store import create_run, load_run

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- 合成fixture ----------

def _minimal_inputs(root: Path) -> dict:
    return {"source_root": str(root), "fingerprint": "f" * 64, "raw_inventory": []}


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
    """target/comparisonsからpipelineを1本作り、全stage成功のfake handlersを返す。"""
    source_root = tmp_path / "source"
    source_root.mkdir()
    request = resolve_request(source_root, {"target": target, "comparisons": comparisons or []})
    inputs = _minimal_inputs(source_root)
    pipeline_path = create_run(source_root, request, inputs)

    calls: list[str] = []
    handlers = {key: _recorder(calls) for key in _HANDLER_KEYS}
    # PCAは常に何かresult_refsを残す合成版にする（partial/failed判定の材料）。
    handlers["pca"] = _recorder(calls, result_refs=["pca-result"])
    return pipeline_path, handlers, calls


@pytest.fixture
def differential_run(tmp_path):
    """target=differential、comparisons=[]のrun。brief記載のRED用fixture。

    pcaまでは成功、resolve_comparisonsはCOMPARISON_REQUIREDのneeds_inputを返す
    ——空の比較配列でも差次的目標には比較準備ゲートがあり、それがneeds_inputで
    止めることを確認するのがこのfixtureの役目。
    """
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=[])
    handlers["resolve_comparisons"] = _recorder(
        calls, status="needs_input",
        error={"code": "COMPARISON_REQUIRED", "message": "比較を指定してください。", "details": {}})
    return path, handlers, calls


# ---------- brief記載のRED（そのまま） ----------

def test_missing_comparison_keeps_exploration_and_stops(differential_run):
    path, handlers, calls = differential_run
    result = run_engine(path, handlers)
    assert result["status"] == "needs_input"
    assert "upstream" in calls and "pca" in calls
    assert not any(name.startswith("export:") for name in calls)
    assert result["request"]["effective_target"] == "differential"
    assert result["needs_input"]["code"] == "COMPARISON_REQUIRED"
    assert result["needs_input"]["stage_id"] == "resolve_comparisons"


# ---------- target別必須工程・比較準備ゲート ----------

def test_exploratory_target_marks_comparison_stage_out_of_scope(tmp_path):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    result = run_engine(path, handlers)
    assert result["status"] == "completed"
    assert "resolve_comparisons" not in calls
    assert result["stages"]["resolve_comparisons"]["status"] == "skipped"
    assert not any(name.startswith(("differential:", "export:")) for name in calls)
    # exploratoryでは必須の上流・PCA・reportまでは必ず呼ばれる。
    for expected in ("prepare_input", "upstream", "validate_outputs", "load_dataset",
                     "resolve_metadata", "preprocess", "pca", "report"):
        assert expected in calls


def test_differential_target_with_two_comparisons_runs_all_stages_in_order(tmp_path):
    comparisons = [
        {"comparison_id": "cmp1", "reference_group": "control", "test_group": "treated"},
        {"comparison_id": "cmp2", "reference_group": "control", "test_group": "other"},
    ]
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=comparisons)
    result = run_engine(path, handlers)
    assert result["status"] == "completed"
    assert calls == [
        "prepare_input", "upstream", "validate_outputs", "load_dataset",
        "resolve_metadata", "preprocess", "pca", "resolve_comparisons",
        "differential:cmp1", "export:cmp1", "differential:cmp2", "export:cmp2",
        "report",
    ]


def test_one_of_two_comparisons_failing_yields_partial_and_stops(tmp_path):
    comparisons = [
        {"comparison_id": "cmp1", "reference_group": "control", "test_group": "treated"},
        {"comparison_id": "cmp2", "reference_group": "control", "test_group": "other"},
    ]
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=comparisons)

    def failing_differential(context: dict) -> dict:
        calls.append(context["stage_id"])
        if context["comparison_id"] == "cmp1":
            raise RuntimeError("boom")
        return {"status": "succeeded", "result_refs": [], "warnings": [], "error": None}

    handlers["differential"] = failing_differential

    result = run_engine(path, handlers)
    assert result["status"] == "partial"  # pcaが有効な出力を残しているのでfailedへは落ちない
    assert "differential:cmp1" in calls
    assert "differential:cmp2" not in calls
    assert "export:cmp1" not in calls
    stage = result["stages"]["differential:cmp1"]
    assert stage["status"] == "failed"
    assert stage["error"]["code"] == "RuntimeError"


def test_missing_explicit_preprocess_prerequisite_becomes_needs_input(tmp_path):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")

    def preprocess_handler(context: dict) -> dict:
        calls.append(context["stage_id"])
        raise DomainError("PREPROCESS_PREREQUISITE_MISSING",
                         "QC不足のため明示要求のdrift_correctを実施できません。",
                         {"field": "drift_correct"})

    handlers["preprocess"] = preprocess_handler

    result = run_engine(path, handlers)
    assert result["status"] == "needs_input"
    assert result["needs_input"]["code"] == "PREPROCESS_PREREQUISITE_MISSING"
    assert result["needs_input"]["stage_id"] == "preprocess"
    assert "pca" not in calls  # preprocessで止まりPCAへ進まない


# ---------- cancel境界 ----------

def test_cancel_requested_stops_before_next_stage(tmp_path):
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=[])
    original_pca = handlers["pca"]

    def pca_then_request_cancel(context: dict) -> dict:
        outcome = original_pca(context)
        atomic_write_json(cancel_request_path(path), {"cancel_requested": True})
        return outcome

    handlers["pca"] = pca_then_request_cancel

    result = run_engine(path, handlers)
    assert result["status"] == "cancelled"
    assert "pca" in calls
    assert "resolve_comparisons" not in calls  # 次のstageへ進む前に取消を検知して止まる


# ---------- state保存失敗 ----------

def test_state_save_failure_propagates_and_keeps_prior_results(tmp_path, monkeypatch):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")
    real_save_run = store_module.save_run

    def flaky_save_run(pipeline_path, record, *, expected_revision):
        if record["stages"]["pca"]["status"] == "running":
            raise OSError("simulated disk failure")
        return real_save_run(pipeline_path, record, expected_revision=expected_revision)

    monkeypatch.setattr(store_module, "save_run", flaky_save_run)

    with pytest.raises(OSError, match="simulated disk failure"):
        run_engine(path, handlers)

    record = load_run(path)
    assert record["stages"]["preprocess"]["status"] == "succeeded"
    assert record["stages"]["pca"]["status"] == "pending"
    assert "pca" not in calls  # handlerはまだ一度も呼ばれていない


# ---------- 同時engine起動拒否 ----------

def test_second_engine_start_is_refused(tmp_path):
    path, handlers, calls = _build_pipeline(tmp_path, target="exploratory")

    def upstream_with_reentrant_attempt(context: dict) -> dict:
        calls.append(context["stage_id"])
        with pytest.raises(DomainError) as excinfo:
            run_engine(path, handlers)
        assert excinfo.value.code == "PIPELINE_ALREADY_RUNNING"
        return {"status": "succeeded", "result_refs": [], "warnings": [], "error": None}

    handlers["upstream"] = upstream_with_reentrant_attempt

    result = run_engine(path, handlers)
    assert result["status"] == "completed"
    assert "upstream" in calls


# ---------- AST: 禁止import ----------

_FORBIDDEN_IMPORT_PREFIXES = (
    "lipidmix.core.session_state",
    "lipidmix.core.mcp_core",
    "lipidmix.tools",
)


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


def test_engine_and_worker_do_not_import_global_session():
    for relative in ("lipidmix/pipeline/engine.py", "lipidmix/pipeline/worker.py"):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for name in _imported_module_names(source):
            forbidden = any(name == prefix or name.startswith(prefix + ".")
                           for prefix in _FORBIDDEN_IMPORT_PREFIXES)
            assert not forbidden, f"{relative} が禁止importを含む: {name}"


# ---------- worker.py のCLI配線 ----------

def test_worker_main_invokes_run_worker_with_parsed_path(monkeypatch, tmp_path, capsys):
    from lipidmix.pipeline import worker

    seen = {}

    def fake_run_worker(path):
        seen["path"] = Path(path)
        return {"status": "completed"}

    monkeypatch.setattr(worker, "run_worker", fake_run_worker)
    exit_code = worker.main(["--pipeline", str(tmp_path)])
    assert exit_code == 0
    assert seen["path"] == tmp_path
    assert "status=completed" in capsys.readouterr().out


# ---------- 試験harness: 実プロセスとしてrun_engineを起動する ----------

def test_worker_harness_runs_pipeline_as_real_subprocess(tmp_path):
    path, _handlers, _calls = _build_pipeline(tmp_path, target="exploratory")
    harness = REPO_ROOT / "tests" / "pipeline_worker_harness.py"
    completed = subprocess.run(
        [sys.executable, str(harness), "--pipeline", str(path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["status"] == "completed"
