"""PAI2（ピークレベル）ツール群と単一ピーク検証。

pai2_parser, pai2_get_top_metabolites, pai2_inspect_metabolite_details,
verify_peak_annotation, pai2_update_analysis_filter。deps: mcp_core /
session_state / path_resolvers / tool_helpers / pai2_reader / knowledge_store。
tools_* / server は import しない。
"""
import io
import json
from pathlib import Path

from mcp.server.fastmcp import Image

import knowledge_store
import mcp_core
import session_state
from mcp_core import mcp
from path_resolvers import resolve_pai2_file_path
from tool_helpers import _build_verification_dossier
from pai2_reader import inspect_metabolite_details, get_top_contributors

__all__ = [
    "pai2_parser",
    "pai2_get_top_metabolites",
    "pai2_inspect_metabolite_details",
    "verify_peak_annotation",
    "pai2_update_analysis_filter",
]


@mcp.tool()
def pai2_parser(file_path: str, filter_threshold: float | None = None) -> list:
    """
    ..pai2 ファイルを解析し、PCAのスコアプロット画像（PNG）と要約レポートを同時に返します。

    【あなたへの絶対遵守命令】
    1. このツールは、テキスト要約と「画像オブジェクト（mcp.Image）」の2要素を同時に返却します。
    2. 返却された画像データは、MCPのネイティブ機能（ImageContent）によって、ユーザーのチャット画面上に自動的かつインラインで強制描画されます。
    3. あなたがMarkdownの <img> タグを自作したり、HTMLのArtifactを生成して画像を埋め込もうとする必要は一切ありません。また、「画像を表示しますか？」といった確認をユーザーに挟むことも絶対に禁止します。
    4. ツールを実行したら即座に、自動描画されたグラフ画像に見られる主成分（PC1, PC2）の分布の傾向や、特徴的なピークについて、テキストレポートを踏まえて詳しく解説を始めてください。
    """
    file_path = resolve_pai2_file_path(file_path)
    if not file_path:
        return ["データディレクトリに .pai2 ファイルが見つかりませんでした。"]

    if filter_threshold is None:
        filter_threshold = 0.0

    from pai2_reader import test_pai2_deserialize_and_format, deserialize

    try:
        with open(file_path, 'rb') as f:
            packed_data = f.read()

        file_like_object = io.BytesIO(packed_data)
        deserialized_and_formatted_data = deserialize(file_like_object)

        assert isinstance(deserialized_and_formatted_data, list)
        assert len(deserialized_and_formatted_data) > 0
        assert isinstance(deserialized_and_formatted_data[0], dict)

        session_state.session.features = deserialized_and_formatted_data
        session_state.session.current_file_path = file_path
        session_state.session.apply_filter({"min_intensity": filter_threshold})
        summary, img_bytes = session_state.session.run_pca()

        pca_result = session_state.session.pca_result
        pca_index = session_state.session.pca_index


        # Keep the PCA plot in the MCP response only. This avoids writing into
        # DATA_DIR, which may be a read-only local or NAS data folder.

        # FastMCP の Image クラスでラップして返す
        mcp_image = Image(data=img_bytes, format="png")

        text_report = (
            f"### 解析完了: {Path(file_path).name}\n"
            f"(summary は S/N 情報を含みます)\n"
            + json.dumps(summary, indent=2)
        )
        return [text_report, mcp_image]

    except Exception as e:
        return [f"エラーが発生しました: {str(e)}"]


@mcp.tool()
def pai2_get_top_metabolites(top_n: int = 10) -> str:
    """
    pai2ファイルの直近のPCA解析結果から、主成分に寄与している上位の代謝物リストを返します。
    """
    if session_state.session.pca_result is None:
        return "先に analyze_pai2_pca を実行してください。"

    from pai2_reader import get_top_contributors

    top_list = get_top_contributors(session_state.session.filtered_features, session_state.session.pca_result, top_n)
    return f"上位{top_n}件の代謝物:\n{json.dumps(top_list, indent=2, ensure_ascii=False)}"


