# 生データフォルダ起点の解析統括・結果整合性・実験情報入力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 承認済みspecの1・3・4を実装し、既存メソッドを利用できる生データフォルダから、検証済み上流・探索解析・指定された2群比較へ自動進行し、中断・訂正・再送でも結果を取り違えないようにする。

**Architecture:** Console監視と終了検証を共通サービスにし、MCP接続から独立したワーカーがpipeline-run.v1の工程を進める。解析関数は明示的なDatasetStateを受け取り、メタデータ・前処理・比較・出力をfingerprintで結ぶ。単体ツールとpipelineは同じサービスを使い、ワーカーから公開MCPツールやグローバルセッションを呼ばない。

**Tech Stack:** Python（検証コマンドは`C:/Python314/python.exe`）、既存FastMCP/NumPy/SciPy/scikit-learn/matplotlib、pytest、stdlibのsubprocess/json/hashlib/ctypes。Windowsプロセス管理はJob Objectを使う。新しい外部サービス・必須Python依存は追加しない。

**Spec:** [承認済み統合spec](../specs/2026-09-05-raw-folder-pipeline-integrity-and-metadata-design.md)。2026-09-05にユーザーが承認。本planは同specの実装計画であり、specファイル冒頭に残るDraft表記は会話での承認を取り消すものではない。

## Global Constraints

- 対象は`C:\Users\yuu18\Lipidmix_with_LLM`のみ。外部リポジトリは変更しない。
- 「2. ラボ標準の解析条件を用意する」は含めない。
- 初期版は1フォルダ直下の1極性・1形式・LC-MS lipidomics・peak_heightを処理単位とする。
- `analysis-job.v1/v2`の読込互換を維持し、新規書込も`analysis-job.v2`を維持する。
- 新規`pipeline-run.v1`を`pipeline-run.json`へ保存する。既存analysis-jobの意味を「下流までの完了」に拡張しない。
- 終了証跡は`console-execution.v1`、要求は`pipeline-request.v1`、シートは`sample-manifest.v1`、自動前処理は`conservative-v1`。
- 元のrawファイルを移動・上書き・削除しない。
- 正のlog2FCはtest_groupが高い方向とする。既存`export_contract.py`を唯一の正準とし、列数・列順・contract_version・log2FC方向を変えない。
- `pipeline_status`は読取専用とし、監視・成果物確定をポーリング呼出しに依存させない。
- 自動再試行は行わない。Consoleの再試行だけは`rerun_upstream=true`の明示を必要とする。
- 通常のdataset_preprocessの既定引数は変えない。pipelineのauto解決を別関数に置く。
- 日本語のコメント・docstring・コミットメッセージを使用する。戻り値は`json_payload()`、MCPは`structured_output=False`。
- エラー応答は既存の`console_error / mztab_error / missing_state`形式を使用する。入力要求をmissing_stateに偽装して同じ呼出しを無限反復させない。
- 実rawや既存成果物を単体テストのfixtureにしない。質量データを使う検証はms-data-parser MCP経路を用いる。
- 実MS-DIALを使う検証は、隔離入力と明示的に許可された実行に限定する。plan承認を実データ実行許可と読み替えない。
- コード実装は今回は行わない。本planの全チェックボックスは未着手として残す。

## 実行前の確認と進め方

三つの領域は終了証跡・DatasetState・再開状態を共有するため、一つのplanで管理する。A〜Eのレビュー区切りで独立に検証できる成果へ分け、異なるリポジトリや任意の大規模リファクタへ広げない。

- [ ] 使用する`using-git-worktrees`スキルで実装用の隔離作業場所を確保する。既に隔離されている場合は重ねて作らない。既定ブランチ名は`codex/raw-folder-pipeline`。
- [ ] specとplanが実装側のcheckoutから読めることを確認する。現在は両文書が未追跡の場合があるため、未保存変更を含まないworktreeへ移っただけで文書を失わない。
- [ ] ユーザーの既存変更`docs/superpowers/specs/2026-09-04-mslipidmapper-transferable-assets.md`を混ぜない。今回のspec/plan以外を勝手にstageしない。
- [ ] 下記で基準を確認し、実行環境・git SHA・結果を記録する。過去planの「.venvはない」や固定テスト件数を転記しない。

```powershell
git status --short
git branch --show-current
C:/Python314/python.exe -c "import sys; print(sys.executable); print(sys.version)"
C:/Python314/python.exe -m pytest tests -q
```

各タスクはRED → GREEN → 関連回帰 → レビュー → 明示ファイルだけのコミットで終える。新APIの初回REDがImportErrorなら、それだけで検証を済ませず、API作成後に対象の振る舞いのassertが失敗することまで確認する。タスク内の複数ケースは一度に実装せず、表のケースごとに小さなRED/GREENを繰り返す。

コミット前hookの全テストは迂回しない。実装時にはdocs/HISTRY.md・docs/task.mdへ追記専用で進捗を記録し、実行前に未完了をDONEにしない。履歴更新は既存の規約に従い、追跡外ファイルをgitへ強制追加しない。

## ファイル構成と責務

下表の「新規」は計画されたファイルであり、現時点の実在を意味しない。

| 区分 | ファイル | 責務 |
|---|---|---|
| 新規 | `lipidmix/core/atomic_io.py` | 原子的JSON保存、JSON正規化、hash |
| 新規 | `lipidmix/core/process_control.py` | Windowsのプロセスidentity、OSロック、Job Object、切り離し起動 |
| 新規 | `lipidmix/console/execution.py` | 終了証跡の検証、監視、共通最終化 |
| 新規 | `lipidmix/console/validation.py` | 完了条件・入力とassayの対応検証 |
| 新規 | `lipidmix/console/worker.py` | 単体Console用の非同期監視エントリ |
| 変更 | `lipidmix/console/runner.py`, `detached.py`, `job_manager.py`, `output_collector.py` | 共通監視への接続、UUID、操作ファイル除外、旧形式読込 |
| 変更 | `lipidmix/handoff/schema.py`, `lipidmix/tools/console_tools.py` | 原子的保存、状態反映、所有jobの保護 |
| 新規 | `lipidmix/mztab/loading.py` | session非依存の読込、検証と出所 |
| 新規 | `lipidmix/analysis/result_state.py` | 結果ID、依存hash、無効化、有効性検査 |
| 新規 | `lipidmix/analysis/sample_manifest.py` | シートの厳密読込、対応、出所、適用 |
| 新規 | `lipidmix/analysis/preprocess_policy.py` | conservative-v1の解決・適用確認 |
| 新規 | `lipidmix/analysis/dataset_service.py` | トランザクション付き前処理・PCA・比較サービス |
| 新規 | `lipidmix/analysis/dataset_export.py` | 特定の差次的結果から契約TSVを生成 |
| 新規 | `lipidmix/plots/result_output.py` | 指定結果の選択・PCA/volcano保存 |
| 変更 | `lipidmix/mztab/dataset_state.py`, `lipidmix/analysis/dataset_analysis.py` | 状態フィールド、明示メタデータの使用 |
| 変更 | `lipidmix/tools/mztab_tools.py`, `dataset_analysis_tools.py`, `reports.py` | 共通サービスの薄いMCPラッパー |
| 変更 | `lipidmix/arf/tools.py`, `lipidmix/core/session_state.py` | ARFの所属データと描画来歴 |
| 新規 | `lipidmix/pipeline/__init__.py`, `request.py`, `inputs.py` | 要求の厳密解決、入力の選択・固定 |
| 新規 | `lipidmix/pipeline/store.py`, `engine.py`, `recovery.py` | 永続状態・冪等性、工程実行、再開・取消 |
| 新規 | `lipidmix/pipeline/report.py`, `service.py`, `worker.py` | 確定結果レポート、公開サービス、ワーカー入口 |
| 新規 | `lipidmix/tools/pipeline_tools.py` | 5件のpipeline MCPツール |
| 新規 | `tests/pipeline_fixtures.py`, `tests/fixtures/fake_console.py`, `tests/pipeline_worker_harness.py` | 合成入力、実子プロセスを使うfake実行、試験用エントリ |
| 新規/変更 | 各タスクの`tests/test_*.py`、`USAGE.md`、`docs/workflow/*.md` | 境界の検証と公開契約 |

新規ディレクトリの`__init__.py`は副作用のないものにする。MCP coreをimportして状態ディレクトリを作る処理をワーカーに持ち込まない。型は既存DatasetStateを除き、境界で厳密検証するJSON互換dictを基本とし、独自の巨大オブジェクトグラフを永続化しない。

## タスク間の共通契約

- パス引数は内部ではPath、MCPではstr。保存するpipeline成果物はpipeline_root相対パス。job内の既存root付きpathは維持する。
- `DomainError`はTask 1で`lipidmix/core/atomic_io.py`に置く小さな例外とする。`code: str, message: str, details: dict`を持ち、`str(exc)`は`f"{code}: {message}"`を返す。数値層にMCP依存を足さない。
- `result`は既存計算のdictを保ち、その`provenance`に`result_id, kind, dataset_id, request_revision, input_fingerprint, parent_ids, code_version, effective_parameters, warnings`を追加する。既存のresults/volcano/scores等のキーを改名しない。
- `run_record`はspec §9のidentity/request/status/stages/upstream/inputs/results/needs_input/warningsを持つdict。stage IDは`prepare_input, upstream, validate_outputs, load_dataset, resolve_metadata, preprocess, pca, resolve_comparisons, differential:<comparison_id>, export:<comparison_id>, report`。
- `StageResult`は`{"status": "succeeded|skipped|needs_input|failed", "result_refs": [], "warnings": [], "error": nullまたはエラーdict}`。例外を成功結果に変換しない。
- `handlers`は`dict[str, Callable[[dict], dict]]`。engineは登録された工程関数にstage contextを渡す。contextはrun_record、pipeline_path、必要な結果参照、ワーカー固有のDatasetStateを含む。状態を共有するのは同じworker内だけ。
- 文書中の代表テストは実装時に指定testファイルへ追加する。helperを使う場合は下記Task 1/4で定義する。代表テスト以外の追加ケースも各表の具体的な条件と期待値でパラメータ化する。

---

### Task 1: 終了証跡・原子的保存・合成fixtureの基礎

**Files:** Create `lipidmix/core/atomic_io.py`, `lipidmix/console/execution.py`, `tests/pipeline_fixtures.py`, `tests/test_execution_record.py`; Modify `lipidmix/handoff/schema.py::AnalysisJob.save`.

