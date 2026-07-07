"""Agent-loop の CLI REPL。ローカル qwen3:14b で MS-DIAL 解析を対話実行する。

起動: .venv-1/Scripts/python.exe agent_repl.py
前提: Ollama 起動＋qwen3:14b。server.mcp からツールスキーマを取得する。
"""
import asyncio
import os

import httpx

import interp_eval
import phase_router
import server
from agent_core import Agent, execute_tool, ollama_chat
from phase_router import RouterState

# .env からは AZURE_ のみ取り込む（interp_eval_run.py と同じ根拠＝LIPIDMIX_* はテンプレの
# ダミーで server import を壊すため除外。既存 os.environ は上書きしない）。
try:
    from dotenv import dotenv_values, find_dotenv
    _envpath = find_dotenv(usecwd=True)
    for _k, _v in (dotenv_values(_envpath) if _envpath else {}).items():
        if _k.startswith("AZURE_") and _v and _k not in os.environ:
            os.environ[_k] = _v
except ImportError:
    pass


def safe_classify(query: str, candidates: list) -> str:
    """route 用分類器。Ollama transport 例外時は "" を返し route のフォールバックに委ねる。"""
    try:
        return phase_router.ollama_classify_fn(query, candidates)
    except (httpx.HTTPError, KeyError, ValueError):
        return ""


async def _get_tool_schemas() -> dict:
    tools = await server.mcp.list_tools()
    return {
        t.name: {
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "").strip(),
                "parameters": t.inputSchema,
            },
        }
        for t in tools
    }


def _build_interp_fn():
    """高価値ターンのクラウド最終解釈腕を組み立てる。creds/トグル未充足なら None（純ローカル）。"""
    if os.environ.get("LIPIDMIX_CLOUD_INTERP", "1") == "0":
        return None  # 明示 kill-switch
    if not (os.environ.get("AZURE_OPENAI_ENDPOINT")
            and os.environ.get("AZURE_OPENAI_API_KEY")
            and os.environ.get("AZURE_OPENAI_DEPLOYMENT")):
        return None  # creds なし → 完全ローカル縮退
    return lambda messages: interp_eval.azure_generate(messages)


def main() -> None:
    schemas = asyncio.run(_get_tool_schemas())
    agent = Agent(
        tool_schemas=schemas,
        chat_fn=ollama_chat,
        execute_fn=execute_tool,
        classify_fn=safe_classify,
        interp_fn=_build_interp_fn(),
    )
    state = RouterState(dataset_loaded=False)
    conversation: list = []
    print("MS-DIAL ローカル解析エージェント（空行 or 'quit' で終了）")
    while True:
        try:
            query = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not query or query.lower() == "quit":
            break
        try:
            answer = agent.run_turn(query, state, conversation)
        except (httpx.HTTPError, KeyError, ValueError):
            print("Ollamaに接続できません（起動とモデルを確認してください）")
            continue
        print(f"[{state.last_phase}·{state.last_arm}] {answer}")


if __name__ == "__main__":
    main()
