"""解釈品質評価の手動オーケストレータ（Ollama 起動＋実データ前提）。

使い方（repo ルートから）:
    .venv-1/Scripts/python.exe interp_eval_run.py freeze
    .venv-1/Scripts/python.exe interp_eval_run.py generate
    .venv-1/Scripts/python.exe interp_eval_run.py sheet
    # ↑ で出た blind_sheet.md を審判が採点し verdicts.json を用意してから:
    .venv-1/Scripts/python.exe interp_eval_run.py aggregate
"""
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

# .env からは AZURE_OPENAI_* のみを取り込む（クラウド腕の認証情報）。同ファイルの
# LIPIDMIX_* はテンプレの未記入プレースホルダ（NAS/ダミーパス）で、取り込むと server
# import 時の mkdir が壊れるため意図的に除外する。既存の os.environ は上書きしない。
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
from interp_eval_cases import CASES

OUT = Path("interp_eval_out")
FROZEN = OUT / "frozen"
INTERP = OUT / "interp"
SYSTEM = ie.INTERP_SYSTEM

# (model_key, model/表示名, think)。azure_ 接頭のキーはクラウド腕へディスパッチ（think 不使用）。
# 実 deployment は .env の AZURE_OPENAI_DEPLOYMENT（gpt-5.4-mini-kamegai）。表示名は実体に合わせる。
MODELS = [("qwen3_off", "qwen3:14b", False),
          ("qwen3_on", "qwen3:14b", True),
          ("qwen25_7b", "qwen2.5:7b", False),
          ("azure_gpt5mini", "gpt-5.4-mini", None)]

# チャンピオン対決（分界点実測）: クラウド腕 vs ローカル最良のみを対戦させる。
CHAMPION_KEYS = ["qwen3_on", "azure_gpt5mini"]


def do_freeze():
    FROZEN.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        session_state.session = server.AnalysisSession()
        out = ""
        for step in case.pipeline:
            out = ac.execute_tool(step.name, step.args)
        # 実運用の run_turn はツール出力を _truncate(8000) してからモデルに渡すため、
        # 解釈を実挙動と一致させ、かつ文脈窓超過を防ぐため凍結側でも同じ切り詰めを適用する。
        out = ac._truncate(out)
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
            dest = INTERP / f"{fc.id}__{key}.txt"
            if dest.exists():  # 冪等: 生成済みは再生成しない（azure 追加時に既存ローカルを温存）
                print(f"skip {fc.id} / {key} (exists)")
                continue
            text = ie.generate_interp(key, model, think, msgs)
            dest.write_text(text, encoding="utf-8")
            print(f"generated {fc.id} / {key} ({len(text)} chars)")


def do_sheet(model_keys=None):
    """盲検シートを生成する。model_keys 未指定はローカル3本、CHAMPION_KEYS 指定で
    クラウド腕 vs ローカル最良のチャンピオン対決（分界点実測）。"""
    model_keys = model_keys or ie.MODEL_KEYS
    OUT.mkdir(parents=True, exist_ok=True)
    frozen = _load_frozen()
    interp = {}
    for fc in frozen:
        for key in model_keys:
            interp[(fc.id, key)] = (INTERP / f"{fc.id}__{key}.txt").read_text(encoding="utf-8")
    items, answer_key = ie.build_blind_sheet(frozen, interp, model_keys, seed=20260707)
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
            # 凍結出力は freeze で既に _truncate(8000) 済み＝モデルが見た全量。審判にも
            # 同一全量を見せる（[:4000] だと審判の根拠がモデル入力の半分になり誤判定を招く）。
            "", "### ツール結果（根拠）", "```", fc.tool_output, "```", "",
            "### 出力A", it["a_text"], "", "### 出力B", it["b_text"], "",
            "### 採点（5軸 hallucination/accuracy/completeness/utility/language ＋ overall、"
            "各 A|B|tie）", "",
        ]
    (OUT / "answer_key.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "blind_sheet.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT/'blind_sheet.md'} ({len(items)} items)")


def do_aggregate(model_keys=None):
    """verdicts を集計する。model_keys 未指定はローカル3本、CHAMPION_KEYS 指定で
    クラウド腕 vs ローカル最良のチャンピオン対決（do_sheet --champion と対）。"""
    model_keys = model_keys or ie.MODEL_KEYS
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
    agg = ie.aggregate(verdicts, model_keys)
    (OUT / "summary.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 解釈品質サマリ", "", "## 総合勝敗"]
    for m, c in agg["overall"].items():
        lines.append(f"- {m}: win={c['win']} loss={c['loss']} tie={c['tie']}")
    lines += ["", "## 軸別勝数"]
    for ax, d in agg["by_axis"].items():
        lines.append(f"- {ax}: " + ", ".join(f"{m}={n}" for m, n in d.items()))
    # フェーズ別の総合勝敗（＝分界点: どのフェーズでクラウドがローカル最良を上回るか）。
    by_phase = {}
    for v in verdicts:
        slot = by_phase.setdefault(v.phase_label, {m: 0 for m in model_keys} | {"tie": 0})
        slot[v.overall] += 1
    lines += ["", "## フェーズ別 総合"]
    for ph, d in by_phase.items():
        lines.append(f"- {ph}: " + ", ".join(f"{m}={n}" for m, n in d.items()))
    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT/'summary.md'}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    champion = "--champion" in sys.argv[2:]
    if cmd == "sheet":
        do_sheet(model_keys=CHAMPION_KEYS if champion else None)
        return
    if cmd == "aggregate":
        do_aggregate(model_keys=CHAMPION_KEYS if champion else None)
        return
    {"freeze": do_freeze, "generate": do_generate}.get(cmd, lambda: print(
        "usage: interp_eval_run.py freeze|generate|sheet [--champion]|"
        "aggregate [--champion]"))()


if __name__ == "__main__":
    main()
