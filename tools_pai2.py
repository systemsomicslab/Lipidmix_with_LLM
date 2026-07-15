"""PAI2（1測定ファイルのピーク一覧）ツール群と単一ピーク検証。

pai2_parser（在庫要約）, pai2_inspect_peak, verify_peak_annotation。
PAI2 は単一サンプルなのでサンプル間比較（オミクス PCA）は行わない（複数サンプルの
多変量比較は ARF/ARF2 を使う）。deps: mcp_core / session_state / path_resolvers /
tool_helpers / pai2_reader / knowledge_store。tools_* / server は import しない。
"""
import io
import json
from pathlib import Path

import knowledge_store
import mcp_core
import session_state
from mcp_core import mcp
from path_resolvers import resolve_pai2_file_path
from tool_helpers import _build_verification_dossier
from pai2_reader import inspect_peak_details, summarize_pai2_inventory

__all__ = [
    "pai2_parser",
    "pai2_inspect_peak",
    "verify_peak_annotation",
]


@mcp.tool()
def pai2_parser(file_path: str, filter_threshold: float | None = None) -> str:
    """1つの .pai2（単一測定ファイル）を解析し、ピーク在庫の要約を返します。

    返すのは注釈状況・m/z・RT・強度・S/N の分布と、強度上位ピーク（生化学的に意味のある
    ランキング）です。PAI2 は単一サンプルなので、サンプル間比較（オミクス PCA）はこの単位
    では行えません（複数サンプルの多変量比較は ARF/ARF2 を使う）。個々のピークは
    pai2_inspect_peak / verify_peak_annotation で深掘りできます。MS/MS は
    同名 .dcl（dcl_index がリスト順に対応）を参照します。
    """
    file_path = resolve_pai2_file_path(file_path)
    if not file_path:
        return "データディレクトリに .pai2 ファイルが見つかりませんでした。"

    if filter_threshold is None:
        filter_threshold = 0.0

    from pai2_reader import deserialize

    try:
        with open(file_path, 'rb') as f:
            packed_data = f.read()

        file_like_object = io.BytesIO(packed_data)
        deserialized_and_formatted_data = deserialize(file_like_object)

        assert isinstance(deserialized_and_formatted_data, list)
        assert len(deserialized_and_formatted_data) > 0
        assert isinstance(deserialized_and_formatted_data[0], dict)

        # 別データセットへ切り替えるので前データ由来の解析成果を破棄してから load する。
        session_state.session.reset_analysis_state()
        session_state.session.features = deserialized_and_formatted_data
        session_state.session.current_file_path = file_path
        session_state.session.apply_filter({"min_intensity": filter_threshold})

        summary = summarize_pai2_inventory(session_state.session.filtered_features)
        text_report = (
            f"### PAI2 解析完了: {Path(file_path).name}\n"
            + json.dumps(summary, indent=2, ensure_ascii=False)
        )
        return session_state.session.maybe_prepend_caveat(text_report)

    except Exception as e:
        return f"[ERROR] PAI2 解析に失敗しました: {str(e)}"


@mcp.tool()
def pai2_inspect_peak(peak_id: str | None = None, peak_name: str | None = None) -> str:
    """特定のピークについて、強度・S/N・MS/MS相当の情報を返す。

    返り値には signal_to_noise フィールドが含まれます。peak_id か peak_name の
    いずれかを指定する。
    """
    if session_state.session.filtered_features is None:
        return json.dumps(
            {"status": "error", "message": "先に pai2_parser を実行してデータを読み込んでください。"},
            ensure_ascii=False, indent=2,
        )

    details = inspect_peak_details(
        session_state.session.filtered_features,
        peak_id=peak_id,
        peak_name=peak_name,
    )
    return json.dumps(details, indent=2, ensure_ascii=False)


@mcp.tool()
def verify_peak_annotation(
    peak_id: str | None = None, peak_name: str | None = None
) -> str:
    """指定した1ピークのアノテーションが生化学的に妥当かを検証するドシエを返す。

    分析化学的な同定確度（精密質量誤差ppm・アダクト/イオンモード整合）を決定的に
    判定し、生物学的妥当性は関連 knowledge slug を添えて LLM の判断に委ねる。
    先に pai2_parser でデータを読み込むこと。peak_id か peak_name の
    いずれかを指定する。
    """
    if session_state.session.filtered_features is None:
        return json.dumps(
            {"status": "error", "message": "先に pai2_parser を実行してデータを読み込んでください。"},
            ensure_ascii=False,
            indent=2,
        )
    if peak_id is None and peak_name is None:
        return json.dumps(
            {"status": "error", "message": "peak_id か peak_name のいずれかを指定してください。"},
            ensure_ascii=False,
            indent=2,
        )

    matches = []
    for feat in session_state.session.filtered_features:
        if peak_id is not None and str(feat.get("id")) == str(peak_id):
            matches.append(feat)
        elif (
            peak_name is not None
            and isinstance(feat.get("name"), str)
            and peak_name.lower() in feat.get("name", "").lower()
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
