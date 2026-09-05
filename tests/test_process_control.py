# tests/test_process_control.py
"""Windowsのプロセス所有権・identity・OS排他の検証。

モックした偽プロセスではなく、実際の無害なPython子プロセス・孫プロセスを起動して
「本当に終了したか」「本当に排他できているか」を確かめる。Job Objectもbyte-range
lockもOSの状態であり、Python側の記録を読んでも検証にならないため。
"""
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lipidmix.core.atomic_io import DomainError
from lipidmix.core.process_control import (
    detach_breakaway_mode,
    file_lock,
    launch_detached,
    process_identity,
    same_process,
    start_owned_process,
)
import lipidmix.core.process_control as pc

_REPO_ROOT = str(Path(__file__).resolve().parents[1])

#: 検証中はずっと生きていてほしいだけの無害な子。テストは必ず明示的に終了させる。
_SLEEP = [sys.executable, "-c", "import time; time.sleep(120)"]

windows_only = pytest.mark.skipif(os.name != "nt",
                                  reason="Windows Job Objectの検証")


def _wait_for_file(path: Path, timeout: float = 20.0) -> str:
    """子プロセスが書くファイルが空でなくなるまで待って中身を返す。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text.strip():
            return text
        time.sleep(0.02)
    raise AssertionError(f"子プロセスが {path} を書かなかった")


def _wait_until_gone(identity: dict, timeout: float = 10.0) -> bool:
    """identityのプロセスが消えるまで待つ。消えたらTrue。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not same_process(identity):
            return True
        time.sleep(0.02)
    return not same_process(identity)


def _kill_pid_if_same(identity: dict) -> None:
    """テスト後始末。pid再利用を踏まないようidentity一致時だけ終了させる。"""
    if not identity or not same_process(identity):
        return
    try:
        os.kill(int(identity["pid"]), signal.SIGTERM)
    except OSError:
        pass


