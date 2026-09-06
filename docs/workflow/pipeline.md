# 生データフォルダpipeline呼び出し連鎖

MCP層は薄い(`lipidmix/tools/pipeline_tools.py`)。実体は `lipidmix/pipeline/service.py`
（受付・工程handler一式）と `lipidmix/pipeline/recovery.py`（読取専用status・取消・
再開判定）にある。工程本体は独立したworkerプロセス（`lipidmix/pipeline/worker.py`）が
`lipidmix/pipeline/engine.py::run_engine()` を通じて進めるため、MCP呼び出し自身の
連鎖と、workerが後で進める連鎖は別物として分けて書く。

## pipeline_run

1. lipidmix/tools/pipeline_tools.py  pipeline_run()
2. └─ lipidmix/pipeline/service.py  start_pipeline()
3.    └─ lipidmix/pipeline/service.py  _prepare_run()
4.    │  └─ lipidmix/pipeline/request.py  resolve_request()
5.    │  └─ lipidmix/pipeline/inputs.py  inspect_inputs()
6.    │  └─ lipidmix/pipeline/service.py  _precheck_manifest()
7.    │  │  └─ lipidmix/analysis/sample_manifest.py  parse_manifest()
8.    │  └─ lipidmix/pipeline/store.py  find_or_create_run()
9.    └─ lipidmix/pipeline/service.py  launch_pipeline_worker()
10.   │  └─ lipidmix/core/process_control.py  launch_detached()
11.   └─ lipidmix/pipeline/service.py  _await_launch_handshake()

事前検査(手順6-7)で壊れた実験情報シート等の既知の不正入力を検出した場合、
`find_or_create_run` でrunは作るがworkerは起動しない(`needs_input`のまま保存。
手順9以降を実行しない)。差次的解析で `comparisons` を指定し忘れた場合はここでは
検出しない——それは唯一、実際にworkerを起動したうえで下流の
`resolve_comparisons` 工程が検出する(brief「下流の群不足だけはworkerを起動できる」)。

`launch_pipeline_worker` は `sys.executable` で `python -m lipidmix.pipeline.worker`
を起動し、cwdは呼び出し元のcwdや別checkoutではなくこのcheckout自身(`_REPO_ROOT`)へ
固定する。起動プロセスのstdout/stderrは `launch_detached` がログファイルへ結ぶため、
起動元プロセスがworkerのstdoutパイプを継承して待ち続ける経路(`bInheritHandles=TRUE`
かつハンドル未指定)は無い。`_await_launch_handshake` は短い上限だけ待ち、超えても
起動失敗と決め付けず再起動もしない(`launch.handshake="not_confirmed"`のまま
`pipeline_path`を返す。確認は`pipeline_status`に委ねる)。

## pipeline_plan

1. lipidmix/tools/pipeline_tools.py  pipeline_plan()
2. └─ lipidmix/pipeline/service.py  plan_pipeline()
3.    └─ lipidmix/pipeline/service.py  _prepare_run()

`pipeline_run` と事前検査までは完全に同じ経路(`_prepare_run`)を共有するが、
`launch_pipeline_worker` を一切呼ばない——検査・計画・不足情報の保存だけで終える。

## pipeline_status

1. lipidmix/tools/pipeline_tools.py  pipeline_status()
2. └─ lipidmix/pipeline/recovery.py  read_status()
3.    └─ lipidmix/pipeline/store.py  load_run()
4.    └─ lipidmix/core/process_control.py  same_process()

`load_run` は読取専用で、このツール自身は `pipeline-run.json` を一切書き換えない。
`status="running"`のときだけ`same_process`でworker identityの生存を確認するが、
確認できなくても`status`フィールドは変えず`observed_health`という別軸で返す。

## pipeline_resume

1. lipidmix/tools/pipeline_tools.py  pipeline_resume()
2. └─ lipidmix/pipeline/service.py  resume_pipeline()
3.    └─ lipidmix/pipeline/recovery.py  prepare_resume()
4.    │  └─ lipidmix/pipeline/request.py  merge_updates()
5.    ├─ [status=="planned"のときだけ] lipidmix/pipeline/service.py  launch_pipeline_worker()
6.    └─ lipidmix/pipeline/service.py  _await_launch_handshake()

`prepare_resume`は新しいattempt/revisionを用意し、どのstageを`pending`へ戻すかを
決めて保存するだけで、それ自体はworkerを起動しない(no-op resumeで無駄な起動を
避ける)。`resume_pipeline`は`prepare_resume`の戻り値`status`が`"planned"`の
ときだけ`launch_pipeline_worker`を呼ぶ。`rerun_upstream=True`を明示しない限り
Console実行はやり直さない。