**Interfaces:** Produces `DomainError(code: str, message: str, details: dict | None = None)`, `canonical_hash(value: object) -> str`, `atomic_write_json(path: Path, data: dict) -> None`, `validate_execution_record(data: dict) -> dict`. 消費者は検証済みdictだけを保存する。日時・UUIDを計算hashに混ぜない。

- [ ] **1. RED用のhelperとテストを書く。** helperは新規証跡の全必須フィールドを持ち、他テストが独自に違う形を作らないようにする。

```python
# tests/pipeline_fixtures.py
def execution_record(**overrides):
    record = {
        "schema": "console-execution.v1", "execution_id": "exec-1", "job_id": "job-1",
        "started_at": "2026-09-05T00:00:00Z", "ended_at": "2026-09-05T00:00:01Z",
        "pid": 1234, "process_identity": {"pid": 1234, "creation_time": 100},
        "command_sha256": "a" * 64, "method_sha256": "b" * 64, "exe_sha256": "c" * 64,
        "exit_code": 0, "termination": "exited", "timeout_s": 21600,
        "collection": {"status": "succeeded"}, "validation": {"status": "pending"},
    }
    record.update(overrides)
    return record
```

```python
import json
import pytest
from pipeline_fixtures import execution_record
from lipidmix.core.atomic_io import DomainError, atomic_write_json
from lipidmix.console.execution import validate_execution_record

def test_exit_code_bool_is_not_an_integer_receipt():
    with pytest.raises(DomainError, match="EXECUTION_RECORD_INVALID"):
        validate_execution_record(execution_record(exit_code=True))

def test_failed_replace_keeps_old_json(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text('{"status":"running"}', encoding="utf-8")
    def fail_replace(*args):
        raise OSError("locked")
    monkeypatch.setattr("lipidmix.core.atomic_io.os.replace", fail_replace)
    with pytest.raises(OSError):
        atomic_write_json(path, {"status": "completed"})
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"
```

- [ ] **2. REDを確認する。** `C:/Python314/python.exe -m pytest tests/test_execution_record.py -q`。不正exit_codeの受理または旧JSONの破壊を失敗条件にする。
- [ ] **3. 保存と検証を実装する。** 同じ親のNamedTemporaryFile、UTF-8、allow_nan=False、flush、os.fsync、os.replaceの順。失敗時は未確定一時ファイルだけを片付ける。原本をunlinkしない。

```python
def canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def validate_exit_fields(data):
    rc = data["exit_code"]
    if rc is not None and type(rc) is not int:
        raise DomainError("EXECUTION_RECORD_INVALID", "終了コードが不正です")
    if data["termination"] == "exited" and rc is None:
        raise DomainError("EXECUTION_RECORD_INVALID", "終了コードがありません")
```

`validate_exit_fields`は同ファイル内のprivate helperとして作る。schema、enum、正のtimeout、UTC日時順、ID、hashの64桁、pidとidentityの整合も検査する。worker_lost/launch_failedでは不明pid・時刻の扱いを起動情報と区別し、起動失敗のpidはnullを許可する。
- [ ] **4. GREEN/回帰を確認する。** 上記＋`tests/test_console_runner.py`。v1/v2読込、NaN拒否、置換失敗、null rcを各テストにする。
- [ ] **5. レビュー後コミットする。** `git add -- lipidmix/core/atomic_io.py lipidmix/console/execution.py lipidmix/handoff/schema.py tests/pipeline_fixtures.py tests/test_execution_record.py` → `git commit -m "feat: Console終了証跡と原子的保存を定義する"`。

### Task 2: Windowsのidentity・排他・プロセス群管理

**Files:** Create `lipidmix/core/process_control.py`, `tests/test_process_control.py`.

**Interfaces:** Produces `process_identity(pid: int) -> dict | None`, `same_process(expected: dict) -> bool`, `file_lock(path: Path)`（context manager）、`start_owned_process(command: list[str], *, cwd: Path, log_path: Path) -> OwnedProcess`, `launch_detached(command: list[str], *, cwd: Path, log_path: Path) -> dict`。OwnedProcessは`identity: dict, poll() -> int | None, wait(timeout: float | None) -> int, terminate_tree() -> None, close() -> None`。

- [ ] **1. REDを書く。** 実際の無害なPython子プロセスを使う。Windowsでない環境はskip理由を記録し、Windows gateを通ったと扱わない。

```python
import os
import sys
import pytest
from lipidmix.core.process_control import start_owned_process, same_process

@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objectの検証")
def test_termination_reaps_owned_process(tmp_path):
    proc = start_owned_process([sys.executable, "-c", "import time; time.sleep(30)"],
                              cwd=tmp_path, log_path=tmp_path / "child.log")
    identity = proc.identity
    try:
        assert same_process(identity)
        proc.terminate_tree()
        assert proc.wait(timeout=5) != 0
        assert not same_process(identity)
    finally:
        proc.close()
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_process_control.py -q`。
- [ ] **3. ctypesラッパーを実装する。** Win32関数ごとにargtypes/restypeを宣言し、64bit HANDLEをint32へ切り詰めない。CreateProcessWでCREATE_SUSPENDED/CREATE_NO_WINDOW、Job ObjectへAssign、ResumeThreadの順にする。子が割当前に孫を作る競合を避ける。

```python
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# 起動順: CreateJobObjectW -> SetInformationJobObject -> CreateProcessW
#         -> AssignProcessToJobObject -> ResumeThread
# identity: GetProcessTimesのcreation FILETIMEとPIDを保存する。
# terminate_tree: TerminateJobObject後、全所有プロセスの終了を待つ。
```

切り離したworkerは親Jobからのbreakaway可否を検査し、保証できなければ`DETACH_UNSUPPORTED`を返す。console自体はworker所有Jobへ入れる。OSロックはWindowsのmsvcrt byte-range lock（1byteを確保）、必要ならPOSIXのflockに分岐。owner記録を消すだけでロック解除と見なさない。
- [ ] **4. GREEN/追加ケース。** PID同一・creation_time違い→false、Assign失敗→停止してresumeしない、孫プロセスも終了、並行lockの排他、親終了後のworker継続、全HANDLE解放を検証する。`taskkill /IM`など名前による一括終了は使わない。
- [ ] **5. レビュー後コミット。** `git add -- lipidmix/core/process_control.py tests/test_process_control.py` → `git commit -m "feat: Windows解析プロセスの所有権と排他を管理する"`。

### Task 3: 主mzTabと入力サンプル対応の完了ゲート

**Files:** Create `lipidmix/console/validation.py`, `tests/test_console_completion.py`; Modify `lipidmix/console/output_collector.py`, `tests/pipeline_fixtures.py`.

**Interfaces:** Consumes既存`parse_mztab, validate_mztab, collect_artifacts`。Produces `map_assays(parsed: dict, staged_sources: list[str]) -> dict[str, str]`, `validate_outputs(job: AnalysisJob, receipt: dict, expected_sources: list[str]) -> dict`, `completion_status(receipt: dict, validation: dict, has_artifacts: bool) -> str`。validationは`ok, errors, warnings, primary_path, sample_map`。

- [ ] **1. REDを書く。**

```python
from pipeline_fixtures import execution_record
from lipidmix.console.validation import completion_status

def test_intermediate_output_is_not_completed():
    receipt = execution_record(exit_code=1)
    validation = {"ok": False, "errors": ["PRIMARY_MZTAB_MISSING"]}
    assert completion_status(receipt, validation, has_artifacts=True) == "partial"

def test_zero_exit_with_invalid_mztab_is_not_completed():
    assert completion_status(execution_record(), {"ok": False}, True) != "completed"
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_console_completion.py -q`。
- [ ] **3. ゲートとsample mapを実装する。**

```python
def completion_status(receipt, validation, has_artifacts):
    verified = (receipt["termination"] == "exited"
                and type(receipt["exit_code"]) is int
                and receipt["exit_code"] == 0 and validation["ok"])
    return "completed" if verified else ("partial" if has_artifacts else "failed")
```

assay[N]-ms_run_ref→ms_run[N]-locationを辿り、file URIをURL decodeして準備済みrawに厳密対応させる。表示名では結合しない。予定数・列数だけで済ませず集合と一意性を検査する。数値行列が空・有限値なし、極性多数決の致命的不一致、measure不一致、候補複数はok=false。

fixtureに`write_mztab(path: Path, sources: list[Path], *, with_inchikey: bool = True) -> Path`を追加する。MTD version/mode/type、MS quantification単位、各assay/ref/location、SFHとSMF（少なくとも3特徴）、SML/SMEを含む合成値を生成し、実際のreader/validatorに通ることを単体検証する。既存`tests/test_mztab_tools.py`のSFH構文を使い、文字列`x`を成功fixtureにしない。
- [ ] **4. GREEN/回帰。** 上記＋`tests/test_mztab_validator.py tests/test_console_provenance.py`。URL文字・日本語/空白・重複・欠落・追加・1raw複数assayを検証。制御ファイルexecution-result/worker/controlを成果物に数えない。
- [ ] **5. レビュー後コミット。** `git add -- lipidmix/console/validation.py lipidmix/console/output_collector.py tests/test_console_completion.py tests/pipeline_fixtures.py` → `git commit -m "fix: Console完了に定量出力と入力対応の検証を要求する"`。

### Task 4: 同期・非同期共通の監視と全終了経路の収集

**Files:** Modify `lipidmix/console/execution.py`, `lipidmix/console/runner.py`; Create `tests/test_console_supervisor.py`, `tests/fixtures/fake_console.py`.

**Interfaces:** ConsumesTask 1〜3。Produces `supervise(job_path: Path, *, command: list[str] | None = None, cancel_path: Path | None = None) -> dict`。command注入は内部試験用のみ。公開MCP引数にはしない。返り値は終了証跡、結果はjobとsidecarへ原子的に保存する。

- [ ] **1. fakeとREDを書く。** fakeは`--scenario success|nonzero|hang|invalid|missing_sample`、`-i/-o/-m`を受ける試験スクリプトとする。successはTask 3の合成mzTab、nonzeroは中間ファイル後exit(1)、hangは子Pythonも起動して待つ。起動回数を試験用counterへappendする。

```python
import sys
from pathlib import Path
from lipidmix.console.execution import supervise

def test_nonzero_execution_collects_before_return(planned_fake_job):
    job_path, counter = planned_fake_job
    command = [sys.executable, str(Path(__file__).parent / "fixtures/fake_console.py"),
               "--scenario", "nonzero", "--job", str(job_path), "--counter", str(counter)]
    receipt = supervise(job_path, command=command)
    assert receipt["exit_code"] == 1
    assert receipt["collection"]["status"] == "succeeded"
    assert receipt["validation"]["status"] != "succeeded"
```

