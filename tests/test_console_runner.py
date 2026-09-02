# tests/test_console_runner.py
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lipidmix.console.runner import (
    MsdialExeNotFoundError,
    MsdialNonZeroExitError,
    MsdialTimeoutError,
    get_exe_path,
    run_msdial,
)
from lipidmix.console.output_collector import (
    collect_artifacts,
    snapshot,
    _assign_role,
    _infer_mztab_meta,
)
from lipidmix.console.job_manager import (
    create_job,
    list_jobs,
    load_job,
    update_status,
)


# ---------- runner ----------

def test_get_exe_path_missing(monkeypatch):
    monkeypatch.delenv("MSDIAL_EXE", raising=False)
    with pytest.raises(MsdialExeNotFoundError):
        get_exe_path()


def test_get_exe_path_set(monkeypatch):
    monkeypatch.setenv("MSDIAL_EXE", "C:/MsDial/MsdialConsoleApp.exe")
    assert get_exe_path() == "C:/MsDial/MsdialConsoleApp.exe"


def test_run_msdial_success(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    run_dir = tmp_path / "run1"

    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        rc = run_msdial(
            method_file=method,
            dataset_root=tmp_path,
            run_dir=run_dir,
            exe_path="fake_msdial.exe",
        )

    assert rc == 0
    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "fake_msdial.exe" in cmd
    assert str(method) in cmd
    assert (run_dir / "msdial.log").exists()


def test_run_msdial_nonzero_exit(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    run_dir = tmp_path / "run2"

    mock_result = MagicMock()
    mock_result.returncode = 1

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(MsdialNonZeroExitError) as exc_info:
            run_msdial(method_file=method, dataset_root=tmp_path, run_dir=run_dir,
                       exe_path="fake.exe")
    assert exc_info.value.returncode == 1


def test_run_msdial_timeout(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    run_dir = tmp_path / "run3"

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("fake.exe", 1)):
        with pytest.raises(MsdialTimeoutError):
            run_msdial(method_file=method, dataset_root=tmp_path, run_dir=run_dir,
                       exe_path="fake.exe", timeout_s=1)


def test_run_msdial_writes_log(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    run_dir = tmp_path / "run_log"

    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result):
        run_msdial(method_file=method, dataset_root=tmp_path, run_dir=run_dir,
                   exe_path="fake.exe")

    log = run_dir / "msdial.log"
    assert log.exists()
    content = log.read_text(encoding="utf-8")
    assert "CMD:" in content


# ---------- output_collector ----------

def test_snapshot_empty(tmp_path):
    assert snapshot(tmp_path) == {}


def test_snapshot_captures_files(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world")
    s = snapshot(tmp_path)
    assert "a.txt" in s
    assert str(Path("sub") / "b.txt") in s


def test_collect_artifacts_detects_new_files(tmp_path):
    before = snapshot(tmp_path)
    mzdial_out = tmp_path / "msdial"
    mzdial_out.mkdir()
    (mzdial_out / "Height_AlignmentResult_001.mzTab").write_text("MTD\t")
    (mzdial_out / "AlignmentResult_001.arf").write_bytes(b"\x00" * 10)

    mztabs, others = collect_artifacts(tmp_path, before)
    assert len(mztabs) == 1
    assert len(others) == 1
    assert others[0].role == "peak_matrix_source"
    assert others[0].format == "arf"


def test_collect_artifacts_ignores_unchanged(tmp_path):
    (tmp_path / "existing.txt").write_text("old")
    before = snapshot(tmp_path)
    mztabs, others = collect_artifacts(tmp_path, before)
    assert mztabs == []
    assert others == []


@pytest.mark.parametrize("filename,expected_role,expected_fmt", [
    ("Height_AlignmentResult.mzTab", "primary_mztab", "mztab"),
    ("AlignmentResult.arf2",         "spot_catalog",   "arf2"),
    ("AlignmentResult.arf",          "peak_matrix_source", "arf"),
    ("sample01.pai2",                "sample_peaks",   "pai2"),
    ("sample01.dcl",                 "msms_evidence",  "dcl"),
    ("sample01.EIC.aef",             "chromatogram",   "eicaef"),
    ("unknown.bin",                  "unknown",        "bin"),
])
def test_assign_role(filename, expected_role, expected_fmt):
    role, fmt = _assign_role(filename)
    assert role == expected_role
    assert fmt == expected_fmt


@pytest.mark.parametrize("filename,expected_polarity,expected_measure", [
    ("Height_AlignmentResult_Neg.mzTab", "negative", "peak_height"),
    ("Area_AlignmentResult_Pos.mzTab",   "positive", "peak_area_above_zero"),
    ("AlignmentResult.mzTab",            "positive", "peak_height"),
])
def test_infer_mztab_meta(filename, expected_polarity, expected_measure):
    polarity, measure = _infer_mztab_meta(filename)
    assert polarity == expected_polarity
    assert measure == expected_measure


# ---------- job_manager ----------

def test_create_job_writes_json(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    job, job_path = create_job(
        dataset_root=tmp_path,
        method_file=method,
        polarity="negative",
        measure="peak_height",
    )
    assert job_path.is_file()
    assert job.status == "planned"
    assert job.polarity == "negative"


def test_create_job_run_dir_inside_dataset_root(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(
        dataset_root=tmp_path,
        method_file=method,
        polarity="positive",
        measure="peak_height",
    )
    assert str(tmp_path) in str(job_path)


def test_update_status(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(
        dataset_root=tmp_path, method_file=method,
        polarity="positive", measure="peak_height",
    )
    job = update_status(job_path, "completed")
    assert job.status == "completed"
    assert load_job(job_path).status == "completed"


def test_update_status_with_error(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(
        dataset_root=tmp_path, method_file=method,
        polarity="positive", measure="peak_height",
    )
    job = update_status(job_path, "failed", error="timeout")
    assert job.error == "timeout"
    assert load_job(job_path).error == "timeout"


def test_list_jobs_empty(tmp_path):
    assert list_jobs(tmp_path) == []


def test_list_jobs_finds_jobs(tmp_path):
    method = tmp_path / "params.msdial"
    method.touch()
    _, p1 = create_job(dataset_root=tmp_path, method_file=method,
                       polarity="positive", measure="peak_height")
    _, p2 = create_job(dataset_root=tmp_path, method_file=method,
                       polarity="negative", measure="peak_height")
    found = list_jobs(tmp_path)
    assert len(found) == 2
    assert p1 in found or p2 in found


# ---------- console_tools ----------

def test_console_plan_unsupported_area(tmp_path):
    import json as _json
    from lipidmix.tools.console_tools import console_plan
    result = console_plan(
        dataset_root=str(tmp_path),
        method_file=str(tmp_path / "m.msdial"),
        polarity="positive",
        measure="peak_area_above_zero",
    )
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "UNSUPPORTED_AREA_CONSOLE"


def test_console_plan_missing_exe(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.delenv("MSDIAL_EXE", raising=False)
    method = tmp_path / "params.msdial"
    method.touch()
    from lipidmix.tools.console_tools import console_plan
    result = console_plan(
        dataset_root=str(tmp_path),
        method_file=str(method),
        polarity="positive",
        measure="peak_height",
    )
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "MSDIAL_EXE_NOT_FOUND"


def test_console_plan_missing_method_file(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    from lipidmix.tools.console_tools import console_plan
    result = console_plan(
        dataset_root=str(tmp_path),
        method_file=str(tmp_path / "nonexistent.msdial"),
        polarity="positive",
        measure="peak_height",
    )
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_FOUND"


def test_console_plan_success(tmp_path, monkeypatch):
    import json as _json
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    method = tmp_path / "params.msdial"
    method.touch()
    from lipidmix.tools.console_tools import console_plan
    result = console_plan(
        dataset_root=str(tmp_path),
        method_file=str(method),
        polarity="negative",
        measure="peak_height",
    )
    parsed = _json.loads(result)
    assert parsed["status"] == "planned"
    assert parsed["polarity"] == "negative"
    assert session_state.session.current_job_path is not None


def test_console_status_no_job():
    import json as _json
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    from lipidmix.tools.console_tools import console_status
    result = console_status()
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "JOB_NOT_FOUND"


def test_console_run_nonplanned_job(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(
        dataset_root=tmp_path, method_file=method,
        polarity="positive", measure="peak_height",
    )
    update_status(job_path, "completed")
    from lipidmix.tools.console_tools import console_run
    result = console_run(str(job_path))
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "JOB_NOT_PLANNED"


# ---------- Task 0: ブロッカー回帰 ----------

def test_create_job_allows_default_data_dir(tmp_path, monkeypatch):
    """既定データディレクトリ（<repo>/data 配下）を dataset_root にできる。"""
    from lipidmix.core import mcp_core
    from lipidmix.console.job_manager import create_job
    # リポジトリ直下の data/ を模す: BASE_DIR 配下だが DATA_DIR 配下でもある
    fake_repo = tmp_path / "repo"
    data_root = fake_repo / "data" / "study-001"
    data_root.mkdir(parents=True)
    method = tmp_path / "params.msdial"
    method.touch()
    monkeypatch.setattr(mcp_core, "BASE_DIR", fake_repo)
    monkeypatch.setenv("LIPIDMIX_DATA_DIR", str(fake_repo / "data"))

    job, job_path = create_job(dataset_root=data_root, method_file=method,
                              polarity="positive", measure="peak_height")
    assert job_path.is_file()


def test_create_job_still_rejects_source_tree(tmp_path, monkeypatch):
    """データディレクトリ外のリポジトリ内パスは従来どおり拒否する。"""
    from lipidmix.core import mcp_core
    from lipidmix.console.job_manager import create_job
    fake_repo = tmp_path / "repo"
    (fake_repo / "lipidmix").mkdir(parents=True)
    (fake_repo / "data").mkdir(parents=True)
    method = tmp_path / "params.msdial"
    method.touch()
    monkeypatch.setattr(mcp_core, "BASE_DIR", fake_repo)
    monkeypatch.setenv("LIPIDMIX_DATA_DIR", str(fake_repo / "data"))

    with pytest.raises(ValueError):
        create_job(dataset_root=fake_repo / "lipidmix", method_file=method,
                   polarity="positive", measure="peak_height")


def test_collect_artifacts_excludes_operational_files(tmp_path):
    """msdial.log / analysis-job.json は生成物として数えない。"""
    from lipidmix.console.output_collector import collect_artifacts, snapshot
    before = snapshot(tmp_path)
    (tmp_path / "msdial.log").write_text("CMD: fake\n")
    (tmp_path / "analysis-job.json").write_text("{}")
    mztabs, others = collect_artifacts(tmp_path, before)
    assert mztabs == []
    assert others == []


def test_console_run_reports_no_output(tmp_path, monkeypatch):
    """MS-DIAL が終了コード 0 で何も出力しなければ NO_JOB_OUTPUT になる。"""
    import json as _json
    from unittest.mock import MagicMock, patch
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(dataset_root=tmp_path, method_file=method,
                            polarity="positive", measure="peak_height")
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result):
        result = console_run(str(job_path))
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "NO_JOB_OUTPUT"
    assert load_job(job_path).status == "failed"


def test_console_run_unexpected_exception_marks_failed(tmp_path, monkeypatch):
    """exe が実在しない等の想定外例外でも封筒を返し、running に固着させない。"""
    import json as _json
    from unittest.mock import patch
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "definitely_not_here.exe")
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(dataset_root=tmp_path, method_file=method,
                            polarity="positive", measure="peak_height")
    with patch("subprocess.run", side_effect=FileNotFoundError("exe not found")):
        result = console_run(str(job_path))
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "MSDIAL_EXE_NOT_FOUND"
    assert load_job(job_path).status == "failed"
