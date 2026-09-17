# v2 公開入口と上流接続 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `pipeline_run` に v2 要求（`pipeline-request.v2`）を渡すと、profile だけを情報源に入力を固定し、Console を起こして全工程が完走する状態にする。

**Architecture:** 受付は要求の schema で入力配置計画の作り方を選ぶ。v2 は `inputs.plan_from_profile` が `profiles.resolve_profile_inputs`（解決と hash 照合は既に実装済み）の結果を v1 と同じ plan dict の形へ移すだけにし、`stage_inputs` 以降は無変更で共有する。Console 起動は schema で 3 つの値（omics / measure / profile_snapshot）だけを分け、snapshot を載せることで job が自動的に `analysis-job.v3` になる。

**Tech Stack:** Python 3.14（`C:/Python314/python.exe`）、pytest、MessagePack/JSON の既存パーサ層、MS-DIAL Console（本計画では fake Console のみ）。

**Spec:** [docs/superpowers/specs/2026-09-17-v2-public-upstream-design.md](../specs/2026-09-17-v2-public-upstream-design.md)

## Global Constraints

- テストは常にリポジトリルートから `C:/Python314/python.exe -m pytest tests -q` で実行する。
- 全 MCP ツールに `structured_output=False` を付ける。JSON は `lipidmix.core.serialization.json_payload()` で返す。
- 可変状態の正準は `lipidmix.core.mcp_core` と `lipidmix.core.session_state`。`server.<name>` を差し替えない。
- `lipidmix/pipeline/{engine,worker,service,recovery,store}.py` はグローバル session を import しない（`tests/test_pipeline_engine.py` の AST テストが固定）。
- **`lipidmix/console/profiles.py` は module 先頭で `lipidmix.pipeline.inputs` を import している。** `inputs.py` から `profiles` を使うときは**関数内 import** にする（module 先頭に書くと循環 import）。
- 実装を偽の定数返却でテストに合わせない。
- `execution_purpose="routine"` の停止（`PROFILE_VALIDATION_INVALID`）は本計画で変更しない。
- 合成入力での完走を「実 Console 合格」「ソフトウェア完成」と表記しない。
- 各 Task は RED 確認 → 実装 → 対象 GREEN → 全体 GREEN → 該当ファイルだけ commit の順。コミット時に pre-commit フックが全テストを実行する（約 160 秒）。

---

### Task 1: profile 由来の入力配置計画

**Files:**
- Modify: `lipidmix/pipeline/inputs.py`（`inspect_inputs` の直後に追加）
- Test: `tests/test_pipeline_inputs_v2.py`（新規）

**Interfaces:**
- Consumes: `lipidmix.console.profiles.resolve_profile_inputs(profile, source_root, *, raw_root=None) -> dict`（既存。`method` / `dependencies` / `execution_environment` / `polarity` / `measure` / `raw_files` を返す）
- Produces: `lipidmix.pipeline.inputs.plan_from_profile(source_root: Path, request: dict, profile: dict) -> dict`。戻り値は `inspect_inputs` と同じキー（`source_root` / `selected_format` / `entries` / `raw_stat` / `companions` / `method` / `lbm` / `exe` / `polarity` / `unverified`）。Task 2 が受付から呼ぶ。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_pipeline_inputs_v2.py` を新規作成する。fixture はテスト自身が作る（`tests/metabolomics_fixtures.write_profile` が draft profile 一式を書く）。

```python
"""v2 の入力配置計画は profile だけを情報源にする。"""
from pathlib import Path

import pytest

from lipidmix.console.profile_schema import validate_profile
from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline import inputs as inputs_mod
from tests.metabolomics_fixtures import write_profile
import json


def _setup(tmp_path) -> tuple[Path, dict, dict]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    for name in ("A", "B"):
        (source_root / f"{name}.wiff").write_bytes(b"synthetic raw")
    profile_path = write_profile(source_root / "profile.json")
    profile = validate_profile(json.loads(profile_path.read_text(encoding="utf-8")))
    request = {"schema": "pipeline-request.v2", "profile_file": str(profile_path)}
    return source_root, request, profile