def _handle_count() -> int:
    """現在のプロセスが開いているカーネルハンドル数。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE,
                                               ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    count = wintypes.DWORD()
    assert kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(),
                                          ctypes.byref(count))
    return int(count.value)


def _write_script(tmp_path: Path, name: str, source: str) -> Path:
    """子プロセス用スクリプトを書く。

    `-c` に改行入りソースを渡さないのは、コマンドライン経由で崩れる余地を
    残さないため。
    """
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


# ---------- 代表テスト（Task 2 brief） ----------

@windows_only
def test_termination_reaps_owned_process(tmp_path):
    proc = start_owned_process([sys.executable, "-c", "import time; time.sleep(30)"],
                               cwd=tmp_path, log_path=tmp_path / "child.log")
    identity = proc.identity
    try:
        assert same_process(identity)
        proc.terminate_tree()
        assert proc.wait(timeout=5) != 0
        assert not same_process(identity)
    finally:
        proc.close()


# ---------- identity: pid再利用の見分け ----------

@windows_only
def test_same_pid_with_different_creation_time_is_not_same_process(tmp_path):
    """pidが同じでも生成時刻が違えばFalse。pid再利用で別人を殺さないための要。"""
    proc = start_owned_process(_SLEEP, cwd=tmp_path, log_path=tmp_path / "c.log")
    try:
        real = proc.identity
        assert same_process(real)
        recycled = {"pid": real["pid"], "creation_time": real["creation_time"] + 1}
        assert not same_process(recycled)
    finally:
        proc.terminate_tree()
        proc.close()


@windows_only
def test_process_identity_reports_creation_time_and_pid(tmp_path):
    proc = start_owned_process(_SLEEP, cwd=tmp_path, log_path=tmp_path / "c.log")
    try:
        found = process_identity(proc.pid)
        assert found == proc.identity
        assert found["pid"] == proc.pid
        assert isinstance(found["creation_time"], int)
        assert found["creation_time"] > 0
    finally:
        proc.terminate_tree()
        proc.close()


@windows_only
def test_process_identity_is_none_for_exited_process(tmp_path):
    """終了直後はハンドルが残りOpenProcessが成功しうる。生存判定に混ぜない。"""
    proc = start_owned_process([sys.executable, "-c", "pass"],
                               cwd=tmp_path, log_path=tmp_path / "c.log")
    identity = proc.identity
    try:
        assert proc.wait(timeout=20) == 0
        # OwnedProcessがまだハンドルを保持している状態で問い合わせる。
        assert process_identity(proc.pid) is None
        assert not same_process(identity)
    finally:
        proc.close()


@windows_only
@pytest.mark.parametrize("expected", [
    None, {}, {"pid": 1234}, {"creation_time": 100},
    {"pid": None, "creation_time": 100}, {"pid": 0, "creation_time": 100},
    {"pid": -1, "creation_time": 100}, "not-a-dict",
])
def test_same_process_rejects_malformed_identity(expected):
    assert same_process(expected) is False


# ---------- OwnedProcess: poll / wait ----------

@windows_only
def test_poll_returns_none_while_running_then_exit_code(tmp_path):
    proc = start_owned_process([sys.executable, "-c", "import time; time.sleep(0.5)"],
                               cwd=tmp_path, log_path=tmp_path / "c.log")
    try:
        assert proc.poll() is None
        assert proc.wait(timeout=20) == 0
        assert proc.poll() == 0
    finally:
        proc.close()


@windows_only
def test_wait_timeout_raises_domain_error(tmp_path):
    proc = start_owned_process(_SLEEP, cwd=tmp_path, log_path=tmp_path / "c.log")
    try:
        with pytest.raises(DomainError) as excinfo:
            proc.wait(timeout=0.2)
        assert excinfo.value.code == "PROCESS_WAIT_TIMEOUT"
    finally:
        proc.terminate_tree()
        proc.close()


@windows_only
def test_operations_after_close_raise(tmp_path):
    proc = start_owned_process(_SLEEP, cwd=tmp_path, log_path=tmp_path / "c.log")
    identity = proc.identity
    proc.terminate_tree()
    proc.close()
    proc.close()  # 冪等
    with pytest.raises(DomainError) as excinfo:
        proc.poll()
    assert excinfo.value.code == "PROCESS_HANDLE_CLOSED"
    assert not same_process(identity)


@windows_only
def test_child_stdout_goes_to_log_path(tmp_path):
    log_path = tmp_path / "nested" / "child.log"
    proc = start_owned_process(
        [sys.executable, "-c", "print('hello-from-child')"],
        cwd=tmp_path, log_path=log_path)
    try:
        assert proc.wait(timeout=20) == 0
    finally:
        proc.close()
    assert "hello-from-child" in log_path.read_text(encoding="utf-8")


@windows_only
@pytest.mark.parametrize("command, cwd_exists, code", [
    ([], True, "COMMAND_EMPTY"),
    (["python"], False, "CWD_NOT_FOUND"),
])
def test_launch_argument_validation(tmp_path, command, cwd_exists, code):
    cwd = tmp_path if cwd_exists else tmp_path / "missing"
    with pytest.raises(DomainError) as excinfo:
        start_owned_process(command, cwd=cwd, log_path=tmp_path / "c.log")
    assert excinfo.value.code == code


# ---------- 孫プロセスまで終了させる ----------

_SPAWNER_SRC = """\
import subprocess
import sys
import time
from pathlib import Path

grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
Path(sys.argv[1]).write_text(str(grandchild.pid), encoding="utf-8")
time.sleep(300)
"""


@windows_only
def test_terminate_tree_also_reaps_grandchild(tmp_path):
    """孫まで終了させる。親のpidだけをkillすると孫が残り、出力を掴んだままになる。"""
    script = _write_script(tmp_path, "spawner.py", _SPAWNER_SRC)
    pid_file = tmp_path / "grandchild.pid"
    proc = start_owned_process([sys.executable, str(script), str(pid_file)],
                               cwd=tmp_path, log_path=tmp_path / "c.log")
    grandchild_identity = None
    try:
        grandchild_pid = int(_wait_for_file(pid_file).strip())
        grandchild_identity = process_identity(grandchild_pid)
        assert grandchild_identity is not None, "孫プロセスが起動していない"

        proc.terminate_tree()

        assert proc.wait(timeout=5) != 0
        assert not same_process(proc.identity)
        assert not same_process(grandchild_identity), "孫プロセスが残っている"
    finally:
        _kill_pid_if_same(grandchild_identity)
        proc.close()


@windows_only
def test_close_without_terminate_still_reaps_tree(tmp_path):
    """KILL_ON_JOB_CLOSEの確認。close()だけでも孤児を残さない。"""
    script = _write_script(tmp_path, "spawner.py", _SPAWNER_SRC)
    pid_file = tmp_path / "grandchild.pid"
    proc = start_owned_process([sys.executable, str(script), str(pid_file)],
                               cwd=tmp_path, log_path=tmp_path / "c.log")
    grandchild_identity = None
    try:
        grandchild_pid = int(_wait_for_file(pid_file).strip())
        grandchild_identity = process_identity(grandchild_pid)
        assert grandchild_identity is not None
        child_identity = proc.identity

        proc.close()

        assert _wait_until_gone(child_identity), "子がJobハンドル解放で終了していない"
        assert _wait_until_gone(grandchild_identity), "孫が残っている"
    finally:
        _kill_pid_if_same(grandchild_identity)
        proc.close()


# ---------- Assign失敗: resumeせずに停止中の子を始末する ----------

def _spy_create_process(monkeypatch, seen: dict):
    """CreateProcessWの結果から実pidを覗くだけのラッパーを挟む。"""
    real_create = pc._CreateProcessW

    def spy(*args):
        ok = real_create(*args)
        if ok:
            seen["pid"] = int(args[9]._obj.dwProcessId)
        return ok

    monkeypatch.setattr(pc, "_CreateProcessW", spy)


@windows_only
def test_assign_failure_terminates_child_without_resuming(tmp_path, monkeypatch):
    """Assignに失敗したら停止中の子を始末する。resumeすると所有できない子が野に出る。"""
    seen: dict = {}
    resumed: list = []
    _spy_create_process(monkeypatch, seen)
    monkeypatch.setattr(pc, "_AssignProcessToJobObject", lambda job, proc: 0)
    monkeypatch.setattr(pc, "_ResumeThread",
                        lambda handle: (resumed.append(handle), 0)[1])

    with pytest.raises(DomainError) as excinfo:
        start_owned_process(_SLEEP, cwd=tmp_path, log_path=tmp_path / "c.log")

    assert excinfo.value.code == "PROCESS_ASSIGN_FAILED"
    assert resumed == [], "Assign失敗後にResumeThreadを呼んでいる"
    assert "pid" in seen, "子プロセスが起動していない（前提が崩れている）"
    # 停止中の子が本当に消えたことを、pidの解決不能で確かめる。
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and process_identity(seen["pid"]) is not None:
        time.sleep(0.02)
    assert process_identity(seen["pid"]) is None, "停止中の子が残っている"


@windows_only
def test_resume_failure_terminates_child(tmp_path, monkeypatch):
    seen: dict = {}
    _spy_create_process(monkeypatch, seen)
    monkeypatch.setattr(pc, "_ResumeThread", lambda handle: 0xFFFFFFFF)

    with pytest.raises(DomainError) as excinfo:
        start_owned_process(_SLEEP, cwd=tmp_path, log_path=tmp_path / "c.log")

    assert excinfo.value.code == "PROCESS_RESUME_FAILED"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and process_identity(seen["pid"]) is not None:
        time.sleep(0.02)
    assert process_identity(seen["pid"]) is None


# ---------- ハンドル解放 ----------

@windows_only
def test_no_handle_leak_across_start_terminate_close_cycles(tmp_path):
    """成功経路をくり返してもハンドルが積み上がらない。"""
    def cycle(index: int) -> None:
        proc = start_owned_process(_SLEEP, cwd=tmp_path,
                                   log_path=tmp_path / f"c{index}.log")
        try:
            proc.terminate_tree()
        finally:
            proc.close()

    cycle(0)  # 初回はctypes・ログ関連の一時的な確保を含むので基準から外す
    baseline = _handle_count()
    for index in range(1, 7):
        cycle(index)
    assert _handle_count() <= baseline + 2


@windows_only
def test_no_handle_leak_on_assign_failure_path(tmp_path, monkeypatch):
    """失敗経路でもJob・プロセス・スレッド・ログの各ハンドルを解放する。"""
    monkeypatch.setattr(pc, "_AssignProcessToJobObject", lambda job, proc: 0)

    def cycle(index: int) -> None:
        with pytest.raises(DomainError):
            start_owned_process(_SLEEP, cwd=tmp_path,
                                log_path=tmp_path / f"f{index}.log")

    cycle(0)
    baseline = _handle_count()
    for index in range(1, 6):
        cycle(index)
    assert _handle_count() <= baseline + 2


# ---------- OSロック ----------

_LOCKER_SRC = """\
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from lipidmix.core.atomic_io import DomainError
from lipidmix.core.process_control import file_lock

