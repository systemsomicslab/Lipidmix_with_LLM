# 生データフォルダ起点pipeline: 実環境検証記録（Task 20）

作成日: 2026-09-07
対象plan: `.superpowers/sdd/2026-09-05-raw-folder-pipeline-integrity-and-metadata/`
対象spec: `docs/superpowers/specs/2026-09-05-raw-folder-pipeline-integrity-and-metadata-design.md`

## 実環境・コード版

| 項目 | 値 |
|---|---|
| 実行環境 | Windows 11 (`Windows-11-10.0.26200-SP0`) |
| Python | `C:/Python314/python.exe` — CPython 3.14.4 (tags/v3.14.4:23116f9) |
| リポジトリ | `C:\Users\yuu18\Lipidmix_with_LLM\.worktrees\raw-folder-pipeline`（隔離worktree、ブランチ `codex/raw-folder-pipeline`） |
| コード版（git SHA） | `229c35c9be1893fdc27eaf13607d2d645cf39010`（本記録作成直前のHEAD。本記録および付随する文書整合修正はこのSHAの後にコミットする） |
| MS-DIAL Console版 | **未確認**（`MSDIAL_EXE` 環境変数は本セッションで未設定。実Console実行は本タスクで許可されていない — 下記R3参照） |

過去のplan文書に記載された「.venvは無い」やテスト件数はここへ転記していない。すべて本セッションで実測した値のみを記録する。

## 実行範囲の制約（controller ruling R3）

本planのGlobal Constraints「実MS-DIALを使う検証は、隔離入力と明示的に許可された実行に限定する。plan承認を実データ実行許可と読み替えない」に基づき、**実MS-DIAL Consoleの起動、および`ms-data-parser` MCPツールを実データへ適用する検証は、本タスクでは許可されていない**。したがって受入表の3行（実Console探索・実Console比較と再開・実Console取消/timeout）は**未実施**のまま記録する。実施したのはfake Console（合成子プロセス）とプロセスライフサイクルの2行のみ。

## 検証記録

| ケースID | 環境・入力形式 | 許可された実行範囲 | コードまたはMCP引数 | 観測結果・成果物 | 判定 |
|---|---|---|---|---|---|
| fake全受入 | 隔離checkout・合成fixture（`tests/pipeline_fixtures.py::make_source` が作る合成raw、`tests/fixtures/fake_console.py` が模擬するConsole子プロセス）。実rawは一切使用しない | 単体・fake統合テストの実行（制限なし・本リポジトリ内で完結） | `C:/Python314/python.exe -m pytest tests/test_pipeline_end_to_end.py -q`（18件、spec A01〜A08・D01〜D10・E04相当のシナリオを含む） | `18 passed in 49.75s`。前段でTask 20が修正した同時起動テスト（`test_two_concurrent_starts_of_the_same_request_launch_console_once`）を含め全件成功。成果物は各テストのtmp_path配下（pipeline-run.json・console job・TSV・PNG）で、テスト終了時に破棄される一時ファイル | **PASS**（fake環境での完走を実際に観測） |
| Windows親切断 | 実Python子/孫プロセス（`Bystander`ヘルパで起こす孫プロセスと、切り離し起動されたworkerプロセス自身）。実MS-DIALではなくfake Console | 実プロセスのライフサイクル試験（Job Object・切り離し起動の実挙動を実プロセスで確認。本リポジトリ内で完結） | `C:/Python314/python.exe -m pytest tests/test_pipeline_process_lifecycle.py -k test_run_completes_after_the_launcher_process_exits_without_any_status_call -v` | `1 passed in 4.21s`。起動元プロセス（launcher）が`pipeline_status`を一度も呼ばずに終了しても、切り離されたworkerプロセスが単独でrunを`completed`まで進めることを実プロセスで確認（`observed_health`ではなく実際のプロセス終了とrun状態を見ている） | **PASS**（実プロセスで観測） |
| 実Console探索 | 許可された隔離入力（未提供） | **未許可** | `mcp__ms-data-parser__pipeline_run` 等 | — | **未実施**（実データ実行の許可が未取得） |
| 実Console比較と再開 | 同じ上流と明示manifest（未提供） | **未許可** | `mcp__ms-data-parser__pipeline_resume` 等 | — | **未実施**（実データ実行の許可が未取得） |
| 実Console取消/timeout | 許可された試験run（未提供） | **未許可** | `mcp__ms-data-parser__pipeline_cancel` 等 | — | **未実施**（実データ実行の許可が未取得） |