`planned_fake_job` fixtureを本test内に定義する。`tmp_path/source`へ4本のfake rawを作り、既存create_jobでjobを作る（時刻IDはTask 5でUUID化）。input inventoryをsourceの4本から作り、methodとfake exe hashを監視入力へ固定する。
- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_console_supervisor.py -q`。
- [ ] **3. 監視を実装する。** 単調時刻で期限管理し、receiptを先に確定、収集→validation→job保存の各段階を記録する。

```python
deadline = time.monotonic() + job.timeout_s
termination = "exited"
while proc.poll() is None:
    if cancel_path is not None and cancel_path.exists():
        termination = "cancelled"
        proc.terminate_tree()
        break
    if time.monotonic() >= deadline:
        termination = "timeout"
        proc.terminate_tree()
        break
    time.sleep(0.1)
exit_code = proc.wait(timeout=5)
```

上記はsupervise内の監視核。try/finallyでcloseし、起動不能でもlaunch_failedを保存する。収集失敗はbefore snapshotを保持、保存不能は復旧情報を応答に含める。生入力の開始/終了fingerprintとmethod hashを比較する。復旧用証跡を最終化前に消さない。
- [ ] **4. GREEN/回帰。** nonzero、timeout、取消、invalid、sample欠落、no output、保存失敗、snapshot変化を検証。`tests/test_console_runner.py`も実行し、失敗時に即returnして収集を飛ばす旧分岐を共通経路へ移す。
- [ ] **5. レビュー後コミット。** `git add -- lipidmix/console/execution.py lipidmix/console/runner.py tests/test_console_supervisor.py tests/fixtures/fake_console.py` → `git commit -m "fix: Consoleの全終了経路で証跡と成果物を保存する"`。

### Task 5: 単体Consoleの切り離しワーカーと旧経路の移行

**Files:** Create `lipidmix/console/worker.py`; Modify `lipidmix/console/runner.py`, `lipidmix/console/detached.py`, `lipidmix/console/job_manager.py`, `lipidmix/tools/console_tools.py`, `tests/test_console_detached.py`, `tests/test_console_cleanup.py`, `tests/test_console_runner.py`, `USAGE.md`.

**Interfaces:** Produces `launch_console_worker(job_path: Path) -> dict`, worker CLI `python -m lipidmix.console.worker --job <path>`。workerはTask 4のsuperviseを呼ぶ。job IDはUUIDを含める。`console_run(job_path=None, detach=False)`の公開形は維持する。

- [ ] **1. REDを書く。** 旧テストの「xというmzTabがあればcompleted」をTask 3の有効fixtureと証跡へ置換し、証跡なしのケースを独立に残す。

```python
def test_legacy_detached_state_is_not_promoted_by_files(tmp_path, monkeypatch):
    import json
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_status
    root = tmp_path / "source"
    root.mkdir()
    method = root / "method.txt"
    method.write_text("Ion mode: Positive\n", encoding="ascii")
    job, path = create_job(root, method, "positive", "peak_height")
    out = Path(job.run_dir)
    (out / ".detached-state.json").write_text('{"pid":123,"befores":{}}')
    (out / "intermediate.pai2").write_bytes(b"intermediate")
    monkeypatch.setattr("lipidmix.console.runner.is_process_running", lambda pid: False)
    console_status(str(path))
    assert load_job(path).status != "completed"
```

本testにPath importを付ける。Task 5で旧stateの安全な検出を維持するため、is_process_runningの互換wrapperは残す。
- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_console_detached.py tests/test_console_cleanup.py -q`。
- [ ] **3. 結線を実装する。**

```python
command = [sys.executable, "-m", "lipidmix.console.worker", "--job", str(job_path)]
worker = launch_detached(command, cwd=Path(__file__).resolve().parents[2],
                         log_path=Path(job.run_dir) / "worker.log")
# 受理応答のpidはworker。Console pidは起動後のexecution-start記録に置く。
```

job単位のOS lockはworkerがsupervise全体で保持。単体の同期呼出しも同じlockを取得する。新規runではconsole_statusは保存済み状態を読むだけ。旧detachedはidentity/終了コード不明として`EXECUTION_UNRESOLVED`を返し、成果物だけで完成へ進めない。cleanupはowner metadataがactiveなら拒否。pipeline ownerの具体的書込はTask 14で接続する。
- [ ] **4. GREEN/回帰。** UUID衝突なし、status無呼出しで完了、親終了、所有job cleanup拒否、旧v1/v2 status、worker起動後sidecar書込失敗を検証する。USAGEへpidの意味と旧ジョブの扱いを反映。
- [ ] **5. レビュー後コミット。** 上記Filesを明示stageし、`git commit -m "feat: 単体Consoleを独立監視ワーカーへ移行する"`。

**レビュー区切りA:** A01〜A07とD10のConsole側を確認。終了コード不明を成功扱いしないこと、statusが完了処理を担わないことを独立レビューする。Windowsの子/孫停止と親切断の実プロセステストが未実施なら、この区切りを完了にしない。

### Task 6: 結果ID・トランザクション・派生結果の無効化

**Files:** Create `lipidmix/analysis/result_state.py`, `lipidmix/analysis/dataset_service.py`, `tests/test_result_state.py`; Modify `lipidmix/mztab/dataset_state.py`, `lipidmix/tools/dataset_analysis_tools.py`, `tests/pipeline_fixtures.py`, `tests/test_dataset_analysis_tools.py`.

**Interfaces:** Produces `dataset_fingerprint(ds: DatasetState) -> str`, `metadata_fingerprints(rows: list[dict]) -> dict`, `invalidate_results(ds: DatasetState, changed: set[str]) -> None`, `assert_current(ds: DatasetState, result: dict) -> None`, `preprocess_dataset(ds: DatasetState, recipe: dict) -> dict`, `pca_dataset(ds: DatasetState, n_components: int = 5, log_transform: bool = False) -> dict`, `compare_dataset(ds: DatasetState, group_a: list[str], group_b: list[str], **options) -> dict`。最後の3関数は成功時だけdsの状態を更新する。

- [ ] **1. helperとREDを書く。**

```python
# tests/pipeline_fixtures.pyへ追加
def make_dataset():
    import numpy as np
    from lipidmix.mztab.dataset_state import DatasetState
    ds = DatasetState()
    ds.feature_matrix = np.random.default_rng(7).uniform(100, 200, (6, 8))
    ds.sample_names = [f"S{i}" for i in range(8)]
    ds.sample_assay_ids = [f"assay[{i + 1}]" for i in range(8)]
    ds.feature_ids = [f"F{i}" for i in range(6)]
    ds.quantification_measure = "peak_height"
    ds.validation_result = {"ok": True, "errors": [], "warnings": []}
    ds.feature_metadata = {f"F{i}": {"name": f"Lipid {i}", "mz": 500 + i,
        "rt": 2.0, "inchikey": "IPCSVZSSVZVIGE-UHFFFAOYSA-N",
        "inchikey_source": "database_identifier"} for i in range(6)}
    return ds
```

```python
from pipeline_fixtures import make_dataset
from lipidmix.analysis.dataset_service import preprocess_dataset, compare_dataset

def test_new_preprocess_invalidates_existing_comparison():
    ds = make_dataset()
    preprocess_dataset(ds, {"normalize": "none", "impute": "half_min"})
    result = compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])
    assert result["provenance"]["result_id"]
    preprocess_dataset(ds, {"normalize": "tic", "impute": "half_min"})
    assert ds.last_differential is None
    assert ds.last_pca is None
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_result_state.py tests/test_dataset_analysis_tools.py -q`。
- [ ] **3. 更新を共通サービスへ抽出する。**

```python
PP_FIELDS = {"role", "batch", "injection_order", "qc_pool", "include", "sample_id", "source_file"}

def invalidate_results(ds, changed):
    if changed & (PP_FIELDS | {"dataset", "detection", "recipe"}):
        ds.last_pca = None
        ds.last_differential = None
    elif changed & {"group", "comparison"}:
        ds.last_differential = None
    elif "pca_settings" in changed:
        ds.last_pca = None
```

dsへdataset_id、metadata_revision、preprocess_metadata_hash、preprocess_id、result registryを追加する。上記に加えメタデータ変更時はpp_matrix等も適切に無効化する。前処理は既存run_dataset_preprocessの返り値を一時変数に置き、全部成功してからppフィールドと来歴をまとめて反映。groupだけの変更ではpreprocess hashを変えない。NaN配列hashはdtype/shape/軸を含む正規化バイト列から作り、JSON NaNに依存しない。

新結果は既存dictのprovenanceへUUIDを付け、解析設定・親IDを固定する。同じ計算fingerprintなら有効結果を再利用できるが、古い結果のresult_idを別計算へ付け替えない。
- [ ] **4. GREEN/回帰。** 失敗前処理で旧matrix/recipe/resultが同一、同値レシピ再適用、groupのみ変更、検出mask変更、PCA設定のみ変更を検証。`tests/test_dataset_analysis.py tests/test_dataset_state.py`も実行。
- [ ] **5. レビュー後コミット。** 上記Filesを明示stageし、`git commit -m "fix: 前処理変更時に派生結果を無効化し来歴を固定する"`。

### Task 7: session非依存の読込と不完全・旧出力の区別

**Files:** Create `lipidmix/mztab/loading.py`, `tests/test_dataset_loading_service.py`; Modify `lipidmix/tools/mztab_tools.py`, `lipidmix/mztab/dataset_state.py`, `lipidmix/analysis/dataset_service.py`, `tests/test_mztab_tools.py`, `USAGE.md`, `docs/workflow/mztab.md`.

**Interfaces:** Produces `load_dataset_state(*, mztab_path: Path | None = None, job_path: Path | None = None, allow_incomplete: bool = False) -> DatasetState`。dsは`source_verification`（verified/legacy_unverified/direct_unverified）、`exploratory_only: bool`、`assay_sources: dict[str, str]`を持つ。ConsumesTask 3のmap_assaysとTask 6のdataset_id。

- [ ] **1. REDを書く。** 既存のpartial警告のみテストを、既定拒否と明示読込の2ケースへ分ける。

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.mztab.loading import load_dataset_state

def test_partial_requires_explicit_exploratory_mode(partial_job_path):
    with pytest.raises(DomainError, match="INCOMPLETE_ANALYSIS_JOB"):
        load_dataset_state(job_path=partial_job_path)
    ds = load_dataset_state(job_path=partial_job_path, allow_incomplete=True)
    assert ds.exploratory_only is True