## pipeline_cancel

1. lipidmix/tools/pipeline_tools.py  pipeline_cancel()
2. └─ lipidmix/pipeline/recovery.py  request_cancel()
3.    └─ lipidmix/pipeline/store.py  load_run()
4.    └─ lipidmix/pipeline/engine.py  cancel_request_path()

保存するのは協調的な取消フラグ(小さなJSON)だけで、ここでは一切のプロセスを
直接終了させない。**rawデータは削除しない。** 上流Consoleの停止は既存の
`execution.supervise`の`cancel_path`監視に、下流の停止はengineのstage境界
チェックに委ねる。受理(このツールが成功したこと)と実際の停止確定
(`cancelled`)は別状態で、後者は`pipeline_status`で確認する。

## workerが1回分を進める経路(別プロセス。上記5ツールとは別に進行する)

`pipeline_run`/`pipeline_resume`が起動したworkerプロセス自身の経路。MCP接続や
グローバルsessionには一切触れない(`lipidmix.core.session_state` /
`lipidmix.core.mcp_core` / `lipidmix.tools.*` を意図的にimportしない設計——
`tests/test_pipeline_engine.py`のASTテストがこれを固定する)。

1. lipidmix/pipeline/worker.py  run_worker()
2. └─ lipidmix/pipeline/engine.py  run_engine()
3.    └─ lipidmix/pipeline/service.py  build_handlers()
4.    └─ lipidmix/pipeline/engine.py  _run_stage_loop()
5.       ├─ lipidmix/pipeline/service.py  _handle_prepare_input()
6.       ├─ lipidmix/pipeline/service.py  _handle_upstream()
7.       │  └─ lipidmix/console/execution.py  supervise()
8.       ├─ lipidmix/pipeline/service.py  _handle_validate_outputs()
9.       ├─ lipidmix/pipeline/service.py  _handle_load_dataset()
10.      │  └─ lipidmix/mztab/loading.py  load_dataset_state()
11.      ├─ lipidmix/pipeline/service.py  _handle_resolve_metadata()
12.      ├─ lipidmix/pipeline/service.py  _handle_preprocess()
13.      │  └─ lipidmix/analysis/dataset_service.py  preprocess_auto()
14.      ├─ lipidmix/pipeline/service.py  _handle_pca()
15.      │  └─ lipidmix/analysis/dataset_service.py  pca_dataset()
16.      ├─ lipidmix/pipeline/service.py  _handle_resolve_comparisons()
17.      ├─ [comparisonごと] lipidmix/pipeline/service.py  _handle_differential()
18.      │  └─ lipidmix/analysis/dataset_service.py  run_comparison()
19.      ├─ [comparisonごと] lipidmix/pipeline/service.py  _handle_export()
20.      │  └─ lipidmix/analysis/dataset_export.py  export_dataset_result()
21.      └─ lipidmix/pipeline/service.py  _handle_report()
22.         └─ lipidmix/pipeline/report.py  write_pipeline_report()
23.    └─ lipidmix/pipeline/engine.py  finish_success()
24.       └─ lipidmix/pipeline/report.py  evaluate_target()

`_handle_upstream`は`lipidmix.console.worker`（単体Console用の監視ワーカー）を
一切呼ばない——`execution.supervise`を直接呼ぶことで、pipeline用worker自身が
「起動して見張る」役を兼ねる(Console用workerの二重起動を避ける)。
`console_job_path`は`analysis-job.json`そのものへのパス(`run_dir = Path(job_path)
.parent`という、コードベース全体の規約)として記録する。

`_handle_pca`はPCA不成立を検出した`PreconditionError`（`lipidmix.analysis.
dataset_analysis.run_dataset_pca`が送出。`DomainError`の派生ではないため
`engine._invoke_handler`の`_NEEDS_INPUT_CODES`whitelistでは検出できない）を
handler自身が捕らえ、`needs_input`のStageResultへ直接変換する。これにより
`_invoke_handler`の汎用`except Exception`分岐（`failed`扱い）を経由させない。

`finish_success`は全stageがsucceeded/skippedで止まらずに終えた時点で
`report.evaluate_target(record)`を呼び、その判定(`completed`/`partial`/
`failed`/`needs_input`)をそのまま`record["status"]`へ採用する。差次的解析で
`comparisons`が空のまま(通常は`resolve_comparisons`が先に検出して
到達しない)は防御的な分岐として残る。
