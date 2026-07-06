"""EIC/AEF ツール群: eicaef_parser, top_peak_tops, m/z 範囲検索, RT 範囲検索。

deps: mcp_core / session_state / path_resolvers / eic_aef_reader。
tools_* / server は import しない。
"""
import json
from pathlib import Path

import session_state
from mcp_core import mcp
from path_resolvers import resolve_eicaef_file_path
from eic_aef_reader import (
    summarize_eic_data,
    top_eic_spots_by_peak_top,
    search_eic_by_mz_range,
    search_eic_by_rt_range,
)

__all__ = [
    "eicaef_parser",
    "eicaef_top_peak_tops",
    "eicaef_search_by_mz_range",
    "eicaef_search_by_rt_range",
]


@mcp.tool()
def eicaef_parser(file_path: str | None = None) -> str:
    """
    .eic.aef ファイルに対応する解析用関数
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
            "この結果をもとに、m/z範囲検索やRT範囲検索、上位PeakTop抽出を実行できます。",
        ]
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC解析中にエラーが発生しました: {str(e)}"


@mcp.tool()
def eicaef_top_peak_tops(file_path: str | None = None, top_n: int = 20) -> str:
    """
    EICデータのPeakTop値で上位スポットを返します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session_state.session.load_eic_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        top_spots = top_eic_spots_by_peak_top(parsed, top_n=top_n)
        output_text = [
            f"EIC上位PeakTopスポット: {Path(file_path).name}",
            f"上位{top_n}件:",
        ]
        for spot in top_spots:
            output_text.append(
                f"spot_id={spot['spot_id']} rt={spot['rt']} mz={spot['mz']} "
                f"max_peak_top={spot['max_peak_top']} num_samples={spot['num_samples']}"
            )
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC上位PeakTop抽出中にエラーが発生しました: {str(e)}"


@mcp.tool()
def eicaef_search_by_mz_range(file_path: str | None = None, min_mz: float = 0.0, max_mz: float = 1000.0, max_results: int = 20) -> str:
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
def eicaef_search_by_rt_range(file_path: str | None = None, min_rt: float = 0.0, max_rt: float = 20.0, max_results: int = 20) -> str:
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