```

`partial_job_path`は本test内でTask 3のwrite_mztab、既存create_job/MztabEntryを使って作る。job.status=partial、主mzTab参照を保存し、実行せずsynthetic jobを渡す。
- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_dataset_loading_service.py tests/test_mztab_tools.py -q`。
- [ ] **3. 既存_load_from_job等を共通ローダーへ抽出する。**

```python
if job.status != "completed" and not allow_incomplete:
    raise DomainError("INCOMPLETE_ANALYSIS_JOB", "未完了の解析ジョブです")
ds.exploratory_only = job.status != "completed"
ds.source_verification = "verified" if verified_receipt else "legacy_unverified"
```

verified_receiptはTask 1/3の検証とjob/execution/hash対応が一致した場合だけtrue。completed文字列だけではtrueにしない。direct読込はdirect_unverifiedで既存の単体解析互換を残すが、pipelineはverifiedだけを受ける。exploratory_onlyはcompare_dataset/TSV出力で拒否する。loading.pyはsession_state/mcp_coreをimportしない。MCPのdataset_loadは返ったdsを成功時だけsessionへ代入し、旧要約関数を利用する。
- [ ] **4. GREEN/回帰。** v1/v2旧completed、partial/failed/running、direct、hash不一致、両引数指定、ARF evidence、sample軸保持を検証。`allow_incomplete`は`type(value) is bool`で検査。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "refactor: mzTab読込を分離し未完了解析の利用を制限する"`。

### Task 8: 指定結果の図・TSV出力とARF共存

**Files:** Create `lipidmix/plots/result_output.py`, `lipidmix/analysis/dataset_export.py`, `tests/test_result_output.py`; Modify `lipidmix/tools/reports.py`, `lipidmix/tools/dataset_analysis_tools.py`, `lipidmix/arf/tools.py`, `lipidmix/core/session_state.py`, `tests/test_report_tools.py`, `USAGE.md`, `docs/workflow/plots.md`, `docs/workflow/dataset_analysis.md`.

**Interfaces:** Produces `select_result(candidates: list[dict], *, source: str = "auto", result_id: str | None = None) -> dict`, `save_result_figure(ds: DatasetState, result: dict, path: Path, *, kind: str, title: str | None = None) -> Path`, `export_dataset_result(ds: DatasetState, result: dict, path: Path) -> dict`。候補は`source, result_id, dataset_id, valid, result`を持つ。既存save_*の末尾へsource/result_id引数を追加。

- [ ] **1. REDを書く。**

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.plots.result_output import select_result

def test_auto_does_not_prefer_old_arf():
    candidates = [
        {"source": "arf", "result_id": "a", "dataset_id": "old", "valid": True, "result": {}},
        {"source": "mztab", "result_id": "m", "dataset_id": "new", "valid": True, "result": {}},
    ]
    with pytest.raises(DomainError, match="AMBIGUOUS_RESULT_SOURCE"):
        select_result(candidates)
    assert select_result(candidates, source="mztab", result_id="m")["dataset_id"] == "new"
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_result_output.py tests/test_report_tools.py -q`。
- [ ] **3. 出力元を固定する。**

```python
eligible = [c for c in candidates if c["valid"]
            and (source == "auto" or c["source"] == source)
            and (result_id is None or c["result_id"] == result_id)]
if len(eligible) > 1:
    raise DomainError("AMBIGUOUS_RESULT_SOURCE", "図に使う結果を指定してください")
if not eligible:
    raise DomainError("ANALYSIS_RESULT_NOT_FOUND", "有効な解析結果がありません")
return eligible[0]
```

上記をselect_resultに置く。PCA matplotlibコードをreports.pyから結果指定関数へ移し、volcanoは既存render_volcano_plotを共有。ARF側はファイル切替時の既存resetに来歴を接続し、result_idを付ける。別datasetのIDを指定しても採用しない。

TSV生成はdataset_analysis_toolsの既存コードをdataset_exportへ抽出し、Task 6のassert_currentとTask 7のexploratory_onlyゲートを先に適用。preprocessメタはresult.provenance.effective_parametersから取得する。出力は一時ファイルから確定し、失敗時に新しい完成品として登録しない。spec通り15列、InChIKey背景全行、空欄を維持する。
- [ ] **4. GREEN/回帰。** 古い前処理結果＋新recipe、同sourceの別dataset、候補1件の従来呼出し、不完全出力表示、InChIKey0件を検証。PNGを描画し、数値・群・出所が一致することを確認。`tests/test_dataset_analysis_tools.py tests/test_volcano_plot.py`も実行。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "fix: 図と差次的出力を指定結果と固定来歴に結び付ける"`。

**レビュー区切りB:** A08・B01〜B03・E02/E03の単体経路を確認。古い数字に新しい前処理条件を載せる経路と、ARFが暗黙優先される経路が残っていないことをレビューする。

### Task 9: sample-manifestの厳密読込・対応・出所・原子的適用

**Files:** Create `lipidmix/analysis/sample_manifest.py`, `tests/test_sample_manifest.py`; Modify `lipidmix/mztab/dataset_state.py`, `lipidmix/analysis/dataset_analysis.py`, `tests/pipeline_fixtures.py`.

**Interfaces:** Produces `parse_manifest(path: Path, *, source_root: Path, expected_sources: list[str]) -> list[dict]`, `resolve_metadata(ds: DatasetState, rows: list[dict] | None) -> list[dict]`, `apply_metadata(ds: DatasetState, rows: list[dict]) -> dict`。rowsはspecの8列＋`provenance: dict[str, dict], conflicts: list`を持つ。ds.sample_idsはsample_namesと同じ順の安定ID、表示名は上書きしない。

- [ ] **1. REDを書く。**

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.analysis.sample_manifest import parse_manifest

@pytest.mark.parametrize("order", ["NaN", "1.5", "0", "-1", "Infinity"])
def test_bad_order_is_rejected_before_application(tmp_path, order):
    root = tmp_path / "source"
    root.mkdir()
    (root / "S0.wiff").write_bytes(b"fake")
    sheet = root / "sample-manifest.tsv"
    sheet.write_text("# schema = sample-manifest.v1\n"
        "sample_id\tsource_file\trole\tgroup\tbatch\tinjection_order\tqc_pool\tinclude\n"
        f"S0\tS0.wiff\tsample\tcontrol\tB1\t{order}\t\ttrue\n", encoding="utf-8")
    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_sample_manifest.py -q`。
- [ ] **3. 厳密パーサと出所解決を実装する。**

```python
def parse_injection_order(text):
    if text == "":
        return None
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise DomainError("SAMPLE_MANIFEST_INVALID", "注入順は正の整数です")
    return int(text)
```

csv.DictReader(delimiter="\t")で余剰/不足列を拒否。Path.resolveしたsourceがroot内か検証し、casefold後の衝突、全rawへの行、重複ID/raw、includeのtrue/falseのみを検査する。batch空欄で注入順がある行は未確認batchとして保持し、自動ドリフトに使わない。同じ未確認batch内の注入順重複も拒否する。

明示値はconfidence=confirmed、名前由来はinferred、出所不明mzTab注入順はunverified。各value/source/confidenceを保持し、異なる候補をconflictsに残す。明示role=unknownをsampleへ再推定しない。明示シートなしのrole fallbackだけ既存detect_sample_rolesを使う。apply_metadataは対応/型を全検証してからTask 6のhash比較で変更種別を求め、一括代入と無効化を行う。

Task 11以降の試験helperとして次を`tests/pipeline_fixtures.py`へ追加する。8試料のfixtureに対し、QC数0〜4を同じ契約で生成する。

```python
def metadata_rows(ds, *, n_qc=4, confirmed=True):
    assert len(ds.sample_names) == 8 and 0 <= n_qc <= 4
    qc_orders = [1, 3, 6, 8][:n_qc]
    orders = qc_orders + [i for i in range(1, 9) if i not in qc_orders]
    rows = []
    for i, name in enumerate(ds.sample_names):
        row = dict(sample_id=name, source_file=f"{name}.wiff",
                   role="qc" if i < n_qc else "sample", group=None,
                   batch="B1", injection_order=orders[i],
                   qc_pool="pool1" if i < n_qc else None, include=True)
        row["provenance"] = {
            key: {"value": value, "source": "user_manifest", "confidence": "confirmed"}
            for key, value in row.items()
        }
        if not confirmed:
            for key in ("batch", "injection_order", "qc_pool"):
                row["provenance"][key].update(source="mztab", confidence="unverified")
        row["conflicts"] = []
        rows.append(row)
    return rows
```
- [ ] **4. GREEN/回帰。** C01/C02/C03、include=falseもassay照合対象、シートとassayの順序違い、groupのみ更新のB04、metadata失敗時の旧状態不変。既存dataset_analysisのメタ生成は明示メタデータを優先し、未適用の従来経路は保持する。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "feat: 実験情報シートを厳密に検証して解析状態へ適用する"`。

### Task 10: 要求・比較設定のスキーマと未指定/nullの区別

**Files:** Create `lipidmix/pipeline/__init__.py`, `lipidmix/pipeline/request.py`, `tests/test_pipeline_request.py`.

**Interfaces:** Produces `resolve_request(source_root: Path, explicit: dict | None = None) -> dict`, `merge_updates(request: dict, updates: dict) -> dict`, `request_fingerprint(request: dict) -> str`。要求の相対パスはsource_root基準。unknown keysを拒否し、`effective_target`と`value_sources`を内部解決結果へ付ける。

- [ ] **1. REDを書く。**

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline.request import resolve_request, merge_updates

def test_null_disable_is_distinct_from_omitted(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    original = resolve_request(root)
    assert original["preprocess"]["blank_min_fold"] == "auto"
    changed = merge_updates(original, {"preprocess": {"blank_min_fold": None}})
    assert changed["preprocess"]["blank_min_fold"] is None
    assert changed["preprocess"]["normalize"] == "auto"

def test_resume_cannot_change_upstream(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(DomainError, match="NEW_PIPELINE_REQUIRED"):
        merge_updates(resolve_request(root), {"method_file": "other.txt"})
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_pipeline_request.py -q`。
- [ ] **3. 解決順と厳密な型検査を実装する。**

```python
UPDATABLE = {"target", "sample_manifest", "preprocess", "comparisons"}

def merge_updates(request, updates):
    if set(updates) - UPDATABLE:
        raise DomainError("NEW_PIPELINE_REQUIRED", "上流条件の変更には新しい解析が必要です")
    out = copy.deepcopy(request)
    for key, value in updates.items():
        if key == "preprocess":
            out[key].update(value)
        else:
            out[key] = copy.deepcopy(value)
    return validate_request(out, internal=True, updated_fields=updates)
```