補足: 上記2行の「観測結果」は本タスク実行中に実際にコマンドを流して得た出力であり、過去のplan/reviewドキュメントからの転記ではない。実行ログの全文は本セッションのツール呼び出し履歴に残る（標準出力の要約のみここに記載）。

## コード・登録・文書の最終検証（brief手順2）

すべて本タスク実行時に実測。

### 2-1. 腐敗防止テスト（登録・README・workflow文書・パッケージ構成）

```
C:/Python314/python.exe -m pytest tests/test_server_registration.py tests/test_readme_links.py tests/test_workflow_docs.py tests/test_package_layout.py -q
```

実測出力:

```
..........................          [100%]
26 passed, 973 subtests passed in 1.78s
```

下記「文書整合の修正」を適用した**後**の状態での実測。`docs/workflow/pipeline.md`の呼び出し連鎖を書き換えた結果、`test_workflow_docs.py`のsubtest件数が変化している（連鎖行が増えた分、1行=1 subtestの検証対象が増えた）。

### 2-2. 全体テストスイート

```
C:/Python314/python.exe -m pytest tests -q
```

実測出力（末尾）:

```
============================== warnings summary ===============================
tests/test_preprocessing.py::TestImpute::test_all_nan_column_fills_zero
  ...\lipidmix\analysis\preprocessing.py:369: RuntimeWarning: Mean of empty slice
    col_mean = np.nanmean(out, axis=0)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1567 passed, 1 warning, 1026 subtests passed in 131.56s (0:02:11)
```

`1567 passed`はタスク開始時点で報告されていた件数と一致し、xfailed/skippedは0件。`Mean of empty slice`警告は既知の既存警告（本タスクの変更と無関係）。subtest数（1010→1026）は本タスクで`docs/workflow/pipeline.md`に呼び出し連鎖の行を追加したことによる増分（AST検証が1行ごとにsubtestを生成するため）で、失敗の兆候ではない。

### 2-3. 差分・状態確認

```
git diff --check
```
→ 出力なし（終了コード0。空白関連の壊れた差分なし）。

```
git status --short
```
→ 実測（本記録作成前の時点）:
```
 M docs/superpowers/specs/2026-09-03-end-to-end-pipeline-design.md
 M docs/superpowers/specs/2026-09-05-raw-folder-pipeline-integrity-and-metadata-design.md
 M docs/workflow/index.md
 M docs/workflow/pipeline.md
 M lipidmix/tools/pipeline_tools.py
 M tests/fixtures/fake_console.py
 M tests/test_pipeline_end_to_end.py
```
（本記録ファイル自体は新規のため`git status`には`??`として別途現れる。コミット時にbriefが指定する対象ファイルのみをstageする。）

## 実データ実行未実施の範囲（brief手順4: 未実施範囲の分離）

- **実装・fake検証済み**: pipeline層の5 MCPツール（`pipeline_plan`/`pipeline_run`/`pipeline_status`/`pipeline_resume`/`pipeline_cancel`）、worker経路（`prepare_input`〜`report`の全stage handler）、Windows Job Objectによるプロセス管理、切り離し起動、owner lock・state更新lockによる排他、pipeline-run.v1の永続化・楽観的並行制御・再開/取消契約。これらはすべて合成fixtureとfake Console（実子プロセスだが本物のMS-DIALではない）、および実プロセスのライフサイクル試験によって検証済み。
- **実機完走確認済み**: なし。実MS-DIAL Consoleを起動した実行、実raw（`.wiff`/`.raw`/`.d`/`.mzML`等）を入力とした実行は、本タスクを含めこのplanの実装期間中に一度も許可・実施されていない。
- したがって**spec全体の完成は宣言しない**。実装は目標状態（spec）が要求する契約を満たすようfake環境で検証済みだが、実MS-DIAL・実rawでの完走は別途の許可と実行が必要な、独立した未検証事項として残る。

## 文書整合（累積したレビュー指摘の解消）

Task 20で解消した6件の文書ドリフトは、`.superpowers/sdd/2026-09-05-raw-folder-pipeline-integrity-and-metadata/task-20-report.md`に詳細を記録する。要約:

