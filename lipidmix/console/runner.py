"""MS-DIAL Console 実行ラッパー。

exe_path は引数で注入できる（テスト用 fake の差し込みに使う）。
既定は環境変数 MSDIAL_EXE から取得し、未設定なら EnvironmentError を上げる。
stdout/stderr は run_dir/msdial.log に書く（リポジトリ外）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


class MsdialExeNotFoundError(EnvironmentError):
    pass


class MsdialTimeoutError(TimeoutError):
    pass


class MsdialNonZeroExitError(RuntimeError):
    def __init__(self, returncode: int):
        super().__init__(f"MS-DIAL Console が終了コード {returncode} で終了しました")
        self.returncode = returncode


def get_exe_path() -> str:
    """環境変数 MSDIAL_EXE から実行ファイルパスを取得する。"""
    exe = os.environ.get("MSDIAL_EXE", "").strip()
    if not exe:
        raise MsdialExeNotFoundError(
            "環境変数 MSDIAL_EXE が設定されていません。"
            "MS-DIAL Console の実行ファイルパスを MSDIAL_EXE に設定してください。"
        )
    return exe


def run_msdial(
    method_file: Path,
    dataset_root: Path,
    run_dir: Path,
    timeout_s: int = 3600,
    exe_path: str | None = None,
) -> int:
    """MS-DIAL Console を実行し、終了コードを返す。

    Parameters
    ----------
    method_file:
        MS-DIAL のメソッドファイル（.msdial / .mdproject）。
    dataset_root:
        生データフォルダ（-i 引数）。
    run_dir:
        出力先（-o 引数）。msdial.log もここに書く。
    timeout_s:
        タイムアウト秒数（既定 1 時間）。
    exe_path:
        省略時は MSDIAL_EXE 環境変数から取得。テストでは fake パスを注入する。

    Raises
    ------
    MsdialExeNotFoundError: MSDIAL_EXE 未設定。
    MsdialTimeoutError: タイムアウト。
    MsdialNonZeroExitError: 終了コード非ゼロ。
    """
    exe = exe_path or get_exe_path()
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "msdial.log"

    msdial_out_dir = run_dir / "msdial"
    msdial_out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [exe, "lcms", "-i", str(dataset_root), "-o", str(msdial_out_dir), "-m", str(method_file)]

    try:
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"CMD: {' '.join(cmd)}\n\n")
            result = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
    except subprocess.TimeoutExpired as exc:
        raise MsdialTimeoutError(
            f"MS-DIAL Console がタイムアウトしました（{timeout_s}s）"
        ) from exc

    if result.returncode != 0:
        raise MsdialNonZeroExitError(result.returncode)

    return result.returncode
