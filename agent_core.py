"""Agent-loop の中核: ツール実行の継ぎ目とループ論理。

MCP サーバと phase_router は無改変で import する。ツール実行は in-process 直呼び
（getattr(server, name)）で、これが将来 MCP プロトコル越しへ差し替える継ぎ目。
全 I/O（Ollama チャット・ツール実行・フェーズ分類）は Agent に注入され、run_turn を
Ollama 非依存でテストできる。
"""
import json
import os
from dataclasses import dataclass
from typing import Callable

import httpx

import phase_router
from phase_router import RouterState
import server
from interp_eval import INTERP_SYSTEM

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
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:  # ツール実行時・戻り値直列化時の例外はモデルに見せてループ継続させる
        return json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


AGENT_MODEL = os.environ.get("LIPIDMIX_AGENT_MODEL", "qwen3:14b")


def ollama_chat(messages: list, tools: list) -> dict:
    """Ollama /api/chat に tools 付きで問い合わせ、message dict を返す。

    httpx 例外はここでは捕えず呼び出し側（run_turn 直下の REPL）に委ねる。
    """
    payload = {
        "model": AGENT_MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    resp = httpx.post(phase_router.OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["message"]


DEFAULT_SYSTEM = (
    "あなたはMS-DIALリピドミクス解析アシスタントです。提供されたツールを使って"
    "ユーザーの要求に応え、ツールの結果を踏まえて日本語で簡潔に解釈・回答してください。"
)

# load_dataset 成功時の戻り値ヘッダ（tools_dataset.py 参照）。成功/失敗とも list を返すため、
# このマーカーの有無で成功を判定する（v1 の実務的ヒューリスティック）。
_LOAD_SUCCESS_MARKER = "データセット読み込み"

# クラウド送りが分界点(7-0)で優勢だった高価値ツール（PCA/QC/文献）。実行ツール集合が
# これに触れたターンの最終解釈をクラウドへエスカレートする。
CLOUD_TIER_TOOLS = {"arf_re_pca", "arf_pca_preprocessed", "arf_preprocess", "paper_search"}
# 差次は slim 化後ローカルで互角。QC と同じ arf_preprocess を伴走するため、
# arf_differential が走ったターンはクラウド送りを取り消す（local veto）。
LOCAL_VETO_TOOLS = {"arf_differential"}


def should_escalate(executed: set[str]) -> bool:
    """このターンで実行したツール集合が高価値(クラウド送り)かを判定する純述語。"""
    return bool(executed & CLOUD_TIER_TOOLS) and not (executed & LOCAL_VETO_TOOLS)


@dataclass
class Agent:
    tool_schemas: dict           # name -> Ollama tool schema（起動時 list_tools() から構築）
    chat_fn: Callable            # (messages: list, tools: list) -> message dict
    execute_fn: Callable         # (name: str, args: dict) -> str
    classify_fn: Callable        # (query: str, candidates: list) -> str（route 用）
    interp_fn: Callable | None = None  # (messages: list) -> str（高価値ターンのクラウド最終解釈。None=純ローカル）
    max_rounds: int = 5
    system_prompt: str = DEFAULT_SYSTEM

    def run_turn(self, query: str, state: RouterState, conversation: list) -> str:
        """1 ユーザーターンを実行する。route を1回引いてフェーズ固定、有界ループでツール実行。

        ループ終端（tool_call なし）で、このターンの実行ツール集合が高価値なら（interp_fn
        注入時のみ）蓄積会話をクラウドへ渡して最終解釈を得る。非高価値・interp_fn 無し・
        クラウド例外・空応答はローカル最終散文へ縮退する。state と conversation を更新し、
        最終応答テキストを返す。
        """
        routed = phase_router.route(query, state, self.classify_fn)
        state.last_phase = routed.phase
        tools = [self.tool_schemas[n] for n in routed.tool_names if n in self.tool_schemas]

        conversation.append({"role": "user", "content": query})
        turn_start = len(conversation) - 1  # 今ターンの証拠スライス起点（user を含む）
        executed: set[str] = set()
        for _ in range(self.max_rounds):
            messages = [{"role": "system", "content": self.system_prompt}] + conversation
            msg = self.chat_fn(messages, tools)
            calls = msg.get("tool_calls") or []
            if not calls:
                local_content = msg.get("content") or ""
                if self.interp_fn is not None and should_escalate(executed):
                    try:
                        cloud = self.interp_fn(
                            [{"role": "system", "content": INTERP_SYSTEM}]
                            + conversation[turn_start:])
                        if cloud.strip():
                            conversation.append({"role": "assistant", "content": cloud})
                            state.last_arm = "cloud"
                            return cloud
                    except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
                        pass  # クラウド失敗 → ローカル最終散文へフォールバック
                conversation.append({"role": "assistant", "content": local_content})
                state.last_arm = "local"
                return local_content
            conversation.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments") or {}
                executed.add(name)
                out = _truncate(self.execute_fn(name, args))
                conversation.append({"role": "tool", "content": out, "tool_name": name})
                if name == "load_dataset" and _LOAD_SUCCESS_MARKER in out:
                    state.dataset_loaded = True
        return "（ツール呼び出しが上限に達しました）"