1. spec §9.1（`docs/superpowers/specs/2026-09-05-raw-folder-pipeline-integrity-and-metadata-design.md`）の`pipeline-run.v1`フィールド表に`worker`行を追加。**承認済み文書への変更**であり、本記録とTask 20報告で明示している（黙って追記していない）。
2. `docs/workflow/index.md`の一気通貫の順序の案内先を、超過した2026-09-03 specから`docs/workflow/pipeline.md`へ更新。2026-09-03 specの§9にも現状を追記。
3. `docs/workflow/pipeline.md`の`pipeline_run`/`pipeline_resume`呼び出し連鎖を、`_prepare_run`内の`_mark_needs_input_without_launch`分岐・再利用run時の無起動分岐・`_launch_and_await`中間関数を反映するよう書き直し。
4. `lipidmix/tools/pipeline_tools.py`の`pipeline_resume`/`pipeline_run`docstringを実装に合わせて補足。
5. `SAMPLE_MAPPING_MISMATCH`→`SAMPLE_MAPPING_MISSING`へ、spec §13 A03と`tests/fixtures/fake_console.py`のdocstringを本番コード（`lipidmix/console/validation.py`）に合わせて統一。
6. plan Task 19のシナリオ表が言う`partial`は誤りで、spec §9.1の状態機械と実装は`failed`（有効な解析出力なしのケース）で一致している。これはplan文書側の誤りとしてここに記録するのみで、実装は変更していない（`tests/test_pipeline_end_to_end.py`に既にこの食い違いを明記するコメントがある）。

## 既知の制限（最終スコープ再レビューで判明。controller裁定により修正せず出荷）

以下は「動作未確認」ではなく「現状の挙動を確認したうえで、このまま出荷する」と
裁定された既知の制限。将来直す場合の見立ても添えるが、本タスク（Task 20）では
コードを変更していない。

> **2026-09-07 追記**: このうち1件目（mid-run resume staleness）は後続の
> レビュー修正で解消した。下記「2026-09-07 レビュー指摘の修正」を参照。

- **resume中のrequest/planが古いまま進む（mid-run resume staleness）。**
  `engine._run_stage_loop`は`request`と`plan`をloop開始時に一度だけ読み、以降
  読み直さない。`recovery.prepare_resume`はworkerが生きている（status
  `running`・healthが`ok`）resumeを拒否しない——statusは`running`のままなので
  二重起動はしないが、request revisionは上げられ、stageはリセットされる。
  conflictリトライ導入前は、生きているworkerの次の`save_run`が
  `STATE_REVISION_CONFLICT`で落ちてworkerが死に、`worker_missing → planned`が
  正しい全面再実行を生んでいた。今はそのリトライがconflictを吸収するため、
  workerはresume前のrequestのまま完走できてしまう。具体的には、`preprocess`
  実行中に`pipeline_resume(updates={"preprocess": {...}})`が届くと、
  revision 2のrunがrevision 1の数値のまま`completed`になり得て、`warnings`
  には何も残らない。このフローにはresume前後を問わずテストが無い。直すなら
  最も安く効くのは、loopの各反復の先頭でrevisionを照合し、古ければ完走させず
  中断する仕組み。
- **リトライ経路でresult_refsが重複しうる（duplicate result refs）。**
  `engine.commit_stage_outcome`の再適用は、読み直したstageから
  `previous_result_refs`を導出する。並行して書き込んだのが同じstageを
  リセットする`prepare_resume`（`result_refs = []`にする）だった場合、
  再適用側は`previous == []`と見て同じrefsを`record["results"]`へもう一度
  追記してしまう。`store._assert_results_append_only`はこれを拒否しない
  （追記であることに変わりはないため）し、`report._achieved_ref`は一致する
  最後の要素を採るため現時点で実害は出ていない——が、履歴には重複が残る。
- **`engine.py`の`save_run`直呼び出し箇所**（`_save_with_retry`を経由しない
  もの）は`STATE_REVISION_CONFLICT`をそのまま呼び出し元へ伝播させる。各箇所は
  loadしてすぐsaveするだけの短い区間で、間にハンドラを挟まないため、
  伝播した先はworkerが死んで次のresumeが状態を作り直すことで自己修復する
  （上記のmid-run resume stalenessが同じ自己修復経路を悪化させている点に注意）。

## 参照

- 検証コマンドの実行者: 本タスク（Task 20）自身。実MS-DIAL・実データの実行は行っていない。
- 詳細な報告: `.superpowers/sdd/2026-09-05-raw-folder-pipeline-integrity-and-metadata/task-20-report.md`

## 2026-09-07 レビュー指摘の修正

