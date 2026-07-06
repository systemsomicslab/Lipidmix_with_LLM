"""phase-router ライブ回帰ハーネス（Ollama 起動が前提の手動スクリプト）。

各ケースを route() に通し、返った subset ツールだけを Ollama に渡して、期待ツールを
選べるかを実 qwen3:14b(think OFF) で測る。受け入れ基準 >= 11/12。

使い方（repo ルートから）:
    .venv-1/Scripts/python.exe phase_router_eval.py
"""
import asyncio

import httpx

import server
import phase_router as pr

OLLAMA = pr.OLLAMA_URL
MODEL = pr.CLASSIFIER_MODEL

SYSTEM = (
    "あなたはMS-DIALリピドミクス解析アシスタントです。ユーザーの要求に対し、"
    "提供されたツールから最適なものを1つ呼び出してください。"
)

# (クエリ, 期待ツール, dataset_loaded 前提)
CASES = [
    ("C:/data/exp1 のフォルダのデータを読み込んで解析を始めて", "load_dataset", False),
    ("解析フォルダにある既存レポートの一覧を見せて", "list_reports", False),
    ("analysis_id=exp1 の知識カバレッジ（GAP小問）を確認して", "knowledge_coverage", True),
    ("ロード済みのARF行列に前処理（正規化・QC）を適用して", "arf_preprocess", True),
    ("前処理後の行列で群間の差次的解析を実行して", "arf_differential", True),
    ("いまのARFデータで使えるClass IDの一覧を見せて", "arf_list_classes", True),
    ("PAI2ファイル C:/data/x.pai2 を解析して", "pai2_parser", True),
    ("PAI2の上位代謝物を見せて", "pai2_get_top_metabolites", True),
    ("m/z 700〜720 の範囲でEICピークを検索して", "eicaef_search_by_mz_range", True),
    ("Europe PMC で 'macrophage LPS ceramide' を検索して", "paper_search", True),
    ("_inbox の保留中ノートのレビューキューを見せて", "ingest_review_queue", True),
    ("analysis_id=exp1 の直近PCA結果をPNG図として保存して", "save_pca_figure", True),
]


async def get_tool_schemas():
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


def call_ollama(tools, query):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": query},
        ],
        "tools": tools,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    r = httpx.post(OLLAMA, json=payload, timeout=300)
    r.raise_for_status()
    msg = r.json()["message"]
    calls = msg.get("tool_calls") or []
    return [c["function"]["name"] for c in calls]


def main():
    by_name = asyncio.run(get_tool_schemas())
    ok = 0
    last_phase = None
    for query, expected, loaded in CASES:
        state = pr.RouterState(dataset_loaded=loaded, last_phase=last_phase)
        rr = pr.route(query, state, pr.ollama_classify_fn)
        last_phase = rr.phase
        tools = [by_name[n] for n in rr.tool_names if n in by_name]
        got = call_ollama(tools, query)
        hit = expected in got
        ok += hit
        mark = "OK " if hit else "NG "
        print(f"{mark}[{rr.phase:10}|{rr.reason:8}|{len(tools):2}t] "
              f"exp={expected:26} got={got}")
    print("-" * 60)
    print(f"命中: {ok}/{len(CASES)}  （受け入れ基準 >= 11）")


if __name__ == "__main__":
    main()
