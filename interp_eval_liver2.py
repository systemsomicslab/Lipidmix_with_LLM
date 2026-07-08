"""解釈精度評価（20230824_liver）の手動オーケストレータ。

使い方（repo ルート、Ollama 起動＋Azure creds 前提）:
    .venv-1/Scripts/python.exe interp_eval_liver2.py freeze     # gold 正準ツール出力を凍結
    .venv-1/Scripts/python.exe interp_eval_liver2.py run_auto   # ハイブリッド/Azure を run_turn 実行

gold(Opus) 解析・rubric 作成・採点は別途（Claude Code セッションが実施）。
"""
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

# .env からは AZURE_ のみ取り込む（interp_eval_run.py と同じ規約）。
try:
    from dotenv import dotenv_values, find_dotenv
    _envpath = find_dotenv(usecwd=True)
    for _k, _v in (dotenv_values(_envpath) if _envpath else {}).items():
        if _k.startswith("AZURE_") and _v and _k not in os.environ:
            os.environ[_k] = _v
except ImportError:
    pass

import agent_core as ac
import interp_eval as ie
import server
import session_state
from agent_repl import _get_tool_schemas, safe_classify
from interp_eval_cases_liver2 import DIR_NEG, DIR_POS, LIVER2_CASES
from phase_router import RouterState

OUT = Path("interp_eval_out/liver2")
FROZEN = OUT / "frozen"
INTERP = OUT / "interp"
AUTO_MODELS = ["hybrid", "azure"]

# 対象データは do_run_auto で事前ロード（プライム）済み。全モデルに同一の前提を与えて
# 再 load_dataset の相対パス誤読を防ぎ、分析ツール駆動を単離する。
SYSTEM_PRELOADED = ac.DEFAULT_SYSTEM + (
    "\n対象データセットは既に読み込み済みです（load_dataset 実行済み）。再度 load_dataset を"
    "呼ばず、arf_re_pca / arf_preprocess / arf_differential / arf2_annotate_identities / "
    "paper_search などの分析ツールを使って解析し、結果を根拠に日本語で簡潔に解釈してください。"
    "群名や Class ID が必要なら arf_list_classes で確認できます。")


def build_agent(key, schemas):
    """自動モデルの Agent を組み立てる。hybrid=qwen3:14b+Azureエスカレーション、azure=Azure単体。"""
    if key == "azure":
        chat_fn = lambda messages, tools: ie.openai_chat(messages, tools)
        interp_fn = None
    elif key == "hybrid":
        chat_fn = ac.ollama_chat
        interp_fn = lambda messages: ie.azure_generate(messages)
    else:
        raise ValueError(f"unknown model key: {key}")
    return ac.Agent(
        tool_schemas=schemas, chat_fn=chat_fn, execute_fn=ac.execute_tool,
        classify_fn=safe_classify, interp_fn=interp_fn,
        system_prompt=SYSTEM_PRELOADED, max_rounds=8)


def do_freeze():
    """各ケースの pipeline を正準実行し、最後のツール出力を gold 証拠として凍結する。"""
    FROZEN.mkdir(parents=True, exist_ok=True)
    for case in LIVER2_CASES:
        session_state.session = server.AnalysisSession()
        out = ""
        for step in case.pipeline:
            out = ac.execute_tool(step.name, step.args)
        out = ac._truncate(out)
        last = case.pipeline[-1]
        fc = ie.FrozenCase(case.id, case.phase_label, case.mode, case.query,
                           last.name, last.args, out)
        (FROZEN / f"{case.id}.json").write_text(
            json.dumps(asdict(fc), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"froze {case.id} ({len(out)} chars)")


def _directory(mode):
    return DIR_NEG if mode == "NEG" else DIR_POS


def do_run_auto():
    """自動2モデルを run_turn で各ケース実行し、トランスクリプトを保存する（冪等）。"""
    INTERP.mkdir(parents=True, exist_ok=True)
    schemas = asyncio.run(_get_tool_schemas())
    for case in LIVER2_CASES:
        directory = _directory(case.mode)
        for key in AUTO_MODELS:
            dest = INTERP / f"{case.id}__{key}.txt"
            if dest.exists():
                print(f"skip {case.id} / {key} (exists)")
                continue
            os.environ["LIPIDMIX_DATA_DIR"] = directory
            session_state.session = server.AnalysisSession()
            # データロードは全モデル共通の決定的セットアップ（解析ツール駆動を単離）。
            ac.execute_tool("load_dataset", {"directory": directory})
            state = RouterState(dataset_loaded=True)
            conversation = []
            agent = build_agent(key, schemas)
            final = agent.run_turn(case.query, state, conversation)
            dest.write_text(ie.transcript_text(conversation, final), encoding="utf-8")
            print(f"ran {case.id} / {key} ({len(final)} chars, arm={state.last_arm})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"freeze": do_freeze, "run_auto": do_run_auto}.get(
        cmd, lambda: print("usage: interp_eval_liver2.py freeze|run_auto"))()


if __name__ == "__main__":
    main()