def test_plan_has_the_same_shape_as_the_v1_plan(tmp_path):
    source_root, request, profile = _setup(tmp_path)
    plan = inputs_mod.plan_from_profile(source_root, request, profile)
    assert set(plan) == {"source_root", "selected_format", "entries", "raw_stat",
                         "companions", "method", "lbm", "exe", "polarity", "unverified"}
    assert plan["selected_format"] == "wiff"
    assert len(plan["raw_stat"]) == 2


def test_method_and_executable_come_from_the_profile(tmp_path):
    source_root, request, profile = _setup(tmp_path)
    plan = inputs_mod.plan_from_profile(source_root, request, profile)
    assert plan["method"]["sha256"] == profile["processing"]["method_sha256"]
    assert plan["exe"]["sha256"] == profile["software"]["executable_sha256"]
    assert plan["polarity"]["source"] == "profile"
    assert plan["polarity"]["value"] == profile["acquisition"]["polarity"]


def test_every_declared_dependency_becomes_a_method_override(tmp_path):
    source_root, request, profile = _setup(tmp_path)
    plan = inputs_mod.plan_from_profile(source_root, request, profile)
    for dependency in profile["processing"]["dependencies"]:
        assert dependency["method_key"] in plan["method"]["overrides"]


def test_a_changed_dependency_stops_before_any_plan_is_returned(tmp_path):
    source_root, request, profile = _setup(tmp_path)
    target = Path(profile["processing"]["dependencies"][0]["path"])
    if not target.is_absolute():
        target = (source_root / target)
    target.write_text("tampered", encoding="utf-8")
    with pytest.raises(DomainError) as excinfo:
        inputs_mod.plan_from_profile(source_root, request, profile)
    assert excinfo.value.code == "INPUT_CHANGED"
```

- [ ] **Step 2: RED を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_pipeline_inputs_v2.py -q`
Expected: 4 件とも FAIL。理由は `AttributeError: module 'lipidmix.pipeline.inputs' has no attribute 'plan_from_profile'`。typo ではなく「関数が無い」ことによる失敗であることを確認する。

- [ ] **Step 3: 最小の実装を書く**

`lipidmix/pipeline/inputs.py` の `inspect_inputs` の直後に追加する。**`profiles` は関数内 import**（module 先頭に書くと `profiles` → `pipeline.inputs` の循環になる）。

```python
def plan_from_profile(source_root: Path, request: dict, profile: dict) -> dict:
    """profileだけを情報源に入力配置計画を作る（v2）。

    `inspect_inputs`と同じ形のplanを返すので、`stage_inputs`・`_plan_fingerprint`・
    `_handle_prepare_inputs_v2`は共有できる。違うのは**決め方**だけ——methodを
    フォルダから推定せず、LBMを必須にせず、環境設定の実行体へフォールバックしない。

    解決とhash照合はやり直さない。`resolve_profile_inputs`が既にmethod・依存・
    実行体・rawの実在とhash一致を検証しているので、ここはその結果を形へ移すだけ。
    """
    # profiles は module 先頭で pipeline.inputs を import しているため関数内で読む。
    from lipidmix.console import profiles as profiles_mod

    source_root = Path(source_root).expanduser()
    if not source_root.is_dir():
        raise DomainError("DATASET_ROOT_NOT_FOUND", f"source_rootが存在しません: {source_root}",
                          {"source_root": str(source_root)})

    profile_path = Path(request["profile_file"])
    resolved = profiles_mod.resolve_profile_inputs(
        profile, profile_path.parent, raw_root=source_root)

    # raw形式はprofileが宣言しない（データ由来であってmethod由来ではない）。
    ext, _formats = _resolve_raw_format(source_root, request.get("keep_extension"))
    primaries, companions_map = _collect_primaries_and_companions(source_root, ext)
    entries, raw_stat = _build_entries_and_stat(source_root, primaries, companions_map)
    companions = {p.name: [c.name for c in cs]
                  for p, cs in companions_map.items() if cs}

    environment = resolved["execution_environment"]
    exe_path = Path(environment["executable_path"])
    if not console_runner.is_console_exe(str(exe_path)):
        raise DomainError(
            "MSDIAL_EXE_NOT_CONSOLE",
            f"profileが宣言した実行体がMS-DIAL Consoleではありません: {exe_path}",
            {"exe_path": str(exe_path)})

    # v1がLBM1件に使っていた任意キーdictを、全依存へそのまま一般化する。
    overrides = {dep["method_key"]: dep["source_path"]
                 for dep in resolved["dependencies"] if dep.get("present")}
    lbm = next((dep for dep in resolved["dependencies"]
                if dep["method_key"] == method_file_mod.LBM_KEY), None)

    return {
        "source_root": str(source_root.resolve()),
        "selected_format": ext,
        "entries": entries,
        "raw_stat": raw_stat,
        "companions": companions,
        "method": {
            "source_path": resolved["method"]["source_path"],
            "sha256": resolved["method"]["sha256"],
            "effective_relative_path": None,
            "effective_sha256": None,
            "overrides": overrides,
        },
        "lbm": ({"path": lbm["source_path"], "sha256": lbm["sha256"]} if lbm
                else {"path": None, "sha256": None}),
        "exe": {"path": str(exe_path), "sha256": environment["executable_sha256"],
                "version": environment["msdial_version"]},
        "polarity": {"value": resolved["polarity"], "source": "profile"},
        # 極性をrawから検証していない点はv1と同じ。黙って確定扱いにしない。
        "unverified": ["polarity_from_profile_not_verified_from_raw"],
    }
```

