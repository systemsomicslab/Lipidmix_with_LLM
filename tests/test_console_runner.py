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
    count_raw_inputs,
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


def test_is_console_exe_accepts_output_with_lcms():
    from lipidmix.console.runner import is_console_exe
    completed = MagicMock()
    completed.stdout = "MSDIAL Console Application 5.5\n  lcms   Run LC-MS data processing\n"
    with patch("subprocess.run", return_value=completed):
        assert is_console_exe("fake.exe") is True


def test_is_console_exe_rejects_gui():
    """GUI はコンソール出力を持たず、--help でウィンドウを開いて返らない。"""
    from lipidmix.console.runner import is_console_exe
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 15)):
        assert is_console_exe("gui.exe") is False


def test_is_console_exe_rejects_missing_file():
    from lipidmix.console.runner import is_console_exe
    with patch("subprocess.run", side_effect=OSError("not found")):
        assert is_console_exe("nope.exe") is False


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


def test_run_msdial_closes_stdin(tmp_path):
    """MCP stdio サーバの JSON-RPC 入力を子プロセスに継承させない。

    MS-DIAL Console は入力フォルダに複数フォーマットが混在すると
    Console.ReadLine() で対話する。stdin を継承したままだと子が
    プロトコルのバイト列を食うか、応答が来ずタイムアウトまでブロックする。
    """
    method = tmp_path / "params.txt"
    method.touch()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_msdial(method_file=method, dataset_root=tmp_path,
                   run_dir=tmp_path / "run1", exe_path="fake.exe")
    assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_run_msdial_flushes_cmd_header_before_child_writes(tmp_path):
    """CMD 行を flush してから子に fd を渡す。"""
    method = tmp_path / "params.txt"
    method.touch()
    run_dir = tmp_path / "run1"

    def fake_run(cmd, stdout=None, stderr=None, timeout=None, stdin=None):
        os.write(stdout.fileno(), b"CHILD OUTPUT\n")
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("subprocess.run", side_effect=fake_run):
        run_msdial(method_file=method, dataset_root=tmp_path,
                   run_dir=run_dir, exe_path="fake.exe")
    lines = (run_dir / "msdial.log").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("CMD: ")


def test_run_msdial_passes_project_flag(tmp_path):
    """save_project=True のとき -p を渡す。"""
    method = tmp_path / "params.txt"
    method.touch()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_msdial(method_file=method, dataset_root=tmp_path,
                   run_dir=tmp_path / "run1", exe_path="fake.exe",
                   save_project=True)
    assert mock_run.call_args.args[0][-1] == "-p"


def test_run_msdial_omits_project_flag_by_default(tmp_path):
    method = tmp_path / "params.txt"
    method.touch()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_msdial(method_file=method, dataset_root=tmp_path,
                   run_dir=tmp_path / "run1", exe_path="fake.exe")
    assert "-p" not in mock_run.call_args.args[0]


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
    # 信号ゼロのファイル名。既定値へ落とすのは呼び出し側（collect_artifacts）の
    # 判断で、推定関数は「何も語っていない」を None で返す。
    ("AlignmentResult.mzTab",            None,       None),
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


def test_raw_input_summary_counts_by_extension(tmp_path):
    from lipidmix.console.job_manager import raw_input_summary
    (tmp_path / "a.wiff").touch()
    (tmp_path / "b.wiff").touch()
    (tmp_path / "a.wiff2").touch()
    (tmp_path / "note.txt").touch()
    (tmp_path / "c.d").mkdir()
    assert raw_input_summary(tmp_path) == {"wiff": 2, "wiff2": 1, "d": 1}


def test_count_raw_inputs_totals_summary(tmp_path):
    (tmp_path / "a.wiff").touch()
    (tmp_path / "a.abf").touch()
    assert count_raw_inputs(tmp_path) == 2


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
    method.write_text("Ion mode: Positive\n", encoding="ascii")
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


def test_console_plan_rejects_non_console_exe(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "gui.exe")
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    (tmp_path / "a.wiff").touch()
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: False)
    from lipidmix.tools.console_tools import console_plan
    result = console_plan(dataset_root=str(tmp_path), method_file=str(method),
                          polarity="negative", measure="peak_height")
    assert _json.loads(result)["error"]["code"] == "MSDIAL_EXE_NOT_CONSOLE"


def test_console_plan_success(tmp_path, monkeypatch):
    import json as _json
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.msdial"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    (tmp_path / "a.wiff").touch()
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


