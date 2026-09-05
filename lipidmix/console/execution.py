"""Console終了証跡（console-execution.v1）の検証。

MS-DIAL Consoleを監視するワーカーが書く execution-result.json の契約。
`validate_execution_record`は検証済みdictだけを消費者へ渡すための唯一の入口で、
妥当性を確認しないまま `pipeline_status` 等へ流用してはいけない。

Task 4 で同じファイルへ `supervise()`（監視の実体）を足す予定。ここでは
検証だけを扱い、監視・プロセス制御には踏み込まない。
"""
from __future__ import annotations

import re
from datetime import datetime

from lipidmix.core.atomic_io import DomainError

SCHEMA = "console-execution.v1"

# spec 5.1: 終了証跡の termination 種別。
TERMINATIONS = frozenset({"exited", "timeout", "cancelled", "worker_lost", "launch_failed"})

# 起動自体に失敗した／監視を見失ったケースは、pid・process_identityの不明を
# 起動情報（command/method/exe の各hash）とは別に許可する（spec 5.1）。
_UNKNOWN_PID_TERMINATIONS = frozenset({"worker_lost", "launch_failed"})

_HASH_FIELDS = ("command_sha256", "method_sha256", "exe_sha256")
_ID_FIELDS = ("execution_id", "job_id")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _fail(message: str, details: dict | None = None) -> None:
    raise DomainError("EXECUTION_RECORD_INVALID", message, details)


def validate_exit_fields(data: dict) -> None:
    """exit_codeの型とterminationとの整合を検査する。"""
    rc = data["exit_code"]
    if rc is not None and type(rc) is not int:
        raise DomainError("EXECUTION_RECORD_INVALID", "終了コードが不正です")
    if data["termination"] == "exited" and rc is None:
        raise DomainError("EXECUTION_RECORD_INVALID", "終了コードがありません")


def _parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        _fail(f"{field_name} が空です", {field_name: value})
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        _fail(f"{field_name} の日時形式が不正です", {field_name: value})
    if dt.tzinfo is None:
        _fail(f"{field_name} はUTCのタイムゾーンを持つ必要があります", {field_name: value})
    return dt


def _validate_pid_and_identity(data: dict) -> None:
    pid = data.get("pid")
    identity = data.get("process_identity")
    termination = data.get("termination")

    if pid is None:
        if termination not in _UNKNOWN_PID_TERMINATIONS:
            _fail("pid が不正です（起動失敗・監視喪失以外でnullは許可されません）",
                 {"pid": pid, "termination": termination})
        if identity is not None:
            _fail("pidがnullのprocess_identityはnullでなければなりません", {"process_identity": identity})
        return

    if type(pid) is not int or pid <= 0:
        _fail("pid が不正です", {"pid": pid})
    if not isinstance(identity, dict) or identity.get("pid") != pid:
        _fail("process_identity がpidと整合しません", {"pid": pid, "process_identity": identity})
    if "creation_time" not in identity:
        _fail("process_identity に creation_time がありません", {"process_identity": identity})


def validate_execution_record(data: dict) -> dict:
    """console-execution.v1 の必須フィールドと整合性を検証する。

    妥当なら検証済みdict（浅いコピー）を返す。不正ならDomainError
    （code="EXECUTION_RECORD_INVALID"）を送出する。呼び出し側は検証済み
    dictだけを保存・伝播させ、生の入力をそのまま流用しない。
    """
    if data.get("schema") != SCHEMA:
        _fail(f"schema が {SCHEMA} ではありません", {"schema": data.get("schema")})

    for field_name in _ID_FIELDS:
        value = data.get(field_name)
        if not isinstance(value, str) or not value:
            _fail(f"{field_name} が空です", {field_name: value})

    termination = data.get("termination")
    if termination not in TERMINATIONS:
        _fail("termination が不正です", {"termination": termination})

    timeout_s = data.get("timeout_s")
    if type(timeout_s) is not int or timeout_s <= 0:
        _fail("timeout_s は正の整数である必要があります", {"timeout_s": timeout_s})

    for field_name in _HASH_FIELDS:
        value = data.get(field_name)
        if not isinstance(value, str) or not _HEX64.match(value):
            _fail(f"{field_name} が64桁の16進数ではありません", {field_name: value})

    started_at = _parse_utc(data.get("started_at"), "started_at")
    ended_at = _parse_utc(data.get("ended_at"), "ended_at")
    if ended_at < started_at:
        _fail("ended_at が started_at より前です",
             {"started_at": data.get("started_at"), "ended_at": data.get("ended_at")})

    validate_exit_fields(data)
    _validate_pid_and_identity(data)

    return dict(data)
