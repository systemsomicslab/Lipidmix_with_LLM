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


def _persist(pipeline_root, name: str):
    """`lipidmix.pipeline.report.persist_result`を使い、`output_name=name`の
    実ファイル付きrefを作る（Task18: `evaluate_target`はhash照合まで行うため、
    文字列のダミーrefでは`achieved_outputs`に数えられない）。データ内容は
    固定なので、`_ALWAYS_RECONSTRUCT_STAGE_IDS`（preprocess/pca）が再実行時にも
    同じhashのrefを返し、`commit_stage_outcome`の重複追記防止が効く。
    """
    from lipidmix.pipeline import report as report_mod
    return report_mod.persist_result(pipeline_root, {
        "output_name": name, "kind": "synthetic",
        "result_id": f"{name.replace(':', '_')}-result",
        "data": {"synthetic": True, "name": name},
    })


def _recorder(calls: list[str], *, status: str = "succeeded",
              output_names: list[str] | None = None, error: dict | None = None):
    def handler(context: dict) -> dict:
        calls.append(context["stage_id"])
        refs = [_persist(context["pipeline_root"], name) for name in (output_names or [])]
        return {"status": status, "result_refs": refs, "warnings": [], "error": error}
    return handler


def _upstream_recorder(calls: list[str]):
    """`record["upstream"]["verification"]`をcompletedへ書く合成upstream。

    `evaluate_target`は`record["upstream"]["verification"]["status"]`が
    `"completed"`でない限りどのoutputも`achieved`と認めないため、これが無いと
    合成pipelineは（result_refsを積んでいても）常にfailed/partialになる。
    """
    def handler(context: dict) -> dict:
        calls.append(context["stage_id"])
        return {"status": "succeeded", "result_refs": [], "warnings": [], "error": None,
                "record_updates": {"upstream": {
                    "console_job_path": None, "execution_id": "exec-fake",
                    "verification": {"status": "completed"}}}}
    return handler


def _differential_recorder(calls: list[str]):
    def handler(context: dict) -> dict:
        calls.append(context["stage_id"])
        cid = context["comparison_id"]
        ref = _persist(context["pipeline_root"], f"differential:{cid}")
        return {"status": "succeeded", "result_refs": [ref], "warnings": [], "error": None}
    return handler


def _export_recorder(calls: list[str]):
    def handler(context: dict) -> dict:
        calls.append(context["stage_id"])
        cid = context["comparison_id"]
        refs = [_persist(context["pipeline_root"], f"volcano:{cid}"),
                _persist(context["pipeline_root"], f"tsv:{cid}")]
        return {"status": "succeeded", "result_refs": refs, "warnings": [], "error": None}
    return handler


_HANDLER_KEYS = (
    "prepare_input", "upstream", "validate_outputs", "load_dataset",
    "resolve_metadata", "preprocess", "pca", "resolve_comparisons",
    "differential", "export", "report",
)


def _build_pipeline(tmp_path: Path, *, target: str, comparisons: list | None = None):
    """target/comparisonsからpipelineを1本作り、全stage成功のfake handlersを返す。

    `save_project=False`を明示する——既定Trueのままだと`evaluate_target`が
    `gui_project`refも必須にするが、この合成fixtureはvalidate_outputsで
    GUI projectを作らない（Task18: `build_handlers`のvalidate_outputs handlerが
    実際に登録する対象で、ここでは検証しない）。
    """
    source_root = tmp_path / "source"
    source_root.mkdir()
    request = resolve_request(source_root, {"target": target, "comparisons": comparisons or [],
                                            "save_project": False})
    inputs = _minimal_inputs(source_root)
    pipeline_path = create_run(source_root, request, inputs)

    calls: list[str] = []
    handlers = {key: _recorder(calls) for key in _HANDLER_KEYS}
    handlers["upstream"] = _upstream_recorder(calls)
    handlers["preprocess"] = _recorder(calls, output_names=["preprocess"])
    # PCAは常にpca/pca_figureのrefsを残す合成版にする（partial/failed判定の材料）。
    handlers["pca"] = _recorder(calls, output_names=["pca", "pca_figure"])
    handlers["differential"] = _differential_recorder(calls)
    handlers["export"] = _export_recorder(calls)
    handlers["report"] = _recorder(calls, output_names=["quality_report"])
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


def test_a_recorded_export_background_empty_warning_downgrades_completed_to_partial(tmp_path):
    """spec §9.2/§11: InChIKey 0件でTSVを作れない場合はEXPORT_BACKGROUND_EMPTYを
    記録し、有効な出力を残したpartialとする——全stageがsucceededでも「completed」
    を名乗らせない（brief「quality_report作成前の必須判定と最終確定は分け…
    最終completedはreport保存後のstate更新で確定する」の実体）。
    """
    comparisons = [{"comparison_id": "cmp1", "reference_group": "control", "test_group": "treated"}]
    path, handlers, calls = _build_pipeline(tmp_path, target="differential", comparisons=comparisons)

    def export_with_empty_background(context: dict) -> dict:
        calls.append(context["stage_id"])
        return {"status": "succeeded", "result_refs": [], "warnings": [
            {"code": "EXPORT_BACKGROUND_EMPTY",
             "message": "InChIKeyが0件のためTSVを作成しませんでした。"}],
            "error": None}

    handlers["export"] = export_with_empty_background

    result = run_engine(path, handlers)

    assert result["status"] == "partial"
    assert any(w["code"] == "EXPORT_BACKGROUND_EMPTY" for w in result["warnings"])
    assert "export:cmp1" in calls  # stage自体は成功として最後まで進む


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
        return {"status": "succeeded", "result_refs": [], "warnings": [], "error": None,
                "record_updates": {"upstream": {
                    "console_job_path": None, "execution_id": "exec-fake",
                    "verification": {"status": "completed"}}}}

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
