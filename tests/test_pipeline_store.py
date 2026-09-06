"""pipeline-run.v1 の永続化・受付冪等性・Console job所有権のテスト（Task 14）。

`find_or_create_run` はsource_root単位の索引で受付を直列化する。索引baseは
`LIPIDMIX_PIPELINE_INDEX_DIR` 環境変数でtmpへ差し替える（本番既定は
`%LOCALAPPDATA%`）。2プロセス同時受付のテストは、実際に2つのPythonプロセスを
起こして競合させる（モックでは file_lock の実効性を検証できない）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline.request import resolve_request
from lipidmix.pipeline.store import find_or_create_run

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _index_base(tmp_path, monkeypatch):
    """索引baseをtmpへ注入する（本番は %LOCALAPPDATA%）。"""
    index_base = tmp_path / "_pipeline_index_base"
    monkeypatch.setenv("LIPIDMIX_PIPELINE_INDEX_DIR", str(index_base))
    return index_base


def _minimal_inputs(root: Path, *, fingerprint: str = "a" * 64) -> dict:
    return {"source_root": str(root), "fingerprint": fingerprint, "raw_inventory": []}


# ---------- brief記載のRED（そのまま） ----------

def test_same_request_id_is_content_addressed(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = {"source_root": str(root), "fingerprint": "a" * 64, "raw_inventory": []}
    first = find_or_create_run(root, req, inputs, request_id="request-1")
    assert find_or_create_run(root, req, inputs, request_id="request-1") == first
    changed = {**req, "target": "differential", "effective_target": "differential"}
    with pytest.raises(DomainError, match="IDEMPOTENCY_CONFLICT"):
        find_or_create_run(root, changed, inputs, request_id="request-1")


# ---------- create_run / load_run / save_run の基本契約 ----------

def test_create_run_writes_expected_shape(tmp_path):
    from lipidmix.pipeline.store import RUN_FILENAME, SCHEMA, load_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = _minimal_inputs(root)

    from lipidmix.pipeline.store import create_run
    pipeline_root = create_run(root, req, inputs)

    assert (pipeline_root / RUN_FILENAME).is_file()
    assert (pipeline_root / "requests" / "revision-0001.json").is_file()
    record = load_run(pipeline_root)
    assert record["schema"] == SCHEMA
    assert record["identity"]["source_root"] == str(root.resolve())
    assert record["identity"]["pipeline_root"] == str(pipeline_root.resolve())
    assert record["status"] == "planned"
    assert record["state_revision"] == 0
    assert record["request"]["revision"] == 1
    assert record["request"]["request_id"] is None
    assert record["request"]["saved_path"] == "requests/revision-0001.json"
    assert record["request"]["effective_target"] == "exploratory"
    assert set(record["stages"]) == {
        "prepare_input", "upstream", "validate_outputs", "load_dataset",
        "resolve_metadata", "preprocess", "pca", "resolve_comparisons", "report",
    }
    for stage in record["stages"].values():
        assert stage["status"] == "pending"
    assert record["results"] == []
    assert record["needs_input"] is None
    assert record["warnings"] == []


def test_create_run_stage_ids_include_comparisons(tmp_path):
    from lipidmix.pipeline.store import create_run, load_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root, {
        "target": "differential",
        "comparisons": [{"comparison_id": "ko_vs_wt", "reference_group": "wt", "test_group": "ko"}],
    })
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    record = load_run(pipeline_root)
    assert "differential:ko_vs_wt" in record["stages"]
    assert "export:ko_vs_wt" in record["stages"]


def test_create_run_uses_output_root_when_given(tmp_path):
    from lipidmix.pipeline.store import create_run

    root = tmp_path / "source"
    root.mkdir()
    out_dir = tmp_path / "elsewhere"
    req = resolve_request(root, {"output_root": str(out_dir)})
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    assert out_dir.resolve() in pipeline_root.resolve().parents
    assert (root / "runs").exists() is False


def test_create_run_rejects_missing_source_root(tmp_path):
    from lipidmix.pipeline.store import create_run

    missing = tmp_path / "not_there"
    req = resolve_request(missing)
    with pytest.raises(DomainError, match="DATASET_ROOT_NOT_FOUND"):
        create_run(missing, req, {"fingerprint": "a" * 64})


def test_create_run_rejects_inputs_without_fingerprint(tmp_path):
    from lipidmix.pipeline.store import create_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    with pytest.raises(DomainError, match="PIPELINE_INPUTS_INVALID"):
        create_run(root, req, {"source_root": str(root)})


def test_load_run_missing_pipeline_run_json(tmp_path):
    from lipidmix.pipeline.store import load_run

    empty = tmp_path / "empty_pipeline_root"
    empty.mkdir()
    with pytest.raises(DomainError, match="PIPELINE_RUN_NOT_FOUND"):
        load_run(empty)


def test_save_run_round_trips_and_bumps_revision(tmp_path):
    from lipidmix.pipeline.store import create_run, load_run, save_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    record = load_run(pipeline_root)
    record["status"] = "running"
    save_run(pipeline_root, record, expected_revision=0)

    reloaded = load_run(pipeline_root)
    assert reloaded["status"] == "running"
    assert reloaded["state_revision"] == 1


def test_save_run_rejects_stale_expected_revision(tmp_path):
    from lipidmix.pipeline.store import create_run, load_run, save_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    record = load_run(pipeline_root)
    save_run(pipeline_root, record, expected_revision=0)  # -> state_revision=1

    stale = load_run(pipeline_root)
    stale["status"] = "failed"
    with pytest.raises(DomainError, match="STATE_REVISION_CONFLICT"):
        save_run(pipeline_root, stale, expected_revision=0)  # 既に1になっている


def test_save_run_rejects_rewriting_results_history(tmp_path):
    from lipidmix.pipeline.store import create_run, load_run, save_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    record = load_run(pipeline_root)
    record["results"].append({"result_id": "r1", "kind": "pca", "relative_path": "results/r1/x.json",
                              "hash": "h1", "parent_ids": [], "request_revision": 1})
    save_run(pipeline_root, record, expected_revision=0)

    tampered = load_run(pipeline_root)
    tampered["results"][0]["hash"] = "tampered"
    with pytest.raises(DomainError, match="PIPELINE_RESULTS_IMMUTABLE"):
        save_run(pipeline_root, tampered, expected_revision=1)

    shrunk = load_run(pipeline_root)
    shrunk["results"] = []
    with pytest.raises(DomainError, match="PIPELINE_RESULTS_IMMUTABLE"):
        save_run(pipeline_root, shrunk, expected_revision=1)


def test_save_run_appending_new_results_is_allowed(tmp_path):
    from lipidmix.pipeline.store import create_run, load_run, save_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    record = load_run(pipeline_root)
    record["results"].append({"result_id": "r1", "kind": "pca", "relative_path": "r1.json",
                              "hash": "h1", "parent_ids": [], "request_revision": 1})
    save_run(pipeline_root, record, expected_revision=0)

    grown = load_run(pipeline_root)
    grown["results"].append({"result_id": "r2", "kind": "differential", "relative_path": "r2.json",
                             "hash": "h2", "parent_ids": ["r1"], "request_revision": 1})
    save_run(pipeline_root, grown, expected_revision=1)

    final = load_run(pipeline_root)
    assert [r["result_id"] for r in final["results"]] == ["r1", "r2"]


def test_save_run_failure_leaves_record_unchanged_and_releases_lock(tmp_path, monkeypatch):
    from lipidmix.pipeline import store as store_mod
    from lipidmix.pipeline.store import create_run, load_run, save_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    record = load_run(pipeline_root)
    record["status"] = "running"

    def _boom(path, data):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(store_mod, "atomic_write_json", _boom)
    with pytest.raises(OSError):
        save_run(pipeline_root, record, expected_revision=0)
    monkeypatch.undo()

    # 保存は失敗しているので、内容もrevisionも直前のまま。
    unchanged = load_run(pipeline_root)
    assert unchanged["status"] == "planned"
    assert unchanged["state_revision"] == 0

    # ロックは解放されている（デッドロックせず、同じexpected_revisionで再試行できる）。
    save_run(pipeline_root, record, expected_revision=0)
    assert load_run(pipeline_root)["status"] == "running"


# ---------- 相対化 ----------

def test_create_run_relativizes_paths_actually_under_pipeline_root(tmp_path):
    """method.source_path等はpipeline_root外なら絶対のまま、配下なら相対化する。"""
    from lipidmix.pipeline.store import create_run, load_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)

    outside_lbm = tmp_path / "shared_lib" / "fake.lbm2"
    outside_lbm.parent.mkdir(parents=True, exist_ok=True)
    outside_lbm.write_text("lbm", encoding="utf-8")

    # inputs はcreate_run呼出し前にはpipeline_rootを知らないので、いったん
    # 「配下にある体」の絶対パスを後から差し込んで検証する
    # （実運用ではTask13のstage_inputsがpipeline_root決定後に実配置して返す）。
    inputs = _minimal_inputs(root)
    inputs["lbm"] = {"path": str(outside_lbm), "sha256": "x"}
    inputs["exe"] = {"path": str(outside_lbm)}  # ダミー（存在チェックはstore側で行わない）
    inputs["method"] = {"source_path": str(outside_lbm)}

    pipeline_root = create_run(root, req, inputs)
    record = load_run(pipeline_root)
    # source_root外を指すので絶対のまま。
    assert record["inputs"]["lbm"]["path"] == str(outside_lbm)
    assert record["inputs"]["method"]["source_path"] == str(outside_lbm)

    # 今度はpipeline_root配下を指す絶対パスを与え、相対化されることを確認する。
    inputs2 = _minimal_inputs(root, fingerprint="b" * 64)
    staged = pipeline_root / "input" / "effective-method.txt"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("m", encoding="utf-8")
    inputs2["method"] = {"source_path": str(staged)}
    inputs2["manifest"] = [{"sample_id": "S1", "source_file": str(staged)}]

    from lipidmix.pipeline.store import _relativize_inputs_paths
    relativized = _relativize_inputs_paths(inputs2, pipeline_root)
    assert relativized["method"]["source_path"] == "input/effective-method.txt"
    assert relativized["manifest"][0]["source_file"] == "input/effective-method.txt"


# ---------- 受付冪等性: 索引の詳細ケース ----------

def test_content_addressed_reuse_without_explicit_request_id(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = _minimal_inputs(root)
    first = find_or_create_run(root, req, inputs)
    second = find_or_create_run(root, req, inputs)
    assert first == second


def test_different_input_fingerprint_creates_a_new_run(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    first = find_or_create_run(root, req, _minimal_inputs(root, fingerprint="a" * 64))
    second = find_or_create_run(root, req, _minimal_inputs(root, fingerprint="b" * 64))
    assert first != second


def _mark_completed_with_result(pipeline_root, *, sha_ok: bool):
    from lipidmix.pipeline.store import load_run, save_run

    record = load_run(pipeline_root)
    result_path = pipeline_root / "results" / "r1.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text("hello", encoding="utf-8")
    import hashlib
    real_hash = hashlib.sha256(b"hello").hexdigest()
    record["results"].append({
        "result_id": "r1", "kind": "pca", "relative_path": "results/r1.json",
        "hash": real_hash if sha_ok else "0" * 64,
        "parent_ids": [], "request_revision": 1,
    })
    record["status"] = "completed"
    save_run(pipeline_root, record, expected_revision=record["state_revision"])


def test_completed_run_with_verified_artifacts_is_reused(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = _minimal_inputs(root)
    first = find_or_create_run(root, req, inputs, request_id="req-A")
    _mark_completed_with_result(first, sha_ok=True)

    second = find_or_create_run(root, req, inputs, request_id="req-A")
    assert second == first


def test_completed_run_with_broken_artifact_raises_integrity_mismatch(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = _minimal_inputs(root)
    first = find_or_create_run(root, req, inputs, request_id="req-B")
    _mark_completed_with_result(first, sha_ok=False)

    with pytest.raises(DomainError, match="RESULT_INTEGRITY_MISMATCH"):
        find_or_create_run(root, req, inputs, request_id="req-B")


def test_completed_run_with_broken_artifact_and_no_request_id_creates_new_run(tmp_path):
    """明示request_idが無い場合は、壊れたcompleted runを黙って再利用しない代わりに
    RESULT_INTEGRITY_MISMATCHでも止めず、新しいrunを作る（brief該当なし、
    店の解釈をreportに記載）。"""
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = _minimal_inputs(root)
    first = find_or_create_run(root, req, inputs)
    _mark_completed_with_result(first, sha_ok=False)

    second = find_or_create_run(root, req, inputs)
    assert second != first


def test_index_entry_with_vanished_run_self_heals(tmp_path):
    import shutil

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = _minimal_inputs(root)
    first = find_or_create_run(root, req, inputs, request_id="req-C")
    shutil.rmtree(first)

    second = find_or_create_run(root, req, inputs, request_id="req-C")
    assert second != first
    assert second.is_dir()


def test_index_entry_with_vanished_run_and_no_request_id_self_heals(tmp_path):
    import shutil

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = _minimal_inputs(root)
    first = find_or_create_run(root, req, inputs)
    shutil.rmtree(first)

    second = find_or_create_run(root, req, inputs)
    assert second != first
    assert second.is_dir()


def test_uuid_collision_is_retried(tmp_path, monkeypatch):
    from lipidmix.pipeline import store as store_mod

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)

    fixed = "11111111111111111111111111111111"
    calls = {"n": 0}
    real_uuid4 = store_mod.uuid.uuid4

    class _FixedUUID:
        def __init__(self, hexvalue):
            self.hex = hexvalue

    def _fake_uuid4():
        calls["n"] += 1
        if calls["n"] == 1:
            # 1回目はぶつかる値を予約しておく（衝突を実際に起こす）。
            (root / "runs" / f"pipeline_{fixed}").mkdir(parents=True, exist_ok=True)
            return _FixedUUID(fixed)
        return real_uuid4()

    monkeypatch.setattr(store_mod.uuid, "uuid4", _fake_uuid4)
    pipeline_root = store_mod.create_run(root, req, _minimal_inputs(root))
    assert pipeline_root.name != f"pipeline_{fixed}"
    assert calls["n"] >= 2


# ---------- 読取専用importが索引dirを作らない ----------

def test_load_run_does_not_create_index_dir(tmp_path, _index_base):
    from lipidmix.pipeline.store import create_run, load_run

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    assert not _index_base.exists()

    load_run(pipeline_root)
    assert not _index_base.exists()


# ---------- register_job_owner / pipeline_owner_block_reason ----------

def test_pipeline_owner_block_reason_none_when_unowned(tmp_path):
    from lipidmix.pipeline.store import pipeline_owner_block_reason

    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()
    assert pipeline_owner_block_reason(run_dir) is None


def test_pipeline_owner_block_reason_blocks_when_pipeline_active(tmp_path):
    from lipidmix.pipeline.store import create_run, register_job_owner, pipeline_owner_block_reason

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    pipeline_root = create_run(root, req, _minimal_inputs(root))  # status=planned(活動中)

    run_dir = tmp_path / "console_run_dir"
    run_dir.mkdir()
    job_path = run_dir / "analysis-job.json"
    job_path.write_text("{}", encoding="utf-8")
    register_job_owner(job_path, pipeline_root)

    block = pipeline_owner_block_reason(run_dir)
    assert block is not None
    assert block["reason"] == "active"
    assert block["pipeline_path"] == str(pipeline_root.resolve())


def test_pipeline_owner_block_reason_allows_when_pipeline_terminal(tmp_path):
    from lipidmix.pipeline.store import (
        create_run, load_run, save_run, register_job_owner, pipeline_owner_block_reason,
    )

    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    pipeline_root = create_run(root, req, _minimal_inputs(root))
    record = load_run(pipeline_root)
    record["status"] = "cancelled"
    save_run(pipeline_root, record, expected_revision=0)

    run_dir = tmp_path / "console_run_dir"
    run_dir.mkdir()
    job_path = run_dir / "analysis-job.json"
    job_path.write_text("{}", encoding="utf-8")
    register_job_owner(job_path, pipeline_root)

    assert pipeline_owner_block_reason(run_dir) is None


def test_pipeline_owner_block_reason_undeterminable_when_pipeline_unreadable(tmp_path):
    from lipidmix.pipeline.store import register_job_owner, pipeline_owner_block_reason

    run_dir = tmp_path / "console_run_dir"
    run_dir.mkdir()
    job_path = run_dir / "analysis-job.json"
    job_path.write_text("{}", encoding="utf-8")
    vanished_pipeline_root = tmp_path / "vanished_pipeline"
    register_job_owner(job_path, vanished_pipeline_root)  # pipeline-run.jsonが存在しない

    block = pipeline_owner_block_reason(run_dir)
    assert block is not None
    assert block["reason"] == "undeterminable"


def test_pipeline_owner_block_reason_undeterminable_when_sidecar_corrupt(tmp_path):
    from lipidmix.pipeline.store import pipeline_owner_block_reason
    from lipidmix.console.job_manager import pipeline_owner_path

    run_dir = tmp_path / "console_run_dir"
    run_dir.mkdir()
    pipeline_owner_path(run_dir).write_text("{not valid json", encoding="utf-8")

    block = pipeline_owner_block_reason(run_dir)
    assert block is not None
    assert block["reason"] == "owner_record_invalid"


# ---------- 実子プロセスで競合させる受付テスト ----------

_RACE_WORKER_SCRIPT = r"""
import json
import os
import sys
import time
from pathlib import Path

