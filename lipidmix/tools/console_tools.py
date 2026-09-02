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
) -> str:
    """MS-DIAL Console の実行計画を作成し、analysis-job.json を生成します。

    Parameters
    ----------
    dataset_root:
        生データフォルダのパス（.wiff / .raw 等が入っているフォルダ）。
        リポジトリ外のパスを指定してください。
    method_file:
        MS-DIAL のメソッドファイル（.msdial / .mdproject）へのパス。
        MS-DIAL GUI であらかじめ設定を作成しておいてください。
    polarity:
        "positive" または "negative"。
    measure:
        "peak_height"（既定）または "peak_area_above_zero"。
        現行 MS-DIAL Console は peak_height のみ正確に出力します。
        "peak_area_above_zero" を指定すると UNSUPPORTED_AREA_CONSOLE で停止します。
    omics:
        "lipidomics"（既定）または "metabolomics"。

    成功すると session.current_job_path にジョブパスが設定され、
    console_run でそのまま実行できます。
    """
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

    try:
        from lipidmix.console.runner import get_exe_path
        get_exe_path()
    except EnvironmentError as exc:
        return console_error("MSDIAL_EXE_NOT_FOUND", str(exc))

    from lipidmix.console.job_manager import create_job, count_raw_inputs
    try:
        input_count = count_raw_inputs(root)
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

    session_state.session.current_job_path = str(job_path)

    return json_payload({
        "status": "planned",
        "job_id": job.job_id,
        "job_path": str(job_path),
        "run_dir": job.run_dir,
        "polarity": polarity,
        "measure": measure,
        "omics": omics,
        "input_count": input_count,
        "method_file": str(mf),
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

    run_dir = Path(job.run_dir)
    from lipidmix.console.output_collector import snapshot
    before = snapshot(run_dir)

    update_status(resolved, "running")

    from lipidmix.console.runner import (
        run_msdial,
        MsdialExeNotFoundError,
        MsdialTimeoutError,
        MsdialNonZeroExitError,
    )
    try:
        run_msdial(
            method_file=Path(job.method_file),
            dataset_root=Path(job.dataset_root),
            run_dir=run_dir,
        )
    except MsdialExeNotFoundError as exc:
        update_status(resolved, "failed", error=str(exc))
        return console_error("MSDIAL_EXE_NOT_FOUND", str(exc))
    except MsdialTimeoutError as exc:
        update_status(resolved, "failed", error=str(exc))
        return console_error("MSDIAL_TIMEOUT", str(exc))
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

    # ここから先（生成物収集・ハッシュ計算・再読込・保存）は run_msdial 自体は
    # 成功済みなので、上の try/except（MsdialExeNotFoundError 等）は対象外。
    # だが無防備だと running に固着する経路が残る: Windows でウイルススキャナ等が
    # 生成直後のファイルをロックしていると collect_artifacts 内の sha256_file が
    # PermissionError を投げ、analysis-job.json は running のまま更新されずに
    # 例外がツール境界を突き抜ける。以降の console_run は JOB_NOT_PLANNED で
    # 全て拒否し、誰も直せない（Task 0 で潰したはずの症状の再発）。
    # サイドカー（feature-qc.tsv）自身の失敗は既存どおりこの外側ガードに
    # 届かせない——副産物のためにジョブを failed にしない設計を維持する。
    try:
        from lipidmix.console.output_collector import collect_artifacts
        mztab_entries, other_artifacts = collect_artifacts(run_dir, before)

        if not mztab_entries and not other_artifacts:
            update_status(resolved, "failed", error="実行後に新規生成物が見つかりません")
            return console_error(
                "NO_JOB_OUTPUT",
                "MS-DIAL Console が終了しましたが、出力ファイルが生成されませんでした。"
                f"ログを確認してください: {run_dir / 'msdial.log'}",
            )

        job = load_job(resolved)
        job.primary_mztab_files = mztab_entries
        job.artifacts = other_artifacts

        # サイドカー（feature-qc.tsv）。生成物のファイル名からサンプル名を拾う。
        # 失敗しても実行は成功扱い——副産物のためにジョブを failed にしない。
        from lipidmix.console import sidecar
        try:
            sample_names = _sample_names_from_artifacts(other_artifacts)
            if sample_names:
                sidecar_path = run_dir / sidecar.SIDECAR_SUBDIR / sidecar.FEATURE_QC_FILENAME
                sidecar.generate_feature_qc_tsv(
                    sidecar.build_sample_meta_from_names(sample_names), sidecar_path,
                )
                from lipidmix.handoff.schema import Artifact, sha256_file
                job.artifacts.append(Artifact(
                    path=str(sidecar_path.relative_to(run_dir)),
                    role=sidecar.ARTIFACT_ROLE,
                    format="tsv",
                    sha256=sha256_file(sidecar_path),
                ))
            else:
                job.warnings.append(
                    "サンプル別ファイル（.pai2）が見つからず feature-qc.tsv を生成しませんでした。")
        except OSError as exc:
            job.warnings.append(f"feature-qc.tsv の生成に失敗しました: {exc}")

        job.status = "completed"
        save_job(job, resolved)
    except Exception as exc:  # 想定外。running に固着させないことが最優先
        update_status(resolved, "failed", error=repr(exc))
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
        "run_dir": job.run_dir,
        "mztab_files": [
            {"path": e.path, "polarity": e.polarity, "measure": e.measure}
            for e in job.primary_mztab_files
        ],
        "artifact_count": len(job.artifacts),
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

def _sample_names_from_artifacts(artifacts) -> list[str]:
    """サンプル別生成物（.pai2）のファイル名からサンプル名を重複なく拾う。

    .pai2 は測定 1 本ごとに 1 ファイル出るので、サンプル一覧の最も素直な出どころ。
    """
    names: list[str] = []
    seen: set[str] = set()
    for art in artifacts:
        if art.format != "pai2":
            continue
        stem = Path(art.path).stem
        if stem not in seen:
            seen.add(stem)
            names.append(stem)
    return names


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