- [ ] **Step 4: 対象 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_pipeline_inputs_v2.py -q`
Expected: 4 passed。

- [ ] **Step 5: 全体 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: 既存の失敗が増えていないこと。`tests/test_package_layout.py` と `tests/test_pipeline_engine.py` が緑であること。

- [ ] **Step 6: commit**

```bash
git add lipidmix/pipeline/inputs.py tests/test_pipeline_inputs_v2.py
git commit -m "feat: profile由来の入力配置計画を追加"
```

---

### Task 2: 受付を schema で分岐し、停止を解除する

**Files:**
- Modify: `lipidmix/pipeline/service.py:277-290`（`_prepare_run`）
- Test: `tests/test_pipeline_inputs_v2.py`（追記）

**Interfaces:**
- Consumes: Task 1 の `inputs.plan_from_profile(source_root, request, profile)`
- Produces: `service.plan_pipeline(dataset_root, request)` が v2 要求で `pipeline-run.json` を作る。Task 5 の E2E がこれを入口にする。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_pipeline_inputs_v2.py` に追記する。ここで縛るのは「v2 で `inspect_inputs` を呼ばない」ことなので、呼ばれたら失敗する監視を置く。

```python
def test_v2_request_does_not_go_through_the_v1_inspection(tmp_path, monkeypatch):
    from lipidmix.pipeline import service

    source_root, request, _profile = _setup(tmp_path)

    def _fail(*args, **kwargs):
        raise AssertionError("v2 要求が v1 の inspect_inputs へ落ちた")

    monkeypatch.setattr(inputs_mod, "inspect_inputs", _fail)
    result = service.plan_pipeline(source_root, request)
    assert result["status"] in {"planned", "needs_input"}, result


def test_v2_request_does_not_touch_the_environment_executable(tmp_path, monkeypatch):
    from lipidmix.pipeline import service

    source_root, request, _profile = _setup(tmp_path)

    def _fail():
        raise AssertionError("v2 要求が環境設定の実行体を参照した")

    monkeypatch.setattr(service, "_resolve_exe_path", _fail)
    service.plan_pipeline(source_root, request)


def test_a_v2_request_without_a_profile_stops_instead_of_falling_back(tmp_path):
    from lipidmix.core.atomic_io import DomainError as _DomainError
    from lipidmix.pipeline import service

    source_root, request, _profile = _setup(tmp_path)
    Path(request["profile_file"]).unlink()
    with pytest.raises(_DomainError) as excinfo:
        service.plan_pipeline(source_root, request)
    assert excinfo.value.code != "PIPELINE_V2_UPSTREAM_UNAVAILABLE"
    assert excinfo.value.code in {"PIPELINE_REQUEST_INVALID", "PROFILE_NOT_FOUND"}
```

