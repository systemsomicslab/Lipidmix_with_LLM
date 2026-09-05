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

__all__ = ["console_plan", "console_prepare_input", "console_method_template",
           "console_method_candidates", "console_run", "console_status",
           "console_cleanup", "job_list"]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
          structured_output=False)
def console_plan(
    dataset_root: str,
    method_file: str | None = None,
    polarity: str = "positive",
    measure: str = "peak_height",
    omics: str = "lipidomics",
    save_project: bool = True,
    timeout_s: int = 21600,
    lbm_file: str | None = None,
) -> str:
    """MS-DIAL Console の実行計画を作成し、analysis-job.json を生成します。

    Parameters
    ----------
    dataset_root:
        生データフォルダのパス（.wiff / .raw 等が入っているフォルダ）。
        リポジトリ外のパスを指定してください。
    method_file:
        MS-DIAL Console のパラメータファイル（ASCII テキスト。`key: value` 形式）。
        **省略できます** — 省略時は dataset_root から MS-DIAL GUI が実行のたびに
        自動保存する `<project>_param_<終了時刻>.txt` を探し、`Ion mode` が
        polarity と一致する最新のものを使います。
        **`.mdproject` / `.mddata` は使えません**（ZIP なので Console は中身を
        読めず、全パラメータが既定値のまま実行されます）。
    lbm_file:
        脂質ライブラリ（`.lbm2`）のパス。省略時は「メソッドファイルの宣言 →
        環境変数 MSDIAL_LBM → MSDIAL_EXE と同じフォルダ」の順に、MS-DIAL GUI と
        同じ規則で自動解決します。GUI 由来のパラメータは `Lbm file path:` が
        必ず空なので、この自動解決が無いと**警告なしで同定 0 件**になります。
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

    from lipidmix.console import method_file as method_file_mod

    discovered_from: str | None = None
    if method_file is None:
        candidates, searched = method_file_mod.discover_method_candidates(
            root, polarity=polarity, omics=omics)
        direct = [c for c in candidates if c.usable == "direct"]
        if direct:
            discovered_from = direct[0].path
            mf = Path(discovered_from)
        elif candidates:
            # 別極性を黙って採ると Ion mode と Searched adduct ions が違うまま走り、
            # 別の解析になる。console_method_template を通して caveat を出させる。
            return console_error(
                "METHOD_FILE_CHOICE_REQUIRED",
                f"極性 {polarity} に一致するパラメータファイルはありませんが、"
                f"別極性の候補が {len(candidates)} 件あります。"
                "console_method_template(based_on=<選んだ path>, polarity=...) で"
                "その極性用に変換してから console_plan に渡してください。"
                "検出・アライメント条件は元のまま引き継がれます。",
                {"dataset_root": str(root), "polarity": polarity,
                 "searched": searched,
                 "candidates": [_candidate_payload(c) for c in candidates]},
                required_tools=["console_method_template", "console_plan"])
        else:
            return console_error(
                "METHOD_FILE_NOT_GIVEN",
                "method_file が省略され、使えるパラメータファイルも"
                f"見つかりませんでした（極性 {polarity}）: {root}  "
                "MS-DIAL GUI は解析のたびに `<project>_param_<終了時刻>.txt` を"
                "プロジェクトフォルダへ自動保存します。その極性でも別極性でも"
                "GUI 実行が無い場合は、他のデータセットのパラメータを "
                "console_method_template の based_on で明示してください。",
                {"dataset_root": str(root), "polarity": polarity,
                 "searched": searched},
                required_tools=["console_method_template", "console_method_candidates"])
    else:
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

    method_keys = method_file_mod.read_method_keys(mf)
    declared_mode = (method_keys.get("ion mode") or "").strip().lower()
    if declared_mode and declared_mode != polarity:
        return console_error(
            "METHOD_FILE_POLARITY_MISMATCH",
            f"メソッドファイルの `Ion mode: {method_keys.get('ion mode')}` と "
            f"polarity={polarity!r} が食い違っています: {mf}  "
            "Console はメソッドファイル側を使うため、このまま実行すると"
            "宣言と別の極性の結果が analysis-job に記録されます。",
            {"method_file": str(mf), "method_ion_mode": declared_mode, "declared": polarity})

    try:
        from lipidmix.console import runner as console_runner
        exe = console_runner.get_exe_path()
    except EnvironmentError as exc:
        return console_error("MSDIAL_EXE_NOT_FOUND", str(exc),
                             _msdial_exe_setup_help())
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
            "解析に使うほうだけを残したフォルダを作って指定してください。"
            "console_prepare_input(dataset_root, keep_extension) がハードリンクで"
            "そのフォルダを作ります（実体コピーなし・元フォルダは無変更）。",
            {"formats": formats},
            required_tools=["console_prepare_input"],
        )
    input_count = sum(formats.values())

    lbm = method_file_mod.resolve_lbm(
        method_keys, mf, omics=omics, exe_path=exe, env=os.environ, override=lbm_file)
    if lbm.error_code:
        return console_error(lbm.error_code, lbm.message or "",
                             {"candidates": list(lbm.candidates)} if lbm.candidates else None)

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

    # 解決した LBM は run_dir の実効メソッドファイルに書き、ジョブをそちらへ向ける。
    # ユーザーのパラメータファイルは触らない（GUI が次に開いたときの整合が崩れる）。
    if lbm.path and lbm.source != "method_file":
        effective = method_file_mod.write_effective_method_file(
            mf, Path(job.run_dir) / "effective-method.txt",
            {method_file_mod.LBM_KEY: lbm.path})
        job.method_file = str(effective)
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
        "method_file": job.method_file,
        "method_source": {
            "given": method_file,
            "discovered_from": discovered_from,
            "effective": job.method_file,
        },
        "lbm": {"path": lbm.path, "source": lbm.source},
        "warnings": warnings,
        "next": "console_run を呼び出して実行を開始してください",
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
          structured_output=False)
def console_run(job_path: str | None = None, detach: bool = False) -> str:
    """MS-DIAL Console を実行します。

    job_path: analysis-job.json へのパス。省略時は session.current_job_path を使用します。
    事前に console_plan を実行しておく必要があります。

    detach: True にすると Console を**親から切り離して起動し、待たずに戻ります**
        （pid を返す）。実データ 60 サンプルは約 44 分かかるため、既定の同期実行では
        1 ツール呼び出しがその間ずっと戻らず、呼び出し元が中断されると生成物ごと
        失います。切り離した実行の完了確認と生成物の収集は `console_status` が
        引き継ぎます。**長い実行ではこちらを使ってください。**

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

    if detach:
        # 切り離した先では実行前スナップショットを撮れないので、ここで残す。
        # 完了確認と収集は console_status が引き継ぐ。
        from lipidmix.console.detached import write_detached_state
        try:
            pid = console_runner.run_msdial_detached(
                method_file=Path(job.method_file),
                dataset_root=dataset_root,
                run_dir=run_dir,
                exe_path=exe,
                save_project=job.save_project,
            )
        except OSError as exc:
            update_status(resolved, "failed", error=str(exc))
            return console_error(
                "MSDIAL_EXE_NOT_FOUND",
                f"MS-DIAL Console を起動できませんでした: {exc}",
                {"exe": exe, "log": str(run_dir / "msdial.log")})
        write_detached_state(run_dir, pid, befores)
        return json_payload({
            "status": "running",
            "job_id": job.job_id,
            "job_path": str(resolved),
            "pid": pid,
            "run_dir": job.run_dir,
            "log": str(run_dir / "msdial.log"),
            "next": "console_status で完了を確認してください（完了時に生成物を収集します）",
        })

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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
          structured_output=False)
