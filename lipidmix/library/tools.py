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
from lipidmix.library.defaults import DEFAULT_MS2_TOL as _DEFAULT_MS2_TOL
from lipidmix.library.defaults import DEFAULT_MZ_TOL as _DEFAULT_MZ_TOL
from lipidmix.library.defaults import DEFAULT_RT_TOL as _DEFAULT_RT_TOL
from lipidmix.library.defaults import pick_tol as _pick_tol
from lipidmix.library.store import open_store
from lipidmix.plots import mirror as mirror_plot
from lipidmix.plots import render as plot_render

__all__ = ["library_load", "library_match_feature", "library_plot_mirror"]

# library_load が要約に添える化合物クラス分布の上位件数。
_TOP_COMPOUND_CLASSES = 10

# library_match_feature の候補の並び順。MS-DIAL の主要な照合指標
# （mzTab の id_confidence_measure[5] に対応）を採用している。戻り値の
# "ranked_by" と docstring の両方がこの定数を指す——コード内コメントだけに
# しておくと、rank 列が何の降順かを呼び出し側（MCP クライアント）が
# 知る手段が無くなる。
_RANK_KEY = "weighted_dot_product"

# 候補一覧 TSV の列（先頭に列名を 1 回だけ出す）。スペクトル座標・alignment は
# 意図的に含めない（座標は session.library.last_match にだけ持つ）。
_CANDIDATE_TABLE_COLUMNS = [
    "rank", "name", "precursor_mz", "ion_mode", "adduct", "rt", "formula",
    "ontology", "compound_class", "simple_dot_product", "weighted_dot_product",
    "reverse_dot_product", "matched_peaks_percentage", "matched_peaks_count",
    "entropy_similarity",
]


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

    # 古い store の sqlite3 接続を閉じてから差し替える（Minor 8: 閉じずに上書きすると
    # library_load を繰り返すたびに接続が漏れる。LibraryStore.close() は最初から
    # 存在していたが、ここで呼んでいなかった）。
    old_store = session_state.session.library.store
    if old_store is not None:
        old_store.close()

    # 別ライブラリへの切り替え起こりうるので、古い照合結果を新ライブラリのものと
    # 取り違えないよう破棄する（ArfState.reset_analysis と同じ考え方）。
    session_state.session.library.store = store_obj
    session_state.session.library.source_path = resolved
    session_state.session.library.last_match = None

    path = Path(resolved)
    summary = store_obj.summary()
    search_params = summary.get("search_params")
    skipped = summary.get("skipped_no_precursor_mz", 0)

    payload = {
        "status": "success",
        "file": path.name,
        "source_sha256": summary["source_sha256"],
        "record_count": summary["record_count"],
        "ion_modes": summary["ion_modes"],
        "compound_classes": store_obj.compound_class_counts(_TOP_COMPOUND_CLASSES),
        "search_params": search_params,
        "skipped_no_precursor_mz": skipped,
    }
    notes = []
    if search_params is None:
        notes.append(
            f"`.msp` には照合の許容幅（search_params）が同梱されないため、"
            f"library_match_feature は既定値 mz_tol={_DEFAULT_MZ_TOL} / "
            f"ms2_tol={_DEFAULT_MS2_TOL} を使用します（明示的に指定すれば上書きできます）。"
        )
    if skipped:
        # Important 3: PRECURSORMZ が無い/パース不能なレコードは黙って捨てず、
        # 件数を表に出す（黙って捨てると「record_count が元ファイルと合わない」で悩む）。
        notes.append(
            f"precursor m/z が無い（または解釈できなかった）レコードを {skipped} 件、"
            f"読み飛ばしました（record_count には含まれません）。"
        )
    if notes:
        payload["note"] = " ".join(notes)
    return json_payload(round_floats(payload))