lock_path = Path(sys.argv[2])
result_path = Path(sys.argv[3])
timeout = float(sys.argv[4])
hold_s = float(sys.argv[5])
try:
    with file_lock(lock_path, timeout=timeout, poll_interval=0.02):
        result_path.write_text("acquired", encoding="utf-8")
        time.sleep(hold_s)
except DomainError as exc:
    result_path.write_text(exc.code, encoding="utf-8")
"""


def _run_locker(tmp_path: Path, lock_path: Path, result_name: str,
                timeout: float, hold_s: float = 0.0) -> str:
    script = _write_script(tmp_path, "locker.py", _LOCKER_SRC)
    result_path = tmp_path / result_name
    subprocess.run([sys.executable, str(script), _REPO_ROOT, str(lock_path),
                    str(result_path), str(timeout), str(hold_s)],
                   cwd=str(tmp_path), stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60, check=True)
    return result_path.read_text(encoding="utf-8").strip()


def test_file_lock_excludes_a_concurrent_process(tmp_path):
    """別プロセスは、こちらが握っている間ロックを取れない。"""
    lock_path = tmp_path / "index.lock"
    with file_lock(lock_path):
        assert _run_locker(tmp_path, lock_path, "blocked.txt",
                           timeout=0.5) == "LOCK_TIMEOUT"
    # 解放後は同じ別プロセスが取れる。
    assert _run_locker(tmp_path, lock_path, "free.txt", timeout=10) == "acquired"


def test_lock_file_left_on_disk_does_not_mean_locked(tmp_path):
    """ロックの正体はOSの状態。ファイルが残っていること＝ロック中ではない。"""
    lock_path = tmp_path / "index.lock"
    with file_lock(lock_path):
        pass
    assert lock_path.exists()
    assert _run_locker(tmp_path, lock_path, "after.txt", timeout=10) == "acquired"


def test_deleting_owner_record_does_not_release_the_lock(tmp_path):
    """owner記録を消してもロックは解けない。記録の有無で解放を判断しない。"""
    lock_path = tmp_path / "index.lock"
    owner_record = tmp_path / "index.owner"
    with file_lock(lock_path):
        owner_record.write_text("worker-1", encoding="utf-8")
        owner_record.unlink()
        assert _run_locker(tmp_path, lock_path, "still.txt",
                           timeout=0.5) == "LOCK_TIMEOUT"


def test_file_lock_releases_on_exception(tmp_path):
    lock_path = tmp_path / "index.lock"
    with pytest.raises(RuntimeError):
        with file_lock(lock_path):
            raise RuntimeError("boom")
    assert _run_locker(tmp_path, lock_path, "after-error.txt",
                       timeout=10) == "acquired"


def test_file_lock_is_exclusive_within_the_same_process(tmp_path):
    """同一プロセスの別ハンドルからも取れない（OSロックであることの確認）。"""
    lock_path = tmp_path / "index.lock"
    with file_lock(lock_path):
        with pytest.raises(DomainError) as excinfo:
            with file_lock(lock_path, timeout=0.3, poll_interval=0.02):
                pass
        assert excinfo.value.code == "LOCK_TIMEOUT"


def test_file_lock_creates_missing_parent_directory(tmp_path):
    lock_path = tmp_path / "nested" / "deeper" / "index.lock"
    with file_lock(lock_path) as held:
        assert held == lock_path
        assert lock_path.exists()


# ---------- 切り離し起動 ----------

@pytest.mark.parametrize("in_job, flags, expected", [
    (False, 0, "not_in_job"),
    (False, pc.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, "not_in_job"),
    (True, 0, "inherited_job"),
    (True, pc.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, "unsupported"),
    (True, pc.JOB_OBJECT_LIMIT_BREAKAWAY_OK, "breakaway_ok"),
    (True, pc.JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK, "silent_breakaway"),
    (True, pc.JOB_OBJECT_LIMIT_BREAKAWAY_OK
     | pc.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, "breakaway_ok"),
    (True, pc.JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
     | pc.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, "silent_breakaway"),
])
def test_breakaway_mode_decision_table(in_job, flags, expected):
    assert pc._breakaway_mode(in_job, flags) == expected


_DETACHER_SRC = """\
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from lipidmix.core.atomic_io import DomainError
from lipidmix.core.process_control import launch_detached