`validate_request(data: dict, *, internal: bool = False, updated_fields: dict | None = None) -> dict`を同ファイルに定義し、spec §10のキー・enum・数値域を全検査する。外部入力では内部キーeffective_target/value_sourcesを拒否する。internal=Trueは検証済み要求の更新専用とし、effective_targetを再計算し、updated_fieldsで指定されたフィールドの出所をexplicit_updateへ更新、未変更フィールドの出所を維持する。preprocessは指定された子キーだけを更新対象とする。更新dict自身には内部キーを許可しない。sample_manifest=nullは「既定シート再探索」ではなく自動一覧への明示切替として保持。comparisonsは配列全体を置換し、ID重複・path脱出・同じreference/testを拒否する。preprocessの更新値がdictであることと更新キーを、上記update呼出しより先に検証する。

上流情報の不足は後続Task 13/14でneeds_inputへ変換するが、不正JSON/未知キー/不正値はこの段階でDomainErrorを返す。新しいメソッドで不足を解消する場合はpipeline_runへ新requestを渡す。上流を変更できないpipeline_resumeに同じ入力要求を戻すループを作らない。
- [ ] **4. GREEN。** boolをtimeout/閾値として拒否、NaN/Infinity、明示値>ファイル>既定、target固定/更新、manifest解除、preprocess深い更新、比較方向/ラベルIDを検証。
- [ ] **5. レビュー後コミット。** `git add -- lipidmix/pipeline/__init__.py lipidmix/pipeline/request.py tests/test_pipeline_request.py` → `git commit -m "feat: パイプライン要求と更新の厳密な契約を定義する"`。

### Task 11: conservative-v1と適用/skipの監査

**Files:** Create `lipidmix/analysis/preprocess_policy.py`, `tests/test_preprocess_policy.py`; Modify `lipidmix/analysis/dataset_service.py`, `lipidmix/analysis/preprocessing.py`（不足する適用状況の返却だけ）、`tests/test_preprocessing.py`.

**Interfaces:** Produces `resolve_policy(ds: DatasetState, requested: dict, metadata: list[dict]) -> dict`, `check_applied_policy(plan: dict, report: dict) -> None`, `preprocess_auto(ds: DatasetState, requested: dict, metadata: list[dict]) -> dict`。planはrequested_recipe/resolved_recipe/applied_steps/skipped_steps/reasons/policy_version/assumptionsを持つ。applied_stepsは計算完了まで空にする。

- [ ] **1. REDを書く。**

```python
from pipeline_fixtures import make_dataset, metadata_rows
from lipidmix.analysis.preprocess_policy import resolve_policy

def test_unverified_order_never_enables_auto_drift():
    ds = make_dataset()
    rows = metadata_rows(ds, confirmed=False)
    plan = resolve_policy(ds, {"policy": "conservative-v1", "drift_correct": "auto"}, rows)
    assert plan["resolved_recipe"]["drift_correct"] is False
    assert "drift_correct" in plan["skipped_steps"]

def test_confirmed_pool_with_enclosing_qc_enables_drift():
    ds = make_dataset()
    plan = resolve_policy(ds, {"policy": "conservative-v1"}, metadata_rows(ds))
    assert plan["resolved_recipe"]["normalize"] == "pqn"
    assert plan["resolved_recipe"]["drift_correct"] is True
    assert plan["resolved_recipe"]["max_qc_rsd"] == 0.30
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_preprocess_policy.py -q`。
- [ ] **3. policy表を分岐として実装する。**

```python
same_pool = len(confirmed_pool_ids) == 1 and not missing_pool
qc_eligible = single_confirmed_batch and same_pool and not failed_qc
resolved = {
    "normalize": "pqn" if qc_eligible and n_qc >= 3 else "none",
    "blank_min_fold": 3.0 if n_blank and n_sample else None,
    "drift_correct": bool(qc_eligible and n_qc >= 4 and all_orders_confirmed and all_samples_enclosed),
    "max_qc_rsd": 0.30 if qc_eligible and n_qc >= 3 else None,
    "impute": "half_min", "min_detection_rate": 0.0,
}
```

上記の集計はinclude=true対象で算出し、QChealthは既存detect_failed_qcの生強度評価を使う。explicit overrideを最後に検査して反映するが、複数poolを単一QC参照にする指定を拒否する。unknown orderやQC不足の明示補正は`PREPROCESS_PREREQUISITE_MISSING`。autoではskip理由を保持。

計算をtemporary DatasetStateへ適用し、reportの未適用・unscaled_samples・有限な有効行をcheck_applied_policyで検査してからTask 6のcommitを行う。NORMALIZATION_DEGENERATE、特徴ゼロ、PCA不成立はneeds_inputへ変換できるDomainErrorにする。RSD/blank関数がcaveatだけ返して未実施をappliedに数える場合は、数値式を変えずstatusを追加する。
- [ ] **4. GREEN/追加ケース。** QC数0/2/3/4、unknown batch、multi batch、2pool、失敗QC、QC区間外試料、blankなし、明示false/null、検出maskなし、係数退化をパラメータ化する。単体dataset_preprocess既定は従来どおりであることを回帰確認。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "feat: 根拠と適用状況を記録する自動前処理を追加する"`。

### Task 12: 比較前提・交絡・メタデータMCPを接続する

**Files:** Modify `lipidmix/analysis/dataset_service.py`, `lipidmix/tools/dataset_analysis_tools.py`, `server.py`, `tests/test_dataset_analysis_tools.py`, `tests/test_server_registration.py`, `tests/test_workflow_docs.py`, `USAGE.md`, `docs/workflow/dataset_analysis.md`; Create `tests/test_comparison_contract.py`.

**Interfaces:** Produces `resolve_comparison(ds: DatasetState, comparison: dict, metadata: list[dict]) -> dict`, `run_comparison(ds: DatasetState, comparison: dict, metadata: list[dict]) -> dict`、公開`dataset_set_sample_metadata(manifest_path: str) -> str`。resolvedはsample IDs、旧計算に渡すsample_names、group labels、confounding、overrideを持つ。

- [ ] **1. REDを書く。**

```python
import pytest
from pipeline_fixtures import make_dataset, metadata_rows
from lipidmix.core.atomic_io import DomainError
from lipidmix.analysis.dataset_service import resolve_comparison

def test_group_names_do_not_determine_reference_implicitly():
    ds = make_dataset()
    rows = metadata_rows(ds, n_qc=0)
    for i, row in enumerate(rows):
        row["group"] = "control" if i < 4 else "treated"
    with pytest.raises(DomainError, match="COMPARISON_REQUIRED"):
        resolve_comparison(ds, {}, rows)
```

Task 9のmetadata_rowsのn_qc=0を使い、全試料をsample、注入順を1〜8にする。
- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_comparison_contract.py tests/test_dataset_analysis_tools.py -q`。
- [ ] **3. 比較ガードとツールを実装する。**

```python
if not comparison.get("reference_group") or not comparison.get("test_group"):
    raise DomainError("COMPARISON_REQUIRED", "対照群と比較群を指定してください")
included = [r for r in metadata if r["include"] and r["role"] == "sample"]
group_a = [r["sample_id"] for r in included if r["group"] == comparison["reference_group"]]
group_b = [r["sample_id"] for r in included if r["group"] == comparison["test_group"]]
```

sample_id→sample_namesの対応を検証し、重複・未知・各群n<2をreject。既存check_confoundingを使い、完全交絡はallow_confounded=trueなしではCONFOUNDED_COMPARISON。batch情報不足は「評価不可」であり、完全交絡と決め付けない。result来歴に正の向き、実際の群、未調整overrideを固定する。直接のdataset_differentialとpipelineの新しい明示比較の違いをUSAGEへ記載する。

metadata MCPはTask 9のparse/resolve/applyを呼び、失敗時はsessionを変えない。登録とUSAGE/workflow/EXPECTED_TOOLSをこのタスクで更新し、新公開ツールだけが文書から漏れる中間状態を残さない。
- [ ] **4. GREEN/回帰。** QC/blank/unknown除外、include=false、重複/不足、方向、完全交絡の停止と明示継続、メタ更新によるPCA再利用/差次無効化。`tests/test_server_registration.py tests/test_readme_links.py tests/test_workflow_docs.py`を実行。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "feat: 実験情報の訂正と明示比較の前提検証を接続する"`。

**レビュー区切りC:** B04・C01〜C07を全確認。トークン由来のrole、未確認batch/order、明示qc_poolを混同しないこと、auto skipと明示要求失敗が分かれることをレビューする。

### Task 13: 入力一覧・メソッド選択・解析専用配置

**Files:** Create `lipidmix/pipeline/inputs.py`, `tests/test_pipeline_inputs.py`; Modify `lipidmix/console/input_prep.py`, `lipidmix/console/method_file.py`, `tests/pipeline_fixtures.py`, `tests/test_console_input_prep.py`.

**Interfaces:** Produces `inspect_inputs(source_root: Path, request: dict, *, exe_path: Path) -> dict`, `select_method(candidates: list[dict]) -> dict`, `stage_inputs(plan: dict, pipeline_root: Path) -> dict`, `verify_inputs(snapshot: dict) -> None`。input planはsource_root、raw inventory、選択形式、companions、method/LBM/exeのパス/hash、極性/出所、未検証条件を持つ。

- [ ] **1. REDを書く。**

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline.inputs import select_method

def test_distinct_methods_are_not_selected_by_mtime():
    candidates = [
        {"path": "own.txt", "sha256": "a" * 64, "mtime": 1, "polarity": "negative"},
        {"path": "sibling.txt", "sha256": "b" * 64, "mtime": 2, "polarity": "negative"},
    ]
    with pytest.raises(DomainError, match="METHOD_FILE_CHOICE_REQUIRED"):
        select_method(candidates)

def test_byte_identical_copies_are_one_method():
    candidates = [{"path": name, "sha256": "a" * 64, "polarity": "negative"}
                  for name in ["own.txt", "copy.txt"]]
    assert select_method(candidates)["sha256"] == "a" * 64
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_pipeline_inputs.py -q`。
- [ ] **3. snapshotと入力配置を実装する。**

```python
groups = {}
for candidate in candidates:
    groups.setdefault(candidate["sha256"], []).append(candidate)
if len(groups) != 1:
    raise DomainError("METHOD_FILE_CHOICE_REQUIRED", "解析条件を一つ選んでください",
                      {"candidates": candidates})
chosen = sorted(next(iter(groups.values())), key=lambda c: c["path"])[0]
```

