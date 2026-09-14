"""稼働中のサーバ版数と、origin/main からの遅れを 1 行で出すためのモジュール。

**なぜ要るか**: MCP サーバはクライアント（Claude Desktop 等）が起動したまま
生き続けるので、`git pull` でコードを更新しても**次の会話でも古いプロセスが
動き続ける**。実際に、削除済みの caveat 文面が返ってきて初めて気づいた
（docs/HISTRY.md 2026-09-04(4)）。まず版数を状態ツールの戻り値に刻み、
ズレていたらサーバを再起動する、というのが元の対処。

配布先（DEPLOY.md のメンバー各自のクローン）では、そもそも更新が出たことに
気づけない。そこで起動時に 1 回だけ `git fetch` し、`origin/main` より遅れて
いれば通知する。**自動 pull はしない** —— 走っているプロセスの裏でコードが
差し替わると、import 済みモジュールだけが古いまま残る混在状態になり、
上の事故より厄介な壊れ方をする。更新の適用はユーザが明示的に行う。

判定できないときは**すべて黙る**（オフライン・認証失敗・git 不在・main 以外の
ブランチ）。通知機能が解析の邪魔をしてはいけないので、不明は通知なしに倒す。
"""
from __future__ import annotations

import subprocess
import threading
from functools import lru_cache
from pathlib import Path

VERSION = "0.1.0"

#: 配布の正準ブランチ。ユーザのクローンはここに乗っている前提。
UPSTREAM_BRANCH = "main"
#: ローカルの git 操作（ネットワークを伴わない）の上限。
GIT_TIMEOUT_SEC = 10.0
#: fetch の上限。daemon スレッドで走るので、切れても誰も待たされない。
FETCH_TIMEOUT_SEC = 15.0

_UNSET = object()
#: fetch が**成功した**ことを示す。未完了と失敗はどちらも「黙る」なので同一視してよい。
_fetch_ok = threading.Event()
_update_cache: object = _UNSET
_check_started = False
_check_lock = threading.Lock()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(*args: str, timeout: float = GIT_TIMEOUT_SEC) -> str | None:
    """git の標準出力（strip 済み）を返す。失敗・git 不在なら None。

    呼び出し側は None を「判定できなかった」として黙る側に倒すこと。
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(_repo_root()), *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, timeout=timeout, text=True, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or "").strip()


@lru_cache(maxsize=1)
def server_version() -> str:
    """`<version>+<git の短縮 SHA>` を返す。git が無ければ版数だけ。

    プロセス生存中は変わらないのでキャッシュする（起動時のコードが何かを
    示す値なので、途中で作業ツリーが変わっても更新しないのが正しい）。
    """
    sha = _git("rev-parse", "--short", "HEAD")
    return f"{VERSION}+{sha}" if sha else VERSION


def _run_update_fetch() -> None:
    """`origin/<UPSTREAM_BRANCH>` の ref を 1 回だけ更新する。

    成功したときだけフラグを立てる。失敗を握り潰すのは意図的で、
    オフラインのメンバーに通知の失敗を見せる意味がないため。
    """
    if _git("fetch", "--quiet", "origin", UPSTREAM_BRANCH,
            timeout=FETCH_TIMEOUT_SEC) is not None:
        _fetch_ok.set()


def start_update_check() -> None:
    """起動時に 1 回だけ、裏で fetch を投げる（プロセスにつき 1 回）。

    ツール呼び出しの中で同期実行すると、最初の 1 呼び出しがネットワーク待ちで
    止まる。daemon スレッドにしてあるので、終わっていなければ通知しないだけで
    サーバの終了を妨げない。
    """
    global _check_started
    with _check_lock:
        if _check_started:
            return
        _check_started = True
    threading.Thread(target=_run_update_fetch,
                     name="lipidmix-update-check", daemon=True).start()


def _compute_update_status() -> dict | None:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != UPSTREAM_BRANCH:
        # 開発者は feature ブランチに、巻き戻し検証は detached HEAD にいる。
        # どちらも「配布物が遅れている」話ではないので鳴らさない。
        return None
    count = _git("rev-list", "--count", f"HEAD..origin/{UPSTREAM_BRANCH}")
    if count is None:
        return None
    try:
        behind = int(count)
    except ValueError:
        return None
    if behind <= 0:
        return None
    return {
        "behind": behind,
        "message": (f"origin/{UPSTREAM_BRANCH} より {behind} コミット遅れています。"
                    "git pull の後、MCP クライアントを再起動してください"
                    "（pull だけでは起動中の古いプロセスが動き続けます）。"),
    }


def update_status() -> dict | None:
    """遅れていれば `{"behind": N, "message": ...}`、それ以外は None。

    fetch が終わるまでは None を返すが**キャッシュしない**。起動直後の
    1 回目で黙ったことを覚えてしまうと、その後 fetch が届いても永久に
    通知できなくなるため。
    """
    global _update_cache
    if not _fetch_ok.is_set():
        return None
    if _update_cache is _UNSET:
        _update_cache = _compute_update_status()
    return _update_cache  # type: ignore[return-value]