- [ ] **Step 2: RED を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_pipeline_inputs_v2.py -q -k "v1_inspection or environment_executable or without_a_profile"`
Expected: 前 2 件は `PIPELINE_V2_UPSTREAM_UNAVAILABLE` で FAIL、3 件目は `assert excinfo.value.code != "PIPELINE_V2_UPSTREAM_UNAVAILABLE"` で FAIL。

- [ ] **Step 3: 最小の実装を書く**

`lipidmix/pipeline/service.py` の `_prepare_run` を書き換える。`raise DomainError("PIPELINE_V2_UPSTREAM_UNAVAILABLE", ...)` のブロック（`stage_plan.is_v2_request` の if 全体）を次で置き換える。

```python
    if stage_plan.is_v2_request(request_resolved):
        # v1受付へ落とすとprofile以外のmethod/LBM/実行体を採用してしまう。
        # v2はprofileが唯一の情報源なので、経路そのものを分ける。
        profile = _profile_arguments(source_root, request).get("profile")
        if not profile:
            raise DomainError(
                "PIPELINE_REQUEST_INVALID",
                "v2要求にはprofileが必要です（profile_fileが解決できませんでした）。",
                {"schema": request_resolved["schema"]})
        plan = inputs_mod.plan_from_profile(source_root, request_resolved, profile)
    else:
        exe_path = _resolve_exe_path()
        plan = inputs_mod.inspect_inputs(source_root, request_resolved, exe_path=exe_path)
    plan["fingerprint"] = _plan_fingerprint(plan)
```

`_profile_arguments` は同じ引数で 2 回呼ばれることになるが、profile の読込は純粋な読取りなので副作用は無い。1 回にまとめる整理は Task 3 で `_prepare_run` が snapshot を触るときに行わない——**この Task の範囲を広げない**。

- [ ] **Step 4: 対象 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_pipeline_inputs_v2.py -q`
Expected: 7 passed。

- [ ] **Step 5: 全体 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: 既存の `PIPELINE_V2_UPSTREAM_UNAVAILABLE` を期待するテストがあれば、この Task で意味が変わったので更新する（`tests/test_metabolomics_profile_boundary.py`）。停止の代わりに「v1 へ落ちない」ことを縛る形へ書き換え、削除だけで済ませない。

- [ ] **Step 6: commit**

```bash
git add lipidmix/pipeline/service.py tests/test_pipeline_inputs_v2.py tests/test_metabolomics_profile_boundary.py
git commit -m "feat: v2受付をprofile由来の入力計画へ接続"
```

---

### Task 3: Console 起動の schema 分岐と job v3

**Files:**
- Modify: `lipidmix/pipeline/service.py:580-640`（`_handle_upstream`）
- Test: `tests/test_metabolomics_stages.py`（追記）

**Interfaces:**
- Consumes: 保存済み成果物 `profile`（`store.read_result_data(pipeline_root, results, "profile")` が `{"profile": ..., "snapshot": ...}` を返す）
- Produces: v2 の run が書く `analysis-job.json` は `schema == "analysis-job.v3"` で `profile_snapshot` を持つ。Task 5 の E2E がこれを確認する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_metabolomics_stages.py` に追記する。`_handle_upstream` を直接呼ぶと Console を起こすので、`console_execution.supervise` を差し替えて job の中身だけを見る。

```python
def test_v2_upstream_writes_a_v3_job_with_the_profile_snapshot(tmp_path, monkeypatch):
    import json as _json

    from lipidmix.console import execution as console_execution
    from lipidmix.handoff.schema import AnalysisJob
    from lipidmix.pipeline import service

    context = _v2_upstream_context(tmp_path)  # 下の Step 3 で追加する helper

    monkeypatch.setattr(console_execution, "supervise",
                        lambda job_path, cancel_path=None: {
                            "execution_id": "exec-test", "termination": "exited",
                            "exit_code": 0})
    monkeypatch.setattr(service.job_manager, "load_job",
                        lambda path: AnalysisJob.load(path))

    service._handle_upstream(context)

    job_path = next(Path(context["pipeline_root"]).rglob("analysis-job.json"))
    data = _json.loads(job_path.read_text(encoding="utf-8"))
    assert data["schema"] == "analysis-job.v3"
    assert data["profile_snapshot"] is not None
    assert data["project"]["omics"] == "metabolomics"
