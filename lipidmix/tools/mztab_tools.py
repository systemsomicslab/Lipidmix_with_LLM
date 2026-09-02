"""mzTab-M 入口 MCP ツール: dataset_load, dataset_status。spec §12 参照。"""
from pathlib import Path

from mcp.types import ToolAnnotations

from lipidmix.core import session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.mcp_errors import missing_state, mztab_error
from lipidmix.core.serialization import json_payload
from lipidmix.mztab.reader import parse_mztab
from lipidmix.mztab.dataset_state import build_dataset_state

__all__ = ["dataset_load", "dataset_status"]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
          structured_output=False)
def dataset_load(mztab_path: str | None = None, job_path: str | None = None) -> str:
    """mzTab-M 2.0 ファイルを読み込んで正準状態（DatasetState）を作る。

    呼び出し方:
      - mztab_path: .mzTab ファイルへの絶対パスを直接指定する場合。
      - job_path: analysis-job.json へのパスを指定する場合。ジョブの
        primary_mztab_files[0] から mzTab-M を自動選択する。
        console_run 完了後はこちらを推奨（polarity・measure が確定済み）。

    両方省略するとエラー、両方指定するとエラー。
    成功すると session.dataset に DatasetState が格納され、
    dataset_status で内容を確認できる。
    既存の session.arf / .arf2 / .pai2 / .eic は変更しない。
    """
    if mztab_path and job_path:
        return mztab_error(
            "MZTAB_NOT_FOUND",
            "mztab_path と job_path を同時に指定できません。どちらか一方を使ってください。",
        )

    if job_path:
        return _load_from_job(job_path)

    if not mztab_path:
        return missing_state(
            "dataset",
            ["dataset_load"],
            "mztab_path または job_path を指定してください。"
            "console_run 完了後は job_path（analysis-job.json のパス）を推奨します。",
        )

    # mztab_path 経路（既存動作）
    p = Path(mztab_path)
    if not p.is_file():
        return mztab_error(
            "MZTAB_NOT_FOUND",
            f"mzTab-M ファイルが見つかりません: {mztab_path}",
        )

    parse_result = parse_mztab(p)
    validation = _validate_or_error(parse_result, p.name)
    if isinstance(validation, str):
        return validation

    ds = build_dataset_state(parse_result, p.name, p)
    session_state.session.dataset = ds

    return _summary_text(ds, p.name)


def _load_from_job(job_path_str: str) -> str:
    """analysis-job.json を読み、primary_mztab_files[0] から DatasetState を構築する。"""
    job_p = Path(job_path_str).expanduser()
    if not job_p.is_file():
        return mztab_error(
            "MZTAB_NOT_FOUND",
            f"analysis-job.json が見つかりません: {job_path_str}",
        )

    try:
        from lipidmix.handoff.schema import AnalysisJob
        job = AnalysisJob.load(job_p)
    except (ValueError, KeyError) as exc:
        return mztab_error(
            "MZTAB_NOT_FOUND",
            f"analysis-job.json の読み込みに失敗しました: {exc}",
        )

    if not job.primary_mztab_files:
        return mztab_error(
            "MZTAB_NOT_FOUND",
            f"ジョブ {job.job_id} に primary_mztab_files がありません。"
            "console_run を先に実行してください。",
        )

    # 最初のエントリを使う（polarity・measure が異なる複数ファイルがある場合は
    # mztab_path で明示的に指定することを促す）
    entry = job.primary_mztab_files[0]
    mztab_abs = (Path(job.run_dir) / entry.path).resolve()

    if not mztab_abs.is_file():
        return mztab_error(
            "MZTAB_NOT_FOUND",
            f"mzTab-M ファイルが見つかりません: {mztab_abs}  "
            f"（job_id={job.job_id}, entry.path={entry.path}）",
        )

    parse_result = parse_mztab(mztab_abs)
    validation = _validate_or_error(parse_result, mztab_abs.name)
    if isinstance(validation, str):
        return validation

    ds = build_dataset_state(parse_result, mztab_abs.name, mztab_abs)

    # ジョブ由来フィールドを設定
    ds.job_path = str(job_p)
    for art in job.artifacts:
        abs_p = str((Path(job.run_dir) / art.path).resolve())
        ds.artifact_paths.setdefault(art.role, []).append(abs_p)

    session_state.session.dataset = ds

    lines = [_summary_text(ds, mztab_abs.name)]
    if len(job.primary_mztab_files) > 1:
        others = [e.path for e in job.primary_mztab_files[1:]]
        lines.append(
            f"- ※ ジョブには他に {len(others)} 件の mzTab-M があります: "
            + ", ".join(others)
            + "  別ファイルを読むには mztab_path で直接指定してください。"
        )
    artifact_summary = {role: len(paths) for role, paths in ds.artifact_paths.items()}
    if artifact_summary:
        lines.append(f"- 付随アーティファクト: {artifact_summary}")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_status() -> str:
    """現在の DatasetState の概要を返す。

    dataset_load を先に実行しておくこと。
    未実行の場合は missing_state エンベロープを返す。
    """
    ds = session_state.session.dataset
    if ds is None:
        return missing_state(
            "dataset",
            ["dataset_load"],
            "DatasetState がありません。先に dataset_load を実行してください。"
            "console_run 完了後は dataset_load(job_path=<analysis-job.json へのパス>) を推奨します。",
        )
    payload = {
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
    }
    if ds.job_path:
        payload["job_path"] = ds.job_path
    if ds.artifact_paths:
        payload["artifact_roles"] = {role: len(paths) for role, paths in ds.artifact_paths.items()}
    return json_payload(payload)


# ---------- 内部ヘルパ ----------

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


def _summary_text(ds, filename: str) -> str:
    lines = [
        f"## dataset_load 完了: {filename}",
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
