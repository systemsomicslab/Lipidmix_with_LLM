"""EIC/AEF ツール群: eic_parser, eic_rank_by_max_intensity, m/z 範囲検索, RT 範囲検索。

deps: mcp_core / session_state / path_resolvers / eic_aef_reader。
tools_* / server は import しない。
"""
import json
from pathlib import Path

import session_state
from mcp_core import mcp
from path_resolvers import resolve_eicaef_file_path
from eic_aef_reader import (
    read_eic_spot_css1,
    summarize_eic_data,
    top_eic_spots_by_max_intensity,
    search_eic_by_mz_range,
    search_eic_by_rt_range,
)
from eic_plot import EICPlotPayload, build_eic_plot_payload

__all__ = [
    "eic_parser",
    "eic_plot_chromatograms",
    "eic_rank_by_max_intensity",
    "eic_search_by_mz_range",
    "eic_search_by_rt_range",
]


@mcp.tool()
def eic_parser(file_path: str | None = None) -> str:
    """
    .EIC.aef ファイルに対応する解析用関数
    ファイルを解析し、テキスト要約を返します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.load_eic_data(file_path)
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
        return session_state.session.maybe_prepend_caveat("\n".join(output_text))
    except Exception as e:
        return f"EIC解析中にエラーが発生しました: {str(e)}"


@mcp.tool()
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
    session_state.session.last_eic_plot = payload
    return payload


@mcp.tool()
def eic_rank_by_max_intensity(file_path: str | None = None, top_n: int = 20) -> str:
    """EICスポットを強度（各サンプルのクロマトグラム最大強度の最大値）で降順に返します。

    強度上位のスポットを抽出します。旧 eicaef_top_peak_tops は peak_top（ピーク頂点の
    横軸=RT座標。強度ではない）で並べていたため強度上位にならず、本ツールで置き換えました。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.load_eic_data(file_path)
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


@mcp.tool()
def eic_search_by_mz_range(file_path: str | None = None, min_mz: float = 0.0, max_mz: float = 1000.0, max_results: int = 20) -> str:
    """
    EICデータのm/z範囲でスポットを検索します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.load_eic_data(file_path)
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


@mcp.tool()
def eic_search_by_rt_range(file_path: str | None = None, min_rt: float = 0.0, max_rt: float = 20.0, max_results: int = 20) -> str:
    """
    EICデータのRT範囲でスポットを検索します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.load_eic_data(file_path)
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
