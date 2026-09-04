"""Console 実行層の MCP ツール: console_plan, console_run, console_status, job_list。

spec §8 参照。
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.types import ToolAnnotations

from lipidmix.core import session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.mcp_errors import console_error, mztab_error
from lipidmix.core.serialization import json_payload

__all__ = ["console_plan", "console_run", "console_status", "job_list"]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
          structured_output=False)
def console_plan(
    dataset_root: str,
    method_file: str,
    polarity: str = "positive",
    measure: str = "peak_height",
    omics: str = "lipidomics",
    save_project: bool = True,
    timeout_s: int = 21600,
) -> str:
    """MS-DIAL Console の実行計画を作成し、analysis-job.json を生成します。

    Parameters
    ----------
    dataset_root:
        生データフォルダのパス（.wiff / .raw 等が入っているフォルダ）。
        リポジトリ外のパスを指定してください。
    method_file:
        MS-DIAL Console のパラメータファイル（ASCII テキスト。`key: value` 形式）。
        MS-DIAL GUI の Export > Parameter で出力できます。
        **`.mdproject` / `.mddata` は使えません**（ZIP なので Console は中身を
        読めず、全パラメータが既定値のまま実行されます）。
    polarity:
        "positive" または "negative"。
    measure:
        "peak_height"（既定）または "peak_area_above_zero"。
        現行 MS-DIAL Console は peak_height のみ正確に出力します。
        "peak_area_above_zero" を指定すると UNSUPPORTED_AREA_CONSOLE で停止します。
    omics:
        "lipidomics"（既定）または "metabolomics"。
    save_project:
        True（既定）なら MS-DIAL Console に -p を渡し、GUI で開ける
        .mdproject を出力フォルダに生成させます。
    timeout_s:
        MS-DIAL Console のタイムアウト秒数（既定 21600 ＝ 6 時間）。
        実測では 4 サンプルで約 3 分。60 サンプル規模では 1 時間を超え得ます。

    成功すると session.current_job_path にジョブパスが設定され、
    console_run でそのまま実行できます。
    """
    execution_error = _execution_options_error(save_project, timeout_s)
    if execution_error:
        return execution_error

    if measure == "peak_area_above_zero":
        return mztab_error(
            "UNSUPPORTED_AREA_CONSOLE",
            "現行 MS-DIAL Console は peak_area_above_zero を正確に出力できません。"
            "peak_height を使用するか、GUI から Area 出力を別途実行してください。",
        )

    if polarity not in ("positive", "negative"):
        return console_error("JOB_NOT_PLANNED", f"polarity は 'positive' または 'negative' です: {polarity!r}")
    if omics not in ("lipidomics", "metabolomics"):
        return console_error("JOB_NOT_PLANNED", f"omics は 'lipidomics' または 'metabolomics' です: {omics!r}")

    root = Path(dataset_root).expanduser()
    if not root.is_dir():
        return console_error("JOB_NOT_PLANNED", f"dataset_root が存在しません: {dataset_root}")

    mf = Path(method_file).expanduser()
    if not mf.is_file():
        return console_error("METHOD_FILE_NOT_FOUND", f"メソッドファイルが見つかりません: {method_file}")
    if not _looks_like_method_text(mf):
        return console_error(
            "METHOD_FILE_NOT_TEXT",
            f"メソッドファイルが MS-DIAL Console の読める形式ではありません: {method_file}  "
            "Console は ASCII のテキストを `key: value`（例 `Ion mode: Negative`）として"
            "1 行ずつ読みます。.mdproject / .mddata は ZIP なので、渡しても"
            "エラーにならず全パラメータが既定値のまま実行されます。"
            "MS-DIAL GUI の Export > Parameter で出したパラメータファイルを指定してください。",
            {"method_file": str(mf)},
        )

    try:
        from lipidmix.console import runner as console_runner
        exe = console_runner.get_exe_path()
    except EnvironmentError as exc:
        return console_error("MSDIAL_EXE_NOT_FOUND", str(exc))
    if not console_runner.is_console_exe(exe):
        return console_error(
            "MSDIAL_EXE_NOT_CONSOLE",
            f"MSDIAL_EXE が MS-DIAL Console ではありません: {exe}  "
            "--help にサブコマンド `lcms` が現れませんでした。GUI の MSDIAL.exe を"
            "指している可能性があります（GUI はコマンドラインを解釈せずウィンドウを"
            "開いたままになります）。MsdialWorkbench の Console 実行体"
            "（MSDIALCUI.exe）のパスを設定してください。",
            {"exe": exe},
        )

    from lipidmix.console.job_manager import create_job, raw_input_summary
    formats = raw_input_summary(root)
    if not formats:
        return console_error(
            "MIXED_RAW_FORMATS",
            f"データフォルダに MS-DIAL が読める計測ファイルがありません: {dataset_root}  "
            "対象拡張子: abf / ibf / cdf / mzml / wiff / raw / d / wiff2 / qgd / lcd / lrp / imzml",
            {"formats": formats},
        )
    if len(formats) > 1:
        return console_error(
            "MIXED_RAW_FORMATS",
            "データフォルダに MS-DIAL が対象とする拡張子が 2 種類以上あります: "
            + ", ".join(f"{ext}×{n}" for ext, n in sorted(formats.items()))
            + "。MS-DIAL Console はこの状態で対話プロンプトを出すため、"
            "stdin を塞いだ実行では異常終了します。続行できたとしても、"
            "同じ測定が複数の解析ファイルとして扱われます。"
            "SCIEX の出力は 1 測定につき .wiff と .wiff2 が両方できるのが普通なので、"
            "解析に使うほうだけを残したフォルダを作って指定してください"
            "（.wiff.scan は拡張子が .scan なので残して構いません）。",
            {"formats": formats},
        )
    input_count = sum(formats.values())

    try:
        job, job_path = create_job(
            dataset_root=root,
            method_file=mf,
            polarity=polarity,  # type: ignore[arg-type]
            measure=measure,    # type: ignore[arg-type]
            omics=omics,        # type: ignore[arg-type]
            input_count=input_count,
        )
    except ValueError as exc:
        return console_error("DATASET_ROOT_IN_REPO", str(exc))

    from lipidmix.console.job_manager import save_job
    job.save_project = save_project
    job.timeout_s = timeout_s
    save_job(job, job_path)
    session_state.session.current_job_path = str(job_path)

    warnings: list[str] = []
    if any(p.is_file() and (p.name.startswith("AlignResult-") or "AlignmentResult" in p.name)
           for p in root.iterdir()):
        warnings.append(
            "既存のアライメント結果がデータフォルダにあります。MS-DIAL Console は"
            "実行のたびに別タイムスタンプの一式を同じフォルダへ追加するため、"
            "複数バッチが混在します。どれが今回の生成物かは console_status の"
            "artifacts で確認してください。")

    return json_payload({
        "status": "planned",
        "job_id": job.job_id,
        "job_path": str(job_path),
        "run_dir": job.run_dir,
        "polarity": polarity,
        "measure": measure,
        "omics": omics,
        "input_count": input_count,
        "save_project": save_project,
        "timeout_s": timeout_s,
        "method_file": str(mf),
        "warnings": warnings,
        "next": "console_run を呼び出して実行を開始してください",
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
          structured_output=False)
def console_run(job_path: str | None = None) -> str:
    """MS-DIAL Console を実行します。

    job_path: analysis-job.json へのパス。省略時は session.current_job_path を使用します。
    事前に console_plan を実行しておく必要があります。

    実行完了後、session.current_job_path は同じジョブを指し続けます。
    結果は console_status または dataset_load で確認してください。
    """
    resolved = _resolve_job_path(job_path)
    if isinstance(resolved, str):
        return resolved  # error envelope

    from lipidmix.console.job_manager import load_job, update_status, save_job
    try:
        job = load_job(resolved)
    except FileNotFoundError:
        return console_error("JOB_NOT_FOUND", f"analysis-job.json が見つかりません: {resolved}")
    except ValueError as exc:
        return console_error("JOB_NOT_FOUND", str(exc))

    if job.status != "planned":
        return console_error(
            "JOB_NOT_PLANNED",
            f"実行できるのは status=planned のジョブだけです。現在のステータス: {job.status}",
            {"job_id": job.job_id, "status": job.status},
        )

    execution_error = _execution_options_error(job.save_project, job.timeout_s)
    if execution_error:
        return execution_error

    from lipidmix.console import runner as console_runner
    try:
        exe = console_runner.get_exe_path()
    except EnvironmentError as exc:
        update_status(resolved, "failed", error=str(exc))
        return console_error("MSDIAL_EXE_NOT_FOUND", str(exc))
    try:
        is_console = console_runner.is_console_exe(exe, raise_on_os_error=True)
    except OSError as exc:
        update_status(resolved, "failed", error=str(exc))
        return console_error("MSDIAL_EXE_NOT_FOUND", str(exc))
    if not is_console:
        return console_error(
            "MSDIAL_EXE_NOT_CONSOLE",
            f"MSDIAL_EXE が MS-DIAL Console ではありません: {exe}  "
            "--help にサブコマンド `lcms` が現れませんでした。GUI の MSDIAL.exe を"
            "指している可能性があります（GUI はコマンドラインを解釈せずウィンドウを"
            "開いたままになります）。MsdialWorkbench の Console 実行体"
            "（MSDIALCUI.exe）のパスを設定してください。",
            {"exe": exe},
        )

    run_dir = Path(job.run_dir)
    dataset_root = Path(job.dataset_root)
    from lipidmix.console.output_collector import RUNS_SUBDIR, snapshot
    # MS-DIAL Console は -o にエクスポートだけを出し、.pai2 / .dcl / .arf /
    # .arf2 / .EIC.aef は生データフォルダへ出す。両方を撮らないと MS/MS 根拠の
    # 経路が丸ごと空になる（docs/HISTRY.md 2026-09-03(6)）。run_dir は
    # dataset_root 配下なので、dataset_root 側では runs/ を除く。
    roots = {"run_dir": run_dir, "dataset_root": dataset_root}
    befores = {
        "run_dir": snapshot(run_dir),
        "dataset_root": snapshot(dataset_root, exclude_dir_names={RUNS_SUBDIR}),
    }

    update_status(resolved, "running")

    from lipidmix.console.runner import (
        run_msdial,
        MsdialExeNotFoundError,
        MsdialTimeoutError,
        MsdialNonZeroExitError,
    )
    timeout_error: MsdialTimeoutError | None = None
    try:
        run_msdial(
            method_file=Path(job.method_file),
            dataset_root=dataset_root,
            run_dir=run_dir,
            timeout_s=job.timeout_s,
            exe_path=exe,
            save_project=job.save_project,
        )
    except MsdialExeNotFoundError as exc:
        update_status(resolved, "failed", error=str(exc))
        return console_error("MSDIAL_EXE_NOT_FOUND", str(exc))
    except MsdialTimeoutError as exc:
        timeout_error = exc
    except MsdialNonZeroExitError as exc:
        update_status(resolved, "failed", error=str(exc))
        return console_error(
            "MSDIAL_NONZERO_EXIT", str(exc),
            {"log": str(run_dir / "msdial.log")},
        )
    except OSError as exc:
        # MSDIAL_EXE が実在しないパスを指す等。console_plan は環境変数が
        # 空でないことしか見ていないため、ここが実在確認の最後の砦になる。
        update_status(resolved, "failed", error=str(exc))
        return console_error(
            "MSDIAL_EXE_NOT_FOUND",
            f"MS-DIAL Console を起動できませんでした: {exc}",
            {"exe": os.environ.get("MSDIAL_EXE", ""), "log": str(run_dir / "msdial.log")},
        )
    except Exception as exc:  # 想定外。running に固着させないことが最優先
        update_status(resolved, "failed", error=repr(exc))
        return console_error(
            "MSDIAL_NONZERO_EXIT",
            f"MS-DIAL Console の実行中に想定外のエラーが発生しました: {exc!r}",
            {"log": str(run_dir / "msdial.log")},
        )

    # 成功時もタイムアウト時も同じ二重ルートを一度だけ収集し、収集後の
    # 出力判定・永続化までを同じ最終化用の保護経路に置く。タイムアウトは Console
    # の停止理由であって、停止前の生成物を捨てる理由ではない。
    try:
        from lipidmix.console.output_collector import collect_artifacts
        # ジョブが宣言した polarity / measure を渡す。MS-DIAL のアライメント出力名は
        # 極性トークンを持たないので、渡さないと全エントリが既定の positive になる。
        mztab_entries, other_artifacts = collect_artifacts(
            roots, befores,
            declared_polarity=job.polarity,
            declared_measure=job.measure,
        )

        if timeout_error:
            status = "partial" if mztab_entries or other_artifacts else "failed"
            _persist_collected_outputs(
                resolved, mztab_entries, other_artifacts, status=status, error=str(timeout_error))
            return console_error(
                "MSDIAL_TIMEOUT", str(timeout_error),
                _timeout_details(resolved, status, len(mztab_entries), len(other_artifacts)),
            )

        if not mztab_entries and not other_artifacts:
            update_status(resolved, "failed", error="実行後に新規生成物が見つかりません")
            return console_error(
                "NO_JOB_OUTPUT",
                "MS-DIAL Console が終了しましたが、出力ファイルが生成されませんでした。"
                f"ログを確認してください: {run_dir / 'msdial.log'}",
            )

        job = _persist_collected_outputs(
            resolved, mztab_entries, other_artifacts, status="completed", error=None)
    except Exception as exc:  # 想定外。running に固着させないことが最優先
        error = repr(exc)
        if timeout_error:
            error = f"{timeout_error}; 実行後処理に失敗しました: {error}"
        _record_finalization_failure(resolved, error)
        if timeout_error:
            return console_error(
                "MSDIAL_TIMEOUT", str(timeout_error),
                _timeout_details(resolved, "failed", 0, 0),
            )
        return console_error(
            "JOB_POST_RUN_FAILED",
            "MS-DIAL Console の実行後処理（生成物収集・保存）でエラーが"
            f"発生しました: {exc!r}",
            {"log": str(run_dir / "msdial.log")},
        )

    return json_payload({
        "status": "completed",
        "job_id": job.job_id,
        "job_path": str(resolved),
        "mztab_files": len(mztab_entries),
        "other_artifacts": len(other_artifacts),
        "run_dir": job.run_dir,
        "warnings": job.warnings,
        "next": "dataset_load でmzTab-M を読み込み、解析を開始してください",
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def console_status(job_path: str | None = None) -> str:
    """ジョブの現在のステータスを返します。

    job_path: analysis-job.json へのパス。省略時は session.current_job_path を使用します。
    """
    resolved = _resolve_job_path(job_path)
    if isinstance(resolved, str):
        return resolved

    from lipidmix.console.job_manager import load_job
    try:
        job = load_job(resolved)
    except (FileNotFoundError, ValueError) as exc:
        return console_error("JOB_NOT_FOUND", str(exc))

    return json_payload({
        "job_id": job.job_id,
        "status": job.status,
        "polarity": job.polarity,
        "measure": job.measure,
        "omics": job.omics,
        "dataset_root": job.dataset_root,
        "run_dir": job.run_dir,
        "mztab_files": [
            {"path": e.path, "polarity": e.polarity, "measure": e.measure, "root": e.root}
            for e in job.primary_mztab_files
        ],
        "artifact_count": len(job.artifacts),
        # 生成物は 1 サンプルにつき複数出る（.pai2 / .dcl / _tags.xml / .mdpeak /
        # .mdmsp）。60 サンプルで数百行になるので、列名を 1 回だけ書く TSV で返す。
        # 1 件 1 JSON オブジェクトにするとキー名の反復だけで戻り値が数万字になり、
        # 後ろの warnings / error が埋没する。
        "artifacts": _artifacts_tsv(job.artifacts),
        "execution": {"save_project": job.save_project, "timeout_s": job.timeout_s},
        "warnings": job.warnings,
        "error": job.error,
        "updated_at": job.updated_at,
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def job_list(dataset_root: str) -> str:
    """dataset_root/runs/ 以下のジョブ一覧を新しい順に返します。

    dataset_root: 生データフォルダのパス（console_plan で指定したものと同じ）。
    """
    root = Path(dataset_root).expanduser()
    if not root.is_dir():
        return console_error("JOB_NOT_FOUND", f"dataset_root が存在しません: {dataset_root}")

    from lipidmix.console.job_manager import list_jobs, load_job
    paths = list_jobs(root)
    if not paths:
        return json_payload({"dataset_root": str(root), "jobs": [], "count": 0})

    jobs = []
    for p in paths:
        try:
            j = load_job(p)
            jobs.append({
                "job_id": j.job_id,
                "status": j.status,
                "polarity": j.polarity,
                "measure": j.measure,
                "updated_at": j.updated_at,
                "job_path": str(p),
            })
        except Exception:
            jobs.append({"job_path": str(p), "status": "unreadable"})

    return json_payload({"dataset_root": str(root), "jobs": jobs, "count": len(jobs)})


# ---------- 内部ヘルパ ----------

def _looks_like_method_text(path: Path) -> bool:
    """MS-DIAL Console の ConfigParser が読める形かを判定する。

    ConfigParser は ASCII のテキストを 1 行ずつ読み、`#` 始まりを飛ばして
    最初の `:` または `=` で key/value に割る。ここでは先頭 8192 バイトだけを
    調べ、テキストであることと key/value 行が 1 つ以上あることを確認する。

    **先頭だけを読む**。この判定が弾く相手（.mdproject / .mddata）は 60 サンプル
    規模で GB 級になり、全体を読むとエラー封筒を返す前にメモリを潰す。
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(8192)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    text = head.decode("ascii", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        positions = [i for i in (stripped.find(":"), stripped.find("=")) if i > 0]
        if positions:
            return True
    return False


def _artifacts_tsv(artifacts) -> str:
    """生成物一覧を TSV（列名 1 回）で返す。生成物ゼロなら空文字。

    列名だけの行を返すと「1 件ある」と読めるため、空のときは何も返さない
    （件数は artifact_count が持つ）。
    """
    if not artifacts:
        return ""
    lines = ["path\trole\tformat\troot"]
    lines.extend(f"{a.path}\t{a.role}\t{a.format}\t{a.root}" for a in artifacts)
    return "\n".join(lines)


def _meta_conflict_warnings(mztab_entries) -> list[str]:
    """ファイル名と宣言値が食い違ったエントリを warning にする。

    collect_artifacts はファイル名側を採用する（実物の性質を語るのはファイル）。
    採用の事実だけを validation に残して黙っていると、ユーザーは自分が
    console_plan で宣言した値と違うものを解析していることに気付けない。
    """
    warnings: list[str] = []
    for entry in mztab_entries:
        for axis, detail in (entry.validation.get("conflicts") or {}).items():
            warnings.append(
                f"{entry.path}: {axis} がジョブの宣言と食い違います"
                f"（ファイル名={detail['filename']} / 宣言={detail['job_declared']}）。"
                "ファイル名側を採用しました。"
            )
    return warnings


def _unsupported_mztab_warnings(artifacts) -> list[str]:
    """未対応 measure の .mzTab（Normalized*）を拾ったことを伝える。

    記録は残すが正準候補にはしない（spec §8.1）。黙って捨てると
    「出力があるのに dataset_load が読めない」という説明不能な状態になる。
    """
    from lipidmix.console.output_collector import UNSUPPORTED_MZTAB_ROLE
    paths = [a.path for a in artifacts if a.role == UNSUPPORTED_MZTAB_ROLE]
    if not paths:
        return []
    return [
        "未対応の定量種別（normalized）の mzTab を検出しました: "
        + ", ".join(paths)
        + "。peak_height / peak_area_above_zero のみ対応するため、"
        "正準候補（primary_mztab_files）には含めていません。"
    ]


def _missing_per_sample_output_warnings(artifacts) -> list[str]:
    """サンプル別ファイル（.pai2）が 1 つも出ていないことを伝える。

    MS-DIAL Console は .pai2 を**生データフォルダ側**に書く（-o ではない。
    docs/HISTRY.md 2026-09-03(6) の実走で確認）。collect_artifacts が両ルートを
    見るようになったので、この検査は「本当に出ていない」ときだけ発火する。
    .pai2 が無いと pai2_parser / dcl_find_msms が読むものが無く、MS/MS 根拠の
    経路が丸ごと空になる。アライメント結果だけは出ているので実行は成功扱いの
    まま、「後で MS/MS を辿れない」ことだけ先に知らせる。
    """
    if any(a.format == "pai2" for a in artifacts):
        return []
    return [
        "サンプル別ファイル（.pai2）が 1 つも生成されていません。"
        "MS/MS 根拠（pai2_parser / dcl_find_msms）を辿る経路が使えません。"
    ]


def _execution_options_error(save_project: object, timeout_s: object) -> str | None:
    """新しい実行オプションを、ジョブ作成・実行の両入口で同じ規則で検査する。"""
    if type(save_project) is not bool:
        return console_error(
            "JOB_NOT_PLANNED",
            f"save_project は bool です: {save_project!r}",
        )
    if type(timeout_s) is not int or timeout_s <= 0:
        return console_error(
            "JOB_NOT_PLANNED",
            f"timeout_s は 0 より大きい int です: {timeout_s!r}",
        )
    return None


def _persist_collected_outputs(
    job_path: Path,
    mztab_entries,
    other_artifacts,
    *,
    status: str,
    error: str | None,
):
    """収集済みの生成物と終端状態を一度だけ永続化する。"""
    from lipidmix.console.job_manager import load_job, save_job

    job = load_job(job_path)
    job.primary_mztab_files = mztab_entries
    job.artifacts = other_artifacts
    job.warnings.extend(_meta_conflict_warnings(mztab_entries))
    job.warnings.extend(_unsupported_mztab_warnings(other_artifacts))
    job.warnings.extend(_missing_per_sample_output_warnings(other_artifacts))
    job.status = status  # type: ignore[assignment]
    job.error = error
    save_job(job, job_path)
    return job


def _record_finalization_failure(job_path: Path, error: str) -> None:
    """最終化に失敗したジョブを、書き込める場合は failed として残す。"""
    from lipidmix.console.job_manager import update_status

    try:
        update_status(job_path, "failed", error=error)
    except Exception:
        # ロック等で failed を書けない場合も、MCP 境界から例外を漏らさない。
        pass


def _timeout_details(
    job_path: Path,
    status: str,
    mztab_count: int,
    artifact_count: int,
) -> dict:
    """タイムアウト封筒へ、後からジョブを追える最小限の状況を入れる。"""
    return {
        "job_path": str(job_path),
        "status": status,
        "mztab_files": mztab_count,
        "other_artifacts": artifact_count,
    }


def _resolve_job_path(job_path: str | None) -> Path | str:
    """引数またはセッションから job_path を解決する。失敗はエラーエンベロープ文字列を返す。"""
    target = job_path or session_state.session.current_job_path
    if not target:
        return console_error(
            "JOB_NOT_FOUND",
            "job_path が指定されておらず、セッションにジョブもありません。"
            "先に console_plan を実行してください。",
            {"required_tools": ["console_plan"]},
        )
    return Path(target).expanduser()
