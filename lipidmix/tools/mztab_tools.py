"""mzTab-M 入口 MCP ツール: dataset_load, dataset_status。spec §12 参照。"""
from pathlib import Path

from mcp.types import ToolAnnotations

from lipidmix.core import session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.mcp_errors import mztab_error
from lipidmix.core.serialization import json_payload
from lipidmix.mztab.reader import parse_mztab
from lipidmix.mztab.dataset_state import build_dataset_state

__all__ = ["dataset_load", "dataset_status"]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
          structured_output=False)
def dataset_load(mztab_path: str) -> str:
    """mzTab-M 2.0 ファイルを読み込んで正準状態（DatasetState）を作る。

    mztab_path: .mzTab ファイルへの絶対パス（list_data_files で取得できる）。
    成功すると session.dataset に DatasetState が格納され、dataset_status で内容を確認できる。
    ファイルが存在しない場合は MZTAB_NOT_FOUND、構造不正は MZTAB_STRUCTURE_INVALID を返す。
    既存の session.arf / .arf2 / .pai2 / .eic は変更しない。
    """
    p = Path(mztab_path)
    if not p.is_file():
        return mztab_error(
            "MZTAB_NOT_FOUND",
            f"mzTab-M ファイルが見つかりません: {mztab_path}",
        )

    parse_result = parse_mztab(p)
    validation = _validate_or_error(parse_result, p.name)
    if isinstance(validation, str):
        return validation  # error envelope

    ds = build_dataset_state(parse_result, p.name, p)
    session_state.session.dataset = ds

    lines = [
        f"## dataset_load 完了: {p.name}",
        f"- source_format: {ds.source_format}",
        f"- 特徴量数: {len(ds.feature_ids)}",
        f"- サンプル数: {len(ds.sample_names)}",
        f"- 定量種別: {ds.quantification_measure or '不明'} ({ds.quantification_confidence})",
        f"- 検証: {'OK' if ds.validation_result['ok'] else 'NG — ' + '; '.join(ds.validation_result['errors'])}",
        f"- InChIKey 付き: {ds.inchikey_coverage.get('with_inchikey', 0)}/{ds.inchikey_coverage.get('total_features', 0)} 件",
    ]
    if ds.validation_result.get("warnings"):
        lines.append(f"- 警告 {len(ds.validation_result['warnings'])} 件: " +
                     "; ".join(ds.validation_result["warnings"][:3]))
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_status() -> str:
    """現在の DatasetState の概要を返す。

    dataset_load を先に実行しておくこと。
    未実行の場合は MZTAB_NOT_FOUND を返す。
    """
    ds = session_state.session.dataset
    if ds is None:
        return mztab_error(
            "MZTAB_NOT_FOUND",
            "DatasetState がありません。先に dataset_load を実行してください。",
        )
    return json_payload({
        "source_format": ds.source_format,
        "source_files": list(ds.source_files.keys()),
        "quantification_measure": ds.quantification_measure,
        "quantification_confidence": ds.quantification_confidence,
        "n_features": len(ds.feature_ids),
        "n_samples": len(ds.sample_names),
        "validation": {
            "ok": ds.validation_result.get("ok"),
            "n_errors": len(ds.validation_result.get("errors", [])),
            "n_warnings": len(ds.validation_result.get("warnings", [])),
        },
        "inchikey_coverage": ds.inchikey_coverage,
    })


def _validate_or_error(parse_result: dict, filename: str) -> dict | str:
    """バリデーションを実行し、失敗ならエラーエンベロープ文字列を返す。成功なら validation dict。"""
    from lipidmix.mztab.validator import validate_mztab
    result = validate_mztab(parse_result)
    if not result["ok"]:
        return mztab_error(
            "MZTAB_STRUCTURE_INVALID",
            f"mzTab-M 構造不正 ({filename}): " + "; ".join(result["errors"]),
            {"errors": result["errors"], "warnings": result["warnings"]},
        )
    return result
