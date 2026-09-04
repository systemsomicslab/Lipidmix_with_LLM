"""切り離して起動した Console 実行の引き継ぎ状態。

`console_run(detach=True)` は起動して即座に戻るので、**完了時の生成物収集を
別の呼び出し（`console_status`）が引き継ぐ**必要がある。収集には「実行前に
何があったか」のスナップショットが要るが、それを撮れるのは起動側だけ。

置き場所は `analysis-job.json` ではなく run_dir のサイドカー。
`analysis-job.v2` は別リポジトリ（massbank-context）との契約でもあるので、
実行方式という内部事情でスキーマを増やさない。
"""
from __future__ import annotations

import json
from pathlib import Path

STATE_FILENAME = ".detached-state.json"


def state_path(run_dir: Path) -> Path:
    return Path(run_dir) / STATE_FILENAME


def write_detached_state(run_dir: Path, pid: int, befores: dict[str, dict]) -> Path:
    """pid と実行前スナップショットを run_dir に残す。"""
    path = state_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": int(pid), "befores": befores}, ensure_ascii=False),
        encoding="utf-8")
    return path


def read_detached_state(run_dir: Path) -> dict | None:
    """引き継ぎ状態を返す。無い・壊れているなら None。"""
    path = state_path(run_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "pid" not in data:
        return None
    data.setdefault("befores", {})
    return data


def clear_detached_state(run_dir: Path) -> None:
    """収集を終えたら消す。残すと 2 回目の console_status がもう一度収集し、
    確定済みのジョブを上書きしてしまう。"""
    try:
        state_path(run_dir).unlink()
    except OSError:
        pass
