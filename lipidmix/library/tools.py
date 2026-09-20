"""参照ライブラリ（MS/MS スペクトル照合）の MCP ツール群。

下の層（`lipidmix.library.dbs` / `.msp` / `.store`、`lipidmix.analysis.spectral_match`、
`lipidmix.plots.mirror`）はすべて完成済み。ここはそれらを MCP の公開面へ繋ぐだけの層で、
`dcl/tools.py` と同じ流儀（`missing_state` 封筒・`json_payload`・TSV 一覧・
`structured_output=False`）に揃える。

deps: mcp_core / session_state / mcp_errors / path_resolvers / library.{dbs,msp,store} /
analysis.spectral_match / plots.{mirror,render} / dcl.reader / arf2.reader
（`format_spots_as_table` の借用のみ）。server は import しない。
"""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from lipidmix.analysis.spectral_match import match_spectrum
from lipidmix.arf2.reader import format_spots_as_table
from lipidmix.core import mcp_errors, session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.path_resolvers import resolve_dcl_file_path, resolve_library_path
from lipidmix.core.serialization import json_payload, round_floats
from lipidmix.dcl.reader import deserialize_dcl, get_msms_by_precursor
from lipidmix.library import dbs as dbs_reader
from lipidmix.library import msp as msp_reader
from lipidmix.library.store import open_store
from lipidmix.plots import mirror as mirror_plot
from lipidmix.plots import render as plot_render

__all__ = ["library_load", "library_match_feature", "library_plot_mirror"]

# MsRefSearchParameterBase の既定値（`docs/schema/molecule_ms_reference.md`）。
# store の search_params が None（`.msp` 由来）のときだけ使う。
_DEFAULT_MZ_TOL = 0.01
_DEFAULT_MS2_TOL = 0.025
_DEFAULT_RT_TOL = 0.2

# library_load が要約に添える化合物クラス分布の上位件数。
_TOP_COMPOUND_CLASSES = 10

# 候補一覧 TSV の列（先頭に列名を 1 回だけ出す）。スペクトル座標・alignment は
# 意図的に含めない（座標は session.library.last_match にだけ持つ）。
_CANDIDATE_TABLE_COLUMNS = [
    "rank", "name", "precursor_mz", "ion_mode", "adduct", "rt", "formula",
    "ontology", "compound_class", "simple_dot_product", "weighted_dot_product",
    "reverse_dot_product", "matched_peaks_percentage", "matched_peaks_count",
    "entropy_similarity",
]


def _iter_library_records(path: Path):
    """`.msp`/`.dbs` を拡張子で振り分けて正規化レコードを読む（`store._iter_records_for` と
    同じ振り分けだが、こちらは化合物クラス分布のためだけに独立して呼ばれる）。"""
    if path.suffix.lower() == ".msp":
        return msp_reader.iter_records(path)
    return dbs_reader.iter_records(path)


def _top_compound_classes(path: Path, top_n: int = _TOP_COMPOUND_CLASSES) -> list[dict]:
    counts: dict[str, int] = {}
    for record in _iter_library_records(path):
        name = record.get("compound_class")
        if name:
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [{"name": name, "count": count} for name, count in ranked]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
          structured_output=False)
def library_load(file_path: str | None = None, rebuild: bool = False) -> str:
    """参照ライブラリ（`*_Loaded.msp2.dbs` 優先、無ければ `*.msp`）を読み込み、
    照合用の SQLite store を構築（または既存キャッシュを再利用）する。

    `library_match_feature` の前提。別ライブラリへ切り替えると直近の照合結果
    （`library_plot_mirror` が読む座標）は破棄する——古い照合を新ライブラリの
    結果と取り違えないため。

    `rebuild=True` はキャッシュを無視して構築し直す（元ファイルが壊れている
    疑いがあるときなど）。通常は不要——store は元ファイルの sha256 をキーに
    キャッシュされるため、内容が変わらない限り再構築しない。
    """
    resolved = resolve_library_path(file_path)
    if not resolved:
        return json_payload({
            "status": "error",
            "message": "データディレクトリに参照ライブラリ（*_Loaded.msp2.dbs または *.msp）が見つかりませんでした。",
        })

    try:
        store_obj = open_store(resolved, rebuild=rebuild)
    except Exception as exc:  # noqa: BLE001 - 壊れたライブラリは文言で返す（MCP が扱いやすい）
        return json_payload({"status": "error", "message": f"参照ライブラリの読み込みに失敗しました: {exc}"})

    # 別ライブラリへの切り替え起こりうるので、古い照合結果を新ライブラリのものと
    # 取り違えないよう破棄する（ArfState.reset_analysis と同じ考え方）。
    session_state.session.library.store = store_obj
    session_state.session.library.source_path = resolved
    session_state.session.library.last_match = None

    path = Path(resolved)
    summary = store_obj.summary()
    search_params = summary.get("search_params")

    payload = {
        "status": "success",
        "file": path.name,
        "source_sha256": summary["source_sha256"],
        "record_count": summary["record_count"],
        "ion_modes": summary["ion_modes"],
        "compound_classes": _top_compound_classes(path),
        "search_params": search_params,
    }
    if search_params is None:
        payload["note"] = (
            f"`.msp` には照合の許容幅（search_params）が同梱されないため、"
            f"library_match_feature は既定値 mz_tol={_DEFAULT_MZ_TOL} / "
            f"ms2_tol={_DEFAULT_MS2_TOL} を使用します（明示的に指定すれば上書きできます）。"
        )
    return json_payload(round_floats(payload))


