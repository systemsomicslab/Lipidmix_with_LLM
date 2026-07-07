"""高価値ツールの最終 payload 実サイズを測り、8000字 truncate に埋没するか判定する。

実行: PYTHONPATH=<proj> .venv-1/Scripts/python.exe tools/measure_payloads.py
実データ（interp_eval_cases の DIR_NEG / DIR_POS）が必要。
"""
import sys
from agent_core import execute_tool, _truncate
from interp_eval_cases import CASES

LIMIT = 8000


def main() -> int:
    buried = []
    for case in CASES:
        last_out = None
        for step in case.pipeline:
            last_out = execute_tool(step.name, step.args)
        n = len(last_out)
        after = len(_truncate(last_out))
        flag = "BURIED" if n > LIMIT else "ok"
        if n > LIMIT:
            buried.append(case.id)
        print(f"{case.id:20s} phase={case.phase_label:12s} "
              f"chars={n:7d} after_trunc={after:6d} {flag}")
    if buried:
        print(f"\nFAIL: {len(buried)} 件が 8000字を超過: {buried}")
        return 1
    print("\nOK: 全高価値ケースが 8000字以内（BURIED なし）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
