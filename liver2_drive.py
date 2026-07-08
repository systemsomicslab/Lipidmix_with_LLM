"""サブエージェント用ツール駆動ヘルパ。

データをプライムし、指定ツール列を1プロセス内で順に実行して出力を印字する。session は
プロセス内のみ有効なので、毎回 steps の先頭に load_dataset を含めること。

使い方（サブエージェントが繰り返し呼ぶ）:
  .venv-1/Scripts/python.exe liver2_drive.py <DIR> \
    '[{"name":"load_dataset","args":{"directory":"<DIR>"}},{"name":"arf_re_pca","args":{"top_features":10}}]'
"""
import json
import os
import sys

import agent_core as ac
import server
import session_state


def drive(directory, steps):
    """steps（{"name","args"} の列）を新規セッションで順に実行し、整形テキストを返す。"""
    os.environ["LIPIDMIX_DATA_DIR"] = directory
    session_state.session = server.AnalysisSession()
    parts = []
    for s in steps:
        args = dict(s.get("args") or {})
        # load_dataset は directory 明示が要る（env フォールバックが効かない環境がある）。
        # サブエージェントが Windows パスを JSON に埋めずに済むよう drive 側で注入する。
        if s["name"] == "load_dataset" and not args.get("directory"):
            args["directory"] = directory
        out = ac._truncate(ac.execute_tool(s["name"], args))
        parts.append(f"=== {s['name']} {json.dumps(args, ensure_ascii=False)} ===\n{out}")
    return "\n\n".join(parts)


def main():
    if len(sys.argv) < 3:
        print("usage: liver2_drive.py <DIR> '<steps-json>'")
        return
    print(drive(sys.argv[1], json.loads(sys.argv[2])))


if __name__ == "__main__":
    main()