候補探索は既存discover_method_candidatesを元source_rootに対して使い、選択前にspec通り極性/omicsを絞る。上記は内容同一候補の代表選択に限る。メソッドのraw byte hashでまとめるため、異なる相対パス基準で同じ文字列を含む候補は参照先hashも照合し、異なる実効参照なら一意と扱わない。

ファイル形式はos.link→copy2、ディレクトリrawはcopytree。コピー前に対象の合計容量と空き容量を検査。新規の配置先は空であることを要求し、再開時はsource inventoryと配置済みinventoryを照合する。symlink/reparse pointが元root外へ出る入力は拒否し、コピーで外部階層を巻き込まない。随伴はwiff.scan・timeseries.data等の明示ルールのみとし、PAI2/DCL/ARF/タグ等をstem一致で含めない。

`.wiff/.wiff2`のstem集合が一致する場合だけwiff既定。元rawなし・子のみ、混在不一致、容量不足、配置済み不一致は個別code。STAT fingerprintは相対パス/size/mtime_ns、ディレクトリrawは内部ファイルを列挙し、内容hashと区別して保存する。

LBMは既存resolverで解決後hashを固定。非空のメソッドfile/pathキーは、既知の参照キー登録から原本基準で絶対解決する。未対応の相対参照キーはMETHOD_REFERENCE_UNRESOLVED。未知キーの値をパスと決め付けてコピーしない。ASCII実効メソッドへ書けない文字がある場合、文字を`?`に置換せず`METHOD_ENCODING_UNSUPPORTED`。実効methodを常にpipeline内へ保存する。

tests/pipeline_fixturesへ`make_source(root: Path) -> dict`を追加する。rootにS0..S7.wiff、`lab_param_202609050001.txt`（Ion mode/Target omics/Lbm file path）、fake.lbm2、fake.exeを作り、root/method/lbm/exeのPathを返す。fake.exeは実行しないunit test用で、preflightのis_console_exeは試験内で差し替える。
- [ ] **4. GREEN/回帰。** D07/D08/D05、wiff.scan保持、wiff2除外、異なる出力先、readonly source＋別output_root、raw stat変化、元method不変、参照パス基準違いを検証。旧console_prepare_inputの公開互換も確認。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "feat: 生データと実効条件を解析専用領域へ固定する"`。

### Task 14: pipeline-run保存・受付冪等性・所有権

**Files:** Create `lipidmix/pipeline/store.py`, `tests/test_pipeline_store.py`; Modify `lipidmix/console/job_manager.py`, `lipidmix/tools/console_tools.py`, `tests/test_console_cleanup.py`.

**Interfaces:** Produces `create_run(source_root: Path, request: dict, inputs: dict) -> Path`, `load_run(path: Path) -> dict`, `save_run(path: Path, record: dict, *, expected_revision: int) -> None`, `find_or_create_run(source_root: Path, request: dict, inputs: dict, *, request_id: str | None = None) -> Path`, `register_job_owner(job_path: Path, pipeline_path: Path) -> None`。save_runのexpected_revisionは制御state_revision。要求のrequest.revisionとは別カウンタ。

- [ ] **1. REDを書く。**

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline.request import resolve_request
from lipidmix.pipeline.store import find_or_create_run

def test_same_request_id_is_content_addressed(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    req = resolve_request(root)
    inputs = {"source_root": str(root), "fingerprint": "a" * 64, "raw_inventory": []}
    first = find_or_create_run(root, req, inputs, request_id="request-1")
    assert find_or_create_run(root, req, inputs, request_id="request-1") == first
    changed = {**req, "target": "differential", "effective_target": "differential"}
    with pytest.raises(DomainError, match="IDEMPOTENCY_CONFLICT"):
        find_or_create_run(root, changed, inputs, request_id="request-1")
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_pipeline_store.py -q`。
- [ ] **3. ロックと保存を実装する。**

```python
with file_lock(source_index_dir / "index.lock"):
    # source_index_dirは正規化source_rootのhashごとに一つ。
    existing = lookup_request(request_id, request_hash, input_hash)
    if existing is not None:
        return existing
    pipeline_path = create_run(source_root, request, inputs)
    write_index_entry(pipeline_path, request_id, request_hash, input_hash)
    return pipeline_path
```

lookup_request/write_index_entryはstore.pyのprivate関数として定義する。sourceの書込権限とoutput_rootによらず受付排他を共有するため、同一ホストの`%LOCALAPPDATA%/Lipidmix/pipeline-index/<source_hash>/`へ小さな索引を保存する。索引にはrun path/request_id/hash/ownerだけを置き、解析ログ・行列は置かない。testsでは索引baseをtmpへ注入する。読取statusのimport時には索引dirを作らない。

input hashはraw stat・実効method/LBM/exe hashを含む。活動中一致runを返し、completed一致runも全必須artifact hashを確認して再利用。明示request_idが一致する既存runの成果物が壊れていたら、新runを黙って作らず`RESULT_INTEGRITY_MISMATCH`を返す。

runの更新はpipeline lock内でexpected state_revisionを照合して原子的保存。request revisionファイルと結果履歴は書換え不可。job ownership sidecarをConsole起動前に作り、単体console_run/cleanupの保護判定へつなぐ。所有pipelineが活動中か判定不能なら削除を許可しない。
- [ ] **4. GREEN。** 2プロセス同時受付、同一入力・異なるoutput_rootでも排他、同一秒UUID、state更新競合、保存失敗、completed再送、索引はあるがrun消失、D10の単体実行/cleanupを検証。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "feat: パイプライン状態と受付の冪等性を永続化する"`。

### Task 15: 工程エンジンとsessionを持たないworker

**Files:** Create `lipidmix/pipeline/engine.py`, `lipidmix/pipeline/worker.py`, `tests/test_pipeline_engine.py`, `tests/pipeline_worker_harness.py`.

**Interfaces:** Produces `run_engine(pipeline_path: Path, handlers: dict[str, Callable[[dict], dict]]) -> dict`, `build_stages(request: dict) -> list[dict]`, worker CLI `python -m lipidmix.pipeline.worker --pipeline <path>`。標準handlerの組立`build_handlers() -> dict`はTask 18のservice.pyで実装する。Task 15では注入handlerでengineを独立に検証する。

- [ ] **1. REDを書く。**

```python
from lipidmix.pipeline.engine import run_engine

def test_missing_comparison_keeps_exploration_and_stops(differential_run):
    path, handlers, calls = differential_run
    result = run_engine(path, handlers)
    assert result["status"] == "needs_input"
    assert "upstream" in calls and "pca" in calls
    assert not any(name.startswith("export:") for name in calls)
    assert result["request"]["effective_target"] == "differential"
```

`differential_run` fixtureはTask 14のcreate_runでtarget=differential、comparisons=[]のrunを作り、各stageのhandlerを呼出し履歴へappendする関数として実装する。pcaまではsucceeded、resolve_comparisonsはCOMPARISON_REQUIREDのneeds_inputを返す。空比較配列でもdifferential目標にはこの比較準備ゲートを作り、stageがないからcompletedとしない。exploratory目標では比較工程一式を対象外として記録する。
- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_pipeline_engine.py -q`。
- [ ] **3. 工程進行を実装する。**

```python
runtime = {}
for stage in record["stages"]:
    if stage["status"] in {"succeeded", "skipped"} and stage_inputs_unchanged(stage):
        continue
    if cancel_requested(pipeline_path):
        return finish_cancelled(pipeline_path, record)
    mark_stage_running(pipeline_path, record, stage)
    outcome = handlers[stage["handler"]](make_context(record, stage, runtime))
    commit_stage_outcome(pipeline_path, record, stage, outcome)
    if outcome["status"] in {"needs_input", "failed"}:
        return finish_interrupted(pipeline_path, record, outcome)
return finish_success(pipeline_path, record)
```

上記helperはengine.py内へ定義する。stage_inputs_unchangedはTask 14の状態・artifact hashを検査、make_contextはruntime内のworker固有dsを参照する。runtimeはプロセス内限定で、run_recordへのシリアライズやglobal sessionへの格納をしない。再開時はTask 16の再構築を行い、メモリ上に存在しないDatasetStateを工程skipだけで利用しない。handlerキーとstage_idを分け、comparison別のstageは共通handlerへcomparison_idを渡す。group情報不足でも上流/PCAまで進める。一方、manifest不正は受付時に拒否する。実装ではhandler呼出しをtry/exceptで囲み、入力で解消するDomainErrorコードだけneeds_inputへ、それ以外の例外はfailedのStageResultへ変換して保存する。未知例外はtracebackをログへ残し、成功結果として返さない。

workerはpipeline lockを実行のownerとして保持するが、status/cancel要求保存を妨げない。長期owner lockと短いstate更新lockを分離する。stage境界で原子的保存し、失敗時は成功済み結果を保持。有効な解析出力があればpartial、なければfailed、入力で解決するものはneeds_input。図やreportの失敗を成功に変換しない。

試験harnessは同じengineをimportし、test専用fake commandをhandlerへ注入する。production workerはtest環境変数や任意import名を入力から受け付けない。Task 18のbuild_handlersができるまではproduction workerを公開ツールへ登録しない。
- [ ] **4. GREEN。** target別必須工程、空比較ゲート、比較2件の一方失敗、明示処理不足、stage順、cancel境界、state保存失敗、同時engine起動拒否を検証。session_state/mcp_core/toolsへのimportがないことをASTで検査する。
- [ ] **5. レビュー後コミット。** `git add -- lipidmix/pipeline/engine.py lipidmix/pipeline/worker.py tests/test_pipeline_engine.py tests/pipeline_worker_harness.py` → `git commit -m "feat: 永続工程を進める独立解析ワーカーを追加する"`。

### Task 16: 再開・再構築・取消・中断の読取表示

**Files:** Create `lipidmix/pipeline/recovery.py`, `tests/test_pipeline_recovery.py`; Modify `lipidmix/pipeline/store.py`, `lipidmix/pipeline/engine.py`.

**Interfaces:** Produces `prepare_resume(path: Path, *, updates: dict | None = None, request_id: str | None = None, rerun_upstream: bool = False) -> dict`, `request_cancel(path: Path) -> dict`, `read_status(path: Path, *, include_details: bool = False) -> dict`。prepare_resumeはlaunchせず、新revision/attemptと再利用判断を保存する。

- [ ] **1. REDを書く。**

```python
from lipidmix.pipeline.recovery import read_status

def test_status_does_not_finalize_or_rewrite(run_with_lost_worker, monkeypatch):
    path = run_with_lost_worker
    before = path.read_bytes()
    monkeypatch.setattr("lipidmix.pipeline.recovery.same_process", lambda identity: False)
    status = read_status(path)
    assert status["observed_health"] == "worker_missing"
    assert path.read_bytes() == before
    assert status["status"] != "completed"
```