def _measured_spectrum(precursor_mz: float, *, rt: float | None = None,
                       dcl_file: str | None = None, mz_tol: float = _DEFAULT_MZ_TOL,
                       rt_tol: float = _DEFAULT_RT_TOL) -> list[list[float]] | None:
    """`.dcl` から測定 MS/MS を引く。**全ピーク**（`top_n_peaks` で間引かない——
    間引くと採点の数値が変わる。Task 1 で確認済み）。

    `.dcl` が無い、または該当 precursor の MS/MS が無ければ `None`。

    **`mz_tol` / `rt_tol` の既定はここだけで完結させる**（呼び出し元の
    `library_match_feature` は library 側の許容幅——`.dbs` の `search_params`
    由来なら実値、RT は上流既定 100.0 相当——をここへ絶対に流し込まないこと。
    最終レビュー Important 7: 以前は library 側で解決した `resolved_rt_tol`
    をここにも渡していたため、その `.dbs` を読ませると `.dcl` 側の RT 窓が
    ±100 分になり、precursor が近い無関係なピークまで拾っていた）。

    `.dcl` の同じ precursor に複数ヒットしうる（同一 m/z の別溶出ピーク）ため、
    `rt` が渡されたときは **RT 距離が最も近いヒットを選ぶ**（最終レビュー
    Important 7: 以前は `hits[0]`——`.dcl` ファイル内の並び順の先頭——を無条件に
    採っており、別のピークの測定スペクトルを黙って採点しうる不具合があった）。
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
    if rt is not None:
        hits = sorted(hits, key=lambda hit: abs(hit["rt"] - rt))
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

    `ion_mode` は `"positive"` / `"negative"`（大小は問わない——`store.candidates()`
    が `COLLATE NOCASE` で比較するため、PAI2 由来の `IonMode.Positive.name`
    のような `"Positive"` 表記もそのまま渡してよい）。

    `mz_tol` / `ms2_tol` / `rt_tol` は明示指定が無ければ store の `search_params`
    （`.dbs` 由来なら実値）を使い、それも無ければ既定値
    （`_DEFAULT_MZ_TOL` / `_DEFAULT_MS2_TOL` / `_DEFAULT_RT_TOL`、現在の値は
    0.01 / 0.025 / 0.2）を使う。**これらは参照ライブラリの候補検索（`store.candidates()`
    の precursor m/z 窓・RT 窓）にだけ効く。** `.dcl` から測定 MS/MS を引く窓は
    `_measured_spectrum` 内部の固定既定（`dcl/reader.py` と同じ `mz_tol=0.01`/
    `rt_tol=0.2`）を常に使い、ここの引数や store の `search_params` の影響を受けない
    （最終レビュー Important 7: `.dbs` の `RtTolerance` 既定 100.0 のような
    ライブラリ用の値を `.dcl` 側の RT 窓に流用すると、同じ precursor m/z の
    別ピークの測定スペクトルを黙って拾ってしまうため、2 つの窓を分離した）。
    足切り（`relative_amp_cutoff` / `absolute_amp_cutoff`）と質量範囲
    （`mass_range_begin` / `mass_range_end`）も同じ `search_params` から採って
    採点（`match_spectrum`）に渡す——検証 CLI（`scripts/verify_spectral_match.py`）
    と同じ集合（最終レビュー Important 2）。

    `.dcl` に MS/MS が無い（未取得）場合は `status="not_found"` を返す——
    「候補と合わなかった」のではなく「照合する測定スペクトルがそもそも無い」ことを
    区別するため、`dcl_find_msms` と同じ文言の方針に揃える。

    候補は **`weighted_dot_product` の降順**で返す（`rank` 列・`ranked_by`
    フィールドが同じ基準を指す）。MS-DIAL の主要な照合指標で、mzTab の
    `id_confidence_measure[5]` に対応する。同点は候補順を温存する（安定ソート）。

    `top_n` は上位何件を返すかの上限（既定 5）。負値を渡すと全件を返す
    （明示的な仕様ではなく Python のスライス挙動に由来する副作用的な動作
    ——件数を絞りたくないだけなら大きな正の値を渡すこと）。

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
    # match_spectrum の採点前処理パラメータ。検証 CLI（scripts/verify_spectral_match.py）
    # と同じ集合を search_params から採る（Important 2）。既定は
    # MsRefSearchParameterBase の Key 0/1/7/8（`dbs.py` の `_SEARCH_PARAM_KEYS`）。
    mass_begin = _pick_tol(None, search_params, "mass_range_begin", 0.0)
    mass_end = _pick_tol(None, search_params, "mass_range_end", 2000.0)
    relative_amp_cutoff = _pick_tol(None, search_params, "relative_amp_cutoff", 0.0)
    absolute_amp_cutoff = _pick_tol(None, search_params, "absolute_amp_cutoff", 0.0)

    # `.dcl` 側の窓は library 側の resolved_mz_tol/resolved_rt_tol を渡さない
    # （Important 7 — 上のクラス docstring参照）。_measured_spectrum 自身の
    # 固定既定（dcl/reader.py と同じ mz_tol=0.01/rt_tol=0.2）を使う。
    measured = _measured_spectrum(precursor_mz, rt=rt, dcl_file=dcl_file)
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
        (record, match_spectrum(
            measured, record["spectrum"], ms2_tol=resolved_ms2_tol,
            mass_begin=mass_begin, mass_end=mass_end,
            relative_amp_cutoff=relative_amp_cutoff, absolute_amp_cutoff=absolute_amp_cutoff,
        ))
        for record in candidates
    ]
    # ランク付けの基準は _RANK_KEY（weighted_dot_product、MS-DIAL の主要な照合指標）。
    # 同点はそのまま候補順で温存する（安定ソート）。docstring と payload["ranked_by"]
    # がこの基準を呼び出し側へ明示する（コード内コメントだけに留めない）。
    scored.sort(key=lambda item: item[1][_RANK_KEY], reverse=True)
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
            "mass_begin": mass_begin, "mass_end": mass_end,
            "relative_amp_cutoff": relative_amp_cutoff, "absolute_amp_cutoff": absolute_amp_cutoff,
        },
        "measured_peak_count": len(measured),
        "n_candidates": len(candidates),
        "top_n": len(top),
        "ranked_by": _RANK_KEY,
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
