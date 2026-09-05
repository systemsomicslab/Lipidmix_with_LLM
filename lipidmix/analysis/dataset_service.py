"""DatasetState を明示的に受け取る解析サービス（spec §6）。

MCP ツールも pipeline ワーカーもここを通る。ツール層はグローバルな
`session.dataset` を渡すだけ、ワーカーは自分が持つ DatasetState を渡すだけで、
**同じ関数が同じ更新規則を実行する**。実行方式によって状態の更新順が変わると、
「単体ツールでは無効化されるが pipeline では残る」ような差が生まれる。

この層の約束は 3 つ:

1. **成功してからまとめて反映する。** 前処理は途中で失敗しうる。行列だけ差し替えて
   レシピは古いまま、という半端な状態を作らない（`preprocess_dataset`）。
2. **入力が変わったら派生結果を捨てる。** 判断は `result_state.invalidate_results`
   に一本化する。
3. **結果に来歴を付ける。** 既存の戻り値のキーは一切改名せず、`provenance` を
   足すだけ（`dataset_export_differential` や図の保存が読むキーを動かさない）。
"""
from __future__ import annotations

from lipidmix.analysis import result_state
from lipidmix.analysis.dataset_analysis import (
    run_dataset_differential,
    run_dataset_pca,
    run_dataset_preprocess,
)
from lipidmix.analysis.preprocess_policy import check_applied_policy, resolve_policy
from lipidmix.analysis.sample_manifest import apply_metadata

__all__ = ["compare_dataset", "pca_dataset", "preprocess_auto", "preprocess_dataset"]


def preprocess_dataset(ds, recipe: dict, *, request_revision: int | None = None) -> dict:
    """前処理を適用し、成功したときだけ状態を差し替える。

    同値のレシピを同じ入力へ再適用した場合は**計算し直さず**、前回の結果と
    result_id をそのまま返す（派生結果も生かしたまま）。同じ計算に別の ID を
    付けると来歴が「別の実行」に見えるし、無関係な再計算を強いる。

    Returns
    -------
    dict
        `run_dataset_preprocess` の report に `provenance` を足したもの。
    """
    metadata_hash = _metadata_hash(ds)
    fingerprint = result_state.preprocess_fingerprint(ds, recipe, metadata_hash)

    current = ds.results.get(ds.preprocess_id) if ds.preprocess_id else None
    if (current is not None and ds.pp_matrix is not None
            and current["provenance"]["input_fingerprint"] == fingerprint):
        return current

    # ここから先が「新しい前処理」。計算は一時変数へ受け、全部成功してから反映する。
    (pp_matrix, pp_sample_names, pp_feature_names,
     roles, sample_meta, report) = run_dataset_preprocess(ds, recipe)

    # 前処理をやり直した以上、前の行列から出た結果は全部古い。
    result_state.invalidate_results(ds, {"recipe"})

    ds.pp_matrix = pp_matrix
    ds.pp_sample_names = pp_sample_names
    ds.pp_feature_names = pp_feature_names
    ds.roles = roles
    ds.sample_meta = sample_meta
    ds.preprocessing_recipe = dict(recipe)
    ds.preprocess_metadata_hash = metadata_hash

    result = dict(report)
    result["provenance"] = result_state.new_provenance(
        ds, kind="preprocess", input_fingerprint=fingerprint,
        effective_parameters=dict(recipe),
        warnings=list(report.get("caveats", [])),
        request_revision=request_revision)
    ds.preprocess_id = result["provenance"]["result_id"]
    return result_state.register_result(ds, result)


def preprocess_auto(ds, requested: dict, metadata: list[dict], *,
                    request_revision: int | None = None) -> dict:
    """conservative-v1でレシピを解決し、検査してから前処理をcommitする（spec §8）。

    順序は「一時計算 → 検査 → commit」を守る:

    1. `resolve_policy` でレシピを決める（``ds`` は書き換えない）。
    2. 実験情報シートを確定する（`apply_metadata`。Task 9 の一括適用契約——
       検証で1件でも落ちれば ``ds`` には触れない）。
    3. `run_dataset_preprocess` で**一時的に**計算する（``ds.pp_matrix`` 等はまだ
       書き換わらない）。
    4. `check_applied_policy` でその結果を検査する。ここで例外なら ``ds`` の
       前処理系フィールドは一切書き換わっていない（前段の metadata 確定だけが
       残る——実験情報の確定自体は前処理の成否と独立に有効な情報のため）。
    5. 検査を通ってはじめて Task 6 の `preprocess_dataset`（whole-or-nothingの
       commitは再実装せずここへ委譲する）で本commitする。
    """
    plan = resolve_policy(ds, requested, metadata)
    apply_metadata(ds, metadata)

    _, _, _, _, _, report = run_dataset_preprocess(ds, plan["resolved_recipe"])
    check_applied_policy(plan, report)

    result = preprocess_dataset(ds, plan["resolved_recipe"], request_revision=request_revision)
    plan["applied_steps"] = list(result.get("recipe_applied", []))
    plan["result"] = result
    return plan