```

- [ ] **Step 2: RED を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_stages.py -q -k "v3_job"`
Expected: FAIL。`data["schema"] == "analysis-job.v2"`（snapshot を載せていないため）かつ `project.omics == "lipidomics"`。

- [ ] **Step 3: 最小の実装を書く**

`_handle_upstream` の `AnalysisJob(...)` 構築の直前に、schema で分ける値を取る。

```python
    is_v2 = stage_plan.is_v2_request(request)
    profile_snapshot = None
    measure = request.get("measure")
    omics = "lipidomics"
    if is_v2:
        # snapshotはruntimeではなく保存済み成果物から読む——再開でprepare_inputsが
        # skipされるとruntimeは空で、そのとき書くjobだけがsnapshotを失う。
        saved = store.read_result_data(
            Path(pipeline_root), context.get("results") or [], "profile")
        if not saved:
            return _needs_input_outcome(
                "PROFILE_SNAPSHOT_MISSING",
                "profile成果物がまだ固定されていません（prepare_inputsを先に通す）。",
                {"pipeline_root": str(pipeline_root)})
        profile_snapshot = saved[-1]["snapshot"]
        omics = request["omics"]
        measure = saved[-1]["profile"]["processing"]["measure"]
```

`AnalysisJob(...)` の該当引数を差し替える。

```python
            omics=omics,
            polarity=inputs_snapshot["polarity"]["value"], measure=measure,
            run_dir=str(run_dir), save_project=request["save_project"],
            timeout_s=request["timeout_s"],
            profile_snapshot=profile_snapshot,
```

`_needs_input_outcome` が `service.py` に無ければ、同モジュールの既存の needs_input 生成（`_as_needs_input` が包む `PreconditionError` ではなく、outcome を直接返す形）に合わせる。同等の関数が無い場合は次を `_handle_upstream` の直前に足す。

```python
def _needs_input_outcome(code: str, message: str, details: dict) -> dict:
    return {"status": "needs_input", "result_refs": [], "warnings": [],
            "error": {"code": code, "message": message, "details": details},
            "record_updates": {}}
```

テスト用 helper `_v2_upstream_context(tmp_path)` を `tests/test_metabolomics_stages.py` に足す。`MetabolomicsHarness` で run を 1 本作り、`prepare_inputs` まで通した context（`pipeline_root` / `request` / `inputs` / `results` / `pipeline_id` / `attempt`）を組み立てる。既存の同ファイル内 helper（run record を読む処理）を再利用し、新しい fixture 機構を作らない。

- [ ] **Step 4: 対象 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_stages.py -q`
Expected: 追加分を含めて全件 passed。

- [ ] **Step 5: 全体 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: v1 の upstream テスト（`tests/test_pipeline_*`）が緑のままであること。v1 の job が `analysis-job.v2` のままであることを確認する。

- [ ] **Step 6: commit**

```bash
git add lipidmix/pipeline/service.py tests/test_metabolomics_stages.py
git commit -m "feat: v2のConsole起動をjob v3へ接続"
```

---

### Task 4: 再開判定の stage ID 直書きを直す（既存バグ）

**Files:**
- Modify: `lipidmix/pipeline/recovery.py:256`
- Test: `tests/test_pipeline_recovery.py`（既存ファイルへ追記）

**Interfaces:**
- Consumes: 既存の `recovery._upstream_stage_id(record) -> str`
- Produces: 無し（内部修正）。Task 5 の再開シナリオが前提にする。

- [ ] **Step 1: 失敗するテストを書く**

v2 の record で `execute_console` が succeeded なのに終了証跡が読めない状況を作る。現状は `verified=False` になるため `EXECUTION_UNRESOLVED` が飛ぶ。

```python
def test_v2_verified_upstream_is_recognised_by_the_v2_stage_id(tmp_path):
    from lipidmix.pipeline import recovery

    job_path = tmp_path / "console" / "attempt-0001" / "analysis-job.json"
    job_path.parent.mkdir(parents=True)
    job_path.write_text("{}", encoding="utf-8")
    record = {
        "schema": "pipeline-run.v2",
        "stages": {"execute_console": {"status": "succeeded"}},
        "upstream": {"console_job_path": str(job_path)},
    }
    # 終了証跡が無いので、verified を読み違えると EXECUTION_UNRESOLVED になる。
    identity, verified = recovery._console_supervision_state(record)
    assert verified is True
    assert identity is None