def _pick_tol(explicit: float | None, search_params: dict, key: str, default: float) -> float:
    """明示指定 > store の search_params > 既定値、の順で許容幅を決める。

    `search_params.get(key)` が `0.0` のような偽値でも正しく採用されるよう、
    `or` ではなく `is None` で判定する。
    """
    if explicit is not None:
        return explicit
    value = search_params.get(key)
    if value is not None:
        return value
    return default


def _measured_spectrum(precursor_mz: float, *, rt: float | None = None,
                       dcl_file: str | None = None, mz_tol: float = _DEFAULT_MZ_TOL,
                       rt_tol: float = _DEFAULT_RT_TOL) -> list[list[float]] | None:
    """`.dcl` から測定 MS/MS を引く。**全ピーク**（`top_n_peaks` で間引かない——
    間引くと採点の数値が変わる。Task 1 で確認済み）。

    `.dcl` が無い、または該当 precursor の MS/MS が無ければ `None`。
    """
    resolved = resolve_dcl_file_path(dcl_file)
    if not resolved:
        return None
    try:
        results = deserialize_dcl(resolved, include_spectrum=True, top_n_peaks=None)
    except Exception:  # noqa: BLE001 - 壊れた .dcl は「見つからない」と同じ扱いにする
        return None
    hits = get_msms_by_precursor(results, precursor_mz, tol=mz_tol, rt=rt, rt_tol=rt_tol)
    if not hits:
        return None
    return hits[0]["msms_spectrum"]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def library_match_feature(
    precursor_mz: float,
    rt: float | None = None,
    ion_mode: str | None = None,
    dcl_file: str | None = None,
    mz_tol: float | None = None,
    ms2_tol: float | None = None,
    rt_tol: float | None = None,
    top_n: int = 5,
) -> str:
    """測定 MS/MS（`.dcl`）を参照ライブラリ（`library_load` 済みの store）と照合し、
    上位候補を返す。

    `mz_tol` / `ms2_tol` / `rt_tol` は明示指定が無ければ store の `search_params`
    （`.dbs` 由来なら実値）を使い、それも無ければ既定値
    （`_DEFAULT_MZ_TOL` / `_DEFAULT_MS2_TOL` / `_DEFAULT_RT_TOL`、現在の値は
    0.01 / 0.025 / 0.2）を使う。

    `.dcl` に MS/MS が無い（未取得）場合は `status="not_found"` を返す——
    「候補と合わなかった」のではなく「照合する測定スペクトルがそもそも無い」ことを
    区別するため、`dcl_find_msms` と同じ文言の方針に揃える。

    候補一覧は TSV（列名 1 回）で返す。**スペクトル座標と alignment は戻り値に
    含めない**（LLM の文脈を食うため）。座標は `session.library.last_match` に
    持ち、`library_plot_mirror` がそこから読む。
    """
    store_obj = session_state.session.library.store
    if store_obj is None:
        return mcp_errors.missing_state(
            "library_store", ["library_load"],
            "先に library_load を実行してください（参照ライブラリが読み込まれていません）。",
        )

    search_params = store_obj.summary().get("search_params") or {}
    resolved_mz_tol = _pick_tol(mz_tol, search_params, "ms1_tolerance", _DEFAULT_MZ_TOL)
    resolved_ms2_tol = _pick_tol(ms2_tol, search_params, "ms2_tolerance", _DEFAULT_MS2_TOL)
    resolved_rt_tol = _pick_tol(rt_tol, search_params, "rt_tolerance", _DEFAULT_RT_TOL)

    measured = _measured_spectrum(
        precursor_mz, rt=rt, dcl_file=dcl_file, mz_tol=resolved_mz_tol, rt_tol=resolved_rt_tol,
    )
    if not measured:
        return json_payload({
            "status": "not_found",
            "message": (
                f"precursor m/z={precursor_mz}"
                + (f", RT={rt}" if rt is not None else "")
                + " に一致する MS/MS が見つかりませんでした。"
                "MS/MS 未取得（.dcl が無い、または該当 precursor の記録が無い）のであって、"
                "期待フラグメントが合わなかったのではありません。"
            ),
        })

    candidates = store_obj.candidates(
        precursor_mz, mz_tol=resolved_mz_tol, ion_mode=ion_mode, rt=rt, rt_tol=resolved_rt_tol,
    )
    if not candidates:
        return json_payload({
            "status": "no_candidates",
            "message": (
                f"precursor m/z={precursor_mz}±{resolved_mz_tol}"
                + (f", ion_mode={ion_mode}" if ion_mode else "")
                + " に該当する参照レコードが見つかりませんでした。"
            ),
        })

    scored = [
        (record, match_spectrum(measured, record["spectrum"], ms2_tol=resolved_ms2_tol))
        for record in candidates
    ]
    # ランク付けの基準は weighted_dot_product（MS-DIAL の主要な照合指標）。
    # 同点はそのまま候補順で温存する（安定ソート）。
    scored.sort(key=lambda item: item[1]["weighted_dot_product"], reverse=True)
    top = scored[:top_n] if top_n >= 0 else scored

    session_state.session.library.last_match = {
        "precursor_mz": precursor_mz,
        "rt": rt,
        "ion_mode": ion_mode,
        "ms2_tol": resolved_ms2_tol,
        "measured": measured,
        "candidates": [
            {
                "name": record["name"],
                "precursor_mz": record["precursor_mz"],
                "ion_mode": record["ion_mode"],
                "adduct": record["adduct"],
                "rt": record["rt"],
                "formula": record["formula"],
                "inchikey": record["inchikey"],
                "smiles": record["smiles"],
                "compound_class": record["compound_class"],
                "ontology": record["ontology"],
                "library_id": record["library_id"],
                "record_index": record["record_index"],
                "spectrum": record["spectrum"],
                "scores": {k: v for k, v in result.items() if k != "alignment"},
                "alignment": result["alignment"],
            }
            for record, result in top
        ],
    }

    rows = [
        {
            "rank": i + 1,
            "name": record["name"],
            "precursor_mz": record["precursor_mz"],
            "ion_mode": record["ion_mode"],
            "adduct": record["adduct"],
            "rt": record["rt"],
            "formula": record["formula"],
            "ontology": record["ontology"],
            "compound_class": record["compound_class"],
            "simple_dot_product": result["simple_dot_product"],
            "weighted_dot_product": result["weighted_dot_product"],
            "reverse_dot_product": result["reverse_dot_product"],
            "matched_peaks_percentage": result["matched_peaks_percentage"],
            "matched_peaks_count": result["matched_peaks_count"],
            "entropy_similarity": result["entropy_similarity"],
        }
        for i, (record, result) in enumerate(top)
    ]
    table = format_spots_as_table(rows, columns=_CANDIDATE_TABLE_COLUMNS)

    payload = {
        "status": "success",
        "query": {
            "precursor_mz": precursor_mz, "rt": rt, "ion_mode": ion_mode,
            "mz_tol": resolved_mz_tol, "ms2_tol": resolved_ms2_tol, "rt_tol": resolved_rt_tol,
        },
        "measured_peak_count": len(measured),
        "n_candidates": len(candidates),
        "top_n": len(top),
        "candidates_table": table,
    }
    return json_payload(round_floats(payload))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def library_plot_mirror(rank: int = 1, output: str | None = None) -> list | str:
    """直近の `library_match_feature` 結果から対向プロット（mirror plot）を描く。

    上段が測定、下段が参照（`rank` 位の候補）。`output="image"`（既定）は
    サーバ側で描画した PNG を返し、`output="payload"` は座標を丸めた
    `lipidmix.mirror.v1` JSON を返す（Use-LLLM 等、自前で描くクライアント向け）。

    測定側の一致色分けには許容幅（`ms2_tol`）が要る——`library_match_feature`
    が使った値（store の `search_params` か既定値）を自動で引き継ぐ。
    """
    last_match = session_state.session.library.last_match
    if not last_match or not last_match.get("candidates"):
        return mcp_errors.missing_state(
            "library_match", ["library_match_feature"],
            "先に library_match_feature を実行してください（直近の照合結果がありません）。",
        )

    candidates = last_match["candidates"]
    if rank < 1 or rank > len(candidates):
        return json_payload({
            "status": "error",
            "message": f"rank は 1〜{len(candidates)} の範囲で指定してください（受け取った値: {rank!r}）。",
        })

    try:
        mode = plot_render.resolve_plot_output(output)
    except ValueError as exc:
        return json_payload({"status": "error", "message": str(exc)})

    candidate = candidates[rank - 1]
    payload = mirror_plot.build_mirror_payload(
        last_match["measured"], candidate["spectrum"], candidate["alignment"],
        title=f"{candidate['name']} (rank {rank})",
        ms2_tol=last_match.get("ms2_tol"),
    )

    if mode == plot_render.PAYLOAD:
        return json_payload(round_floats(payload))

    png = mirror_plot.render_mirror(payload)
    caption = (
        f"Mirror: measured vs {candidate['name']} "
        f"(rank {rank}, precursor m/z={candidate['precursor_mz']})."
    )
    return [caption, Image(data=png, format="png")]
