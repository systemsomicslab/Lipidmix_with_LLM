"""Agent-loop の中核: ツール実行の継ぎ目とループ論理。

MCP サーバと phase_router は無改変で import する。ツール実行は in-process 直呼び
（getattr(server, name)）で、これが将来 MCP プロトコル越しへ差し替える継ぎ目。
全 I/O（Ollama チャット・ツール実行・フェーズ分類）は Agent に注入され、run_turn を
Ollama 非依存でテストできる。
"""
import json

import phase_router
import server

# LLM が呼べるのは phase_router が定義する 35 ツールだけ（server の任意属性を弾く allowlist）。
_ALLOWED_TOOLS = {n for names in phase_router.PHASES.values() for n in names}


def _truncate(text: str, limit: int = 8000) -> str:
    """ツール戻り値が文脈を圧迫しないよう上限で切り詰める。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（{len(text) - limit}文字を切り詰め）"


def _is_error(result: str) -> bool:
    """ツール戻り値が構造化エラー（dict かつ status==error）かを判定する。"""
    try:
        obj = json.loads(result)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and obj.get("status") == "error"


def execute_tool(name: str, args: dict) -> str:
    """ツールを in-process で実行し、結果を JSON 文字列で返す。raise はしない。"""
    if name not in _ALLOWED_TOOLS:
        return json.dumps({"status": "error", "error": f"unknown tool: {name}"}, ensure_ascii=False)
    fn = getattr(server, name, None)
    if not callable(fn):
        return json.dumps({"status": "error", "error": f"not callable: {name}"}, ensure_ascii=False)
    try:
        result = fn(**args)
    except Exception as e:  # ツール実行時の例外はモデルに見せてループ継続させる
        return json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)
