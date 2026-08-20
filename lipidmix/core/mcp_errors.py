"""クライアント非依存のエラーエンベロープ。

MCP クライアントが「どのツールを先に呼べばよいか」を、サーバ固有の日本語文面を
解釈せずに機械的に判断できるようにする。汎用クライアント（Use-LLLM 等）は
本文を JSON として読み、`error.code` が `missing_state` なら
`error.required_tools` を再実行して状態を復元できる。

なぜ本文の JSON なのか: 使用中の MCP SDK では `isError=true` を作る経路
（lowlevel/server.py の _make_error_result）がテキストのみを返して
structuredContent を捨てる。さらに FastMCP は戻り値アノテーションから
outputSchema を導出するため、`-> str` のツールが失敗時だけ dict を返すこともできない。
機械可読なエラーはテキストに載せるしかない。

このモジュールは依存グラフの leaf（stdlib のみ）。tools_* / server を import しない。
"""
import json

MISSING_STATE = "missing_state"


def missing_state(state: str, required_tools: list[str], message: str) -> str:
    """前提となるセッション状態が無いことを、契約どおりのエンベロープで返す。

    引数:
        state: 欠けている状態の識別子（例 "preprocessed_matrix"）。クライアントは
            これを不透明な文字列として扱い、解釈しない。開示とログのためにある。
        required_tools: その状態を作れるツール名の**代替候補（OR）**。サーバ名は
            含めない（クライアント側の名前空間はクライアントが決める）。先頭ほど優先。
        message: LLM と人間が読む説明。既存の日本語文面をそのまま渡すこと。
            エンベロープを解釈しないクライアントではこれだけが見える。

    3つとも欠かせない。復旧の手掛かりが無いエンベロープを無言で出すと、
    クライアントは「状態不足だが何もできない」状態に陥り、原因究明を誤らせる。
    """
    if not state:
        raise ValueError("state は必須です（欠けている状態の識別子）。")
    if not required_tools:
        raise ValueError("required_tools は必須です（状態を作れるツールの候補）。")
    if not message:
        raise ValueError("message は必須です（LLM と人間が読む説明）。")
    return json.dumps(
        {
            "error": {
                "code": MISSING_STATE,
                "state": state,
                "required_tools": list(required_tools),
                "message": message,
            }
        },
        ensure_ascii=False,
        indent=2,
    )
