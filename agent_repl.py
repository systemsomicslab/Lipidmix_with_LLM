"""Agent-loop の CLI REPL。ローカル qwen3:14b で MS-DIAL 解析を対話実行する。

起動: .venv-1/Scripts/python.exe agent_repl.py
前提: Ollama 起動＋qwen3:14b。server.mcp からツールスキーマを取得する。
"""
import asyncio

import httpx

import phase_router
import server
from agent_core import Agent, execute_tool, ollama_chat
from phase_router import RouterState


def safe_classify(query: str, candidates: list) -> str:
    """route 用分類器。Ollama transport 例外時は "" を返し route のフォールバックに委ねる。"""
    try:
        return phase_router.ollama_classify_fn(query, candidates)
    except httpx.HTTPError:
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


def main() -> None:
    schemas = asyncio.run(_get_tool_schemas())
    agent = Agent(
        tool_schemas=schemas,
        chat_fn=ollama_chat,
        execute_fn=execute_tool,
        classify_fn=safe_classify,
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
        except httpx.HTTPError:
            print("Ollamaに接続できません（起動とモデルを確認してください）")
            continue
        print(f"[{state.last_phase}] {answer}")


if __name__ == "__main__":
    main()
