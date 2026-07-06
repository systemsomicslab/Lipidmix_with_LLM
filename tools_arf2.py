"""ARF2（MS-DIAL 全体カタログ）ツール群: arf2_parser, arf2_annotate_identities。

deps: mcp_core / session_state / path_resolvers / tool_helpers / arf2_reader /
lipid_identity。tools_* / server は import しない。
"""
import io
import json

import lipid_identity
import session_state
from mcp_core import mcp
from path_resolvers import resolve_arf2_file_path
from tool_helpers import _identity_tables

__all__ = ["arf2_parser", "arf2_annotate_identities"]


@mcp.tool()
def arf2_parser(file_path: str | None = None) -> list:
    """
    .arf2 ファイル（MS-DIALの全体カタログ）を解析し、データセットの全体像（メタデータ）を要約して返します。
    このファイルにはサンプル個別の強度データは含まれていないため、PCA等の多変量解析は実行できません。
    データ全体の品質や、アノテーション状況の概観を把握するために使用します。
    """
    file_path = resolve_arf2_file_path(file_path)
    if not file_path:
        return ["データディレクトリに .arf2 ファイルが見つかりませんでした。"]

    from arf2_reader import deserialize, generate_text_summary, summarize_arf2_data
    from pathlib import Path
    import json

    try:
        # ARF2データの読み込み
        with open(file_path, 'rb') as f:
            deserialized_data = deserialize(f)

        if not deserialized_data:
            return [".arf2 ファイルのパースに失敗したか、データが空です。"]

        # 要約テキストの生成
        text_summary = generate_text_summary(deserialized_data)

        # 将来の検索やフィルタリング用に、カタログデータをセッションに保持しておく
        session_state.session.current_file_path = file_path
        session_state.session.features = deserialized_data

        output_text = (
            f"### 📂 ARF2 カタログデータのパース完了: {Path(file_path).name}\n"
            f"このファイルはデータセット全体の要約（平均値等）のみを含んでおり、サンプル別データを持たないためPCAは実行できません。\n\n"
            f"{text_summary}\n\n"
            f"※ 個別のサンプル比較やPCAを行いたい場合は、詳細データを持つ `.arf` (PeakProperties.arf など) を対象に `arf_parser` を使用してください。"
        )

        return [output_text]

    except Exception as e:
        import traceback
        return [f"ARF2解析中にエラーが発生しました: {str(e)}\n{traceback.format_exc()}"]


@mcp.tool()
def arf2_annotate_identities(file_path: str | None = None, max_rows: int = 50) -> str:
    """指定/自動解決の ARF2 スポット注釈を GOSLIN 正規化・RefMet/LIPID MAPS ID・
    MSI レベルで一括標準化して返す（オフライン、上位 max_rows 件）。

    ARF2 には MS/MS 取得フラグ・精密質量誤差が無いため、MSI は保守的にクラス上限で
    評価する（`has_msms=False`, バンドは UNKNOWN）。より確度の高い MSI 評価は個別ピークの
    `verify_peak_annotation`（精密質量・アダクト整合を含むドシエ）を参照。
    """
    path = resolve_arf2_file_path(file_path)
    if not path:
        return json.dumps({"status": "error", "message": ".arf2 が見つかりません。"},
                          ensure_ascii=False, indent=2)
    from arf2_reader import deserialize as arf2_deserialize
    with open(path, "rb") as fh:
        spots = arf2_deserialize(io.BytesIO(fh.read()))
    tables = _identity_tables()
    rows = []
    for spot in spots[:max_rows]:
        raw_name = spot.get("Name") or ""
        name = "" if raw_name.strip().lower() == "unknown" else raw_name
        feat = {"name": name, "ontology": spot.get("Ontology") or "",
                "has_msms": False}
        # ARF2 に MS/MS 取得フラグは無いため has_msms=False（MSI は保守的にクラス上限）
        block = lipid_identity.build_identity_block(
            feat, tables, mass_error_band="UNKNOWN", adduct_band="UNKNOWN")
        rows.append({"MasterAlignmentID": spot.get("MasterAlignmentID"),
                     "name": feat["name"], "normalized": block["goslin"]["normalized"],
                     "refmet": block["reference"]["refmet_name"],
                     "lipid_maps_category": block["reference"]["lipid_maps_category"],
                     "msi_level": block["msi"]["level"]})
    return json.dumps({"status": "success", "count": len(rows), "rows": rows},
                      ensure_ascii=False, indent=2)
