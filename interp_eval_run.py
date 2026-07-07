"""解釈品質評価の手動オーケストレータ（Ollama 起動＋実データ前提）。

使い方（repo ルートから）:
    .venv-1/Scripts/python.exe interp_eval_run.py freeze
    .venv-1/Scripts/python.exe interp_eval_run.py generate
    .venv-1/Scripts/python.exe interp_eval_run.py sheet
    # ↑ で出た blind_sheet.md を審判が採点し verdicts.json を用意してから:
    .venv-1/Scripts/python.exe interp_eval_run.py aggregate
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

import agent_core as ac
import interp_eval as ie
import server
import session_state
from interp_eval_cases import CASES

OUT = Path("interp_eval_out")
FROZEN = OUT / "frozen"
INTERP = OUT / "interp"
SYSTEM = ("あなたはMS-DIALリピドミクス解析アシスタントです。直前のツール結果だけを"
          "根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を"
          "創作しないこと。")

# (model_key, ollama model, think)
MODELS = [("qwen3_off", "qwen3:14b", False),
          ("qwen3_on", "qwen3:14b", True),
          ("qwen25_7b", "qwen2.5:7b", False)]


def do_freeze():
    FROZEN.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        session_state.session = server.AnalysisSession()
        out = ""
        for step in case.pipeline:
            out = ac.execute_tool(step.name, step.args)
        last = case.pipeline[-1]
        fc = ie.FrozenCase(case.id, case.phase_label, case.mode, case.query,
                           last.name, last.args, out)
        (FROZEN / f"{case.id}.json").write_text(
            json.dumps(asdict(fc), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"froze {case.id} ({len(out)} chars)")


def _load_frozen():
    out = []
    for case in CASES:
        d = json.loads((FROZEN / f"{case.id}.json").read_text(encoding="utf-8"))
        out.append(ie.FrozenCase(**d))
    return out


def do_generate():
    INTERP.mkdir(parents=True, exist_ok=True)
    for fc in _load_frozen():
        msgs = ie.build_interp_messages(SYSTEM, fc)
        for key, model, think in MODELS:
            text = ie.ollama_generate(msgs, model=model, think=think)
            (INTERP / f"{fc.id}__{key}.txt").write_text(text, encoding="utf-8")
            print(f"generated {fc.id} / {key} ({len(text)} chars)")


def do_sheet():
    OUT.mkdir(parents=True, exist_ok=True)
    frozen = _load_frozen()
    interp = {}
    for fc in frozen:
        for key, _, _ in MODELS:
            interp[(fc.id, key)] = (INTERP / f"{fc.id}__{key}.txt").read_text(encoding="utf-8")
    items, answer_key = ie.build_blind_sheet(frozen, interp, ie.MODEL_KEYS, seed=20260707)
    fc_by_id = {fc.id: fc for fc in frozen}
    # item 番号だけを可視にし、対戦モデル対（pair_key）は index に隠す＝審判は A/B/tie で盲検採点。
    index = {}
    lines = ["# 盲検比較シート（審判用・モデル名/対戦ペアは伏せてある。各項目 A|B|tie で採点）", ""]
    for n, it in enumerate(items, 1):
        fc = fc_by_id[it["case_id"]]
        ak = answer_key[f"{it['case_id']}::{it['pair_key']}"]
        index[str(n)] = {"case_id": it["case_id"], "phase_label": it["phase_label"],
                         "mode": it["mode"], "pair_key": it["pair_key"],
                         "A": ak["A"], "B": ak["B"]}
        lines += [
            f"## item {n} / {it['phase_label']} / {it['mode']}",
            "", "### ツール結果（根拠）", "```", fc.tool_output[:4000], "```", "",
            "### 出力A", it["a_text"], "", "### 出力B", it["b_text"], "",
            "### 採点（5軸 hallucination/accuracy/completeness/utility/language ＋ overall、"
            "各 A|B|tie）", "",
        ]
    (OUT / "answer_key.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "blind_sheet.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT/'blind_sheet.md'} ({len(items)} items)")


def do_aggregate():
    OUT.mkdir(parents=True, exist_ok=True)
    index = json.loads((OUT / "answer_key.json").read_text(encoding="utf-8"))
    raw = json.loads((OUT / "verdicts.json").read_text(encoding="utf-8"))
    verdicts = []
    for v in raw:
        info = index[str(v["item"])]

        def side_to_model(side):
            return info[side] if side in ("A", "B") else "tie"

        verdicts.append(ie.PairVerdict(
            case_id=info["case_id"], phase_label=info["phase_label"], mode=info["mode"],
            pair_key=info["pair_key"], overall=side_to_model(v["overall"]),
            axes={ax: side_to_model(v["axes"][ax]) for ax in ie.AXES}))
    agg = ie.aggregate(verdicts, ie.MODEL_KEYS)
    (OUT / "summary.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 解釈品質サマリ", "", "## 総合勝敗"]
    for m, c in agg["overall"].items():
        lines.append(f"- {m}: win={c['win']} loss={c['loss']} tie={c['tie']}")
    lines += ["", "## 軸別勝数"]
    for ax, d in agg["by_axis"].items():
        lines.append(f"- {ax}: " + ", ".join(f"{m}={n}" for m, n in d.items()))
    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT/'summary.md'}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"freeze": do_freeze, "generate": do_generate,
     "sheet": do_sheet, "aggregate": do_aggregate}.get(cmd, lambda: print(
        "usage: interp_eval_run.py freeze|generate|sheet|aggregate"))()


if __name__ == "__main__":
    main()
