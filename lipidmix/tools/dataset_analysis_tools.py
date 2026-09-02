"""DatasetState に対する解析 MCP ツール。

dataset_load → dataset_preprocess → dataset_pca / dataset_differential
→ dataset_export_differential の順に実行する。ARF 経路
（arf_preprocess → arf_pca_preprocessed → arf_differential →
arf_export_differential）と同じ純関数を共有しており、同じ入力からは
同じ数字が出る。

戻り値には要約だけを載せ、全量（PCA の loadings・差次的の results/volcano）は
session.dataset に保持する（CLAUDE.md の戻り値肥大禁止）。
"""
from __future__ import annotations

from mcp.types import ToolAnnotations

from lipidmix.analysis.dataset_analysis import PreconditionError
from lipidmix.core import session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.mcp_errors import missing_state, mztab_error
from lipidmix.core.serialization import json_payload

__all__ = [
    "dataset_preprocess",
    "dataset_pca",
    "dataset_differential",
    # dataset_export_differential は Task 5 で追加する
]

# 欠けている状態 → それを作れるツール。missing_state の required_tools になる。
# **自分自身は入れない**（クライアントが同じ呼び出しを繰り返すループになる）。
# Task 5 で "dataset_differential_result" を足す。
_RECOVERY_TOOLS = {
    "dataset": ["dataset_load"],
    "dataset_preprocessed": ["dataset_preprocess"],
}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_preprocess(
    normalize: str = "none",
    blank_min_fold: float | None = None,
    drift_correct: bool = False,
    max_qc_rsd: float | None = None,
    impute: str = "half_min",
) -> str:
    """DatasetState の定量行列に前処理レシピを適用する。

    引数は arf_preprocess と同一。以降の dataset_pca / dataset_differential は
    ここで作った前処理済み行列を消費する。

    normalize: "none"（既定）/ "tic"（行総和）/ "median"（行中央値）/ "pqn"。
    blank_min_fold: 生体試料平均がブランク平均のこの倍数未満の特徴量を背景として
        除去する（例 3.0）。None（既定）でブランク除去なし。
    drift_correct: QC 注入順ドリフト補正。**mzTab-M は注入順を持たないため常に
        未実施になる**（caveat で報告する）。注入順が必要なら ARF 経路を使う。
    max_qc_rsd: QC 群の RSD がこの値を超える特徴量を除去する（例 0.30）。
    impute: "half_min"（既定）/ "knn" / "column_mean" / "none"。

    成功すると session.dataset.pp_matrix に前処理済み行列が設定される。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")

    from lipidmix.analysis.dataset_analysis import run_dataset_preprocess

    recipe = {
        "normalize": normalize,
        "blank_min_fold": blank_min_fold,
        "drift_correct": drift_correct,
        "max_qc_rsd": max_qc_rsd,
        "impute": impute,
    }
    try:
        (pp_matrix, pp_sample_names, pp_feature_names,
         roles, sample_meta, report) = run_dataset_preprocess(ds, recipe)
    except PreconditionError as exc:
        return _from_precondition(exc)

    ds.pp_matrix = pp_matrix
    ds.pp_sample_names = pp_sample_names
    ds.pp_feature_names = pp_feature_names
    ds.roles = roles
    ds.sample_meta = sample_meta
    ds.preprocessing_recipe = recipe

    return json_payload({
        "status": "success",
        "n_samples": len(pp_sample_names),
        "n_features": len(pp_feature_names),
        "features_before": report.get("features_before"),
        "features_removed_total": report.get("features_removed_total"),
        "recipe_applied": report.get("recipe_applied", []),
        "excluded_from_matrix": report.get("excluded_from_matrix", {}),
        "role_counts": _count_roles(roles, pp_sample_names),
        "steps": report.get("steps", {}),
        "caveats": report.get("caveats", []),
        "next": "dataset_pca または dataset_differential を実行してください",
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_pca(n_components: int = 5, log_transform: bool = False) -> str:
    """前処理済み DatasetState に PCA を実行する。

    dataset_preprocess を先に実行しておくこと。
    n_components: 主成分数（既定 5。サンプル数・特徴量数の小さい方で上限が決まる）。
    log_transform: 標準化の前に log10 変換を適用する（既定 False）。

    ローディング全量は戻り値に載せず session.dataset.last_pca に保持する。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")

    from lipidmix.analysis.dataset_analysis import run_dataset_pca

    try:
        result = run_dataset_pca(ds, n_components=n_components, log_transform=log_transform)
    except PreconditionError as exc:
        return _from_precondition(exc)

    ds.last_pca = result
    payload = {k: v for k, v in result.items() if k != "loadings"}
    payload["status"] = "success"
    payload["loadings_note"] = (
        "ローディング全量（特徴量数 × 主成分数）は本要約に非同梱。"
        "セッションに保持しており、寄与特徴量が必要になったら別途取得します。")
    return json_payload(payload)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_differential(
    group_a: list[str],
    group_b: list[str],
    q_threshold: float = 0.05,
    log2fc_threshold: float = 1.0,
    log_transform: bool = True,
    group_a_label: str = "group_a",
    group_b_label: str = "group_b",
) -> str:
    """前処理済み DatasetState で 2 群の差次的解析（Welch t 検定 + BH-FDR）を実行する。

    group_a / group_b: サンプル名のリスト。dataset_preprocess の戻り値
        （role_counts）と dataset_status で名前を確認できる。前処理済み行列に無い
        名前、QC/ブランクは除外し、caveat で名指しする。
    q_threshold: BH-FDR 補正後の有意水準（既定 0.05）。
    log2fc_threshold: この絶対値以上の log2FC を有意として数える（既定 1.0）。
    log_transform: log2(x + 1) 空間で検定する（既定 True。MS 強度は対数正規に近い）。

    **log2FC は正なら group_b が高い（上昇）。** group_a が基準（対照）。
    全特徴量の結果と volcano 点列は戻り値に載せず session.dataset.last_differential
    に保持する。エクスポートは dataset_export_differential を使う。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")

    from lipidmix.analysis.dataset_analysis import run_dataset_differential

    try:
        result = run_dataset_differential(
            ds,
            group_a_samples=group_a,
            group_b_samples=group_b,
            q_threshold=q_threshold,
            log2fc_threshold=log2fc_threshold,
            log_transform=log_transform,
            group_a_label=group_a_label,
            group_b_label=group_b_label,
        )
    except PreconditionError as exc:
        return _from_precondition(exc)

    ds.last_differential = result

    payload = {k: v for k, v in result.items() if k not in ("results", "volcano")}
    payload["status"] = "success"
    payload["differential_contract_version"] = result["contract_version"]
    payload["results_note"] = (
        "全特徴の結果と volcano 点列は本要約に非同梱（セッション保持）。"
        "InChIKey 付きの全行が必要なら dataset_export_differential を実行してください。")
    return json_payload(payload)


# ---------- 内部ヘルパ ----------

def _missing(state: str, message: str) -> str:
    return missing_state(state, _RECOVERY_TOOLS[state], message)


def _from_precondition(exc: PreconditionError) -> str:
    """PreconditionError を kind に応じた封筒へ振り分ける。

    missing_state は「先に別のツールを呼べば直る」場合だけに使う。引数エラーを
    missing_state にすると、契約に従うクライアントが同じ呼び出しを繰り返す。
    """
    if exc.kind == "missing_state":
        return _missing(exc.state, exc.message)
    return mztab_error("DATASET_BAD_REQUEST", exc.message, exc.details or None)


def _count_roles(roles: dict, sample_names: list[str]) -> dict:
    counts: dict[str, int] = {}
    for name in sample_names:
        role = roles.get(name, "sample")
        counts[role] = counts.get(role, 0) + 1
    return counts
