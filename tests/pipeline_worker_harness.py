"""lipidmix.pipeline.engine を実プロセスとして検証するための試験専用harness。

production worker（`lipidmix/pipeline/worker.py`）はTask18の `build_handlers` が
できるまで実handlerを呼べない。ここでは「実プロセスとして起動されて初めて
確認できる」振る舞い（owner lockの実効性、CLI引数からpipeline_pathを受け取って
`run_engine` を最後まで回せること）を、注入した合成fake handlerで検証するためだけに
存在する。`lipidmix.console.worker` の `--console-arg`（実Consoleの代わりに偽コマンドを
起動する試験専用注入口）と同じ位置付けで、本番からは決して呼ばれない
——importせずスクリプトパスとして`python <このファイル>`で起動する
（`tests/fixtures/fake_console.py` と同じ流儀）。

使い方:
    python tests/pipeline_worker_harness.py --pipeline <pipeline_root>
        [--sleep-stage <stage_id> --sleep-seconds <n>]

すべてのhandlerは「呼ばれたら成功する」合成版。`--sleep-stage` を指定すると、
そのstage_idのhandlerだけ指定秒だけ待ってから成功する（owner lockが実際に
保持され続けることを、2プロセス目の起動タイミングと組み合わせて確認する用途）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lipidmix.core.atomic_io import DomainError  # noqa: E402
from lipidmix.pipeline.engine import run_engine  # noqa: E402

#: build_stagesが組み立てうるhandlerキーの全量（stage_idではなくhandlerキー単位）。
_HANDLER_KEYS = (
    "prepare_input", "upstream", "validate_outputs", "load_dataset",
    "resolve_metadata", "preprocess", "pca", "resolve_comparisons",
    "differential", "export", "report",
)

#: handlerキー → 登録するoutput_name一覧（Task18: `lipidmix.pipeline.report.
#: evaluate_target`がhash照合込みで`output_name`付きrefだけを「達成」と数える
#: ため、文字列ダミーのrefでは`finish_success`が常にfailed/partialへ落ちる。
#: 本harnessが検証する対象はengine自体（stage順序・owner lock）で、Task18の
#: 実handler契約と揃えるためだけにこの最小限のマッピングを持つ）。
_OUTPUT_NAMES = {
    "preprocess": ("preprocess",),
    "pca": ("pca", "pca_figure"),
    "report": ("quality_report",),
}


def _persist(pipeline_root, name: str) -> dict:
    from lipidmix.pipeline.report import persist_result
    return persist_result(pipeline_root, {
        "output_name": name, "kind": "synthetic",
        "result_id": f"{name.replace(':', '_')}-result",
        "data": {"synthetic": True, "name": name},
    })


def _build_handlers(sleep_stage: str | None, sleep_seconds: float) -> dict:
    def make(name):
        def handler(context: dict) -> dict:
            if sleep_stage is not None and context["stage_id"] == sleep_stage and sleep_seconds > 0:
                time.sleep(sleep_seconds)
            if name == "upstream":
                return {"status": "succeeded", "result_refs": [], "warnings": [], "error": None,
                        "record_updates": {"upstream": {
                            "console_job_path": None, "execution_id": "exec-fake",
                            "verification": {"status": "completed"}}}}
            if name == "differential":
                cid = context["comparison_id"]
                ref = _persist(context["pipeline_root"], f"differential:{cid}")
                return {"status": "succeeded", "result_refs": [ref], "warnings": [], "error": None}
            if name == "export":
                cid = context["comparison_id"]
                refs = [_persist(context["pipeline_root"], f"volcano:{cid}"),
                        _persist(context["pipeline_root"], f"tsv:{cid}")]
                return {"status": "succeeded", "result_refs": refs, "warnings": [], "error": None}
            names = _OUTPUT_NAMES.get(name, ())
            refs = [_persist(context["pipeline_root"], out_name) for out_name in names]
            return {"status": "succeeded", "result_refs": refs, "warnings": [], "error": None}
        return handler

    return {key: make(key) for key in _HANDLER_KEYS}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline_worker_harness",
        description="run_engineを合成fake handlerで実プロセスとして起動する（試験専用）。")
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--sleep-stage", dest="sleep_stage", default=None)
    parser.add_argument("--sleep-seconds", dest="sleep_seconds", type=float, default=0.0)
    args = parser.parse_args(argv)

    handlers = _build_handlers(args.sleep_stage, args.sleep_seconds)
    try:
        result = run_engine(Path(args.pipeline), handlers)
    except DomainError as exc:
        print(json.dumps({"error_code": exc.code, "message": exc.message}))
        return 1
    print(json.dumps({"status": result.get("status")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
