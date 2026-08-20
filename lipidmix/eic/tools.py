"""EIC/AEF ツール群: eic_parser, eic_plot_chromatograms, eic_plot_compounds,
eic_rank_by_max_intensity, m/z 範囲検索, RT 範囲検索。

deps: mcp_core / session_state / path_resolvers / lipidmix.eic.reader /
lipidmix.eic.identity_map / lipidmix.plots.eic。tools_* / server は import しない。
"""
import json
from pathlib import Path

from lipidmix.core import session_state
from mcp.types import ToolAnnotations
from lipidmix.core.mcp_core import mcp
from lipidmix.core.path_resolvers import resolve_arf2_file_path, resolve_eicaef_file_path
from lipidmix.eic.reader import (
    read_eic_spot_css1,
    read_eic_spots_css1,
    summarize_eic_data,
    top_eic_spots_by_max_intensity,
    search_eic_by_mz_range,
    search_eic_by_rt_range,
)
from lipidmix.eic.identity_map import load_arf2_records, select_identity_candidates
from lipidmix.plots.eic import (
    EICMultiPlotPayload,
    EICPlotPayload,
    build_eic_plot_payload,
    build_multi_compound_plot_payload,
)

__all__ = [
    "eic_parser",
    "eic_plot_chromatograms",
    "eic_plot_compounds",
    "eic_rank_by_max_intensity",
    "eic_search_by_mz_range",
    "eic_search_by_rt_range",
]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def eic_parser(file_path: str | None = None) -> str:
    """
    .EIC.aef ファイルに対応する解析用関数
    ファイルを解析し、テキスト要約を返します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.eic.load_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        summary = summarize_eic_data(parsed)
        output_text = [
            f"EIC解析完了: {Path(file_path).name}",
            "== 基本要約 ==",
            json.dumps(summary, indent=2, ensure_ascii=False),
            "== コメント ==",
            "この結果をもとに、m/z範囲検索やRT範囲検索、強度上位抽出（eic_rank_by_max_intensity）を実行できます。",
        ]
        return session_state.session.maybe_prepend_caveat(
            "\n".join(output_text), topic="eic",
        )
    except Exception as e:
        return f"EIC解析中にエラーが発生しました: {str(e)}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def eic_plot_chromatograms(
    spot_id: int,
    file_path: str | None = None,
    file_ids: list[int] | None = None,
    normalize: str = "none",
    title: str | None = None,
) -> EICPlotPayload:
    """Return client-neutral plot data for selected EIC chromatograms.

    This read-only tool returns structured ``lipidmix.eic.v1`` JSON only. It does
    not render an image and does not write files: Use-LLLM may render the series
    with Plotly, while Claude Desktop or another MCP client may use its own UI.
    Call ``save_eic_figure`` only after the user explicitly requests PNG output.

    ``spot_id`` is exact and should normally come from the existing m/z or RT
    search tools. ``file_ids`` selects at most 12 sample traces; when omitted all
    traces are returned only if the spot contains 12 samples or fewer.
    """
    resolved = resolve_eicaef_file_path(file_path)
    if not resolved:
        raise FileNotFoundError("データディレクトリに .aef ファイルが見つかりませんでした。")
    spot = read_eic_spot_css1(resolved, spot_id, file_ids=file_ids)
    payload = build_eic_plot_payload(
        spot, resolved, normalize=normalize, title=title,
    )
    session_state.session.eic.last_plot = payload
    return payload


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def eic_plot_compounds(
    file_id: int,
    names: list[str] | None = None,
    ontologies: list[str] | None = None,
    file_path: str | None = None,
    arf2_path: str | None = None,
    normalize: str = "none",
    top_n: int = 24,
    title: str | None = None,
) -> EICMultiPlotPayload:
    """Overlay several identified compounds' EIC traces for ONE sample.

    Read-only. Returns structured ``lipidmix.eic.multi.v1`` JSON only; it renders
    no image and writes no file. Call ``save_eic_figure`` only after the user
    explicitly requests PNG output.

    One call plots one sample: ``file_id`` is required. To compare samples, call
    this tool once per sample and show the figures side by side.

    Compounds are selected from the ARF2 annotation: ``names`` matches ARF2
    ``Name`` as a case-insensitive substring and ``ontologies`` matches ARF2
    ``Ontology`` exactly (case-insensitive); the two results are unioned. At
    least one of them is required.

    ``spot_id = AlignmentID`` is verified against the EIC spot RT and m/z, so a
    compound whose identity cannot be confirmed is not drawn. Everything that was
    excluded — mismatch, missing trace, or falling outside ``top_n`` — is listed
    with its reason in ``selection.dropped``; read it before concluding that a
    compound is absent from the sample.
    """
    if isinstance(file_id, bool) or not isinstance(file_id, int):
        raise ValueError("file_id must be an integer")
    if normalize not in {"none", "per_trace_max"}:
        raise ValueError("normalize must be 'none' or 'per_trace_max'")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer")

    resolved = resolve_eicaef_file_path(file_path)
    if not resolved:
        raise FileNotFoundError("データディレクトリに .aef ファイルが見つかりませんでした。")
    resolved_arf2 = resolve_arf2_file_path(arf2_path)
    if not resolved_arf2:
        raise FileNotFoundError("データディレクトリに .arf2 ファイルが見つかりませんでした。")

    records = load_arf2_records(resolved_arf2)
    candidates, caveats, total_matched = select_identity_candidates(
        records, names=names, ontologies=ontologies,
    )
    if not candidates:
        raise ValueError(
            f"names={names} / ontologies={ontologies} に一致する ARF2 スポットがありません。"
            "arf2_parser で脂質名・オントロジーの表記を確認してください。"
        )

    spots = read_eic_spots_css1(
        resolved,
        [candidate["spot_id"] for candidate in candidates],
        file_ids=[file_id],
    )
    payload = build_multi_compound_plot_payload(
        candidates,
        spots,
        file_id=file_id,
        file_path=resolved,
        arf2_path=resolved_arf2,
        normalize=normalize,
        top_n=top_n,
        title=title,
        queries=names,
        ontologies=ontologies,
        caveats=caveats,
        total_matched=total_matched,
    )
    session_state.session.eic.last_plot = payload
    return payload


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def eic_rank_by_max_intensity(file_path: str | None = None, top_n: int = 20) -> str:
    """EICスポットを強度（各サンプルのクロマトグラム最大強度の最大値）で降順に返します。

    強度上位のスポットを抽出します。旧 eicaef_top_peak_tops は peak_top（ピーク頂点の
    横軸=RT座標。強度ではない）で並べていたため強度上位にならず、本ツールで置き換えました。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.eic.load_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        top_spots = top_eic_spots_by_max_intensity(parsed, top_n=top_n)
        output_text = [
            f"EIC強度上位スポット: {Path(file_path).name}",
            f"上位{top_n}件（max_intensity 降順）:",
        ]
        for spot in top_spots:
            output_text.append(
                f"spot_id={spot['spot_id']} rt={spot['rt']} mz={spot['mz']} "
                f"max_intensity={spot['max_intensity']} num_samples={spot['num_samples']}"
            )
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC強度上位抽出中にエラーが発生しました: {str(e)}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def eic_search_by_mz_range(file_path: str | None = None, min_mz: float = 0.0, max_mz: float = 1000.0, max_results: int = 20) -> str:
    """
    EICデータのm/z範囲でスポットを検索します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.eic.load_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        matches = search_eic_by_mz_range(parsed, min_mz, max_mz)
        output_text = [
            f"EIC m/z範囲検索: {min_mz} - {max_mz}",
            f"一致件数: {len(matches)}",
            "上位結果:",
        ]
        for spot in matches[:max_results]:
            output_text.append(
                f"spot_id={spot['spot_id']} rt={spot['rt']} mz={spot['mz']} num_samples={spot['num_samples']}"
            )
        if len(matches) > max_results:
            output_text.append(f"(表示上限: {max_results} 件)")
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC m/z検索中にエラーが発生しました: {str(e)}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def eic_search_by_rt_range(file_path: str | None = None, min_rt: float = 0.0, max_rt: float = 20.0, max_results: int = 20) -> str:
    """
    EICデータのRT範囲でスポットを検索します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.eic.load_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        matches = search_eic_by_rt_range(parsed, min_rt, max_rt)
        output_text = [
            f"EIC RT範囲検索: {min_rt} - {max_rt}",
            f"一致件数: {len(matches)}",
            "上位結果:",
        ]
        for spot in matches[:max_results]:
            output_text.append(
                f"spot_id={spot['spot_id']} rt={spot['rt']} mz={spot['mz']} num_samples={spot['num_samples']}"
            )
        if len(matches) > max_results:
            output_text.append(f"(表示上限: {max_results} 件)")
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC RT検索中にエラーが発生しました: {str(e)}"
