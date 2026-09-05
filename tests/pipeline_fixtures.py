"""pipeline関連テストの合成fixture置き場。

実rawや既存成果物をfixtureにしない（CLAUDE.md方針）。ここは純粋なdictビルダーの集合で、
テストごとに独自の形を作らせないための唯一の正準とする。Task 3・6・9・13・19がここへ
helperを足していく前提なので、既存helperの必須フィールドは減らさない。
"""
from __future__ import annotations


def execution_record(**overrides):
    """console-execution.v1 の妥当な最小レコードを返す。

    overridesで一部フィールドを壊し、validate_execution_recordの拒否条件を
    テストするために使う。
    """
    record = {
        "schema": "console-execution.v1", "execution_id": "exec-1", "job_id": "job-1",
        "started_at": "2026-09-05T00:00:00Z", "ended_at": "2026-09-05T00:00:01Z",
        "pid": 1234, "process_identity": {"pid": 1234, "creation_time": 100},
        "command_sha256": "a" * 64, "method_sha256": "b" * 64, "exe_sha256": "c" * 64,
        "exit_code": 0, "termination": "exited", "timeout_s": 21600,
        "collection": {"status": "succeeded"}, "validation": {"status": "pending"},
    }
    record.update(overrides)
    return record
