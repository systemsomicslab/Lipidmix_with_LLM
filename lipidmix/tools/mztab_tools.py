"""mzTab-M 入口 MCP ツール: dataset_load, dataset_status。spec §12 参照。"""
from pathlib import Path

from mcp.types import ToolAnnotations

from lipidmix.core import session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.mcp_errors import missing_state, mztab_error
from lipidmix.core.serialization import json_payload
from lipidmix.mztab.reader import parse_mztab
from lipidmix.mztab import evidence as mztab_evidence
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
            "DATASET_BAD_REQUEST",
            "mztab_path と job_path を同時に指定できません。どちらか一方だけを"
            "使ってください（mztab_path: .mzTab への直接パス。job_path:"
            " analysis-job.json のパス。console_run 完了後は job_path を推奨）。",
        )

    if job_path:
        return _load_from_job(job_path)

    if not mztab_path:
        # missing_state ではない: 「前提状態が無いので別ツールを実行すれば直る」
        # 復旧可能な状態不足ではなく、引数を直して呼び直すしかない確定エラー。
        # required_tools に dataset_load 自身を挙げると、契約どおりに動く
        # クライアントが同じ呼び出しを再実行して無限ループする。
        return mztab_error(
            "DATASET_BAD_REQUEST",
            "mztab_path または job_path のどちらか一方を指定してください"
            "（mztab_path: .mzTab への直接パス。job_path: analysis-job.json の"
            "パス。console_run 完了後は job_path を推奨）。",
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
    # 検出状態（gap-fill）は mzTab-M に無い。隣接 `.arf` から補えるかを試す。
    # 取り込めなくても解析は続けられるので、封筒ではなく warning で伝える。
    mztab_evidence.attach_to_dataset(ds, p)
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

    # ジョブが宣言した polarity + measure で正準エントリを選ぶ。
    # **[0] を暗黙に採ってはいけない**——エントリは相対パスの辞書順に並ぶので、
    # 実データ（Area_ / Height_ / Normalized* が同居する MS-DIAL 出力）では
    # Area_ が先頭に来る。console_plan が peak_area_above_zero を
    # UNSUPPORTED_AREA_CONSOLE で拒否しているのに、ここで黙って読んでしまう。
    selected = _select_primary_entry(job)
    if isinstance(selected, str):
        return selected  # error envelope
    entry = selected
    mztab_abs = _artifact_abs_path(job, getattr(entry, "root", "run_dir"), entry.path)

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

    # 終端状態が completed でないジョブ（タイムアウト後の partial など）は、
    # MS-DIAL が途中で止まった実行の生成物を指している。mzTab 単体は構文検証を
    # 通ってしまうため、ここで言わないと下流（前処理・PCA・差次的解析・
    # エクスポート）が中断された実行の結果を完了品として扱う。**先頭に差す**。
    if job.status != "completed":
        ds.validation_result.setdefault("warnings", []).insert(0, (
            f"このジョブは status={job.status} です（completed ではありません）。"
            "MS-DIAL Console の実行は最後まで到達しておらず、読み込んだ mzTab-M は"
            "中断時点の生成物です。特徴量・サンプルが欠けている可能性があるため、"
            "console_status の error と msdial.log を確認してください。"))

    # ジョブ由来フィールドを設定
    ds.job_path = str(job_p)
    for art in job.artifacts:
        abs_p = str(_artifact_abs_path(job, getattr(art, "root", "run_dir"), art.path))
        ds.artifact_paths.setdefault(art.role, []).append(abs_p)

    # artifact_paths を入れ終えてから呼ぶ。handoff が記録した peak_matrix_source を
    # 候補の先頭に使えるのは、この時点以降だけ。
    mztab_evidence.attach_to_dataset(ds, mztab_abs)

    session_state.session.dataset = ds

    lines = [_summary_text(ds, mztab_abs.name)]
    if len(job.primary_mztab_files) > 1:
        others = [e.path for e in job.primary_mztab_files if e is not entry]
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

    `samples` に name/role の TSV が入る。dataset_differential の
    group_a / group_b はここに出る名前をそのまま使う。

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
    from lipidmix.core.version import server_version
    payload = {
        # 更新後に MCP サーバを再起動し忘れると古いプロセスが黙って動き続ける。
        # 「おかしい」と思ったときに 1 回で分かるよう、ここに刻む。
        "server_version": server_version(),
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
        # 群指定（dataset_differential）に必要なサンプル名。件数だけ返していた頃は、
        # 名前を知る手段が「わざと群サイズ不足のエラーを起こして details を読む」
        # しか無かった。行が並ぶ一覧なので TSV（列名 1 回）で返す。
        "samples": _samples_tsv(ds),
        # 検出状態の有無。「無い」と「全部未検出」は解釈が正反対なので、
        # 率だけを返して available を省くことはしない。
        "detection": _detection_summary(ds),
    }
    if ds.job_path:
        payload["job_path"] = ds.job_path
    if ds.artifact_paths:
        payload["artifact_roles"] = {role: len(paths) for role, paths in ds.artifact_paths.items()}
    return json_payload(payload)


# ---------- 内部ヘルパ ----------


def _detection_summary(ds) -> dict:
    """検出状態（gap-fill）の要約。行列そのものは返さない（42,840 セル規模）。"""
    qc = ds.feature_qc or {}
    if ds.detected_mask is None:
        return {"available": False,
                "reason": qc.get("reason", "no_candidate"),
                "note": ("mzTab-M の非ゼロ値には gap-fill による補間値が含まれ得ます。"
                         "検出状態が取り込めていないため、検出率・欠測率は語れません。")}
    return {"available": True,
            "source": qc.get("source"),
            "n_cells": qc.get("n_cells"),
            "n_detected": qc.get("n_detected"),
            "gap_filled_rate": qc.get("gap_filled_rate")}

def _artifact_abs_path(job, root: str, rel: str) -> Path:
    """生成物の相対パスを、記録された出所ルートから絶対パスへ戻す。

    MS-DIAL Console は run_dir と生データフォルダの両方へ生成物を出すため、
    analysis-job.v2 の root に応じて解決する。
    """
    base = Path(job.dataset_root) if root == "dataset_root" else Path(job.run_dir)
    return (base / rel).resolve()

def _samples_tsv(ds) -> str:
    """サンプル名と役割を TSV で返す（列名 1 回 + 1 行 1 サンプル）。

    前処理済みなら ds.roles を、まだなら同じ判定関数（detect_sample_roles）を
    その場で適用する。ARF 経路（arf_list_sample_roles）と同じ判定を使うので、
    どちらの経路でも同じ試料が同じ役割になる。
    """
    from lipidmix.analysis import preprocessing
    names = list(ds.sample_names)
    roles = ds.roles or preprocessing.detect_sample_roles(names)
    lines = ["name\trole"]
    lines += [f"{n}\t{roles.get(n, 'sample')}" for n in names]
    return "\n".join(lines)


def _select_primary_entry(job):
    """ジョブの宣言（polarity + measure）で正準 mzTab エントリを一意に選ぶ。

    一意に決まらないときは選ばずに停止する（spec §7「暗黙の単数選択と
    newest_modified_time は廃止する」）。どれを読むかは解析結果そのものを
    変えるので、LLM にもファイル名の辞書順にも決めさせない。
    成功なら MztabEntry、失敗ならエラーエンベロープ文字列を返す。
    """
    candidates = job.primary_mztab_files
    by_measure = [e for e in candidates if e.measure == job.measure]
    if not by_measure:
        return mztab_error(
            "QUANTIFICATION_CONFLICT",
            f"ジョブは measure={job.measure} を宣言していますが、"
            "その定量種別の mzTab-M が生成物にありません。"
            "別種別のファイルを代わりに読むと、宣言と違う数値で解析することになります。"
            "意図的に別種別を読むなら mztab_path で明示してください。",
            {"declared_measure": job.measure, "candidates": _describe(candidates)},
        )

    matched = [e for e in by_measure if e.polarity == job.polarity]
    if not matched:
        return mztab_error(
            "POLARITY_MISMATCH",
            f"ジョブは polarity={job.polarity} を宣言していますが、"
            f"measure={job.measure} の候補にその極性がありません。"
            "極性が違えば検出される脂質クラスが変わるため、代替で読みません。"
            "意図的に別極性を読むなら mztab_path で明示してください。",
            {"declared_polarity": job.polarity, "declared_measure": job.measure,
             "candidates": _describe(candidates)},
        )

    if len(matched) > 1:
        return mztab_error(
            "AMBIGUOUS_PRIMARY_MZTAB",
            f"polarity={job.polarity} / measure={job.measure} の候補が "
            f"{len(matched)} 件あり、一意に決まりません。"
            "mztab_path でどれを読むか明示してください。",
            {"candidates": _describe(matched)},
        )
    return matched[0]


def _describe(entries) -> list[dict]:
    return [{"path": e.path, "polarity": e.polarity, "measure": e.measure}
            for e in entries]


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


def _detection_line(ds) -> str:
    qc = ds.feature_qc or {}
    if ds.detected_mask is None:
        return ("- 検出状態: 取り込めていません"
                f"（{qc.get('reason', 'no_candidate')}）。非ゼロ値に gap-fill 補間が"
                "混在し得るため検出率・欠測率は語れません")
    rate = qc.get("gap_filled_rate")
    percent = f"{rate * 100:.1f}%" if rate is not None else "不明"
    return (f"- 検出状態: {qc.get('source')} 由来 — 実測 {qc.get('n_detected')}/"
            f"{qc.get('n_cells')} セル（gap-fill {percent}）")


def _summary_text(ds, filename: str) -> str:
    lines = [
        f"## dataset_load 完了: {filename}",
        f"- source_format: {ds.source_format}",
        f"- 特徴量数: {len(ds.feature_ids)}",
        f"- サンプル数: {len(ds.sample_names)}",
        f"- 定量種別: {ds.quantification_measure or '不明'} ({ds.quantification_confidence})",
        f"- 検証: {'OK' if ds.validation_result['ok'] else 'NG — ' + '; '.join(ds.validation_result['errors'])}",
        f"- InChIKey 付き: {ds.inchikey_coverage.get('with_inchikey', 0)}/{ds.inchikey_coverage.get('total_features', 0)} 件",
        # 検出状態は入口で言う。実データでは 70% のセルが gap-fill だったので、
        # これを知らずに非ゼロを検出と数えると検出率を 3 倍以上に過大評価する。
        _detection_line(ds),
    ]
    if ds.validation_result.get("warnings"):
        lines.append(f"- 警告 {len(ds.validation_result['warnings'])} 件: " +
                     "; ".join(ds.validation_result["warnings"][:3]))
    return "\n".join(lines)