plan実装後のレビューで挙がった12件を修正した（それぞれ回帰テスト付き。
テストは修正前のコードで実際に落ちることを確認してから追加している）。

### 計画の中心要件（訂正・再開で結果を取り違えない）に関わるもの

1. **稼働中workerが要求の訂正を取り違える**（上記「既知の制限」1件目、
   `engine._run_stage_loop`）。stage境界ごとに最新recordを読み直し、要求版の
   変化（`_request_identity`）と通過済みstageの差し戻し（`_resume_reset_seen`）を
   観測したらstage計画を組み直してpassをやり直す（`runtime`は捨てる）。
   停止せず同じworkerが続けるのは、稼働中workerがいる限り`resume_pipeline`が
   新しいworkerを起こさないため。組み直しは有限回で、超えれば
   `REQUEST_REVISION_CHURN`。
2. **同じシートを書き直した訂正が無視される**（`recovery._stages_to_reset`）。
   `resolve_metadata`がシートの内容hashを`record["inputs"]["manifest_source"]`
   （`inputs.manifest_source_record`）へ残し、resumeはそれを今のファイルと
   突き合わせる。パス文字列の比較だけでは書き直しが変更として現れない。
3. **群の訂正後も旧差次的結果が「現在の結果」として通る**
   （`result_state.is_current`）。差次的結果の来歴へ群割当の指紋
   （`result_state.group_fingerprint`）を刻み、比較の群依存を照合する。
   これで`dataset_export_differential`のdocstringが約束している拒否が実際に働く。
4. **除外試料の未検出が検出率の分母に残る**（`dataset_analysis._apply_detection_filter`）。
   include=falseの位置計算を`_included_sample_indices`へ一本化し、行列と検出
   マスクの両方を同じ集合へ揃える。`min_detection_rate=1.0`が正当な特徴量を
   削らなくなる。
5. **定量列の欠けたmzTabがcompletedになる**（`console.validation.validate_outputs`）。
   MTDのassay対応とSMFの`abundance_assay[N]`列を突き合わせ、
   `ABUNDANCE_COLUMN_MISSING` / `ABUNDANCE_COLUMN_UNMAPPED`を返す。
6. **正当な訂正のあとの再送が偽の破損を報告する**（`store._artifacts_verify`）。
   成果物検証の対象を`current_result_refs`（output_nameごとの最後の1件。
   `report._achieved_ref`と同じ規則）へ揃え、上書きされた旧refのhash不一致を
   `RESULT_INTEGRITY_MISMATCH`と読まないようにした。

### 副次的なもの

7. 完了ゲートがentryとjobの宣言の食い違いを見ていなかった
   （`validate_outputs`。下流の`select_primary_entry`はjobの宣言で選ぶため
   「completedなのに読めない」出力になっていた）。
8. 切り離し起動後に親が無条件で`running`と所有記録を書き、先に終わった
   ワーカーの`completed`を潰していた（`console_tools.console_run`）。
   状態が`planned`のときだけ書き、所有記録はワーカーが書いていなければ書く。
9. ロック取得後にジョブ状態を再確認しておらず、同時`console_run`でMS-DIALが
   二度走りえた（`console.worker.run_job` → `JOB_ALREADY_FINISHED`）。
10. `owner_is_active`がidentityをstatusより先に見ており、同期実行の所有者
    （MCPサーバ自身）が生きている限り`console_cleanup`が永久に`JOB_BUSY`だった。
11. `PreconditionError`がengineの汎用例外分岐に落ち、Pythonのクラス名がcodeの
    `failed`になっていた（`service.build_handlers`が全handlerを`_as_needs_input`で
    包む。`_handle_pca`だけの個別回避を規則へ引き上げた）。
12. spec §7.1の`analysis-request.json`探索と3段の優先順位が未実装だった
    （`request.read_request_file` / `resolve_request`。`value_sources`に
    `"request_file"`が入る）。

あわせて、`preprocess_auto`が検査用とcommit用で同じ行列を2回計算していた
（`dataset_service._reusable_preprocess` / `_commit_preprocess`へ分割し1回に畳んだ）。

検証: `C:/Python314/python.exe -m pytest tests -q` → `1611 passed`
（修正前は`1575 passed`。差分は本修正で追加した回帰テスト）。実MS-DIAL・実rawでの
完走は本修正でも実施しておらず、上記「実データ実行未実施の範囲」はそのまま残る。