`run_with_lost_worker`はTask 14のcreate_run/load/saveでstatus=running、worker identityを設定したfixture。実PIDを終了させない。
- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_pipeline_recovery.py -q`。
- [ ] **3. 再開判断を実装する。**

```python
if console_identity and same_process(console_identity) and not verified_receipt:
    raise DomainError("EXECUTION_UNRESOLVED", "監視を失ったConsoleが稼働しています")
if upstream_needs_rerun and not rerun_upstream:
    raise DomainError("UPSTREAM_RERUN_REQUIRED", "上流の再実行を明示してください")
```

updatesはTask 10で検証し、Task 6の依存区分で再計算stageを選ぶ。metadata/recipe/comparisonの固定コピーをrevision別に保存し、旧snapshotを上書きしない。確認済み上流だけを再利用し、hashを検証してloading→metadata→必要ならpreprocess/PCAを再構築する。保存行列がなくてもsessionなしで再開できるようにする。

worker終了後の受付応答が失われた再送でも新attemptを増やさないよう、resumeのrequest_idと更新内容を記録する。request_id未指定で同じ更新が既に反映済みなら新revisionを作らない。取消はcancel_requested=trueを保存して受理を返し、cancelledは実際にworkerが停止を確認した後に確定する。Job Objectのowner identityが不明なら別PIDを殺さず復旧要求を返す。

収集・validation保存の失敗からのresumeは、既存receipt/beforeを使って再収集できる。Console再実行にすり替えない。statusはread-onlyで、observerの生存確認エラーもdeadと断定しない。
- [ ] **4. GREEN。** D03/D05/D09/E04、再開中の二重呼出し、旧結果保存、group-only更新、cancel再送、PID再利用、hash不一致、同一更新再送、監視不明、結果だけ残る旧jobを検証。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "feat: 解析の再開と取消を証跡と依存関係で制御する"`。

### Task 17: 必須成果物・決定的レポート・部分完了

**Files:** Create `lipidmix/pipeline/report.py`, `tests/test_pipeline_report.py`; Modify `lipidmix/analysis/dataset_export.py`, `lipidmix/plots/result_output.py`, `lipidmix/pipeline/engine.py`.

**Interfaces:** Produces `required_outputs(request: dict) -> list[str]`, `evaluate_target(record: dict) -> dict`, `write_pipeline_report(record: dict, path: Path) -> dict`, `persist_result(pipeline_root: Path, result: dict) -> dict`。出力参照はresult_id/kind/path/sha256/parent_ids/request_revisionを持つ。

- [ ] **1. REDを書く。**

```python
from lipidmix.pipeline.report import required_outputs

def test_differential_requires_background_tsv_for_each_comparison():
    request = {
        "effective_target": "differential", "save_project": False,
        "comparisons": [{"comparison_id": "treated_vs_control"},
                        {"comparison_id": "recovery_vs_control"}],
    }
    outputs = required_outputs(request)
    assert "pca" in outputs
    assert "tsv:treated_vs_control" in outputs
    assert "tsv:recovery_vs_control" in outputs
    assert "gui_project" not in outputs
```

evaluate_targetの部分完了テストではTask 14のcreate_runとTask 17のpersist_resultで、実ファイル・hash・IDを持つ探索結果をtmp_pathへ保存する。Task 1/3の検証済み上流証跡を付け、差次的結果はあるがTSVだけ未生成、output_failuresにEXPORT_BACKGROUND_EMPTYがある状態を作り、status=partialとreason_codesへの同コード保持をassertする。path/hashを持たないダミーのresult_refで有効成果物を代用しない。TSVを生成した正常ケース、保存後に改変したhash不一致ケースも同じfixtureから検証する。

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_pipeline_report.py -q`。
- [ ] **3. 出力をmanifestで確定する。**

```python
def required_outputs(request):
    names = ["preprocess", "pca", "pca_figure", "quality_report"]
    if request["save_project"]:
        names.append("gui_project")
    if request["effective_target"] == "differential":
        for comparison in request["comparisons"]:
            cid = comparison["comparison_id"]
            names.extend([f"differential:{cid}", f"volcano:{cid}", f"tsv:{cid}"])
    return names
```

evaluate_targetは上流verifiedと全必須outputのhash/IDを検査し、target=differentialで比較定義空ならCOMPARISON_REQUIRED。quality_report作成前の必須判定と最終確定は分け、レポートが自分の未生成を理由に永久失敗する循環を作らない。レポートには生成時点のstatusと目標達成内訳を載せ、最終completedはreport保存後のstate更新で確定する。

Markdownレポートはsource、method/LBM/版、実行証跡、role/group/batch/order出所、applied/skip、PCA、比較、InChIKey被覆、未検証条件を固定順で出す。未確認の生物学的解釈は生成しない。表内の改行/パイプ文字をescapeする。全数値結果をJSON/TSVへ永続化する際は非有限値をnull＋理由に変換し、pickleを使わない。

InChIKey0件では内部差次結果を残してTSVを作らず、EXPORT_BACKGROUND_EMPTYを記録。save_project=trueでGUI projectがない場合も下流を保存しpartial。相対リンクは当該pipeline内に制限する。
- [ ] **4. GREEN/出力確認。** TSVをcsv.DictReaderで再読込し列・方向・全背景・空欄を照合。PNGを実描画して指定source/result_idとラベルを確認。古いrevisionの成果物が新reportに混ざらないことを検証する。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "feat: パイプライン成果物と品質レポートを確定する"`。

### Task 18: handler組立・MCP入口・登録文書の統合

**Files:** Create `lipidmix/pipeline/service.py`, `lipidmix/tools/pipeline_tools.py`, `tests/test_pipeline_tools.py`, `docs/workflow/pipeline.md`; Modify `lipidmix/pipeline/worker.py`, `server.py`, `lipidmix/core/mcp_core.py`, `lipidmix/core/mcp_errors.py`, `USAGE.md`, `docs/workflow/index.md`, `tests/test_server_registration.py`, `tests/test_workflow_docs.py`.

**Interfaces:** Produces `plan_pipeline(dataset_root: Path, request: dict | None = None, request_id: str | None = None) -> dict`, `start_pipeline(dataset_root: Path, request: dict | None = None, request_id: str | None = None) -> dict`, `resume_pipeline(path: Path, updates: dict | None = None, request_id: str | None = None, rerun_upstream: bool = False) -> dict`, `build_handlers() -> dict`, `launch_pipeline_worker(path: Path) -> dict`。MCPはspecの5件の名称・引数をそのまま公開する。

- [ ] **1. REDを書く。**

```python
import json
from lipidmix.tools.pipeline_tools import pipeline_run

def test_run_returns_only_compact_dispatch_receipt(monkeypatch):
    monkeypatch.setattr("lipidmix.pipeline.service.start_pipeline",
        lambda *a, **kw: {"status": "running", "pipeline_id": "p1",
                         "pipeline_path": "C:/fake/pipeline-run.json"})
    payload = json.loads(pipeline_run("C:/fake/source"))
    assert payload["pipeline_id"] == "p1"
    assert not {"matrix", "scores", "loadings", "volcano"} & payload.keys()
```

- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_pipeline_tools.py tests/test_server_registration.py -q`。
- [ ] **3. 共通サービスとhandlerを組み立てる。**

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=True), structured_output=False)
def pipeline_run(dataset_root: str, request: dict | None = None,
                 request_id: str | None = None) -> str:
    from lipidmix.pipeline.service import start_pipeline
    try:
        return json_payload(start_pipeline(Path(dataset_root), request, request_id))
    except DomainError as exc:
        return console_error(exc.code, exc.message, exc.details)
```

start_pipelineはresolve_request→inspect_inputs→parse_manifestの事前検査→find_or_create_run→launch。不足なら起動せずneeds_inputを保存するが、下流の群不足だけはworkerを起動できる。is_console_exeの--helpは能力検査であり、解析を走らせない。plan_pipelineは検査・保存のみ。

build_handlersはTask 13 staging、Task 4 supervise、Task 3 validation、Task 7 loading、Task 9 metadata、Task 11 preprocess、Task 6 PCA、Task 12のresolve_comparisonsゲートとcomparison、Task 8出力、Task 17 reportをstageへ登録する。resolve_comparisonsは空配列をCOMPARISON_REQUIREDとし、各比較の方向・群・交絡を検証してから比較別stageへ進む。pipeline用workerが自分でsuperviseを呼ぶため、Console用workerをさらに二重起動しない。

起動受理はworkerのidentity保存と起動handshakeを確認した後に返す。応答待ちは短い上限を設け、上限時は起動失敗と決め付けて再起動せず、pipeline_pathとlaunch状態を返す。mainモジュールをsys.executableで起動し、cwdは当該checkoutへ固定する。利用者のcwdや別checkoutを暗黙利用しない。

pipeline_statusはreadOnlyHint=true。他の4件とmetadata更新はfalse。cancelはraw削除ではない旨を契約に記載。MCP_INSTRUCTIONSでraw入口、既存出力入口、混在時の意図確認を分ける。新規ツールをUSAGEとEXPECTED_TOOLSへ追加し、workflowは非再帰globに入る直下pipeline.mdとする。
- [ ] **4. GREEN/登録回帰。** `tests/test_pipeline_tools.py tests/test_server_registration.py tests/test_readme_links.py tests/test_workflow_docs.py tests/test_package_layout.py`を実行。引数スキーマ、status read-only、相対パス、エラー復旧先、応答の上限・省略数を確認する。
- [ ] **5. レビュー後コミット。** Filesを明示stageし、`git commit -m "feat: 生データから解析を進めるMCP入口を公開する"`。

**レビュー区切りD:** D01〜D10・E01/E04のサービス経路を確認。新しいMCPがグローバルsessionを通らないこと、監視workerとConsoleを重複起動しないこと、再送でrunが増えないことを独立レビューする。

### Task 19: fake Consoleによる一気通貫・切断・並行実行の受入検証

**Files:** Create `tests/test_pipeline_end_to_end.py`, `tests/test_pipeline_process_lifecycle.py`; Modify `tests/pipeline_fixtures.py`, `tests/pipeline_worker_harness.py`, `tests/fixtures/fake_console.py`.

**Interfaces:** ConsumesTask 1〜18の公開サービス。harnessはworkerのcommand/backendだけをfakeへ差し替え、engine/loading/metadata/preprocessing/differential/export/renderは本物を使う。試験用分岐を本番要求スキーマへ追加しない。

- [ ] **1. 受入REDを書く。**