def pca_dataset(ds, n_components: int = 5, log_transform: bool = False, *,
                request_revision: int | None = None) -> dict:
    """PCA を実行し、成功したときだけ `last_pca` を差し替える。"""
    settings = {"n_components": n_components, "log_transform": log_transform}
    result = run_dataset_pca(ds, n_components=n_components,
                             log_transform=log_transform)
    result["provenance"] = result_state.new_provenance(
        ds, kind="pca",
        input_fingerprint=_derived_fingerprint(ds, settings),
        effective_parameters=settings,
        parent_ids=[ds.preprocess_id] if ds.preprocess_id else [],
        warnings=list(result.get("caveats", [])),
        request_revision=request_revision)
    ds.last_pca = result
    return result_state.register_result(ds, result)


def compare_dataset(ds, group_a: list[str], group_b: list[str], *,
                    q_threshold: float = 0.05, log2fc_threshold: float = 1.0,
                    log_transform: bool = True,
                    group_a_label: str = "group_a",
                    group_b_label: str = "group_b",
                    request_revision: int | None = None) -> dict:
    """2 群比較を実行し、成功したときだけ `last_differential` を差し替える。

    探索専用のデータセット（中断された実行の出力）では拒否する。欠けた検体は
    mzTab 上「その群に無い」ようにしか見えず、比較はその欠落ごと結論にする。
    """
    _refuse_exploratory(ds, "2 群比較")
    settings = {
        "group_a": list(group_a), "group_b": list(group_b),
        "q_threshold": q_threshold, "log2fc_threshold": log2fc_threshold,
        "log_transform": log_transform,
        "group_a_label": group_a_label, "group_b_label": group_b_label,
    }
    result = run_dataset_differential(
        ds, group_a_samples=group_a, group_b_samples=group_b,
        q_threshold=q_threshold, log2fc_threshold=log2fc_threshold,
        log_transform=log_transform,
        group_a_label=group_a_label, group_b_label=group_b_label)
    result["provenance"] = result_state.new_provenance(
        ds, kind="differential",
        input_fingerprint=_derived_fingerprint(ds, settings),
        effective_parameters=settings,
        parent_ids=[ds.preprocess_id] if ds.preprocess_id else [],
        warnings=list(result.get("caveats", [])),
        request_revision=request_revision)
    ds.last_differential = result
    return result_state.register_result(ds, result)


def _refuse_exploratory(ds, what: str) -> None:
    """探索専用のデータセットで結論を出す操作を止める。"""
    from lipidmix.core.atomic_io import DomainError

    if getattr(ds, "exploratory_only", False):
        raise DomainError(
            "EXPLORATORY_ONLY_DATASET",
            f"このデータセットは中断された解析の出力（探索専用）なので、{what}には"
            "使えません。Console 実行を完了させてから読み込み直してください。",
            {"job_path": getattr(ds, "job_path", None),
             "source_verification": getattr(ds, "source_verification", None)})


# ---------- 内部ヘルパ ----------

def _metadata_hash(ds) -> str | None:
    """前処理に効くサンプルメタデータの指紋（明示メタデータが無ければ None）。

    明示メタデータ（実験情報シート由来）は Task 9 で `ds.sample_metadata_rows` へ
    入る。無いうちは None で、指紋はデータセットとレシピだけで決まる。
    """
    rows = getattr(ds, "sample_metadata_rows", None)
    if not rows:
        return None
    from lipidmix.core.atomic_io import canonical_hash

    fingerprints = result_state.metadata_fingerprints(rows)
    # 前処理に効く列だけを混ぜる。group を混ぜると、群を付け替えただけで
    # 前処理の指紋が変わり、無関係な再前処理を強いる。
    return canonical_hash({k: v for k, v in fingerprints.items()
                           if k in result_state.PP_FIELDS})


def _derived_fingerprint(ds, settings: dict) -> str:
    """前処理済み行列と設定から、派生結果の入力指紋を作る。"""
    from lipidmix.core.atomic_io import canonical_hash

    return canonical_hash({
        "preprocess_id": ds.preprocess_id,
        "pp_matrix": result_state.array_fingerprint(ds.pp_matrix),
        "samples": list(ds.pp_sample_names),
        "features": list(ds.pp_feature_names),
        "settings": settings,
    })
