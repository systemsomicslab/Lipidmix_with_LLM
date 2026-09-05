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

import math
from pathlib import Path

from mcp.types import ToolAnnotations

from lipidmix.analysis import export_contract
from lipidmix.analysis.dataset_analysis import PreconditionError
from lipidmix.core import session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.mcp_errors import missing_state, mztab_error
from lipidmix.core.serialization import json_payload

__all__ = [
    "dataset_preprocess",
    "dataset_pca",
    "dataset_differential",
    "dataset_export_differential",
]

# 欠けている状態 → それを作れるツール。missing_state の required_tools になる。
# **自分自身は入れない**（クライアントが同じ呼び出しを繰り返すループになる）。
_RECOVERY_TOOLS = {
    "dataset": ["dataset_load"],
    "dataset_preprocessed": ["dataset_preprocess"],
    "dataset_differential_result": ["dataset_differential"],
}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_preprocess(
    normalize: str = "none",
    blank_min_fold: float | None = None,
    drift_correct: bool = False,
    max_qc_rsd: float | None = None,
    impute: str = "half_min",
    min_detection_rate: float = 0.0,
) -> str:
    """DatasetState の定量行列に前処理レシピを適用する。

    引数は arf_preprocess と同一。以降の dataset_pca / dataset_differential は
    ここで作った前処理済み行列を消費する。

    normalize: "none"（既定）/ "tic"（行総和）/ "median"（行中央値）/ "pqn"。
    blank_min_fold: 生体試料平均がブランク平均のこの倍数未満の特徴量を背景として
        除去する（例 3.0）。None（既定）でブランク除去なし。
    drift_correct: QC 注入順ドリフト補正。注入順は mzTab-M の
        `assay[N]-custom[...]` の injection sequence label（MS:4000089）から読む。
        それを持たない mzTab-M では実施できず、caveat で報告する。
    max_qc_rsd: QC 群の RSD がこの値を超える特徴量を除去する（例 0.30）。
    impute: "half_min"（既定）/ "knn" / "column_mean" / "none"。
    min_detection_rate: 実検出率（gap-fill を除く）による特徴量の足切り 0.0-1.0
        （既定 0.0=無効）。**検出状態は mzTab-M 単体には無い**ので、隣接する `.arf`
        から取り込めた場合にだけ使える。取り込めていない状態で 0 より大きい値を
        渡すと引数エラーを返す（黙って未検出 0 件として通さない）。
        取り込み状況は dataset_status の `detection` を見る。

    成功すると session.dataset.pp_matrix に前処理済み行列が設定される。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")

    from lipidmix.analysis.dataset_service import preprocess_dataset

    recipe = {
        "normalize": normalize,
        "blank_min_fold": blank_min_fold,
        "drift_correct": drift_correct,
        "max_qc_rsd": max_qc_rsd,
        "impute": impute,
        "min_detection_rate": min_detection_rate,
    }
    try:
        # 状態の差し替え（成功時のみの反映・派生結果の無効化・来歴の付与）は
        # サービス側が持つ。ツールはそれを呼んで要約を返すだけにする——
        # ここで直接 ds を書き換えると、pipeline ワーカー経由の実行と規則がずれる。
        report = preprocess_dataset(ds, recipe)
    except PreconditionError as exc:
        return _from_precondition(exc)

    return json_payload({
        "status": "success",
        "result_id": report["provenance"]["result_id"],
        "n_samples": len(ds.pp_sample_names),
        "n_features": len(ds.pp_feature_names),
        "features_before": report.get("features_before"),
        "features_removed_total": report.get("features_removed_total"),
        "recipe_applied": report.get("recipe_applied", []),
        "excluded_from_matrix": report.get("excluded_from_matrix", {}),
        "role_counts": _count_roles(ds.roles, ds.pp_sample_names),
        "steps": report.get("steps", {}),
        "caveats": report.get("caveats", []),
        "next": "dataset_pca または dataset_differential を実行してください",
        **({"detection": report["detection"]} if "detection" in report else {}),
        **({"detection_filter": report["detection_filter"]}
           if "detection_filter" in report else {}),
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

    from lipidmix.analysis.dataset_service import pca_dataset

    try:
        result = pca_dataset(ds, n_components=n_components, log_transform=log_transform)
    except PreconditionError as exc:
        return _from_precondition(exc)

    payload = {k: v for k, v in result.items()
               if k not in ("loadings", "provenance")}
    payload["status"] = "success"
    payload["result_id"] = result["provenance"]["result_id"]
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

    group_a / group_b: サンプル名のリスト。使える名前と役割は dataset_status の
        `samples`（name/role の TSV）で確認する。前処理済み行列に無い
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

    from lipidmix.analysis.dataset_service import compare_dataset

    from lipidmix.core.atomic_io import DomainError

    try:
        result = compare_dataset(
            ds,
            group_a,
            group_b,
            q_threshold=q_threshold,
            log2fc_threshold=log2fc_threshold,
            log_transform=log_transform,
            group_a_label=group_a_label,
            group_b_label=group_b_label,
        )
    except PreconditionError as exc:
        return _from_precondition(exc)
    except DomainError as exc:
        return mztab_error(exc.code, exc.message, exc.details or None)

    payload = {k: v for k, v in result.items()
               if k not in ("results", "volcano", "provenance")}
    payload["status"] = "success"
    payload["result_id"] = result["provenance"]["result_id"]
    payload["differential_contract_version"] = result["contract_version"]
    payload["results_note"] = (
        "全特徴の結果と volcano 点列は本要約に非同梱（セッション保持）。"
        "InChIKey 付きの全行が必要なら dataset_export_differential を実行してください。")
    return json_payload(payload)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True),
    structured_output=False)
def dataset_export_differential(output_path: str) -> str:
    """直近の差次的結果を InChIKey 付きの 1 ファイルへ書き出す。

    先に dataset_preprocess → dataset_differential を実行しておくこと。
    出力は arf_export_differential と**同一の契約**（15 列 + contract_version
    メタ行）なので、下流のパスウェイ解析にそのまま渡せる。

    InChIKey は DatasetState.feature_metadata から取る（mzTab-M の
    database_identifier / InChI / SMILES 由来。.arf2 との結合は不要）。
    濃縮解析の背景を保つため、有意な行だけでなく InChIKey が付いた全行を出す。
    ontology と msi_level は mzTab-M に対応物が無いため空欄で、その旨をメタ行に
    書く（空欄を「該当なし」と読み違えさせない）。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")
    last = ds.last_differential
    if not last or last.get("kind") != "two_group":
        return _missing(
            "dataset_differential_result",
            "先に dataset_preprocess → dataset_differential（2群）を実行してください。")
    if (last.get("contract_version") != export_contract.CONTRACT_VERSION
            or last.get("log2fc_sign") != export_contract.LOG2FC_SIGN):
        return _missing(
            "dataset_differential_result",
            "直近の差次的結果は現行エクスポート契約と互換性がありません。"
            "dataset_differential を再実行してください。")

    if getattr(ds, "exploratory_only", False):
        return mztab_error(
            "EXPLORATORY_ONLY_DATASET",
            "このデータセットは中断された解析の出力（探索専用）なので、"
            "差次的エクスポートには使えません。Console 実行を完了させてから"
            "読み込み直してください。",
            {"job_path": ds.job_path,
             "source_verification": getattr(ds, "source_verification", None)})

    from lipidmix.analysis.result_state import is_current
    if not is_current(ds, last):
        # 前処理をやり直した後の古い結果。書き出すと、TSV の数字と現在の
        # 前処理済み行列が別物になる（読み手には見分けがつかない）。
        return _missing(
            "dataset_differential_result",
            "直近の差次的結果は現在の前処理から出たものではありません。"
            "dataset_differential を再実行してください。")

    q_threshold = last["q_threshold"]
    log2fc_threshold = last["log2fc_threshold"]

    rows: list[dict] = []
    n_unannotated = 0
    for result in last["results"]:
        fid = result["feature"]
        meta = ds.feature_metadata.get(fid, {})
        inchikey = str(meta.get("inchikey") or "").strip()
        if not inchikey:
            n_unannotated += 1
            continue
        rows.append({
            "spot_id": fid,                     # mzTab-M の SMF_ID（メタ行で id_space を宣言）
            "name": (meta.get("name") or "").strip(),
            "name_source": "mztab_smf",
            "ontology": "",                     # mzTab-M に対応物なし
            "inchikey": inchikey,
            "inchikey_source": meta.get("inchikey_source") or "",
            "msi_level": None,                  # 同上（.arf2 由来の注釈確度が無い）
            "mz": meta.get("mz"),
            "rt": meta.get("rt"),
            "log2fc": result.get("log2fc"),
            "p_value": result.get("p"),
            "q_value": result.get("q"),
            "mean_a": result.get("mean_a"),
            "mean_b": result.get("mean_b"),
            "significant": export_contract.is_significant(
                q=result.get("q"), log2fc=result.get("log2fc"),
                q_threshold=q_threshold, log2fc_threshold=log2fc_threshold),
        })

    n_total = len(last["results"])
    if not rows:
        return json_payload({
            "status": "error",
            "message": ("InChIKey が付いた特徴が 0 件のため書き出しません。"
                        "下流のパスウェイ解析に使える背景集合がありません。"),
            "n_features_total": n_total,
            "n_with_inchikey": 0,
            "n_unannotated": n_unannotated,
        })

    meta_lines = export_contract.build_meta(
        group_a=last["a"], n_a=last["n_a"],
        group_b=last["b"], n_b=last["n_b"],
        q_threshold=q_threshold, log2fc_threshold=log2fc_threshold,
        log_transform=last.get("log_transform"),
        n_features_total=n_total, n_with_inchikey=len(rows),
        n_unannotated=n_unannotated,
        # mzTab-M に .arf2 由来の注釈確度が無いことを、空欄の意味とあわせて宣言する。
        msi_note=("# ontology / msi_level は mzTab-M に対応物が無いため空欄"
                  "（『該当なし』ではなく『この経路では取得していない』）"),
        source_lines=[
            f"# source_mztab = {'; '.join(ds.source_files) or ''}",
            f"# source_job = {ds.job_path or ''}",
            "# id_space = mztab_smf_id",
            f"# result_id = {(last.get('provenance') or {}).get('result_id', '')}",
            f"# preprocess_id = {ds.preprocess_id or ''}",
        ],
        preprocess_line=f"# preprocess = {ds.preprocessing_recipe}",
    )
    lines = [*meta_lines, "\t".join(export_contract.EXPORT_COLUMNS)]
    lines += [export_contract.format_row(r) for r in rows]

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return json_payload({
        "status": "success",
        "output_path": str(out),
        "contract_version": export_contract.CONTRACT_VERSION,
        "group_a": last["a"],
        "group_b": last["b"],
        "n_features_total": n_total,
        "n_with_inchikey": len(rows),
        "n_unannotated": n_unannotated,
        "log2fc_sign": "log2fc は正なら group_b が高い（上昇）。",
        "note": ("n_unannotated は InChIKey が付かず書き出さなかった行数です。"
                 "「変化が無かった」ではなく「調べていない」行です。"),
    })


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
