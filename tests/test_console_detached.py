"""切り離し実行（detach）— 40 分級の Console を 1 ツール呼び出しで抱えない。

実データ 60 サンプルの実行は 44 分かかる。これを `subprocess.run` で待つと、
MCP の 1 ツール呼び出しがその間ずっと戻らない（stdio のタイムアウトにも当たるし、
呼び出し元の都合でプロセスツリーごと落とされると生成物ごと失う。実測で 26.3 分と
32.9 分の 2 回、アライメント途中で停止した）。

detach では起動して pid を返し、完了は console_status が拾う。
"""
from __future__ import annotations

import json as _json
import os
from pathlib import Path
from unittest.mock import MagicMock

from lipidmix.console.job_manager import create_job, load_job


# ---------- runner ----------

def test_detached_command_matches_the_blocking_one(tmp_path, monkeypatch):
    """切り離しても Console に渡す引数は同じでなければならない。"""
    from lipidmix.console import runner
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        proc = MagicMock()
        proc.pid = 4242
        return proc

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    method = tmp_path / "m.txt"
    method.touch()
    run_dir = tmp_path / "run"
    pid = runner.run_msdial_detached(method_file=method, dataset_root=tmp_path,
                                     run_dir=run_dir, exe_path="fake.exe",
                                     save_project=True)
    assert pid == 4242
    assert seen["cmd"] == runner.build_msdial_cmd(
        "fake.exe", tmp_path, run_dir / "msdial", method, save_project=True)


def test_detached_run_closes_stdin(tmp_path, monkeypatch):
    """混在プロンプトで stdin を読ませない（親の stdin を継承させない）。"""
    from lipidmix.console import runner
    seen = {}
    monkeypatch.setattr("subprocess.Popen",
                        lambda cmd, **kw: (seen.update(kw), MagicMock(pid=1))[1])
    method = tmp_path / "m.txt"
    method.touch()
    runner.run_msdial_detached(method_file=method, dataset_root=tmp_path,
                               run_dir=tmp_path / "run", exe_path="fake.exe")
    assert seen["stdin"] == __import__("subprocess").DEVNULL


def test_detached_run_writes_cmd_header_to_log(tmp_path, monkeypatch):
    from lipidmix.console import runner
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: MagicMock(pid=1))
    method = tmp_path / "m.txt"
    method.touch()
    run_dir = tmp_path / "run"
    runner.run_msdial_detached(method_file=method, dataset_root=tmp_path,
                               run_dir=run_dir, exe_path="fake.exe")
    assert (run_dir / "msdial.log").read_text(encoding="utf-8").startswith("CMD: ")


def test_is_process_running_true_for_self():
    from lipidmix.console.runner import is_process_running
    assert is_process_running(os.getpid()) is True


def test_is_process_running_false_for_absent_pid():
    from lipidmix.console.runner import is_process_running
    # 実在しない可能性が極めて高い pid。誤検出しても False を返すのが安全側。
    assert is_process_running(999_999_999) is False


# ---------- console_run(detach=True) ----------

def _planned(tmp_path, monkeypatch):
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    _, job_path = create_job(dataset_root=tmp_path, method_file=method,
                             polarity="negative", measure="peak_height")
    return job_path


def test_console_run_detach_returns_immediately_with_pid(tmp_path, monkeypatch):
    from lipidmix.tools.console_tools import console_run
    job_path = _planned(tmp_path, monkeypatch)
    monkeypatch.setattr("lipidmix.console.runner.run_msdial_detached",
                        lambda **kw: 31337)
    parsed = _json.loads(console_run(str(job_path), detach=True))
    assert parsed["status"] == "running"
    assert parsed["pid"] == 31337
    assert load_job(job_path).status == "running"


def test_console_run_detach_records_state_for_later_collection(tmp_path, monkeypatch):
    """完了時に「実行前に何があったか」が要る。切り離した先では撮れない。"""
    from lipidmix.tools.console_tools import console_run
    from lipidmix.console.detached import read_detached_state
    (tmp_path / "preexisting.wiff").touch()
    job_path = _planned(tmp_path, monkeypatch)
    monkeypatch.setattr("lipidmix.console.runner.run_msdial_detached", lambda **kw: 31337)
    console_run(str(job_path), detach=True)
    state = read_detached_state(Path(load_job(job_path).run_dir))
    assert state["pid"] == 31337
    assert "preexisting.wiff" in _json.dumps(state["befores"]["dataset_root"])


def test_console_status_reports_still_running(tmp_path, monkeypatch):
    from lipidmix.tools.console_tools import console_run, console_status
    job_path = _planned(tmp_path, monkeypatch)
    monkeypatch.setattr("lipidmix.console.runner.run_msdial_detached", lambda **kw: 31337)
    console_run(str(job_path), detach=True)
    monkeypatch.setattr("lipidmix.console.runner.is_process_running", lambda pid: True)
    parsed = _json.loads(console_status(str(job_path)))
    assert parsed["status"] == "running"
    assert parsed["detached"]["alive"] is True


def test_console_status_finalizes_after_the_detached_process_exits(tmp_path, monkeypatch):
    """切り離した実行の生成物は、誰かが後から収集しないと job に載らない。"""
    from lipidmix.tools.console_tools import console_run, console_status
    job_path = _planned(tmp_path, monkeypatch)
    monkeypatch.setattr("lipidmix.console.runner.run_msdial_detached", lambda **kw: 31337)
    console_run(str(job_path), detach=True)

    run_dir = Path(load_job(job_path).run_dir)
    out = run_dir / "msdial"
    out.mkdir(parents=True, exist_ok=True)
    (out / "AlignResult-2026.mzTab").write_text("x", encoding="utf-8")

    monkeypatch.setattr("lipidmix.console.runner.is_process_running", lambda pid: False)
    parsed = _json.loads(console_status(str(job_path)))
    assert parsed["status"] == "completed"
    assert len(parsed["mztab_files"]) == 1
    assert load_job(job_path).status == "completed"


def test_console_status_marks_failed_when_detached_process_left_nothing(tmp_path, monkeypatch):
    from lipidmix.tools.console_tools import console_run, console_status
    job_path = _planned(tmp_path, monkeypatch)
    monkeypatch.setattr("lipidmix.console.runner.run_msdial_detached", lambda **kw: 31337)
    console_run(str(job_path), detach=True)
    monkeypatch.setattr("lipidmix.console.runner.is_process_running", lambda pid: False)
    parsed = _json.loads(console_status(str(job_path)))
    assert parsed["status"] == "failed"


def test_console_status_finalizes_only_once(tmp_path, monkeypatch):
    """2 回目の console_status が completed を running に戻したりしないこと。"""
    from lipidmix.tools.console_tools import console_run, console_status
    job_path = _planned(tmp_path, monkeypatch)
    monkeypatch.setattr("lipidmix.console.runner.run_msdial_detached", lambda **kw: 31337)
    console_run(str(job_path), detach=True)
    run_dir = Path(load_job(job_path).run_dir)
    (run_dir / "msdial").mkdir(parents=True, exist_ok=True)
    (run_dir / "msdial" / "AlignResult-2026.mzTab").write_text("x", encoding="utf-8")
    monkeypatch.setattr("lipidmix.console.runner.is_process_running", lambda pid: False)
    console_status(str(job_path))
    parsed = _json.loads(console_status(str(job_path)))
    assert parsed["status"] == "completed"
    assert len(parsed["mztab_files"]) == 1