def test_console_plan_rejects_mixed_raw_formats(tmp_path, monkeypatch):
    """.wiff と .wiff2 の併存は MS-DIAL が対話プロンプトを出す条件。"""
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    (tmp_path / "a.wiff").touch()
    (tmp_path / "a.wiff2").touch()
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "MIXED_RAW_FORMATS"
    assert parsed["error"]["details"]["formats"] == {"wiff": 1, "wiff2": 1}


def test_console_plan_rejects_empty_dataset_root(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "MIXED_RAW_FORMATS"


def test_console_plan_warns_existing_alignment_results(tmp_path, monkeypatch):
    """実行のたびに dataset_root へ別タイムスタンプのアライメント一式が積まれる。"""
    import json as _json
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    (tmp_path / "a.wiff").touch()
    (tmp_path / "AlignResult-2026931617_PeakProperties.arf").touch()
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["status"] == "planned"
    assert any("既存のアライメント結果" in w for w in parsed["warnings"])


def test_console_plan_rejects_binary_method_file(tmp_path, monkeypatch):
    """.mdproject は ZIP。渡すと MS-DIAL は全パラメータ既定値で走ってしまう。"""
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    (tmp_path / "a.wiff").touch()
    method = tmp_path / "project.mdproject"
    method.write_bytes(b"PK\x03\x04\x00\x00binary")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_TEXT"


def test_console_plan_rejects_text_without_key_value(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    (tmp_path / "a.wiff").touch()
    method = tmp_path / "empty.txt"
    method.write_text("# comment only\n\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_TEXT"


def test_console_plan_accepts_key_value_method_file(tmp_path, monkeypatch):
    import json as _json
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    (tmp_path / "a.wiff").touch()
    method = tmp_path / "params.txt"
    method.write_text("# MS-DIAL param\nIon mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["status"] == "planned"


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


def test_console_run_rejects_changed_non_console_exe_before_launch(tmp_path, monkeypatch):
    """計画後に MSDIAL_EXE が GUI へ変わっても、ジョブを planned のまま止める。"""
    import json as _json
    from lipidmix.core import session_state
    from lipidmix.console import runner
    from lipidmix.tools.console_tools import console_plan, console_run

    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", "console.exe")
    real_is_console_exe = runner.is_console_exe
    monkeypatch.setattr(runner, "is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    (tmp_path / "a.wiff").touch()
    planned = _json.loads(console_plan(
        dataset_root=str(tmp_path), method_file=str(method),
        polarity="negative", measure="peak_height"))
    job_path = planned["job_path"]

    monkeypatch.setenv("MSDIAL_EXE", "MSDIAL.exe")
    monkeypatch.setattr(runner, "is_console_exe", real_is_console_exe)
    completed = MagicMock()
    completed.stdout = "MS-DIAL GUI output without console commands\n"
    with patch("subprocess.run", return_value=completed) as mock_run:
        result = _json.loads(console_run(job_path))

    assert result["error"]["code"] == "MSDIAL_EXE_NOT_CONSOLE"
    assert load_job(Path(job_path)).status == "planned"
    mock_run.assert_called_once_with(
        ["MSDIAL.exe", "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        timeout=15,
        text=True,
        errors="replace",
    )


def test_console_run_marks_missing_changed_exe_failed(tmp_path, monkeypatch):
    """計画後に MSDIAL_EXE が消えた場合は従来どおり NOT_FOUND/failed にする。"""
    import json as _json
    from lipidmix.console.job_manager import create_job
    from lipidmix.tools.console_tools import console_run

    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    job, job_path = create_job(
        dataset_root=tmp_path, method_file=method,
        polarity="negative", measure="peak_height",
    )
    monkeypatch.setenv("MSDIAL_EXE", "removed-console.exe")
    with patch("subprocess.run", side_effect=OSError("exe not found")) as mock_run:
        result = _json.loads(console_run(str(job_path)))

    assert result["error"]["code"] == "MSDIAL_EXE_NOT_FOUND"
    assert load_job(job_path).status == "failed"
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0][1] == "--help"


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
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
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
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(dataset_root=tmp_path, method_file=method,
                            polarity="positive", measure="peak_height")
    with patch("subprocess.run", side_effect=FileNotFoundError("exe not found")):
        result = console_run(str(job_path))
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "MSDIAL_EXE_NOT_FOUND"
    assert load_job(job_path).status == "failed"


# ---------- F1: 実行後処理（collect_artifacts 以降）のガード ----------

def test_console_run_post_run_failure_marks_job_failed_not_running(tmp_path, monkeypatch):
    """run_msdial 自体は成功しても、その後の生成物ハッシュ計算で例外が出たら
    running に固着させず failed で機械可読エンベロープを返す。

    Windows でウイルススキャナ等が生成直後のファイルをロックしていると
    collect_artifacts 内の sha256_file が PermissionError を投げる実運用の
    再現。ここが無防備だと analysis-job.json は running のまま残り、以降の
    console_run は全部 JOB_NOT_PLANNED で拒否されて誰も直せなくなる
    （Task 0 で潰したはずの症状の再発）。
    """
    import json as _json
    from unittest.mock import MagicMock, patch
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.msdial"
    method.touch()
    job, job_path = create_job(dataset_root=tmp_path, method_file=method,
                               polarity="positive", measure="peak_height")
    run_dir = Path(job.run_dir)

    def _fake_run(cmd, **kwargs):
        out = run_dir / "msdial"
        out.mkdir(parents=True, exist_ok=True)
        (out / "Height_AlignmentResult_ctrl_1.mzTab").write_text("MTD\t")
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("subprocess.run", side_effect=_fake_run), \
         patch("lipidmix.console.output_collector.sha256_file",
               side_effect=PermissionError("locked by AV scanner")):
        result = console_run(str(job_path))

    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "JOB_POST_RUN_FAILED"
    reloaded = load_job(job_path)
    assert reloaded.status == "failed"
    assert reloaded.status != "running"
    assert reloaded.error


# ---------- 成果物メタの正準化（job の宣言値 vs ファイル名の推定） ----------
# 背景: MS-DIAL のアライメント出力名（Height_AlignmentResult_<timestamp>.mzTab）は
# 極性トークンを持たない。旧実装は「信号なし」を positive と区別せず既定に落として
# いたため、negative で計画したジョブの mzTab エントリが全て positive と記録された。

def test_infer_mztab_meta_returns_none_when_filename_has_no_polarity_token():
    polarity, measure = _infer_mztab_meta("Height_AlignmentResult_2026_05_15.mzTab")
    assert polarity is None       # 「信号なし」。positive と断定してはいけない
    assert measure == "peak_height"


def test_infer_mztab_meta_ignores_polarity_substring_inside_a_word():
    polarity, _ = _infer_mztab_meta("Height_Negev_cohort_AlignmentResult.mzTab")
    assert polarity is None       # "Negev" の neg は極性トークンではない


def test_infer_mztab_meta_returns_none_measure_for_normalized_prefix():
    # spec §8.1: normalized value は未対応。peak_height と偽ってはいけない。
    _, measure = _infer_mztab_meta("NormalizedHeight_AlignmentResult_2026.mzTab")
    assert measure is None


def test_collect_artifacts_fills_polarity_from_job_declaration(tmp_path):
    (tmp_path / "Height_AlignmentResult_2026.mzTab").write_text("x", encoding="utf-8")
    mztabs, _ = collect_artifacts(
        tmp_path, {}, declared_polarity="negative", declared_measure="peak_height")
    assert len(mztabs) == 1
    assert mztabs[0].polarity == "negative"
    assert mztabs[0].validation["polarity_source"] == "job_declared"


def test_collect_artifacts_prefers_filename_polarity_over_declaration_and_records_conflict(tmp_path):
    (tmp_path / "Height_AlignmentResult_Neg.mzTab").write_text("x", encoding="utf-8")
    mztabs, _ = collect_artifacts(
        tmp_path, {}, declared_polarity="positive", declared_measure="peak_height")
    # ファイルは実物の性質を語る。宣言は意図でしかないので、食い違いは
    # ファイル側を採ったうえで記録する（黙って上書きしない）。
    assert mztabs[0].polarity == "negative"
    assert mztabs[0].validation["polarity_source"] == "filename"
    assert "polarity" in mztabs[0].validation["conflicts"]


def test_collect_artifacts_excludes_normalized_mztab_from_primary_candidates(tmp_path):
    (tmp_path / "NormalizedHeight_AlignmentResult.mzTab").write_text("x", encoding="utf-8")
    mztabs, others = collect_artifacts(
        tmp_path, {}, declared_polarity="negative", declared_measure="peak_height")
    assert mztabs == []
    assert [a.role for a in others] == ["unsupported_mztab"]


def _fake_msdial_producing(run_dir: Path, filename: str):
    """subprocess.run の代わりに、指定名のファイルを msdial/ に置いて成功を返す。"""
    def fake_run(cmd, **kwargs):
        out = run_dir / "msdial"
        out.mkdir(parents=True, exist_ok=True)
        (out / filename).write_text("x", encoding="utf-8")
        result = MagicMock()
        result.returncode = 0
        return result
    return fake_run


def test_console_run_records_declared_polarity_on_mztab_entries(tmp_path, monkeypatch):
    """極性トークンを持たない出力名でも、宣言した negative が記録される。"""
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(dataset_root=tmp_path, method_file=method,
                             polarity="negative", measure="peak_height")
    run_dir = Path(load_job(job_path).run_dir)

    with patch("subprocess.run",
               side_effect=_fake_msdial_producing(run_dir, "Height_AlignmentResult_2026.mzTab")):
        console_run(str(job_path))

    saved = load_job(job_path)
    assert saved.status == "completed"
    assert [e.polarity for e in saved.primary_mztab_files] == ["negative"]


def test_console_run_warns_when_filename_contradicts_declared_polarity(tmp_path, monkeypatch):
    """ファイル名の極性が宣言と食い違うなら、黙らずに warning へ残す。"""
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(dataset_root=tmp_path, method_file=method,
                             polarity="negative", measure="peak_height")
    run_dir = Path(load_job(job_path).run_dir)

    with patch("subprocess.run",
               side_effect=_fake_msdial_producing(run_dir, "Height_AlignmentResult_Pos.mzTab")):
        console_run(str(job_path))

    saved = load_job(job_path)
    assert [e.polarity for e in saved.primary_mztab_files] == ["positive"]
    assert any("polarity" in w for w in saved.warnings)


# ---------- サイドカー廃止（案 c）: 消費者のいない生成物を出さない ----------
# feature-qc.tsv は「単なる記録」として作られたが、リポジトリ内外に読み手が
# 一人もおらず（別リポ massbank-context / Use-LLLM も参照ゼロ）、書いていた
# 内容（role / batch）は build_dataset_pp_inputs がサンプル名から独立に
# 再導出している。spec §10.1 が要求する feature-qc（SMF_ID × assay_id の
# is_gap_filled / detected_peak）とは別物なので、廃止しても仕様上の穴は
# 増えない（その穴は最初から埋まっていない）。

def test_console_run_writes_no_sidecar_files(tmp_path, monkeypatch):
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.msdial"
    method.touch()
    job, job_path = create_job(dataset_root=tmp_path, method_file=method,
                               polarity="positive", measure="peak_height")
    run_dir = Path(job.run_dir)

    with patch("subprocess.run",
               side_effect=_fake_msdial_producing(run_dir, "ctrl_1.pai2")):
        assert json.loads(console_run(str(job_path)))["status"] == "completed"

    assert not (run_dir / "sidecars").exists()
    assert "sample_qc_sidecar" not in {a.role for a in load_job(job_path).artifacts}


def test_console_run_warns_when_no_per_sample_pai2(tmp_path, monkeypatch):
    """.pai2 が 1 つも出なければ warning を残す（MS/MS 経路が空になる合図）。

    サイドカー生成の副産物として出ていた警告だが、価値はサイドカーではなく
    「サンプル別ファイルが無い＝pai2_parser / dcl_find_msms が読むものが無い」
    という事実の側にある。廃止後も独立した検査として残す。
    """
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.msdial"
    method.touch()
    job, job_path = create_job(dataset_root=tmp_path, method_file=method,
                               polarity="positive", measure="peak_height")
    run_dir = Path(job.run_dir)

    with patch("subprocess.run",
               side_effect=_fake_msdial_producing(run_dir, "Height_Alignment.mzTab")):
        assert json.loads(console_run(str(job_path)))["status"] == "completed"

    warnings = load_job(job_path).warnings
    assert any(".pai2" in w for w in warnings)
    assert not any("feature-qc" in w for w in warnings)


def test_console_run_no_pai2_warning_when_pai2_present(tmp_path, monkeypatch):
    """.pai2 が出ているときに上の警告を出してはいけない（過剰警告の防止）。"""
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    method = tmp_path / "params.msdial"
    method.touch()
    job, job_path = create_job(dataset_root=tmp_path, method_file=method,
                               polarity="positive", measure="peak_height")
    run_dir = Path(job.run_dir)

    with patch("subprocess.run",
               side_effect=_fake_msdial_producing(run_dir, "ctrl_1.pai2")):
        console_run(str(job_path))

    assert not any(".pai2" in w for w in load_job(job_path).warnings)