```

- [ ] **Step 2: RED を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_pipeline_recovery.py -q -k "v2_stage_id"`
Expected: FAIL。`DomainError: EXECUTION_UNRESOLVED`（`verified` が False のまま終了証跡を読もうとする）。

- [ ] **Step 3: 最小の実装を書く**

`lipidmix/pipeline/recovery.py` の `_console_supervision_state` 冒頭 1 行を直す。

```python
    # v1は"upstream"、v2は"execute_console"。同モジュールのprepare_resumeは既に
    # _upstream_stage_idを使っており、ここだけが取り残されていた。
    verified = record.get("stages", {}).get(
        _upstream_stage_id(record), {}).get("status") == "succeeded"
```

- [ ] **Step 4: 対象 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_pipeline_recovery.py -q`
Expected: 追加分を含めて全件 passed。

- [ ] **Step 5: 全体 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: v1 の再開テストが緑のまま（`_upstream_stage_id` は v1 record に対して `"upstream"` を返すので挙動は不変）。

- [ ] **Step 6: commit**

```bash
git add lipidmix/pipeline/recovery.py tests/test_pipeline_recovery.py
git commit -m "fix: v2 recordで上流stageの検証状態を読み違える不具合を修正"
```

---

### Task 5: 公開入口からの E2E と文書更新

**Files:**
- Create: `tests/test_metabolomics_public_entry.py`
- Modify: `docs/workflow/metabolomics.md`、`docs/superpowers/plans/2026-09-16-validated-lcms-metabolomics-audit.md`
- Test: 上記の新規ファイル

**Interfaces:**
- Consumes: Task 2 の `service.plan_pipeline` / `service.start_pipeline`、Task 3 の job v3、Task 4 の再開判定、既存の `tests/pipeline_fixtures.fake_console_command` / `use_fake_console`
- Produces: 無し（検証と文書）。

- [ ] **Step 1: 失敗するテストを書く**

既存の `MetabolomicsHarness`（`run_engine` 直叩き）は置き換えない。受付から起こす経路を別に作る。

```python
"""公開入口からの v2 実行（受付 → fake Console → 全工程）。

MetabolomicsHarness は run_engine を直接回す（工程単体の試験）。ここは
pipeline_run と同じ受付を通す——「工程は通るが入口から起動できない」状態を
検出できるのはこちらだけ。
"""
import json
from pathlib import Path

from lipidmix.pipeline import service, store
from tests.metabolomics_fixtures import write_manifest_v2, write_mztab_v2, write_profile
from tests.pipeline_fixtures import fake_console_command, use_fake_console


def _dataset(tmp_path) -> tuple[Path, dict]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    sources = []
    for name in ("S1", "S2", "S3", "S4", "S5", "S6"):
        path = source_root / f"{name}.wiff"
        path.write_bytes(b"synthetic raw")
        sources.append(path)
    mztab = write_mztab_v2(source_root / "Height_synthetic.mzTab", sources)
    manifest = write_manifest_v2(source_root / "sample-manifest.tsv", sources)
    profile = write_profile(source_root / "profile.json")
    request = {"schema": "pipeline-request.v2", "profile_file": str(profile),
               "execution_purpose": "validation", "sample_manifest": str(manifest)}
    return source_root, request, mztab


def test_the_public_entry_starts_a_v2_run(tmp_path, monkeypatch):
    source_root, request, mztab = _dataset(tmp_path)
    use_fake_console(monkeypatch, fake_console_command({mztab.name: mztab.read_text()}))

    result = service.start_pipeline(source_root, request)
    record = store.load_run(Path(result["pipeline_path"]))
    assert record["schema"] == "pipeline-run.v2"
    assert "execute_console" in record["stages"]


