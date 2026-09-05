"""console_cleanup — 実行のたびに積み上がる生成物を、ジョブ単位で片付ける。

MS-DIAL Console は `-i`（生データフォルダ）側にも生成物を出すので、再実行のたび
別タイムスタンプのアライメント一式が同じフォルダに積まれる。CLAUDE.md が警告する
「複数バッチ混在フォルダ」を自分で作ることになり、実際 POS の実走では 3 回の
実行で毎回 180〜313 ファイルの手作業の掃除が要った。掃除は MCP クライアントには
できない操作なので、ツールにする。

**どれを消すかはジョブの記録から決める**。タイムスタンプの推測で消すと、
別バッチの成果物を巻き込む。
"""
from __future__ import annotations

import json as _json
import os
from pathlib import Path

import pytest

from lipidmix.console.job_manager import create_job, load_job


def _job_with_outputs(tmp_path, *, record=True):
    """完了ジョブと、その生成物を模したファイル群を作る。"""
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    job, job_path = create_job(dataset_root=tmp_path, method_file=method,
                               polarity="negative", measure="peak_height")
    run_dir = Path(job.run_dir)
    (run_dir / "msdial").mkdir(parents=True, exist_ok=True)
    (run_dir / "msdial" / "AlignResult-1.mzTab").write_text("m", encoding="utf-8")
    (tmp_path / "AlignResult-1_PeakProperties.arf").write_text("a", encoding="utf-8")
    (tmp_path / "s1_1.pai2").write_text("p", encoding="utf-8")
    (tmp_path / "keep_me.wiff").write_text("raw", encoding="utf-8")

    if record:
        from lipidmix.handoff.schema import Artifact, MztabEntry
        job.primary_mztab_files = [MztabEntry(
            path="msdial/AlignResult-1.mzTab", polarity="negative",
            measure="peak_height", sha256="x", root="run_dir")]
        job.artifacts = [
            Artifact(path="AlignResult-1_PeakProperties.arf", role="peak_matrix_source",
                     format="arf", sha256="y", root="dataset_root"),
            Artifact(path="s1_1.pai2", role="sample_peaks",
                     format="pai2", sha256="z", root="dataset_root"),
        ]
        job.status = "completed"
        from lipidmix.console.job_manager import save_job
        save_job(job, job_path)
    return job_path


def test_dry_run_lists_without_deleting(tmp_path):
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    parsed = _json.loads(console_cleanup(str(job_path)))
    assert parsed["dry_run"] is True
    assert parsed["count"] == 3
    assert (tmp_path / "s1_1.pai2").exists()


def test_dry_run_is_the_default(tmp_path):
    """消す側を既定にしてはいけない。"""
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    console_cleanup(str(job_path))
    assert (tmp_path / "AlignResult-1_PeakProperties.arf").exists()


def test_never_lists_raw_measurement_files(tmp_path):
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    parsed = _json.loads(console_cleanup(str(job_path)))
    assert not any(p.endswith(".wiff") for p in parsed["files"])


def test_delete_removes_only_recorded_outputs(tmp_path):
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    parsed = _json.loads(console_cleanup(str(job_path), dry_run=False))
    assert parsed["deleted"] == 3
    assert not (tmp_path / "s1_1.pai2").exists()
    assert not (tmp_path / "AlignResult-1_PeakProperties.arf").exists()
    assert (tmp_path / "keep_me.wiff").exists()


def test_delete_marks_the_job_as_cleaned(tmp_path):
    """掃除後のジョブを dataset_load が完了品として読むと、存在しない生成物を指す。"""
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    console_cleanup(str(job_path), dry_run=False)
    job = load_job(job_path)
    assert job.status == "cleaned"


def test_refuses_when_the_job_recorded_nothing(tmp_path):
    """記録が無いジョブでタイムスタンプ推測に走ると、別バッチを巻き込む。"""
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path, record=False)
    parsed = _json.loads(console_cleanup(str(job_path), dry_run=False))
    assert parsed["error"]["code"] == "NO_JOB_OUTPUT"
    assert (tmp_path / "s1_1.pai2").exists()


def test_reports_already_absent_files_without_failing(tmp_path):
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    (tmp_path / "s1_1.pai2").unlink()
    parsed = _json.loads(console_cleanup(str(job_path), dry_run=False))
    assert parsed["deleted"] == 2
    assert parsed["already_absent"] == 1


# ---------- 所有中のジョブを消させない ----------
# 実行中のジョブの生成物を消すと、監視ワーカーが書いている最中のファイルを
# 足元から抜くことになる。所有者（worker.json の owner）が**今も生きている**
# かどうかは process identity で見る。pid だけでは pid 再利用を見分けられない。

_WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="process identity による所有者判定は Windows 専用")


def _own_job(job_path, *, alive: bool) -> None:
    """このジョブを「実行中のワーカーが所有している」状態にする。"""
    from lipidmix.console.worker import write_owner
    from lipidmix.core.process_control import process_identity
    run_dir = Path(load_job(job_path).run_dir)
    identity = (process_identity(os.getpid()) if alive
                else {"pid": 999_999_999, "creation_time": 1})
    write_owner(run_dir, {"kind": "console_worker", "pid": identity["pid"],
                          "identity": identity, "status": "running"})


@_WINDOWS_ONLY
def test_refuses_to_delete_while_a_worker_owns_the_job(tmp_path):
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    _own_job(job_path, alive=True)

    parsed = _json.loads(console_cleanup(str(job_path), dry_run=False))

    assert parsed["error"]["code"] == "JOB_BUSY"
    assert (tmp_path / "s1_1.pai2").exists()
    assert load_job(job_path).status == "completed"


@_WINDOWS_ONLY
def test_dry_run_lists_but_warns_while_a_worker_owns_the_job(tmp_path):
    """一覧は読み取りだけなので許す。ただし黙って渡さない。"""
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    _own_job(job_path, alive=True)

    parsed = _json.loads(console_cleanup(str(job_path)))

    assert parsed["dry_run"] is True
    assert any("worker" in w for w in parsed["warnings"])


@_WINDOWS_ONLY
def test_deletes_when_the_recorded_owner_is_no_longer_running(tmp_path):
    """終了したワーカーの記録が残っているだけなら、片付けを止めない。"""
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    _own_job(job_path, alive=False)

    parsed = _json.loads(console_cleanup(str(job_path), dry_run=False))

    assert parsed["deleted"] == 3
    assert load_job(job_path).status == "cleaned"


@_WINDOWS_ONLY
def test_refusal_names_the_owner_so_the_caller_can_act(tmp_path):
    """「使用中」だけでは、待てばよいのか手で直すのか判断できない。"""
    from lipidmix.tools.console_tools import console_cleanup
    job_path = _job_with_outputs(tmp_path)
    _own_job(job_path, alive=True)

    owner = _json.loads(console_cleanup(str(job_path), dry_run=False))["error"]["details"]["owner"]

    assert owner["kind"] == "console_worker"
    assert owner["pid"] == os.getpid()
    assert owner["active"] is True