task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
os.environ["LIPIDMIX_PIPELINE_INDEX_DIR"] = task["index_dir"]

sys.path.insert(0, task["repo_root"])

ready_path = Path(task["ready"])
go_path = Path(task["go"])
out_path = Path(task["out"])

ready_path.write_text("1", encoding="utf-8")
deadline = time.monotonic() + 30.0
while not go_path.exists():
    if time.monotonic() > deadline:
        out_path.write_text("EXC:go signal timeout", encoding="utf-8")
        raise SystemExit(0)
    time.sleep(0.001)

from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline.store import find_or_create_run

try:
    result = find_or_create_run(
        Path(task["source_root"]), task["request"], task["inputs"],
        request_id=task.get("request_id"),
    )
    out_path.write_text("OK:" + str(result), encoding="utf-8")
except DomainError as exc:
    out_path.write_text("ERR:" + exc.code, encoding="utf-8")
except Exception as exc:  # pragma: no cover - 診断用
    out_path.write_text("EXC:" + repr(exc), encoding="utf-8")
"""


def _run_race(tmp_path, index_base, tasks: list[dict], *, timeout: float = 30.0) -> list[str]:
    """tasksの各要素で本物のPythonプロセスを1つ起動し、ほぼ同時にgoさせて結果を集める。

    「ready」バリアで両方が起動・importを終えてgo待ちに入ったことを確認してから
    「go」ファイルを置くことで、file_lockの奪い合いを実際に起こす
    （逐次実行に潰さない）。
    """
    processes = []
    task_infos = []
    for i, task in enumerate(tasks):
        ready = tmp_path / f"ready_{i}"
        go = tmp_path / "go"  # 全プロセス共通の1枚
        out = tmp_path / f"out_{i}"
        task_path = tmp_path / f"task_{i}.json"
        full_task = {
            **task,
            "index_dir": str(index_base),
            "repo_root": str(REPO_ROOT),
            "ready": str(ready),
            "go": str(go),
            "out": str(out),
        }
        task_path.write_text(json.dumps(full_task), encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-c", _RACE_WORKER_SCRIPT, str(task_path)],
            cwd=str(REPO_ROOT),
        )
        processes.append(proc)
        task_infos.append({"ready": ready, "out": out})

    deadline = time.monotonic() + timeout
    for info in task_infos:
        while not info["ready"].exists():
            if time.monotonic() > deadline:
                for p in processes:
                    p.kill()
                raise TimeoutError("子プロセスがready状態になりませんでした")
            time.sleep(0.001)

    (tmp_path / "go").write_text("1", encoding="utf-8")

    for proc in processes:
        proc.wait(timeout=timeout)

    results = []
    for info in task_infos:
        deadline_out = time.monotonic() + timeout
        while not info["out"].exists():
            if time.monotonic() > deadline_out:
                raise TimeoutError("子プロセスが結果を書きませんでした")
            time.sleep(0.001)
        results.append(info["out"].read_text(encoding="utf-8"))
    return results


def test_two_processes_accept_concurrently_return_same_run(tmp_path, _index_base):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = _minimal_inputs(root)

    results = _run_race(tmp_path, _index_base, [
        {"source_root": str(root), "request": req, "inputs": inputs, "request_id": "shared-1"},
        {"source_root": str(root), "request": req, "inputs": inputs, "request_id": "shared-1"},
    ])

    assert all(r.startswith("OK:") for r in results), results
    paths = {r[len("OK:"):] for r in results}
    assert len(paths) == 1  # 両方とも同じpipeline_rootを返す

    created = list((root / "runs").glob("pipeline_*"))
    assert len(created) == 1  # 実際に作られたディレクトリも1つだけ


def test_two_processes_different_output_root_still_serialize_index_writes(tmp_path, _index_base):
    root = tmp_path / "source"
    root.mkdir()
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    req_a = resolve_request(root, {"output_root": str(out_a)})
    req_b = resolve_request(root, {"output_root": str(out_b)})
    inputs = _minimal_inputs(root)

    results = _run_race(tmp_path, _index_base, [
        {"source_root": str(root), "request": req_a, "inputs": inputs, "request_id": None},
        {"source_root": str(root), "request": req_b, "inputs": inputs, "request_id": None},
    ])

    assert all(r.startswith("OK:") for r in results), results
    paths = {r[len("OK:"):] for r in results}
    assert len(paths) == 2  # output_rootが違うので別々のrunになる

    from lipidmix.pipeline.store import _index_dir_for_source
    index_dir = _index_dir_for_source(root)
    index_data = json.loads((index_dir / "index.json").read_text(encoding="utf-8"))
    # 索引の書込み自体は同じロックの下で直列化され、片方が消えていない
    # （非アトミックなread-modify-writeで負けた側のentryが失われていないか）。
    recorded_paths = {e["pipeline_root"] for e in index_data["entries"]}
    assert recorded_paths == paths