```python
import json
from pathlib import Path

def test_resume_reuses_console_and_completes_exports(pipeline_harness):
    run = pipeline_harness.start(target="differential", comparisons=[])
    waiting = pipeline_harness.wait(run, expected="needs_input")
    assert waiting["request"]["effective_target"] == "differential"
    assert pipeline_harness.console_start_count(run) == 1
    pipeline_harness.resume_with_groups(run)
    completed = pipeline_harness.wait(run, expected="completed")
    assert pipeline_harness.console_start_count(run) == 1
    tsv = pipeline_harness.output_path(completed, "tsv:treated_vs_control")
    assert Path(tsv).is_file()
    assert json.loads(Path(run).read_text(encoding="utf-8"))["status"] == "completed"
```

pipeline_harnessを本testファイルのfixtureとして実装する。`start(target, comparisons)`、`wait(path, expected)`、`console_start_count(path)`、`resume_with_groups(path)`、`output_path(record, kind)`を持つ。startはmake_sourceと固定method、save_project=falseを使い、workerはtests/pipeline_worker_harness.pyを実子プロセスとして起動する。waitは観測だけで最大20秒、タイムアウト時はログを出して所有workerのみ停止。resume_with_groupsは8sampleのcontrol4/treated4の完全なシートと向き付き比較を渡す。
- [ ] **2. RED確認。** `C:/Python314/python.exe -m pytest tests/test_pipeline_end_to_end.py tests/test_pipeline_process_lifecycle.py -q`。fake起動回数、実export数値、実stateを使い、成功dictだけのmockにしない。
- [ ] **3. 次のケースを一件ずつ通す。**

```python
import pytest

@pytest.mark.parametrize("scenario, expected", [
    ("success", "completed"),
    ("nonzero", "partial"),
    ("invalid", "partial"),
    ("missing_sample", "partial"),
    ("hang", "partial"),
])
def test_execution_scenarios(pipeline_harness, scenario, expected):
    run = pipeline_harness.start_scenario(scenario)
    assert pipeline_harness.wait(run, expected=expected)["status"] == expected
```

start_scenarioをharnessに追加し、hangは中間ファイルを出してから停止するようfakeを定義する。partialは「ファイルがあるから成功」ではなく、終了検証失敗と保持された成果物の状態をassertする。

追加受入ケース: MCP相当の親プロセス終了後もstatus無呼出しで完走、同一request同時送信、別run2件の並行、worker喪失、取消、method/raw変化、ディレクトリraw、InChIKey0、GUI project未達、PNG source確認。Windows以外でprocess系がskipならWindows実施待ちと記録。
- [ ] **4. GREENと全suite。** 上記＋`C:/Python314/python.exe -m pytest tests -q`。新機能に由来するfailureを修正し、変更した依存箇所に応じて対象回帰を再実行する。fixture共有による実行順依存は許容しない。
- [ ] **5. 独立レビュー後コミット。** `git add -- tests/test_pipeline_end_to_end.py tests/test_pipeline_process_lifecycle.py tests/pipeline_fixtures.py tests/pipeline_worker_harness.py tests/fixtures/fake_console.py` → `git commit -m "test: パイプラインの切断再開と一気通貫処理を検証する"`。

### Task 20: 実環境検証・文書整合・最終レビュー記録

**Files:** Create `docs/superpowers/notes/2026-09-05-raw-folder-pipeline-validation.md`; Modify `USAGE.md`, `DEPLOY.md`, `docs/workflow/index.md`, `docs/workflow/pipeline.md`, `docs/workflow/dataset_analysis.md`, `docs/workflow/plots.md`, `docs/superpowers/specs/2026-09-03-end-to-end-pipeline-design.md`。実装結果が承認specから変わる場合は変更理由をレビューに出し、黙ってspecへ追記して承認済みにしない。

**Interfaces:** 検証記録は`環境/コード版/Console版/入力形式/許可された実行範囲/ケースID/コマンドまたはMCP引数/観測結果/成果物/未実施理由`を持つ。成功を観測していない行をPASSにしない。

- [ ] **1. 実施前の記録表を作る。** 各行の状態は未実施から開始する。以下を文書の最小表とする。

```markdown
| ケース | 環境・入力 | 実行 | 観測結果・成果物 | 判定 |
|---|---|---|---|---|
| fake全受入 | 隔離checkout・合成fixture | pytestの実コマンドを記録 | 実測出力を記録 | 未実施 |
| Windows親切断 | 実Python子/孫 | process lifecycle test | worker/Console identityと終了を記録 | 未実施 |
| 実Console探索 | 許可された隔離入力 | ms-data-parser pipeline_run | job・receipt・PCA・reportを記録 | 未実施 |
| 実Console比較と再開 | 同じ上流と明示manifest | ms-data-parser pipeline_resume | 上流起動回数・図・TSVを記録 | 未実施 |
| 実Console取消/timeout | 許可された試験run | ms-data-parser pipeline_cancel等 | 子/孫停止と成果物保存を記録 | 未実施 |
```

- [ ] **2. コード・登録・文書の最終検証。**

```powershell
C:/Python314/python.exe -m pytest tests/test_server_registration.py tests/test_readme_links.py tests/test_workflow_docs.py tests/test_package_layout.py -q
C:/Python314/python.exe -m pytest tests -q
git diff --check
git status --short
```

- [ ] **3. 許可のある実環境ケースをMCPで実行し、生成物を検証する。** 出力receiptのrcとhash、mzTabサンプル集合、manifest対応、applied/skip、群と方向、TSVセル、PNGを確認する。rawの前後statと、ハードリンク元が変更されていないことを実測する。real dataをshellの独自解析で代替しない。
- [ ] **4. 未実施範囲を分けて最終レビューする。** 実データの許可または入力がない場合は実走検証未実施として残し、実装・fake検証済みと実機完走確認済みを区別する。実走が未実施ならspec全体の完成を宣言しない。新たな仕様変更・回帰以外の理由で同じsuiteを無制限に繰り返さない。
- [ ] **5. 記録・文書をコミットする。** 対象Filesだけをstageして`git commit -m "docs: パイプラインの検証結果と運用手順を記録する"`。マージ・pushは本planの完了条件に含めない。ブランチ統合は別の依頼に従う。

**レビュー区切りE:** spec全受入、実際の図・TSV・レポート、実行証跡、既存ARF互換、未実施事項を独立レビューする。Task 20の記録が空のまま、テスト件数だけで完成としない。

## specとの対応表

| spec範囲 | 主担当タスク |
|---|---|
| §1〜3 範囲・責務分割・接続非依存 | Global Constraints、5、7、15、18 |
| §4 メソッド・入力隔離・固定 | 10、13、14 |
| §5 終了証跡・完了検証・旧ジョブ | 1〜5、7 |
| §6 依存ID・無効化・図選択 | 6、8、9、12、16 |
| §7 実験情報・出所・比較 | 9、10、12、18 |
| §8 auto前処理と適用確認 | 11、12 |
| §9 状態・再開・冪等・取消 | 14〜16、18、19 |
| §10 MCP引数と入口 | 10、12、18 |
| §11 出力と互換 | 7、8、17、20 |
| §12 導入順 | A→B→C→D→Eのレビュー区切り |
| §13 受入検証 | 19、20、および下表 |
| §14 承認された設計判断 | 全タスクの制約として維持 |

| 受入ID | タスク | 主な検証ファイル |
|---|---|---|
| A01 | 3、4、19 | test_console_completion.py、test_console_supervisor.py |
| A02 | 3、19 | test_console_completion.py |
| A03 | 3、19 | test_console_completion.py、test_pipeline_end_to_end.py |
| A04 | 2、4、19、20 | test_process_control.py、test_pipeline_process_lifecycle.py |
| A05 | 5、15、19、20 | test_console_detached.py、test_pipeline_process_lifecycle.py |
| A06 | 2、5、16、19 | test_process_control.py、test_pipeline_recovery.py |
| A07 | 1、4、16 | test_execution_record.py、test_console_supervisor.py |
| A08 | 5、7、8 | test_dataset_loading_service.py、test_result_output.py |
| B01 | 6、8 | test_result_state.py、test_result_output.py |
| B02 | 6、9、11 | test_result_state.py、test_sample_manifest.py |
| B03 | 8、19 | test_result_output.py、test_pipeline_end_to_end.py |
| B04 | 6、9、12、16 | test_sample_manifest.py、test_comparison_contract.py |
| C01 | 9、10 | test_sample_manifest.py、test_pipeline_request.py |
| C02 | 3、9 | test_console_completion.py、test_sample_manifest.py |
| C03 | 9、11 | test_sample_manifest.py、test_preprocess_policy.py |
| C04 | 11 | test_preprocess_policy.py |
| C05 | 11、15 | test_preprocess_policy.py、test_pipeline_engine.py |
| C06 | 11 | test_preprocess_policy.py |
| C07 | 11 | test_preprocess_policy.py |
| D01 | 13〜15、17〜19 | test_pipeline_end_to_end.py |
| D02 | 15、18、19 | test_pipeline_engine.py、test_pipeline_end_to_end.py |
| D03 | 16、19 | test_pipeline_recovery.py、test_pipeline_end_to_end.py |
| D04 | 14〜16、19 | test_pipeline_store.py、test_pipeline_process_lifecycle.py |
| D05 | 13、14、16、19 | test_pipeline_inputs.py、test_pipeline_recovery.py |
| D06 | 15、19 | test_pipeline_process_lifecycle.py |
| D07 | 13 | test_pipeline_inputs.py |
| D08 | 13、19、20 | test_pipeline_inputs.py、test_pipeline_end_to_end.py |
| D09 | 16、19 | test_pipeline_recovery.py、test_pipeline_process_lifecycle.py |
| D10 | 5、14、19 | test_console_cleanup.py、test_pipeline_process_lifecycle.py |
| E01 | 8、17、19 | test_pipeline_report.py、test_pipeline_end_to_end.py |
| E02 | 8、17、19、20 | test_result_output.py、test_pipeline_report.py |
| E03 | 8、17、19、20 | test_result_output.py、test_pipeline_report.py |
| E04 | 15、16、19 | test_pipeline_recovery.py、test_pipeline_end_to_end.py |

## 完了の報告単位

報告では「実装したタスク」「独立レビュー済みの区切り」「最新のテスト結果」「実機で確認した範囲」「未完了タスク」を分ける。最終タスクに達していない、または実機確認が未実施なら、残りを明示する。

本planは実装用の手順書であり、作成時点ではいずれのタスクも未実行。実行方法はsubagent-driven-developmentによるタスク単位の実装・レビュー、またはexecuting-plansによる同一担当の順次実装・区切りレビューから選べる。
