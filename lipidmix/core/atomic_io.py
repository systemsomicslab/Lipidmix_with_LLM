"""数値・ドメイン層で共有する原子的JSON保存とhash。

MCPに依存しない依存グラフのleaf（stdlibのみ）。lipidmix.core.mcp_core /
lipidmix.<形式>.tools / lipidmix.tools.* をimportしてはいけない
（`lipidmix/core/mcp_core.py` はこのモジュールを含む「leaf」からさらに
上位を組み立てる側なので、循環を避けるためここからは何も引かない）。

`DomainError`は本plan（生データフォルダ起点pipeline）の数値・ドメイン層が
共通で使う唯一の例外。console終了証跡・pipeline状態・結果fingerprintなど、
後続タスクの多くがここからimportする。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


class DomainError(Exception):
    """数値・ドメイン層の共通例外。

    `code`は機械可読な種別（例 "EXECUTION_RECORD_INVALID"）、`message`は
    人間可読な日本語説明。`str(exc)`は`f"{code}: {message}"`を返す。
    """

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details if details is not None else {}

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def canonical_hash(value: object) -> str:
    """値を正規化JSON（key昇順・改行なし）にしてSHA-256を返す。

    呼び出し側の責務: 日時・UUIDなど実行のたびに変わる値をvalueへ混ぜない
    （fingerprintに再現性がなくなるため）。
    """
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def atomic_write_json(path: Path, data: dict) -> None:
    """JSONを原子的に保存する。

    同じ親ディレクトリへNamedTemporaryFileで書き、UTF-8・allow_nan=False で
    直列化し、flush・os.fsync してから os.replace で置換する。この順序を
    崩すと、置換直前にプロセスが落ちた際に中途半端な内容を確定状態として
    読ませてしまう。

    失敗時は未確定の一時ファイルだけを片付け、既存の path には一切触れない
    （path.unlink はしない）。
    """
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            json.dump(data, tmp_file, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            os.unlink(tmp_path)
        raise
