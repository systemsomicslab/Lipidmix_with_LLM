"""稼働中のサーバ版数を 1 行で出すためだけのモジュール。

**なぜ要るか**: MCP サーバはクライアント（Claude Desktop 等）が起動したまま
生き続けるので、`git pull` でコードを更新しても**次の会話でも古いプロセスが
動き続ける**。実際に、削除済みの caveat 文面が返ってきて初めて気づいた
（docs/HISTRY.md 2026-09-04(4)）。

検出機構は作らない —— 配布された環境でコードが勝手に変わることは無く、
頻度も低い。代わりに「おかしい」と思ったときに 1 回で分かるよう、
状態を返すツールに版数を刻む。ズレていたらサーバを再起動する。
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

VERSION = "0.1.0"


@lru_cache(maxsize=1)
def server_version() -> str:
    """`<version>+<git の短縮 SHA>` を返す。git が無ければ版数だけ。

    プロセス生存中は変わらないのでキャッシュする（起動時のコードが何かを
    示す値なので、途中で作業ツリーが変わっても更新しないのが正しい）。
    """
    repo = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, timeout=10, text=True, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return VERSION
    sha = (out.stdout or "").strip()
    return f"{VERSION}+{sha}" if out.returncode == 0 and sha else VERSION