def test_the_started_job_is_v3_and_carries_the_profile_snapshot(tmp_path, monkeypatch):
    source_root, request, mztab = _dataset(tmp_path)
    use_fake_console(monkeypatch, fake_console_command({mztab.name: mztab.read_text()}))

    result = service.start_pipeline(source_root, request)
    job_path = next(Path(result["pipeline_path"]).rglob("analysis-job.json"))
    data = json.loads(job_path.read_text(encoding="utf-8"))
    assert data["schema"] == "analysis-job.v3"
    assert data["profile_snapshot"]["profile_id"] == json.loads(
        Path(request["profile_file"]).read_text(encoding="utf-8"))["profile_id"]
```

`start_pipeline` が worker プロセスを起こす形なら、同期で待つ既存の作法（`tests/test_pipeline_process_lifecycle.py` が使っている待機 helper）に合わせる。新しい待機機構を作らない。

- [ ] **Step 2: RED を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_public_entry.py -q`
Expected: Task 1〜4 が入っていれば通る可能性がある。**通ってしまった場合は RED になっていないので、先に「何が欠けていれば落ちるか」を確かめる**——`profile_snapshot` の assert を外して実行し、落ちないことを確認してから戻す。落ちない試験は追加しても意味がない。

- [ ] **Step 3: 実装（必要な差分だけ）**

Task 1〜4 で足りない部分だけを直す。想定される残り:

- `store.find_or_create_run` が v2 plan の `fingerprint` を受け取れること（v1 と同じキーなので変更不要の見込み）。
- `_precheck_manifest` が v2 要求の `sample_manifest` を見られること。見られない場合は v1 と同じ経路へ寄せる。

差分が要らなければ実装ステップは飛ばし、Step 4 へ進む。

- [ ] **Step 4: 対象 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_public_entry.py -q`
Expected: 全件 passed。

- [ ] **Step 5: 文書を更新する**

`docs/workflow/metabolomics.md` 冒頭の「公開 `pipeline_plan` / `pipeline_run` のv2上流は未接続」「`PIPELINE_V2_UPSTREAM_UNAVAILABLE`で停止する」という記述を、実装後の状態へ書き換える。routine が `PROFILE_VALIDATION_INVALID` で止まることは**そのまま残す**（R2 は未接続のまま）。

`docs/superpowers/plans/2026-09-16-validated-lcms-metabolomics-audit.md` の R1 の項に、完了した範囲（合成入力・fake Console まで）と残る範囲（実 Console = R5）を追記する。**既存の節は書き換えず、日付見出しで追記する。**

- [ ] **Step 6: 全体 GREEN を確認する**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: `tests/test_workflow_docs.py` と `tests/test_readme_links.py` を含めて全件 passed。

- [ ] **Step 7: commit**

```bash
git add tests/test_metabolomics_public_entry.py docs/workflow/metabolomics.md docs/superpowers/plans/2026-09-16-validated-lcms-metabolomics-audit.md
git commit -m "test: 公開入口からのv2実行を検証"
```

---

## 受け入れ条件と Task の対応

| spec の ID | Task |
|---|---|
| S3-01 公開入口から完走 | 2, 5 |
| S3-02 profile 無しは v1 へ落ちない | 2 |
| S3-03 環境設定の実行体を参照しない | 1, 2 |
| S3-04 job が v3 で snapshot を持つ | 3, 5 |
| S3-05 再開で snapshot を失わない | 3 |
| S3-06 binding 訂正で Console を起こし直さない | 5（既存 harness が同じ性質を別経路で固定済み） |
| S3-07 v2 record の verified 判定 | 4 |
| S3-08 hash 改変は起動前に止まる | 1 |
| S3-09 結果を対話セッションへ載せられる | S2 で実装済み。5 で E2E として通す |
| S3-10 v1 の挙動が変わらない | 1〜4 の各 Step 5（全体 GREEN） |

## 最終確認

```powershell
C:/Python314/python.exe -m pytest tests -q
git diff --check
git status --short
```

完了報告には、実装差分・テスト結果・S3-01〜S3-10 の証拠位置・**実 Console が未接続であること**を明記する。合成入力での完走を実 Console 合格と書かない。