def console_prepare_input(
    dataset_root: str,
    keep_extension: str = "wiff",
    out_dir: str | None = None,
) -> str:
    """計測フォーマットが 1 種類だけの入力フォルダを作ります（MIXED_RAW_FORMATS の解消）。

    MS-DIAL は `.wiff` と `.wiff2` を別フォーマットとして数えるため、SCIEX の
    生データフォルダはそのままでは対話プロンプトが出て実行できません。
    このツールは指定した拡張子の計測ファイルと**その随伴ファイル**
    （`.wiff.scan` / `.timeseries.data` 等）だけを集めたフォルダを作ります。

    実体はコピーせずハードリンクを張ります（同一ボリュームで無い場合のみコピー）。
    **元フォルダは一切変更しません。** MS-DIAL の生成物はこの新しいフォルダ側に
    出るので、生データ本体を汚さずに済みます。

    dataset_root: 生データフォルダ。
    keep_extension: 残す計測拡張子（既定 "wiff"）。
    out_dir: 出力先。省略時は `<元フォルダ>_<拡張子>` を兄弟として作ります。
    """
    src = Path(dataset_root).expanduser()
    dest = Path(out_dir).expanduser() if out_dir else src.parent / f"{src.name}_{keep_extension.lower().lstrip('.')}"

    from lipidmix.console.input_prep import prepare_single_format_input
    try:
        result = prepare_single_format_input(src, keep_extension, dest)
    except (ValueError, OSError) as exc:
        return console_error("INPUT_PREP_FAILED", str(exc),
                             {"dataset_root": str(src), "keep_extension": keep_extension})

    from lipidmix.console.job_manager import raw_input_summary
    return json_payload({
        "status": "prepared",
        "out_dir": result.out_dir,
        "primary": result.primary,
        "companions": result.companions,
        "linked": result.linked,
        "copied": result.copied,
        "skipped_existing": result.skipped_existing,
        "mode": result.mode,
        "formats": raw_input_summary(Path(result.out_dir)),
        "next": f"console_plan(dataset_root={result.out_dir!r}, ...) を実行してください",
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def console_status(job_path: str | None = None, include_artifacts: bool = False) -> str:
    """ジョブの現在のステータスを返します。

    job_path: analysis-job.json へのパス。省略時は session.current_job_path を使用します。
    include_artifacts: 生成物の全文一覧（TSV）を含めます。既定 False。
        生成物は 1 サンプルにつき 5 件出るため 60 サンプルで 300 行を超え、
        全文を返すと後ろの warnings / error が埋没します。既定では件数
        (`artifact_count`) と役割別内訳 (`artifacts_by_role`) だけを返します。
        個々のパスが必要なとき（欠落の特定など）だけ True にしてください。
    """
    resolved = _resolve_job_path(job_path)
    if isinstance(resolved, str):
        return resolved

    from lipidmix.console.job_manager import load_job
    try:
        job = load_job(resolved)
    except (FileNotFoundError, ValueError) as exc:
        return console_error("JOB_NOT_FOUND", str(exc))

    job, detached = _finalize_detached_if_done(resolved, job)

    from lipidmix.core.version import server_version
    return json_payload({
        "server_version": server_version(),
        "job_id": job.job_id,
        "status": job.status,
        **({"detached": detached} if detached else {}),
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
        # .mdmsp）。60 サンプルの実走で 313 件になり、全文 TSV は数万字に達して
        # 後ろの warnings / error を埋没させた。既定は役割別の件数だけにし、
        # 全文は include_artifacts=True のときだけ返す。
        "artifacts_by_role": _artifacts_by_role(job.artifacts),
        **({"artifacts": _artifacts_tsv(job.artifacts)} if include_artifacts else {}),
        "execution": {"save_project": job.save_project, "timeout_s": job.timeout_s},
        "warnings": job.warnings,
        "error": job.error,
        "updated_at": job.updated_at,
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
          structured_output=False)
def console_method_template(
    out_path: str,
    polarity: str,
    based_on: str | None = None,
    dataset_root: str | None = None,
    omics: str = "lipidomics",
) -> str:
    """既存のパラメータから、別極性用のメソッドファイルを作ります（最後の手段）。

    **まず console_plan の method_file 省略を試してください。** MS-DIAL GUI は解析の
    たびに `<project>_param_<終了時刻>.txt` を自動保存するので、その極性で一度でも
    GUI 実行があれば、作る必要はありません。

    このツールが要るのは「その極性で一度も処理していない」場合だけです
    （別極性は処理済み、というのが典型）。やることは 2 行の差し替えです:
    `Ion mode` と `Searched adduct ions` をその極性の標準セットにする。
    **検出・アライメント条件は元のまま引き継ぎます**（勝手に変えると別の解析になる）。
    加えて、GUI 由来では必ず空の `Lbm file path` を解決して埋めます。

    out_path: 書き出し先。
    polarity: "positive" / "negative"（作りたい側）。
    based_on: 元にするパラメータファイル。省略時は dataset_root から探します
        （**極性は問いません** — 別極性から作るのがこのツールの用途なので）。
    dataset_root: based_on 省略時の探索先。
    """
    if polarity not in ("positive", "negative"):
        return console_error("JOB_NOT_PLANNED",
                             f"polarity は 'positive' または 'negative' です: {polarity!r}")

    from lipidmix.console import method_file as method_file_mod

    if based_on:
        src = Path(based_on).expanduser()
    elif dataset_root:
        candidates = method_file_mod.find_method_candidates([Path(dataset_root).expanduser()])
        if not candidates:
            return console_error(
                "METHOD_FILE_NOT_GIVEN",
                "元にできるパラメータファイル（`*_param_<ts>.txt`）が見つかりません: "
                f"{dataset_root}  MS-DIAL GUI で一度も解析していないフォルダには存在しません。"
                "他のデータセットのパラメータを based_on で明示してください。",
                {"dataset_root": dataset_root})
        src = Path(candidates[0].path)
    else:
        return console_error("METHOD_FILE_NOT_GIVEN",
                             "based_on か dataset_root のどちらかを指定してください。")

    if not src.is_file():
        return console_error("METHOD_FILE_NOT_FOUND", f"元ファイルが見つかりません: {src}")
    if not _looks_like_method_text(src):
        return console_error(
            "METHOD_FILE_NOT_TEXT",
            f"元ファイルが MS-DIAL Console の読める ASCII テキストではありません: {src}  "
            ".mdproject / .mddata は ZIP で、解析パラメータを含んでいません"
            "（中身は .mddata へのポインタだけです）。",
            {"based_on": str(src)})

    overrides = {
        method_file_mod.ION_MODE_KEY: polarity.capitalize(),
        method_file_mod.ADDUCT_KEY: method_file_mod.STANDARD_ADDUCTS[polarity],
    }

    exe = os.environ.get("MSDIAL_EXE") or None
    lbm = method_file_mod.resolve_lbm(
        method_file_mod.read_method_keys(src), src,
        omics=omics, exe_path=exe, env=os.environ)
    if lbm.error_code:
        return console_error(lbm.error_code, lbm.message or "",
                             {"candidates": list(lbm.candidates)} if lbm.candidates else None)
    if lbm.path:
        overrides[method_file_mod.LBM_KEY] = lbm.path

    dest = Path(out_path).expanduser()
    try:
        method_file_mod.write_effective_method_file(src, dest, overrides)
    except OSError as exc:
        return console_error("METHOD_FILE_NOT_FOUND", f"書き出せませんでした: {exc}")

    return json_payload({
        "status": "written",
        "out_path": str(dest),
        "based_on": str(src),
        "polarity": polarity,
        "lbm": {"path": lbm.path, "source": lbm.source},
        "changed_keys": sorted(overrides),
        "caveat": "検出・アライメント条件は元ファイルのまま引き継いでいます。"
                  "その極性に妥当かは実行前に確認してください。",
        "next": f"console_plan(dataset_root=..., method_file={str(dest)!r}, polarity={polarity!r})",
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
          structured_output=False)
def console_method_candidates(
    dataset_root: str,
    polarity: str | None = None,
    omics: str | None = "lipidomics",
    search_dirs: list[str] | None = None,
) -> str:
    """使えるメソッドファイルの候補を列挙します（console_plan が失敗する前に呼べます）。

    MS-DIAL GUI は解析のたびに `<project>_param_<終了時刻>.txt` をプロジェクト
    フォルダへ自動保存します。その極性で一度も GUI 実行が無いフォルダには存在
    しないため、**兄弟フォルダ**（POS の隣の NEG 等）と**過去 run** まで探します。

    dataset_root: 生データフォルダ。
    polarity: "positive" / "negative"。省略すると極性で区別せず全件返します。
        指定すると各候補に `usable` が付き、一致しないものは
        `needs_polarity_conversion`（console_method_template を経由させる）。
    omics: 既定 "lipidomics"。None で絞り込みません。
    search_dirs: 追加で探すフォルダ。再帰はしません。

    候補には比較用の `key_params`（検出・アライメント条件のうち結果を変える少数）が
    付きます。候補が 10 件を超えるときは付きません（戻り値が肥大するため）。
    """
    root = Path(dataset_root).expanduser()
    if not root.is_dir():
        return console_error("DATASET_ROOT_NOT_FOUND",
                             f"データフォルダが見つかりません: {dataset_root}",
                             {"dataset_root": str(dataset_root)})

    from lipidmix.console import method_file as method_file_mod

    candidates, searched = method_file_mod.discover_method_candidates(
        root, polarity=polarity, omics=omics, search_dirs=search_dirs)
    return json_payload({
        "dataset_root": str(root),
        "polarity": polarity,
        "omics": omics,
        "searched": searched,
        "n_candidates": len(candidates),
        "candidates": [_candidate_payload(c) for c in candidates],
        "next": ("usable=direct なら console_plan(method_file=...)、"
                 "needs_polarity_conversion なら console_method_template("
                 "based_on=..., polarity=...) を通してから console_plan"),
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
          structured_output=False)
def console_cleanup(job_path: str | None = None, dry_run: bool = True) -> str:
    """あるジョブが生成したファイルだけを一覧・削除します（既定は一覧のみ）。

    MS-DIAL Console は生データフォルダ側にも生成物を出すため、再実行のたびに
    別タイムスタンプのアライメント一式が同じフォルダへ積まれます。放置すると
    「複数バッチ混在フォルダ」になり、どれが今回の結果か分からなくなります。

    **消す対象は analysis-job.json が記録した生成物だけ**です。タイムスタンプの
    推測では消しません（別バッチの成果物を巻き込むため）。記録が無いジョブは
    NO_JOB_OUTPUT で拒否します。生データ（.wiff 等）には触れません。

    dry_run: True（既定）なら一覧を返すだけ。False で実際に削除します。
        削除後、ジョブの status は `cleaned` になります（生成物を指したまま
        completed で残ると、dataset_load が存在しないファイルを読もうとします）。
    """
    resolved = _resolve_job_path(job_path)
    if isinstance(resolved, str):
        return resolved

    from lipidmix.console.job_manager import load_job, update_status
    try:
        job = load_job(resolved)
    except (FileNotFoundError, ValueError) as exc:
        return console_error("JOB_NOT_FOUND", str(exc))

    # 生成物は run_dir と dataset_root に書き分かれる。記録された出所から戻す
    # 解決は mztab_tools が正準（関数レベル import で循環を避ける）。
    from lipidmix.tools.mztab_tools import _artifact_abs_path

    targets: list[Path] = []
    for entry in job.primary_mztab_files:
        targets.append(_artifact_abs_path(job, getattr(entry, "root", "run_dir"), entry.path))
    for art in job.artifacts:
        targets.append(_artifact_abs_path(job, getattr(art, "root", "run_dir"), art.path))

    if not targets:
        return console_error(
            "NO_JOB_OUTPUT",
            "このジョブは生成物を 1 件も記録していないため、何を消してよいか決められません"
            f"（status={job.status}）。中断された実行はここに該当します。"
            "ファイル名のタイムスタンプで推測すると別バッチの成果物を巻き込むため、"
            "この場合は console_status と実フォルダを見て手で片付けてください。",
            {"job_id": job.job_id, "status": job.status,
             "dataset_root": job.dataset_root, "run_dir": job.run_dir})

    if dry_run:
        return json_payload({
            "status": "dry_run",
            "dry_run": True,
            "job_id": job.job_id,
            "count": len(targets),
            "files": [str(p) for p in targets],
            "next": "実際に削除するには dry_run=False を指定してください",
        })

    deleted = absent = failed = 0
    errors: list[str] = []
    for path in targets:
        if not path.exists():
            absent += 1
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            failed += 1
            errors.append(f"{path}: {exc}")

    update_status(resolved, "cleaned")
    return json_payload({
        "status": "cleaned",
        "dry_run": False,
        "job_id": job.job_id,
        "deleted": deleted,
        "already_absent": absent,
        "failed": failed,
        "errors": errors,
        "note": "生データ（.wiff 等）と analysis-job.json は残しています。",
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

def _finalize_detached_if_done(job_path: Path, job):
    """切り離した実行が終わっていれば、生成物を収集してジョブを確定する。

    切り離した Console は親を持たないので、誰かが後から収集しないと生成物が
    ジョブに載らない。その「誰か」が console_status。

    Returns: (job, detached_info | None)
    """
    from lipidmix.console.detached import clear_detached_state, read_detached_state

    run_dir = Path(job.run_dir)
    state = read_detached_state(run_dir)
    if state is None:
        return job, None

    from lipidmix.console import runner as console_runner
    pid = state["pid"]
    if console_runner.is_process_running(pid):
        return job, {"pid": pid, "alive": True}

    roots = {"run_dir": run_dir, "dataset_root": Path(job.dataset_root)}
    befores = state.get("befores") or {}
    # 収集の前に消す。この制御ファイル自体は実行前スナップショットの後に書かれる
    # ので、残したまま収集すると「MS-DIAL の生成物」として拾われる。
    clear_detached_state(run_dir)
    try:
        from lipidmix.console.output_collector import collect_artifacts
        mztab_entries, other_artifacts = collect_artifacts(
            roots, befores,
            declared_polarity=job.polarity,
            declared_measure=job.measure,
        )
    except Exception as exc:  # 収集失敗で running に固着させない
        clear_detached_state(run_dir)
        _record_finalization_failure(job_path, repr(exc))
        from lipidmix.console.job_manager import load_job
        return load_job(job_path), {"pid": pid, "alive": False, "collected": False}

    # 生成物ゼロは、起動に失敗したか途中で落ちたかのどちらか。msdial.log を見る。
    status = "completed" if (mztab_entries or other_artifacts) else "failed"
    error = None if status == "completed" else (
        f"切り離し実行の終了後に新規生成物が見つかりません: {run_dir / 'msdial.log'}")
    job = _persist_collected_outputs(
        job_path, mztab_entries, other_artifacts, status=status, error=error)
    clear_detached_state(run_dir)
    return job, {"pid": pid, "alive": False, "collected": True}


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


def _artifacts_by_role(artifacts) -> dict[str, int]:
    """生成物を役割別に数える。役割名は handoff の `Artifact.role`。

    「何が何件出たか」は取りこぼしの検出に足りる（60 サンプルなら各役割 60 件）。
    個々のパスは include_artifacts に譲る。
    """
    counts: dict[str, int] = {}
    for artifact in artifacts:
        counts[artifact.role] = counts.get(artifact.role, 0) + 1
    return dict(sorted(counts.items()))


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


def _candidate_payload(candidate) -> dict:
    """MethodCandidate を UI が読める辞書にする。mtime は ISO 文字列で返す。"""
    from datetime import datetime
    payload = {
        "path": candidate.path,
        "origin": candidate.origin,
        "usable": candidate.usable,
        "ion_mode": candidate.ion_mode,
        "omics": candidate.omics,
        "has_lbm": candidate.has_lbm,
        "mtime": datetime.fromtimestamp(candidate.mtime).isoformat(timespec="seconds"),
    }
    if candidate.key_params is not None:
        payload["key_params"] = candidate.key_params
    return payload


def _msdial_exe_setup_help() -> dict:
    """MSDIAL_EXE 未設定の封筒に、誰が何をすべきかを機械可読で載せる。

    環境変数は MCP クライアントからは設定できず、設定しても**起動中のサーバには
    反映されない**（サーバはクライアントが起動したまま生き続ける）。
    LLM に「設定してください」とだけ返すと、設定を試みて失敗するか黙って諦める。
    人間の作業であることを型で示し、コピペできる手順を渡す。
    """
    from lipidmix.console.runner import msdial_exe_candidates

    try:
        candidates = msdial_exe_candidates()
    except OSError:
        candidates = []
    return {
        "human_action_required": True,
        "why": "環境変数の設定は MCP クライアントからはできません。",
        "candidates": candidates[:10],
        "how_to_set": [
            'PowerShell（恒久設定）: [Environment]::SetEnvironmentVariable('
            "'MSDIAL_EXE','<MSDIALCUI.exe のパス>','User')",
            '.mcp.json の該当サーバに "env": {"MSDIAL_EXE": "<パス>"} を書く',
        ],
        "restart_required": True,
        "restart_note": "設定後は MCP サーバを再起動してください。"
                        "起動中のプロセスは環境変数の変更を読み直しません。",
    }


def _record_mztab_provenance(job, mztab_entries) -> None:
    """成果物の mzTab から、ジョブ側で分からない出所情報を採る。

    - `software.version`: Console 実行からは知りようがない。mzTab の
      `MTD software[1]` に `Msdial console 5.5.241113` が入っている。
    - 極性の裏取り: Console のアライメント出力名には極性トークンが無いので
      `polarity_source` は `job_declared` にしかならない（仕様）。宣言ミスを
      検出する手段が他に無いため、アダクトの多数決で**別フィールドとして**
      裏取りする。`polarity_source` は書き換えない —— 推定を出所として
      記録すると、そちらのほうが嘘になる。
    """
    from lipidmix.console.output_collector import (
        read_adduct_polarity, read_software_version,
    )
    from lipidmix.tools.mztab_tools import _artifact_abs_path

    for entry in mztab_entries:
        path = _artifact_abs_path(job, getattr(entry, "root", "run_dir"), entry.path)
        if not job.software_version:
            version = read_software_version(path)
            if version:
                job.software_version = version
        crosscheck = read_adduct_polarity(path)
        majority = crosscheck["adduct_majority"]
        crosscheck["agrees"] = None if majority is None else (majority == entry.polarity)
        entry.validation["polarity_crosscheck"] = crosscheck


def _polarity_crosscheck_warnings(mztab_entries) -> list[str]:
    """アダクトから推定した極性が宣言と食い違うエントリを warning にする。"""
    warnings: list[str] = []
    for entry in mztab_entries:
        check = entry.validation.get("polarity_crosscheck") or {}
        if check.get("agrees") is False:
            warnings.append(
                f"{entry.path}: アダクトの多数決は {check['adduct_majority']} ですが、"
                f"ジョブの宣言は {entry.polarity} です"
                f"（陽性 {check['n_positive']} / 陰性 {check['n_negative']} 行）。"
                "Console 出力のファイル名に極性が入らないため宣言をそのまま記録して"
                "いますが、console_plan の polarity かメソッドファイルの Ion mode を"
                "確認してください。")
    return warnings


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
    _record_mztab_provenance(job, mztab_entries)
    job.warnings.extend(_meta_conflict_warnings(mztab_entries))
    job.warnings.extend(_polarity_crosscheck_warnings(mztab_entries))
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
