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


def is_console_exe(
    exe_path: str,
    timeout_s: int = 15,
    *,
    raise_on_os_error: bool = False,
) -> bool:
    """MSDIAL_EXE が Console 実行体かを --help の出力で判定する。

    Console は旧ビルドも master ビルドも --help（旧は引数エラー時の usage）に
    サブコマンド名 `lcms` を含む。GUI の MSDIAL.exe はコンソール出力を持たず、
    ウィンドウを開いたまま返らないため、ここで実行前に弾く。既定では
    起動不能も False にするが、実行経路は `raise_on_os_error=True` で
    起動不能を既存の NOT_FOUND 経路へ渡せる。
    """
    try:
        completed = subprocess.run(
            [exe_path, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout_s,
            text=True,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        if raise_on_os_error:
            raise
        return False
    return "lcms" in (completed.stdout or "")


def build_msdial_cmd(
    exe: str,
    dataset_root: Path,
    msdial_out_dir: Path,
    method_file: Path,
    save_project: bool = False,
) -> list[str]:
    """Console のコマンドラインを組む。

    待って実行する経路と切り離して実行する経路で**同じ引数**でなければならない
    ので、組み立てはここ 1 か所に置く。
    """
    cmd = [exe, "lcms", "-i", str(dataset_root), "-o", str(msdial_out_dir),
           "-m", str(method_file)]
    if save_project:
        cmd.append("-p")
    return cmd


def _open_log(run_dir: Path, cmd: list[str]):
    """msdial.log を開き、CMD 行を書いて flush 済みのハンドルを返す。

    fd を子へ渡す前に必ず flush する。親のバッファと子は同じファイル記述の
    オフセットを共有するため、flush しないと子の出力が先頭に、CMD 行がその
    後ろに書かれる（失敗解析でまずログ先頭を見る運用が壊れる）。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    log = (run_dir / "msdial.log").open("w", encoding="utf-8")
    log.write(f"CMD: {' '.join(cmd)}\n\n")
    log.flush()
    return log


def is_process_running(pid: int) -> bool:
    """pid が生きているかを返す（判定できないときは False ＝安全側）。

    切り離して起動した Console は親を持たないので、待つ代わりにこれで見る。
    """
    if not pid or pid < 0:
        return False
    if os.name == "nt":
        import subprocess as _sp
        try:
            out = _sp.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                          stdout=_sp.PIPE, stderr=_sp.DEVNULL, stdin=_sp.DEVNULL,
                          timeout=15, text=True, errors="replace")
        except (OSError, _sp.TimeoutExpired):
            return False
        return str(int(pid)) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 自分のものではないが存在はする
    except OSError:
        return False
    return True


def run_msdial_detached(
    method_file: Path,
    dataset_root: Path,
    run_dir: Path,
    exe_path: str | None = None,
    save_project: bool = False,
) -> int:
    """MS-DIAL Console を親から切り離して起動し、pid を返す（待たない）。

    実データ 60 サンプルは 44 分かかる。`subprocess.run` で待つと MCP の
    1 ツール呼び出しがその間戻らず、呼び出し元の都合でプロセスツリーごと
    落とされると生成物ごと失う（実測 2 回）。切り離しておけば、呼び出し元が
    消えても Console は走り続ける。完了は `is_process_running` で見る。
    """
    exe = exe_path or get_exe_path()
    msdial_out_dir = run_dir / "msdial"
    msdial_out_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_msdial_cmd(exe, dataset_root, msdial_out_dir, method_file, save_project)

    log = _open_log(run_dir, cmd)
    kwargs: dict = {
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "cwd": str(run_dir),
    }
    if os.name == "nt":
        # 新しいプロセスグループ＋コンソール非継承。親が終了しても道連れにしない。
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(cmd, **kwargs)
    finally:
        log.close()
    return int(proc.pid)


def run_msdial(
    method_file: Path,
    dataset_root: Path,
    run_dir: Path,
    timeout_s: int = 3600,
    exe_path: str | None = None,
    save_project: bool = False,
) -> int:
    """MS-DIAL Console を実行し、終了コードを返す。

    Parameters
    ----------
    method_file:
        MS-DIAL Console のパラメータファイル（ASCII テキスト。`key: value` 形式）。
    dataset_root:
        生データフォルダ（-i 引数）。
    run_dir:
        出力先（-o 引数）。msdial.log もここに書く。
    timeout_s:
        タイムアウト秒数（既定 1 時間）。
    exe_path:
        省略時は MSDIAL_EXE 環境変数から取得。テストでは fake パスを注入する。
    save_project:
        True なら -p を付け、GUI で開ける .mdproject を出力させる。

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

    cmd = build_msdial_cmd(exe, dataset_root, msdial_out_dir, method_file, save_project)

    try:
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"CMD: {' '.join(cmd)}\n\n")
            # 子へ fd を渡す前に必ず flush する。親のバッファと子は同じ
            # ファイル記述のオフセットを共有するため、flush しないと
            # 子の出力が先頭に、CMD 行がその後ろに書かれる。
            log.flush()
            result = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout_s,
            )
    except subprocess.TimeoutExpired as exc:
        raise MsdialTimeoutError(
            f"MS-DIAL Console がタイムアウトしました（{timeout_s}s）"
        ) from exc

    if result.returncode != 0:
        raise MsdialNonZeroExitError(result.returncode)

    return result.returncode


#: Console 実行体の名前。GUI（MSDIAL.exe）は候補にしない —— 引数を解釈せず
#: ウィンドウを開いたまま返らないので、設定されると実行時に固まる。
_CONSOLE_EXE_NAMES = ("msdialcui.exe", "msdialconsoleapp.exe")

#: 候補探索の既定の起点。MS-DIAL は zip を展開しただけで使うことが多く、
#: ホーム直下かデスクトップに置かれているのが実情。
def _default_search_roots() -> list[Path]:
    home = Path.home()
    roots = [home, home / "Desktop", home / "Downloads",
             Path("C:/Program Files"), Path("C:/Program Files (x86)")]
    return [r for r in roots if r.is_dir()]


def msdial_exe_candidates(search_roots=None, max_depth: int = 3) -> list[str]:
    """MS-DIAL Console 実行体の候補を返す（設定はしない）。

    MSDIAL_EXE を設定できるのは人間だけなので、サーバにできるのは「どこに
    ありそうか」を示すところまで。深さを制限するのは、ホーム配下を無制限に
    歩くと計画が数十秒止まるため。
    """
    roots = [Path(r) for r in (search_roots if search_roots is not None
                               else _default_search_roots())]
    found: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
            for name in filenames:
                if name.lower() in _CONSOLE_EXE_NAMES:
                    found.append(str(Path(dirpath) / name))
    return found