@mcp.tool()
def pai2_inspect_metabolite_details(metabolite_id: str | None = None, metabolite_name: str | None = None) -> str:
    """特定の代謝物について、強度・S/N・MS/MS相当の情報を返す。

    返り値には signal_to_noise フィールドが含まれます。
    """
    if session_state.session.filtered_features is None:
        return "先に pai2_parser を実行してデータを読み込んでください。"

    details = inspect_metabolite_details(
        session_state.session.filtered_features,
        metabolite_id=metabolite_id,
        metabolite_name=metabolite_name,
    )
    return json.dumps(details, indent=2, ensure_ascii=False)


@mcp.tool()
def verify_peak_annotation(
    metabolite_id: str | None = None, metabolite_name: str | None = None
) -> str:
    """指定した1ピークのアノテーションが生化学的に妥当かを検証するドシエを返す。

    分析化学的な同定確度（精密質量誤差ppm・アダクト/イオンモード整合）を決定的に
    判定し、生物学的妥当性は関連 knowledge slug を添えて LLM の判断に委ねる。
    先に pai2_parser でデータを読み込むこと。metabolite_id か metabolite_name の
    いずれかを指定する。
    """
    if session_state.session.filtered_features is None:
        return json.dumps(
            {"status": "error", "message": "先に pai2_parser を実行してデータを読み込んでください。"},
            ensure_ascii=False,
            indent=2,
        )
    if metabolite_id is None and metabolite_name is None:
        return json.dumps(
            {"status": "error", "message": "metabolite_id か metabolite_name のいずれかを指定してください。"},
            ensure_ascii=False,
            indent=2,
        )

    matches = []
    for feat in session_state.session.filtered_features:
        if metabolite_id is not None and str(feat.get("id")) == str(metabolite_id):
            matches.append(feat)
        elif (
            metabolite_name is not None
            and isinstance(feat.get("name"), str)
            and metabolite_name.lower() in feat.get("name", "").lower()
        ):
            matches.append(feat)

    if not matches:
        return json.dumps(
            {"status": "not_found", "message": "指定された代謝物がフィルタ済みデータ内に見つかりませんでした。"},
            ensure_ascii=False,
            indent=2,
        )

    vocab = knowledge_store.load_vocab(mcp_core.KNOWLEDGE_DIR)
    dossiers = [_build_verification_dossier(feat, vocab) for feat in matches]
    payload = dossiers[0] if len(dossiers) == 1 else {"status": "success", "matches": dossiers}
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def pai2_update_analysis_filter(min_intensity: float = 0.0, min_sn: float = 0.0) -> str:
    """min_intensity / min_sn を更新してPCAを再実行する。"""
    if session_state.session.features is None:
        return "先に pai2_parser を実行してデータを読み込んでください。"

    old_count = len(session_state.session.filtered_features or session_state.session.features)
    old_pc1 = None
    if session_state.session.last_pca_summary and session_state.session.last_pca_summary.get("explained_variance"):
        try:
            old_pc1 = float(session_state.session.last_pca_summary["explained_variance"]["PC1"].strip("%")) / 100.0
        except Exception:
            old_pc1 = None

    new_filter = {"min_intensity": min_intensity}
    if min_sn:
        new_filter["min_sn"] = min_sn

    session_state.session.apply_filter(new_filter)
    summary, img_bytes = session_state.session.run_pca()
    new_count = len(session_state.session.filtered_features or [])

    parts = [
        f"フィルタ更新: min_intensity={min_intensity}, min_sn={min_sn}",
        f"前件数: {old_count}",
        f"後件数: {new_count}",
    ]

    if old_count:
        reduction = 100.0 * (old_count - new_count) / old_count
        parts.append(f"データ損失率: {reduction:.1f}%")

    if summary.get("explained_variance"):
        parts.append(f"PC1 explained variance: {summary['explained_variance']['PC1']}")
        parts.append(f"PC2 explained variance: {summary['explained_variance']['PC2']}")

    if old_pc1 is not None and summary.get("explained_variance"):
        try:
            new_pc1 = float(summary["explained_variance"]["PC1"].strip("%")) / 100.0
            delta = new_pc1 - old_pc1
            parts.append(f"PC1 explained variance change: {delta:+.2%}")
        except Exception:
            pass

    parts.append("フィルタ後の上位寄与代謝物:")
    top_list = get_top_contributors(session_state.session.filtered_features, session_state.session.pca_result, top_n=5)
    parts.append(json.dumps(top_list, indent=2, ensure_ascii=False))

    return "\n".join(parts)