out_path = Path(sys.argv[2])
cwd = Path(sys.argv[3])
log_path = Path(sys.argv[4])
worker_script = sys.argv[5]
try:
    info = launch_detached([sys.executable, worker_script], cwd=cwd,
                           log_path=log_path)
    out_path.write_text(json.dumps(info), encoding="utf-8")
except DomainError as exc:
    out_path.write_text(json.dumps({"error": exc.code}), encoding="utf-8")
"""

_WORKER_SRC = "import time\ntime.sleep(300)\n"


@windows_only
def test_detached_worker_survives_its_parent(tmp_path):
    """切り離したワーカーは、起動した親が終了しても走り続ける。"""
    detacher = _write_script(tmp_path, "detacher.py", _DETACHER_SRC)
    worker = _write_script(tmp_path, "worker.py", _WORKER_SRC)
    out_path = tmp_path / "detached.json"

    parent = subprocess.run(
        [sys.executable, str(detacher), _REPO_ROOT, str(out_path), str(tmp_path),
         str(tmp_path / "worker.log"), str(worker)],
        cwd=str(tmp_path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=60, check=True)
    assert parent.returncode == 0

    info = json.loads(out_path.read_text(encoding="utf-8"))
    assert "error" not in info, f"切り離しに失敗した: {info}"
    identity = info["identity"]
    try:
        # 親（detacher）は既に終了している。それでもワーカーは生きている。
        assert same_process(identity), "親の終了とともにワーカーが消えた"
        assert info["pid"] == identity["pid"]
        assert info["breakaway"] in {"not_in_job", "inherited_job",
                                     "breakaway_ok", "silent_breakaway"}
    finally:
        _kill_pid_if_same(identity)
    assert _wait_until_gone(identity)


@windows_only
def test_detach_from_kill_on_close_job_is_unsupported(tmp_path):
    """親JobがKILL_ON_JOB_CLOSEなら、存続を保証できないので起動しない。"""
    detacher = _write_script(tmp_path, "detacher.py", _DETACHER_SRC)
    worker = _write_script(tmp_path, "worker.py", _WORKER_SRC)
    out_path = tmp_path / "detached.json"

    # start_owned_processのJobはKILL_ON_JOB_CLOSE付き。その中で切り離しを試させる。
    proc = start_owned_process(
        [sys.executable, str(detacher), _REPO_ROOT, str(out_path), str(tmp_path),
         str(tmp_path / "worker.log"), str(worker)],
        cwd=tmp_path, log_path=tmp_path / "detacher.log")
    try:
        assert proc.wait(timeout=60) == 0
    finally:
        proc.close()

    info = json.loads(out_path.read_text(encoding="utf-8"))
    assert info == {"error": "DETACH_UNSUPPORTED"}


@windows_only
def test_detach_breakaway_mode_is_a_known_value():
    assert detach_breakaway_mode() in {
        "not_in_job", "inherited_job", "breakaway_ok", "silent_breakaway",
        "unsupported"}


@windows_only
def test_launch_detached_validates_arguments(tmp_path):
    with pytest.raises(DomainError) as excinfo:
        launch_detached([], cwd=tmp_path, log_path=tmp_path / "c.log")
    assert excinfo.value.code == "COMMAND_EMPTY"
