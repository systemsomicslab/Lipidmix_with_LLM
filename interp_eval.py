"""解釈品質評価の純粋ロジック（Ollama 非依存・unittest で検証）。

I/O オーケストレーションは interp_eval_run.py、ケース定義は interp_eval_cases.py。
"""
from collections import Counter
from dataclasses import dataclass, field

AXES = ["hallucination", "accuracy", "completeness", "utility", "language"]
PHASE_LABELS = ["PCA", "DIFFERENTIAL", "QC", "IDENTITY", "LITERATURE"]
MODES = ["NEG", "POS"]
MODEL_KEYS = ["qwen3_off", "qwen3_on", "qwen25_7b"]


@dataclass
class ToolStep:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class Case:
    id: str
    phase_label: str
    mode: str
    query: str
    pipeline: list  # list[ToolStep]（順に実行、最後の出力を解釈対象に凍結）


@dataclass
class FrozenCase:
    id: str
    phase_label: str
    mode: str
    query: str
    tool_name: str
    tool_args: dict
    tool_output: str


def validate_cases(cases, allowed_tools):
    """ケース集合の構造を検証し、エラー文字列のリストを返す（空＝妥当）。"""
    errors = []
    if len(cases) != 10:
        errors.append(f"ケース数は10であるべき（現在 {len(cases)}）。")
    ids = [c.id for c in cases]
    for dup in [i for i, n in Counter(ids).items() if n > 1]:
        errors.append(f"ID 重複: {dup}")
    seen = set()
    for c in cases:
        if c.phase_label not in PHASE_LABELS:
            errors.append(f"未知の phase_label: {c.phase_label}（{c.id}）")
        if c.mode not in MODES:
            errors.append(f"未知の mode: {c.mode}（{c.id}）")
        key = (c.phase_label, c.mode)
        if key in seen:
            errors.append(f"(phase, mode) 重複: {key}")
        seen.add(key)
        if not c.pipeline:
            errors.append(f"pipeline が空: {c.id}")
        for step in c.pipeline:
            if step.name not in allowed_tools:
                errors.append(f"許可外ツール {step.name}（{c.id}）")
    return errors


def build_interp_messages(system_prompt, frozen):
    """凍結ケースから『ツール結果まで済んだ会話』を組み、続きに解釈だけ出させる。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": frozen.query},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": frozen.tool_name, "arguments": frozen.tool_args}}]},
        {"role": "tool", "content": frozen.tool_output, "tool_name": frozen.tool_name},
    ]
