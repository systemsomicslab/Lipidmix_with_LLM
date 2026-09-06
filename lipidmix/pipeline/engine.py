"""pipeline-run.v1 の工程を1回分進めるエンジン（spec §9）。

**sessionを一切importしない。** `lipidmix.core.session_state` / `lipidmix.core.mcp_core` /
`lipidmix.tools.*` はここからimportしてはいけない（`tests/test_pipeline_engine.py` が
ASTで検査する）。解析の進行状況は `pipeline-run.json`（Task14 `lipidmix.pipeline.store`）と、
このプロセスだけが持つ `runtime`（worker固有の `DatasetState` を積む素のdict）に住む。
`runtime` は絶対に永続化しない——再開後の新しいworkerプロセスは空の `runtime` から
始まる。したがって「前回成功した」という記録だけでstageを飛ばすと、後続stageが
参照する `runtime` の中身が無いまま呼ばれる事故になる。`stage_inputs_unchanged` が
常に `False` を返すのはこの事故を避けるためで、実際の依存fingerprintによる
再利用判定（＝再開時の再構築）はTask16の責務として本関数を差し替える。

**handlerキーとstage_idは別物。** `differential:<comparison_id>` / `export:<comparison_id>`
という複数のstage_idは、`differential` / `export` という**1つのhandlerキー**を共有し、
`comparison_id` はstage_idから機械的に取り出して context へ渡す（`build_stages` が
そのマッピングを組み立てる）。

**例外はStageResultの中でだけ表現する。** handler呼出しは必ずtry/exceptで囲み、
`DomainError` のうち「入力で解消する」codeだけを `needs_input` へ変換する
（`_NEEDS_INPUT_CODES`）。それ以外の `DomainError` および他の例外は
tracebackをログへ残した上で `failed` の `StageResult` に変換する——
どちらの経路でも「例外が成功結果に化ける」ことは絶対にない。

**owner lockとstate更新lockは別ファイル。** `lipidmix.core.process_control.file_lock`
は同一ファイルへの新しい `os.open` ごとに独立したロック要求として扱われ、
同一プロセス内の入れ子取得でも自己デッドロック（timeoutまで待って失敗）する
（`lipidmix/pipeline/store.py` 冒頭の説明と同じ注意）。そこで:

- **owner lock**（`control/worker.lock`）: `run_engine` がstage loop全体の間
  ずっと保持する「このpipelineを今実行しているのは自分だけだ」という主張。
  取得に失敗したら即座に `PIPELINE_ALREADY_RUNNING`（長時間待たない——
  待つ理由がない。取れなければ本当に別workerが動いている）。
- **state更新lock**（`control/pipeline.lock`、Task14 `store.save_run` が
  呼出しごとに取る）: stage境界の1回の保存だけを直列化する短いロック。
  ワーカーがowner lockを握っている間も、`pipeline_status`（読取専用）や
  取消要求の保存はこちらを使うだけで進められる——owner lockの解放を待たない。
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lipidmix.core.atomic_io import DomainError
from lipidmix.core.process_control import file_lock
from lipidmix.pipeline import store

__all__ = [
    "build_stages",
    "cancel_request_path",
    "cancel_requested",
    "commit_stage_outcome",
    "finish_cancelled",
    "finish_interrupted",
    "finish_success",
    "make_context",
    "mark_stage_running",
    "run_engine",
    "stage_inputs_unchanged",
]

_logger = logging.getLogger(__name__)

#: brief記載の4コード。共通context「Task11/Task12」で名指しされた、入力を
#: 与え直せば先へ進める（＝ユーザー操作で解消しうる）DomainErrorだけをここに置く。
#: PCA不成立（"an uncomputable PCA"）はbrief/spec文言上は同格に挙げられているが、
#: 現行実装（`lipidmix/analysis/dataset_analysis.py::run_dataset_pca`）は
#: `DomainError` ではなく別クラスの `PreconditionError` を送出しており、この
#: whitelistでは検出できない——report「懸念」節に記載。
_NEEDS_INPUT_CODES = frozenset({
    "PREPROCESS_PREREQUISITE_MISSING",  # Task11: 前提を欠く明示的な前処理要求
    "NORMALIZATION_DEGENERATE",          # Task11: 正規化係数が0/非有限の試料が残る
    "PREPROCESS_FEATURES_EXHAUSTED",     # Task11: 前処理後に特徴量が0件
    "COMPARISON_REQUIRED",               # Task12: 比較の対照群/比較群が未指定
})

#: owner lockの取得タイムアウト（秒）。長時間待つ理由がない——待っても取れない
#: ということは、本当に別workerがこのpipelineを実行中だということ。
_OWNER_LOCK_TIMEOUT_S = 1.0
_OWNER_LOCK_FILENAME = "worker.lock"

#: 取消の協調フラグ。`request_cancel`（Task16 `lipidmix.pipeline.recovery`）が
#: 書き、ここではその置き場所の契約と読み手だけを持つ。state更新lockと同じく、
#: owner lockの解放を待たずに書ける小さな独立ファイルにする。
_CANCEL_REQUEST_FILENAME = "cancel-request.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _owner_lock_path(pipeline_path: Path) -> Path:
    return Path(pipeline_path) / store.CONTROL_SUBDIR / _OWNER_LOCK_FILENAME


def cancel_request_path(pipeline_path: Path) -> Path:
    """取消フラグの置き場所（Task16 `request_cancel` と読み手を1か所で揃える）。"""
    return Path(pipeline_path) / store.CONTROL_SUBDIR / _CANCEL_REQUEST_FILENAME


def cancel_requested(pipeline_path: Path) -> bool:
    """協調的な取消要求が保存済みかを返す（owner lockを一切取らない読取り専用）。

    `atomic_write_json` は置換が原子的なので、ロック無しで読んでも「書きかけの
    中途半端な内容」を見ることはない（見えるのは直前の内容か、書き終わった
    新しい内容のどちらか）。
    """
    path = cancel_request_path(pipeline_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    return bool(data.get("cancel_requested"))


# ---------- stage計画 ----------

def _comparison_id_from_stage_id(stage_id: str) -> str | None:
    for prefix in ("differential:", "export:"):
        if stage_id.startswith(prefix):
            return stage_id[len(prefix):]
    return None


def build_stages(request: dict) -> list[dict]:
    """要求から、このrunが実行すべきstageの計画を組み立てる。

    各要素は `{"stage_id", "handler", "comparison_id"}`。`differential:<id>` /
    `export:<id>` はどちらも1件のcomparisonに対応するstageで、handlerキーは
    それぞれ `"differential"` / `"export"` の1つだけを共有する
    （`comparison_id` で呼出し先の区別を渡す）。

    `effective_target == "exploratory"` のときは `resolve_comparisons` を計画から
    除く（spec §9.2「比較群を要求しない」）——`store.create_run` が作る
    `record["stages"]` にはtargetを問わず常にこのstage_idが存在するため
    （`store._stage_ids_for` はtargetを見ない）、計画に無いstage_idは
    `run_engine` 側で「対象外」として扱う（handlerを呼ばずskip）。

    stage_id集合そのものの計算（`_BASE_STAGE_IDS` の並び・
    `differential:`/`export:` の追加条件）は `store._stage_ids_for` と
    意図的に同じ規則にしてある——両者がズレると `record["stages"]` に無い
    stage_idを計画してしまい、`run_engine` が `KeyError` で落ちる。
    """
    effective_target = request.get("effective_target") or request.get("target")
    comparisons = request.get("comparisons") or []

    plan: list[dict] = []
    for stage_id in store.STAGE_IDS[:-1]:  # "report"を除いた基本8種
        if stage_id == "resolve_comparisons" and effective_target == "exploratory":
            continue
        plan.append({"stage_id": stage_id, "handler": stage_id, "comparison_id": None})

    if effective_target != "exploratory":
        for comparison in comparisons:
            cid = comparison["comparison_id"]
            plan.append({"stage_id": f"differential:{cid}", "handler": "differential",
                        "comparison_id": cid})
            plan.append({"stage_id": f"export:{cid}", "handler": "export",
                        "comparison_id": cid})

    final_stage_id = store.STAGE_IDS[-1]  # "report"
    plan.append({"stage_id": final_stage_id, "handler": final_stage_id, "comparison_id": None})
    return plan


# ---------- ループ内helper（brief step3） ----------

def stage_inputs_unchanged(stage: dict) -> bool:
    """既存の成功/skip結果をそのまま使ってよいか（＝handlerを呼ばず飛ばせるか）。

    Task15時点では常に `False`。理由: `runtime`（worker固有の `DatasetState` 置き場）
    はプロセス内限定で絶対に永続化されない。もしここで「前回のfingerprintが
    記録済みだから」というpersisted状態だけの判断でskipを許すと、再開後の
    新しいworkerプロセスでは対応するhandlerが一度も呼ばれないまま、後続stageが
    `runtime` に無いはずの `DatasetState` を参照する事故につながる（brief
    「メモリ上に存在しないDatasetStateを工程skipだけで利用しない」）。
    実際の依存fingerprint照合による再利用判定は、再構築（loadingからの
    再実行によるruntime復元）を併せ持つTask16がこの関数を差し替える。
    """
    return False


def make_context(record: dict, stage: dict, runtime: dict) -> dict:
    """handlerへ渡すstage contextを組み立てる。

    `runtime` はこのworkerプロセスだけが持つ素のdict（例: `runtime["dataset"]`に
    `DatasetState` を積む）への参照そのものを渡す——handler間で状態を共有するのは
    同一worker内のこの1呼出し系列だけ、という契約をそのまま体現する。
    """
    stage_id = stage["stage_id"]
    return {
        "pipeline_root": Path(record["identity"]["pipeline_root"]),
        "pipeline_id": record["identity"]["pipeline_id"],
        "stage_id": stage_id,
        "comparison_id": _comparison_id_from_stage_id(stage_id),
        "attempt": stage.get("attempt", 0),
        "request": dict(record["request"]),
        "results": list(record.get("results") or []),
        "runtime": runtime,
    }


def mark_stage_running(pipeline_path: Path, record: dict, stage: dict) -> dict:
    """stageを`running`にして原子的に保存し、再読込した最新recordを返す。

    毎回 `save_run` 後に `load_run` で読み直す（`save_run` は渡した引数の
    dictを書き換えず、内部で深複製した別dictへ`state_revision`/`updated_at`を
    刻んで書くため、呼び出し側が手で追随するより読み直す方が確実）。
    """
    record = copy.deepcopy(record)
    stage_id = stage["stage_id"]
    target = record["stages"][stage_id]
    now = _now_iso()
    target["status"] = "running"
    target["attempt"] = int(target.get("attempt") or 0) + 1
    target["started_at"] = now
    target["updated_at"] = now
    target["error"] = None
    record["status"] = "running"
    store.save_run(pipeline_path, record, expected_revision=record["state_revision"])
    return store.load_run(pipeline_path)


def commit_stage_outcome(pipeline_path: Path, record: dict, stage: dict, outcome: dict) -> dict:
    """StageResultをstageと`results`へ反映して原子的に保存する。

    `outcome["result_refs"]` は無条件に `record["results"]`（append-only、
    `store.save_run` が既存要素の書換えを拒否する）へ追記する——failed/needs_input
    でも、そのstageが実際に有効な出力を残していたなら「有効な解析出力があるか」
    （`finish_interrupted` のpartial/failed判定）の材料になる。
    """
    record = copy.deepcopy(record)
    stage_id = stage["stage_id"]
    target = record["stages"][stage_id]
    target["status"] = outcome.get("status")
    target["updated_at"] = _now_iso()
    target["result_refs"] = list(outcome.get("result_refs") or [])
    target["warnings"] = list(outcome.get("warnings") or [])
    target["error"] = outcome.get("error")

    result_refs = outcome.get("result_refs") or []
    if result_refs:
        record["results"] = list(record.get("results") or []) + list(result_refs)

    for warning in outcome.get("warnings") or []:
        entry = dict(warning)
        entry.setdefault("stage_id", stage_id)
        record.setdefault("warnings", [])
        record["warnings"].append(entry)

    store.save_run(pipeline_path, record, expected_revision=record["state_revision"])
    return store.load_run(pipeline_path)


def _mark_out_of_scope(pipeline_path: Path, record: dict, stage_id: str) -> dict:
    """計画に無いstage（exploratory目標の`resolve_comparisons`）を対象外として記録する。

    「stageが無い＝completedへ数える」を避けるため、`skipped`のまま明示的に
    理由をwarningsへ残す（spec「stageがないからcompletedとしない」の裏返しで、
    こちらは「対象外だからhandlerを呼ばずに進める」ことを可視化する）。
    """
    record = copy.deepcopy(record)
    stage = record["stages"][stage_id]
    if stage["status"] == "skipped":
        return record  # 既に反映済み（run_engineを繰り返し呼んでも増殖させない）
    stage["status"] = "skipped"
    stage["updated_at"] = _now_iso()
    stage["warnings"] = list(stage.get("warnings") or []) + [{
        "code": "STAGE_OUT_OF_SCOPE_FOR_TARGET",
        "stage_id": stage_id,
        "message": f"effective_targetにより対象外のためskipしました: {stage_id}",
    }]
    store.save_run(pipeline_path, record, expected_revision=record["state_revision"])
    return store.load_run(pipeline_path)


def finish_success(pipeline_path: Path, record: dict) -> dict:
    record = copy.deepcopy(record)
    record["status"] = "completed"
    store.save_run(pipeline_path, record, expected_revision=record["state_revision"])
    return store.load_run(pipeline_path)


def finish_interrupted(pipeline_path: Path, record: dict, outcome: dict) -> dict:
    """needs_input/failedで停止する。

    needs_inputはそのままneeds_input。failedは「有効な解析出力
    （`record['results']`が非空）」があればpartial、無ければfailedへ格下げする
    （brief「有効な解析出力があればpartial、なければfailed」）。
    """
    record = copy.deepcopy(record)
    if outcome.get("status") == "needs_input":
        record["status"] = "needs_input"
        error = outcome.get("error") or {}
        record["needs_input"] = {
            "code": error.get("code"),
            "stage_id": outcome.get("stage_id"),
            "message": error.get("message"),
            "details": error.get("details") or {},
        }
    else:
        record["status"] = "partial" if record.get("results") else "failed"
    store.save_run(pipeline_path, record, expected_revision=record["state_revision"])
    return store.load_run(pipeline_path)


def finish_cancelled(pipeline_path: Path, record: dict) -> dict:
    record = copy.deepcopy(record)
    record["status"] = "cancelled"
    store.save_run(pipeline_path, record, expected_revision=record["state_revision"])
    return store.load_run(pipeline_path)


# ---------- handler呼出し ----------

def _domain_error_payload(exc: DomainError) -> dict:
    return {"code": exc.code, "message": exc.message, "details": dict(exc.details)}


def _invoke_handler(handler: Callable[[dict], dict], context: dict) -> dict:
    """handlerを呼び、例外を成功結果へ絶対に変換しないStageResultへ落とす。"""
    try:
        outcome = handler(context)
    except DomainError as exc:
        if exc.code in _NEEDS_INPUT_CODES:
            return {"status": "needs_input", "result_refs": [], "warnings": [],
                    "error": _domain_error_payload(exc)}
        _logger.exception("pipeline stage %s: 入力解消不可のDomainError",
                          context.get("stage_id"))
        return {"status": "failed", "result_refs": [], "warnings": [],
                "error": _domain_error_payload(exc)}
    except Exception as exc:  # noqa: BLE001 - 未知例外を成功へ変換しないための意図的な全捕捉
        _logger.exception("pipeline stage %s: 未知の例外", context.get("stage_id"))
        return {"status": "failed", "result_refs": [], "warnings": [],
                "error": {"code": exc.__class__.__name__, "message": str(exc), "details": {}}}

    outcome = dict(outcome)
    outcome.setdefault("result_refs", [])
    outcome.setdefault("warnings", [])
    outcome.setdefault("error", None)
    return outcome


# ---------- 本体 ----------

def _load_request(pipeline_path: Path, record: dict) -> dict:
    """`record["request"]["saved_path"]` の完全なpipeline-request.v1を読む。

    `record["request"]` 自体はrevision/request_id/content_hash/saved_path/
    effective_targetだけを持つ要約（`store.create_run` 参照）で、
    `target`/`comparisons` 等の本体は別ファイルにある。
    """
    saved_path = record["request"]["saved_path"]
    full_path = Path(pipeline_path) / saved_path
    return json.loads(full_path.read_text(encoding="utf-8"))


def _run_stage_loop(pipeline_path: Path, handlers: dict) -> dict:
    record = store.load_run(pipeline_path)
    request = _load_request(pipeline_path, record)
    plan = {entry["stage_id"]: entry for entry in build_stages(request)}
    runtime: dict = {}

    for stage_id in list(record["stages"].keys()):
        entry = plan.get(stage_id)
        if entry is None:
            record = _mark_out_of_scope(pipeline_path, record, stage_id)
            continue

        stage = record["stages"][stage_id]
        if stage["status"] in {"succeeded", "skipped"} and stage_inputs_unchanged(stage):
            continue

        if cancel_requested(pipeline_path):
            return finish_cancelled(pipeline_path, record)

        record = mark_stage_running(pipeline_path, record, stage)
        stage = record["stages"][stage_id]
        context = make_context(record, stage, runtime)
        outcome = _invoke_handler(handlers[entry["handler"]], context)
        record = commit_stage_outcome(pipeline_path, record, stage, outcome)

        if outcome["status"] in {"needs_input", "failed"}:
            return finish_interrupted(pipeline_path, record, {**outcome, "stage_id": stage_id})

    return finish_success(pipeline_path, record)


def run_engine(pipeline_path: Path, handlers: dict[str, Callable[[dict], dict]]) -> dict:
    """永続化済みrunを、必要な工程がなくなるかstageが止まるまで1回分進める。

    `pipeline_path` は `store.create_run`/`find_or_create_run` が返す
    pipeline_root。戻り値は保存後に読み直した最新の `pipeline-run.json`。

    owner lock（`control/worker.lock`）をstage loop全体の間ずっと保持する。
    取得できなければ即 `PIPELINE_ALREADY_RUNNING`（別workerが実行中、または
    このプロセス自身の入れ子呼出し——`file_lock` は同一プロセスからの
    入れ子取得も別ロック要求として扱うため区別できない。区別する必要も無い:
    どちらの場合も「今は進めてはいけない」という結論は同じ）。

    ロック取得の成否だけを判定するために `__enter__`/`__exit__` を手で呼ぶ
    （`with`一つで両方を包むと、stage loop本体の中で別の場所——例えば
    `store.save_run` 自身の `control/pipeline.lock` 待ち——が`LOCK_TIMEOUT`に
    なった場合まで`PIPELINE_ALREADY_RUNNING`に化けてしまう。それは誤り:
    state更新lockの競合は「別workerが実行中」ではなく単なる一時的な混雑）。
    """
    pipeline_path = Path(pipeline_path)
    owner_lock = file_lock(_owner_lock_path(pipeline_path), timeout=_OWNER_LOCK_TIMEOUT_S)
    try:
        owner_lock.__enter__()
    except DomainError as exc:
        if exc.code == "LOCK_TIMEOUT":
            raise DomainError(
                "PIPELINE_ALREADY_RUNNING",
                f"このpipelineは既に別のworkerが実行中です: {pipeline_path}",
                {"pipeline_root": str(pipeline_path)},
            ) from exc
        raise
    try:
        return _run_stage_loop(pipeline_path, handlers)
    finally:
        owner_lock.__exit__(None, None, None)
