# MS-DIAL Console 統合の実データ適合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MS-DIAL Console の実走で判明した不整合を直し、生データ → Console → mzTab-M → 解析 → エクスポートを実データで正しく通せるようにする。

**Architecture:** 変更は 3 層に閉じる。(1) Console 実行層（`lipidmix/console/` + `lipidmix/tools/console_tools.py`）＝ 実行前ガードと生成物収集を実物に合わせる。(2) 受け渡しスキーマ（`lipidmix/handoff/schema.py`）＝ 生成物が 2 つのルート（`-o` と生データフォルダ）に分かれる事実を表現するため `analysis-job.v2` へ上げる（v1 は読めるまま）。(3) mzTab-M 解析層（`lipidmix/mztab/` + `lipidmix/analysis/dataset_analysis.py`）＝ mzTab が実際に持っている注入順・バッチを読む。数値経路（`preprocessing` / `differential` / `pca` / `export_contract`）には手を入れない。

**Tech Stack:** Python 3.14（`C:/Python314/python.exe`）、pytest、MCP FastMCP、numpy。外部依存の追加なし。

**Spec:** spec は作成しない。要件はすべて実機実測で確定しており、設計上の未決事項が無いため。根拠は `docs/HISTRY.md` の 2026-09-03(4)「上流実走検証」/ 2026-09-03(5)「Console をソースからビルド」/ 2026-09-03(6)「Console 実走完了」、および `docs/task.md` の「Console 適合の是正（実走で確定）」「mzTab-M の注入順・バッチを読む」。**実装前にこの 3 つの HISTRY エントリを読むこと。**

## Global Constraints

- Python は `C:/Python314/python.exe` を使う。`.venv/` は無い。`.venv-1/` は使わない。
- テストはリポジトリルートから `C:/Python314/python.exe -m pytest tests -q` で実行する。現状 **762 件 green**。各タスクの終了時に全体が green であること。
- JSON の戻り値は必ず `lipidmix.core.serialization.json_payload()` で作る（`json.dumps(..., indent=2)` を書かない）。
- 全 MCP ツールに `structured_output=False` を付ける（既存ツールの変更時も外さない）。
- `lipidmix/core/mcp_core.py` と `lipidmix/handoff/schema.py` は依存グラフの leaf。`lipidmix.tools.*` を import してはならない。
- `lipidmix/analysis/export_contract.py` の `EXPORT_COLUMNS` / `CONTRACT_VERSION` は別リポ（massbank-context）との契約。**この計画では一切触らない。**
- 前提状態が無いときは例外でなく `missing_state` エンベロープを返す。引数エラーは `console_error` / `mztab_error`（`missing_state` にしない。クライアントが無限リプレイする）。
- fixture はテスト自身が `tmp_path` に作る。`data/` `analyses/` の実ファイルに依存させない。
- 実機検証に使える Console 実行体: `C:\Users\yuu18\source\repos\MsdialWorkbench\tests\MSDIAL5\MsdialCoreTestApp\bin\Debug\net48\MSDIALCUI.exe`（vendor 対応）。**この計画のタスクはすべて MS-DIAL を起動せずにテストできる**（実行体は fake / monkeypatch で差し替える）。
- 実測で確定している事実（テストのフィクスチャはこれに合わせる）:
  - Console は `-o` に `.mdpeak` / `.mdmsp` / `.mdalign` / `.mzTab` / `.qa.tsv` / `.mdproject` を出す。
  - Console は生データフォルダ（`-i`）に `.pai2` / `.dcl` / `_tags.xml` / `.arf`（`_PeakProperties` / `_DriftSopts`）/ `.arf2` / `.EIC.aef` / `.mddata` / `.msp2` / `.msp2.dbs` を出す。
  - Console 出力の mzTab 名は `AlignResult-<yyyyMdHm>.mzTab`（`Height_` 接頭辞なし・極性トークンなし）。
  - mzTab の MTD に `assay[N]-custom[1] = [MS,MS:4000088,batch label,1]` と `assay[N]-custom[2] = [MS,MS:4000089,injection sequence label,N]` がある（Console 出力・GUI 出力の両方で確認済み）。

## File Structure

| ファイル | 責務 | この計画での変更 |
|---|---|---|
| `lipidmix/console/runner.py` | Console 起動ラッパ | flush / stdin 遮断 / `-p` / 実行体判定 |
| `lipidmix/console/job_manager.py` | ジョブ CRUD・入力集計 | `raw_input_summary()` 追加 |
| `lipidmix/console/output_collector.py` | 生成物の収集と role 付け | 複数ルート対応・`_ROLE_MAP` 拡充 |
| `lipidmix/handoff/schema.py` | `analysis-job.json` | v2（`Artifact.root` / `save_project` / `timeout_s`） |
| `lipidmix/tools/console_tools.py` | Console の MCP 公開層 | 実行前ガード 3 種・二重ルート収集・引数追加 |
| `lipidmix/core/mcp_errors.py` | エラーコード集合 | 新コード 3 件 |
| `lipidmix/tools/mztab_tools.py` | mzTab 入口 | artifact の root 対応パス解決 |
| `lipidmix/mztab/identity.py` | InChIKey 導出 | `rdkit_available()` 追加 |
| `lipidmix/mztab/dataset_state.py` | DatasetState 構築 | assay custom CV term の解釈・RDKit warning |
| `lipidmix/analysis/dataset_analysis.py` | 解析アダプタ | run_order / batch を mzTab から取る |

## Out of Scope

- **`.qa.tsv` の読み取り**。role を与えて収集はするが、中身を解釈する実装はこの計画に含めない。列が `ID / File / <class> / Height / RT / MZ / SN / MSMS / Reference matched` であることはソースで確認済みだが、**どの値がギャップ補完を意味するかは未検証**。ラボの MS-DIAL を再ビルドして実 `.qa.tsv` を採取し、意味を確定してから別計画で扱う（`docs/task.md` に起票済み）。
- `docs/workflow/console.md` の新設（Console 4 ツールは現在 `docs/workflow/` の対象外 16 ツールに含まれており、追加すると `tests/test_workflow_docs.py` のツール数照合を同時に直す必要がある）。
- `docs/task.md`「Console 層の設計指摘8件」（エラーコードの用途重複など）、「significance 判定ロジックの重複」、「DatasetState 経路の図保存未対応」。

---

### Task 1: `run_msdial` を MCP 実行に耐える形にする

Console は入力に複数フォーマットが混在すると `Console.ReadLine()` で対話プロンプトを出す（実データ NEG フォルダは `.wiff` と `.wiff2` があり実際に発火した）。現在 `subprocess.run` は `stdin` を指定していないので **MCP stdio サーバの JSON-RPC 入力を子プロセスが継承する**。また `CMD:` 行を flush せずに子へ fd を渡しているため、ログ内で行順が入れ替わる（実測）。あわせて `-p`（GUI 用 `.mdproject` 生成）を渡せるようにする。

**Files:**
- Modify: `lipidmix/console/runner.py`
- Test: `tests/test_console_runner.py`

**Interfaces:**
- Consumes: なし
- Produces: `run_msdial(method_file, dataset_root, run_dir, timeout_s=3600, exe_path=None, save_project=False) -> int`

- [x] **Step 1: Write the failing tests**

`tests/test_console_runner.py` の runner セクション（`test_run_msdial_success` の直後）に追記する。

```python
def test_run_msdial_closes_stdin(tmp_path):
    """MCP stdio サーバの JSON-RPC 入力を子プロセスに継承させない。

    MS-DIAL Console は入力フォルダに複数フォーマットが混在すると
    Console.ReadLine() で対話する。stdin を継承したままだと子が
    プロトコルのバイト列を食うか、応答が来ずタイムアウトまでブロックする。
    """
    method = tmp_path / "params.txt"
    method.touch()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_msdial(method_file=method, dataset_root=tmp_path,
                   run_dir=tmp_path / "run1", exe_path="fake.exe")
    assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_run_msdial_flushes_cmd_header_before_child_writes(tmp_path):
    """CMD 行を flush してから子に fd を渡す。

    親のテキストバッファと子は同じファイル記述のオフセットを共有するので、
    flush しないと子の出力が先頭に、CMD 行がその後ろに書かれる（実測）。
    失敗解析でログ先頭を見る運用が壊れる。
    """
    method = tmp_path / "params.txt"
    method.touch()
    run_dir = tmp_path / "run1"

    def fake_run(cmd, stdout=None, stderr=None, timeout=None, stdin=None):
        stdout.write("CHILD OUTPUT\n")
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("subprocess.run", side_effect=fake_run):
        run_msdial(method_file=method, dataset_root=tmp_path,
                   run_dir=run_dir, exe_path="fake.exe")
    lines = (run_dir / "msdial.log").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("CMD: ")


def test_run_msdial_passes_project_flag(tmp_path):
    """save_project=True のとき -p を渡す（GUI で開ける .mdproject が出る）。"""
    method = tmp_path / "params.txt"
    method.touch()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_msdial(method_file=method, dataset_root=tmp_path,
                   run_dir=tmp_path / "run1", exe_path="fake.exe",
                   save_project=True)
    assert mock_run.call_args.args[0][-1] == "-p"


def test_run_msdial_omits_project_flag_by_default(tmp_path):
    method = tmp_path / "params.txt"
    method.touch()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_msdial(method_file=method, dataset_root=tmp_path,
                   run_dir=tmp_path / "run1", exe_path="fake.exe")
    assert "-p" not in mock_run.call_args.args[0]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q -k "stdin or flushes or project_flag"`
Expected: FAIL（`stdin` が kwargs に無く KeyError、CMD 行が先頭でない、`-p` が付かない）

- [x] **Step 3: Implement**

`lipidmix/console/runner.py` の `run_msdial` を差し替える。

```python
def run_msdial(
    method_file: Path,
    dataset_root: Path,
    run_dir: Path,
    timeout_s: int = 3600,
    exe_path: str | None = None,
    save_project: bool = False,
) -> int:
    """MS-DIAL Console を実行し、終了コードを返す。

    save_project: True なら -p を付け、GUI で開ける .mdproject を出させる。

    stdin は必ず塞ぐ。MS-DIAL Console は入力フォルダに複数フォーマットが
    混在すると Console.ReadLine() で対話するため、継承すると MCP stdio の
    JSON-RPC 入力を子が食う。塞ぐと MS-DIAL 側は NullReferenceException で
    終了コード 1 を返し、MSDIAL_NONZERO_EXIT として正しく扱える。
    """
    exe = exe_path or get_exe_path()
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "msdial.log"

    msdial_out_dir = run_dir / "msdial"
    msdial_out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [exe, "lcms", "-i", str(dataset_root), "-o", str(msdial_out_dir), "-m", str(method_file)]
    if save_project:
        cmd.append("-p")

    try:
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"CMD: {' '.join(cmd)}\n\n")
            # 子へ fd を渡す前に必ず flush する。親のバッファと子は同じ
            # ファイル記述のオフセットを共有するため、flush しないと
            # 子の出力が先頭に、CMD 行がその後ろに書かれる。
            log.flush()
            result = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout_s,
            )
    except subprocess.TimeoutExpired as exc:
        raise MsdialTimeoutError(
            f"MS-DIAL Console がタイムアウトしました（{timeout_s}s）"
        ) from exc

    if result.returncode != 0:
        raise MsdialNonZeroExitError(result.returncode)

    return result.returncode
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q`
Expected: PASS（既存テストも含めて全件）

- [x] **Step 5: Commit**

```bash
git add lipidmix/console/runner.py tests/test_console_runner.py
git commit -m "fix(console): stdin を塞ぎ CMD 行を flush し -p を渡せるようにする"
```

---

### Task 2: `MSDIAL_EXE` が Console 実行体かを検証する

`get_exe_path()` は環境変数が空でないことしか見ていない。GUI の `MSDIAL.exe` を設定しても `console_plan` が通り、`console_run` で GUI ウィンドウが開いてタイムアウトまでブロックする（実測）。`<exe> --help` の出力に `lcms` が含まれるかで判定する（旧ビルド・master ビルドとも含む。GUI はコンソール出力を持たずタイムアウトする）。

**Files:**
- Modify: `lipidmix/console/runner.py`
- Modify: `lipidmix/core/mcp_errors.py`
- Modify: `lipidmix/tools/console_tools.py`
- Test: `tests/test_console_runner.py`

**Interfaces:**
- Consumes: `get_exe_path() -> str`
- Produces: `is_console_exe(exe_path: str, timeout_s: int = 15) -> bool`
- Produces: エラーコード `MSDIAL_EXE_NOT_CONSOLE`

- [x] **Step 1: Write the failing tests**

```python
def test_is_console_exe_accepts_output_with_lcms():
    from lipidmix.console.runner import is_console_exe
    completed = MagicMock()
    completed.stdout = "MSDIAL Console Application 5.5\n  lcms   Run LC-MS data processing\n"
    with patch("subprocess.run", return_value=completed):
        assert is_console_exe("fake.exe") is True


def test_is_console_exe_rejects_gui():
    """GUI はコンソール出力を持たず、--help でウィンドウを開いて返らない。"""
    from lipidmix.console.runner import is_console_exe
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 15)):
        assert is_console_exe("gui.exe") is False


def test_is_console_exe_rejects_missing_file():
    from lipidmix.console.runner import is_console_exe
    with patch("subprocess.run", side_effect=OSError("not found")):
        assert is_console_exe("nope.exe") is False


def test_console_plan_rejects_non_console_exe(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "gui.exe")
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    (tmp_path / "a.wiff").touch()
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: False)
    from lipidmix.tools.console_tools import console_plan
    result = console_plan(dataset_root=str(tmp_path), method_file=str(method),
                          polarity="negative", measure="peak_height")
    assert _json.loads(result)["error"]["code"] == "MSDIAL_EXE_NOT_CONSOLE"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q -k "console_exe or non_console"`
Expected: FAIL with `ImportError: cannot import name 'is_console_exe'`

- [x] **Step 3: Implement**

`lipidmix/console/runner.py` の末尾に追加する。

```python
def is_console_exe(exe_path: str, timeout_s: int = 15) -> bool:
    """MSDIAL_EXE が Console 実行体かを --help の出力で判定する。

    Console は旧ビルドも master ビルドも --help（旧は引数エラー時の usage）に
    サブコマンド名 `lcms` を含む。GUI の MSDIAL.exe は Subsystem=Windows で
    コンソール出力を持たず、ウィンドウを開いたまま返らない（＝タイムアウト）。
    ここで弾かないと console_run が既定 6 時間ブロックする。
    """
    try:
        completed = subprocess.run(
            [exe_path, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout_s,
            text=True,
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return "lcms" in (completed.stdout or "")
```

`lipidmix/core/mcp_errors.py` の `CONSOLE_ERROR_CODES` に追加する。

```python
    # MSDIAL_EXE が Console ではなく GUI（MSDIAL.exe）等を指している場合。
    # MSDIAL_EXE_NOT_FOUND（未設定・起動できない）とは別: 起動はできるが
    # コマンドラインを解釈しない実行体を指している状態。
    "MSDIAL_EXE_NOT_CONSOLE",
```

`lipidmix/tools/console_tools.py` の `console_plan` にある exe 確認ブロック（現在は `from lipidmix.console.runner import get_exe_path` / `get_exe_path()` の try/except）を差し替える。

```python
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
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q`
Expected: PASS。既存の `test_console_plan_success` と `test_console_plan_missing_method_file` は `MSDIAL_EXE=fake.exe` に対して `is_console_exe` が False を返して落ちるので、両テストに次の 1 行を加えて修正する。

```python
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
```

- [x] **Step 5: Commit**

```bash
git add lipidmix/console/runner.py lipidmix/core/mcp_errors.py lipidmix/tools/console_tools.py tests/test_console_runner.py
git commit -m "feat(console): MSDIAL_EXE が Console 実行体かを --help で検証する"
```

---

### Task 3: 生データの混在を弾き、入力本数を実サポート拡張子で数える

`count_raw_inputs` は `.wiff/.raw/.mzml/.mzxml/.d` しか数えず `.wiff2` も `.abf` も落とす。さらに MS-DIAL は `wiff` と `wiff2` を**別フォーマットとして数える**ため、両方あるフォルダでは対話プロンプトが出る（実データ NEG フォルダで実測）。続行すると 60 サンプルが 120 解析ファイルになる。計画段階で止める。

**Files:**
- Modify: `lipidmix/console/job_manager.py`
- Modify: `lipidmix/core/mcp_errors.py`
- Modify: `lipidmix/tools/console_tools.py`
- Test: `tests/test_console_runner.py`

**Interfaces:**
- Consumes: `is_console_exe`（Task 2）
- Produces: `raw_input_summary(dataset_root: Path) -> dict[str, int]`（拡張子（先頭ドット無し・小文字）→ 件数）
- Produces: `count_raw_inputs(dataset_root: Path) -> int`（後方互換。`raw_input_summary` の合計）
- Produces: エラーコード `MIXED_RAW_FORMATS`
- Produces: `console_plan` の戻り値に `warnings: list[str]`

- [x] **Step 1: Write the failing tests**

```python
def test_raw_input_summary_counts_by_extension(tmp_path):
    from lipidmix.console.job_manager import raw_input_summary
    (tmp_path / "a.wiff").touch()
    (tmp_path / "b.wiff").touch()
    (tmp_path / "a.wiff2").touch()
    (tmp_path / "note.txt").touch()
    (tmp_path / "c.d").mkdir()
    assert raw_input_summary(tmp_path) == {"wiff": 2, "wiff2": 1, "d": 1}


def test_count_raw_inputs_totals_summary(tmp_path):
    from lipidmix.console.job_manager import count_raw_inputs
    (tmp_path / "a.wiff").touch()
    (tmp_path / "a.abf").touch()
    assert count_raw_inputs(tmp_path) == 2


def test_console_plan_rejects_mixed_raw_formats(tmp_path, monkeypatch):
    """.wiff と .wiff2 の併存は MS-DIAL が対話プロンプトを出す条件。"""
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    (tmp_path / "a.wiff").touch()
    (tmp_path / "a.wiff2").touch()
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "MIXED_RAW_FORMATS"
    assert parsed["error"]["details"]["formats"] == {"wiff": 1, "wiff2": 1}


def test_console_plan_rejects_empty_dataset_root(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "MIXED_RAW_FORMATS"


def test_console_plan_warns_existing_alignment_results(tmp_path, monkeypatch):
    """実行のたびに dataset_root へ別タイムスタンプのアライメント一式が積まれる。"""
    import json as _json
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    (tmp_path / "a.wiff").touch()
    (tmp_path / "AlignResult-2026931617_PeakProperties.arf").touch()
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["status"] == "planned"
    assert any("既存のアライメント結果" in w for w in parsed["warnings"])
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q -k "raw_input_summary or count_raw_inputs_totals or mixed_raw or empty_dataset_root or existing_alignment"`
Expected: FAIL with `ImportError: cannot import name 'raw_input_summary'`

- [x] **Step 3: Implement**

`lipidmix/console/job_manager.py` の `count_raw_inputs` を差し替える。

```python
# MS-DIAL の SupportMsRawDataExtension（MsdialCore/Enum/SupportFormat.cs）と同じ集合。
# MS-DIAL はこの一覧に載る拡張子を「解析対象」と見なし、**2 種類以上あると
# 対話プロンプトを出す**（AnalysisFilesParser.ReadInput の Console.ReadLine）。
_RAW_EXTENSIONS = frozenset({
    "abf", "ibf", "cdf", "mzml", "wiff", "raw", "d", "wiff2", "qgd", "lcd", "lrp", "imzml",
})


def raw_input_summary(dataset_root: Path) -> dict[str, int]:
    """データフォルダ直下の計測ファイルを拡張子ごとに数える。

    `.raw` `.d` はベンダーによってはディレクトリなので、ファイル・ディレクトリの
    両方を見る。MS-DIAL 自身も TopDirectoryOnly でしか見ないので非再帰。
    """
    counts: dict[str, int] = {}
    for entry in dataset_root.iterdir():
        ext = entry.suffix.lower().lstrip(".")
        if ext in _RAW_EXTENSIONS:
            counts[ext] = counts.get(ext, 0) + 1
    return counts


def count_raw_inputs(dataset_root: Path) -> int:
    """データフォルダ内の計測ファイル数を返す（raw_input_summary の合計）。"""
    return sum(raw_input_summary(dataset_root).values())
```

`lipidmix/core/mcp_errors.py` の `CONSOLE_ERROR_CODES` に追加する。

```python
    # 入力フォルダに MS-DIAL が対象とする拡張子が 2 種類以上ある（または 0 種類）。
    # 2 種類以上あると MS-DIAL は対話プロンプトを出し、続行すると 1 つの測定が
    # 複数の解析ファイルとして扱われる（.wiff と .wiff2 の併存が実データで該当）。
    "MIXED_RAW_FORMATS",
```

`lipidmix/tools/console_tools.py` の `console_plan` にある `count_raw_inputs` 呼び出しと `create_job` 呼び出しのブロックを差し替える。

```python
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
```

同じく `console_plan` の `return json_payload({...})` の直前に警告の組み立てを置く。

```python
    warnings: list[str] = []
    if any(p.is_file() and (p.name.startswith("AlignResult-") or "AlignmentResult" in p.name)
           for p in root.iterdir()):
        warnings.append(
            "既存のアライメント結果がデータフォルダにあります。MS-DIAL Console は"
            "実行のたびに別タイムスタンプの一式を同じフォルダへ追加するため、"
            "複数バッチが混在します。どれが今回の生成物かは console_status の"
            "artifacts で確認してください。")
```

`json_payload` の辞書に `"warnings": warnings,` を追加する（`"next"` の直前）。

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q`
Expected: PASS。既存の `test_console_plan_success` は `tmp_path` に計測ファイルが無いため `MIXED_RAW_FORMATS`（0 種類）になる。同テストに `(tmp_path / "a.wiff").touch()` を 1 行加えて修正する。

- [x] **Step 5: Commit**

```bash
git add lipidmix/console/job_manager.py lipidmix/core/mcp_errors.py lipidmix/tools/console_tools.py tests/test_console_runner.py
git commit -m "feat(console): 生データの混在を弾き実サポート拡張子で入力を数える"
```

---

### Task 4: メソッドファイルをテキストとして検証する

`ConfigParser.ReadForLcmsParameter` は ASCII のプレーンテキストを `key: value`（または `key=value`）で 1 行ずつ読む。`.mdproject` は ZIP なので、渡しても**エラーにならず全パラメータが既定値のまま走る**。極性も Target omics も効かない。現在の `console_plan` の docstring は `.msdial` / `.mdproject` を案内しており、実データで静かに間違える。

**Files:**
- Modify: `lipidmix/core/mcp_errors.py`
- Modify: `lipidmix/tools/console_tools.py`
- Test: `tests/test_console_runner.py`

**Interfaces:**
- Consumes: なし
- Produces: `_looks_like_method_text(path: Path) -> bool`（`console_tools` 内部ヘルパ）
- Produces: エラーコード `METHOD_FILE_NOT_TEXT`

- [x] **Step 1: Write the failing tests**

```python
def test_console_plan_rejects_binary_method_file(tmp_path, monkeypatch):
    """.mdproject は ZIP。渡すと MS-DIAL は全パラメータ既定値で走ってしまう。"""
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    (tmp_path / "a.wiff").touch()
    method = tmp_path / "project.mdproject"
    method.write_bytes(b"PK\x03\x04\x00\x00binary")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_TEXT"


def test_console_plan_rejects_text_without_key_value(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    (tmp_path / "a.wiff").touch()
    method = tmp_path / "empty.txt"
    method.write_text("# comment only\n\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_TEXT"


def test_console_plan_accepts_key_value_method_file(tmp_path, monkeypatch):
    import json as _json
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    (tmp_path / "a.wiff").touch()
    method = tmp_path / "params.txt"
    method.write_text("# MS-DIAL param\nIon mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["status"] == "planned"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q -k "binary_method or without_key_value or accepts_key_value"`
Expected: FAIL（binary / key-value 無しのケースが `planned` を返す）

- [x] **Step 3: Implement**

`lipidmix/core/mcp_errors.py` の `CONSOLE_ERROR_CODES` に追加する。

```python
    # メソッドファイルが MS-DIAL Console の読めるテキストではない。
    # ConfigParser は ASCII の `key: value` を 1 行ずつ読むだけなので、
    # .mdproject（ZIP）を渡してもエラーにならず全パラメータが既定値になる。
    "METHOD_FILE_NOT_TEXT",
```

`lipidmix/tools/console_tools.py` の内部ヘルパ節（`_resolve_job_path` の近く）に追加する。

```python
def _looks_like_method_text(path: Path) -> bool:
    """MS-DIAL Console の ConfigParser が読める形かを判定する。

    ConfigParser は StreamReader(Encoding.ASCII) で 1 行ずつ読み、`#` 始まりを
    飛ばして最初の `:` または `=` で key/value に割る。したがって「テキストで
    あること」と「key/value 行が 1 つ以上あること」だけを見れば足りる。
    ZIP（.mdproject / .mddata）は NUL バイトを含むのでここで落ちる。
    """
    try:
        head = path.read_bytes()[:8192]
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
```

`console_plan` の `METHOD_FILE_NOT_FOUND` チェックの直後に追加する。

```python
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
```

`console_plan` の docstring の `method_file` の説明を差し替える。

```python
    method_file:
        MS-DIAL Console のパラメータファイル（ASCII テキスト。`key: value` 形式）。
        MS-DIAL GUI の Export > Parameter で出力できます。
        **`.mdproject` / `.mddata` は使えません**（ZIP なので Console は中身を
        読めず、全パラメータが既定値のまま実行されます）。
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q`
Expected: PASS。既存テストで `method.touch()`（空ファイル）を使っているものは `METHOD_FILE_NOT_TEXT` になるため、`method.write_text("Ion mode: Negative\n", encoding="ascii")` に置き換える。

- [x] **Step 5: Commit**

```bash
git add lipidmix/core/mcp_errors.py lipidmix/tools/console_tools.py tests/test_console_runner.py
git commit -m "feat(console): メソッドファイルをテキストとして検証し .mdproject を拒否する"
```

---

### Task 5: `_ROLE_MAP` を Console の実出力に合わせ、unknown はハッシュしない

実走した `console_run` の artifacts 10 件が**全部 `role="unknown"`** だった（`.mdpeak` / `.mdmsp` / `.mdalign`）。Console が実際に出す拡張子を役割付けする。あわせて、role が付かないファイルの sha256 を省く（Task 7 で生データフォルダを見るようになると `.msp2.dbs` の 146MB などを毎回ハッシュすることになるため）。

**Files:**
- Modify: `lipidmix/console/output_collector.py`
- Test: `tests/test_console_runner.py`

**Interfaces:**
- Consumes: なし
- Produces: `_assign_role(rel_str) -> tuple[str, str]` — 対応拡張子を拡張
- Produces: `collect_artifacts` が返す `Artifact.sha256` は role が `"unknown"` のとき空文字

- [x] **Step 1: Write the failing tests**

既存の `test_assign_role` の下に追加する。

```python
@pytest.mark.parametrize("filename,expected_role,expected_fmt", [
    ("msdial/S1.mdpeak", "sample_peak_table", "mdpeak"),
    ("msdial/AlignResult-2026931617.mdalign", "alignment_table", "mdalign"),
    ("msdial/S1.mdmsp", "msms_spectra", "mdmsp"),
    ("msdial/AlignResult-2026931617.qa.tsv", "quality_matrix", "qatsv"),
    ("msdial/Project-2609030417.mdproject", "gui_project", "mdproject"),
    ("Project-2609030417.mddata", "project_data", "mddata"),
    ("S1_2026931617_tags.xml", "peak_tags", "tagsxml"),
    ("S1_2026931617.pai2", "sample_peaks", "pai2"),
])
def test_assign_role_console_outputs(filename, expected_role, expected_fmt):
    role, fmt = _assign_role(filename)
    assert (role, fmt) == (expected_role, expected_fmt)


def test_collect_artifacts_skips_hash_for_unknown_role(tmp_path):
    """role の付かないファイルはハッシュしない。

    生データフォルダには .msp2.dbs のような 100MB 超の副産物があり、
    実行のたびに全部ハッシュすると収集が I/O で支配される。
    """
    (tmp_path / "mystery.bin").write_bytes(b"x" * 1024)
    (tmp_path / "S1.pai2").write_bytes(b"y" * 16)
    _, artifacts = collect_artifacts(tmp_path, {})
    by_path = {a.path: a for a in artifacts}
    assert by_path["mystery.bin"].role == "unknown"
    assert by_path["mystery.bin"].sha256 == ""
    assert by_path["S1.pai2"].sha256 != ""
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q -k "console_outputs or skips_hash"`
Expected: FAIL（`.mdpeak` 等が `("unknown", "mdpeak")` を返す。unknown にも sha256 が入る）

- [x] **Step 3: Implement**

`lipidmix/console/output_collector.py` の `_ROLE_MAP` を差し替える。

```python
# 拡張子パターン → (role, format) のマッピング（長い拡張子を先に評価する）。
#
# 上段は MS-DIAL Console が生データフォルダ（-i）へ書くもの、下段は出力フォルダ
# （-o）へ書くエクスポート。この分担は実走で確認済み（docs/HISTRY.md 2026-09-03(6)）。
# どちらのルートから来たかは Artifact.root が持つので、ここでは拡張子だけを見る。
_ROLE_MAP: list[tuple[str, str, str]] = [
    ("_tags.xml",  "peak_tags",          "tagsxml"),
    (".qa.tsv",    "quality_matrix",     "qatsv"),
    (".msp2.dbs",  "library_cache",      "msp2dbs"),
    (".EIC.aef",   "chromatogram",       "eicaef"),
    (".mzTab",     "primary_mztab",      "mztab"),
    (".mdproject", "gui_project",        "mdproject"),
    (".mdalign",   "alignment_table",    "mdalign"),
    (".mdpeak",    "sample_peak_table",  "mdpeak"),
    (".mdmsp",     "msms_spectra",       "mdmsp"),
    (".mddata",    "project_data",       "mddata"),
    (".msp2",      "library_snapshot",   "msp2"),
    (".arf2",      "spot_catalog",       "arf2"),
    (".arf",       "peak_matrix_source", "arf"),
    (".pai2",      "sample_peaks",       "pai2"),
    (".dcl",       "msms_evidence",      "dcl"),
]
```

同ファイルの `collect_artifacts` にあるハッシュ計算の 2 行を差し替える。

```python
        role, fmt = _assign_role(rel_str)
        # role の付かないファイルはハッシュしない。生データフォルダには
        # .msp2.dbs（実測 146MB）のような大きな副産物があり、実行のたびに
        # 全部ハッシュすると収集が I/O で支配される。
        checksum = sha256_file(fp) if role != "unknown" else ""
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add lipidmix/console/output_collector.py tests/test_console_runner.py
git commit -m "feat(console): Console の実出力に role を与え unknown はハッシュしない"
```

---

### Task 6: `analysis-job` を v2 に上げる（root / save_project / timeout_s）

生成物が `-o` と生データフォルダの 2 か所に分かれる事実をスキーマで表現する。`Artifact` と `MztabEntry` に `root` を持たせ、`AnalysisJob` に `save_project` / `timeout_s` を足す。v1 のファイルは読めるまま（`root="run_dir"` として解釈する）。

**Files:**
- Modify: `lipidmix/handoff/schema.py`
- Test: `tests/test_handoff_schema.py`

**Interfaces:**
- Consumes: なし
- Produces: `SCHEMA_VERSION = "analysis-job.v2"`、`SUPPORTED_SCHEMA_VERSIONS: frozenset[str]`
- Produces: `Artifact(path, role, format, sha256, root="run_dir")`
- Produces: `MztabEntry(path, polarity, measure, sha256, validation, root="run_dir")`
- Produces: `AnalysisJob(..., save_project: bool = False, timeout_s: int = 3600)`

- [x] **Step 1: Write the failing tests**

`tests/test_handoff_schema.py` に追記する。

```python
def test_schema_version_is_v2():
    from lipidmix.handoff.schema import SCHEMA_VERSION
    assert SCHEMA_VERSION == "analysis-job.v2"


def test_artifact_root_defaults_to_run_dir():
    from lipidmix.handoff.schema import Artifact
    a = Artifact(path="msdial/x.mdpeak", role="sample_peak_table", format="mdpeak", sha256="ab")
    assert a.root == "run_dir"


def test_roundtrip_preserves_root_and_execution_fields(tmp_path):
    from lipidmix.handoff.schema import (
        SCHEMA_VERSION, AnalysisJob, Artifact, MztabEntry)
    job = AnalysisJob(
        schema=SCHEMA_VERSION, job_id="j1", status="completed",
        created_at="t", updated_at="t", dataset_root=str(tmp_path), input_count=4,
        software_name="MS-DIAL", software_version="5.5", execution_mode="console",
        method_file="m.txt", omics="lipidomics", polarity="negative",
        measure="peak_height", run_dir=str(tmp_path / "run"),
        primary_mztab_files=[MztabEntry(path="msdial/A.mzTab", polarity="negative",
                                        measure="peak_height", sha256="c1")],
        artifacts=[Artifact(path="S1.pai2", role="sample_peaks", format="pai2",
                            sha256="c2", root="dataset_root")],
        save_project=True, timeout_s=21600,
    )
    p = tmp_path / "analysis-job.json"
    job.save(p)
    loaded = AnalysisJob.load(p)
    assert loaded.artifacts[0].root == "dataset_root"
    assert loaded.primary_mztab_files[0].root == "run_dir"
    assert loaded.save_project is True
    assert loaded.timeout_s == 21600


def test_load_accepts_v1_and_defaults_root(tmp_path):
    """v1 のジョブは生成物がすべて run_dir 側にある前提で書かれている。"""
    import json
    from lipidmix.handoff.schema import AnalysisJob
    p = tmp_path / "analysis-job.json"
    p.write_text(json.dumps({
        "schema": "analysis-job.v1", "job_id": "old", "status": "completed",
        "created_at": "t", "updated_at": "t",
        "source": {"dataset_root": str(tmp_path), "input_count": 1},
        "software": {"name": "MS-DIAL", "version": "", "execution_mode": "console",
                     "method_file": "m.txt"},
        "project": {"omics": "lipidomics", "polarity": "negative", "measure": "peak_height"},
        "run_dir": str(tmp_path),
        "primary_mztab_files": [{"path": "A.mzTab", "polarity": "negative",
                                 "measure": "peak_height", "sha256": "x", "validation": {}}],
        "artifacts": [{"path": "S1.pai2", "role": "sample_peaks", "format": "pai2", "sha256": "y"}],
        "warnings": [], "error": None,
    }, ensure_ascii=False), encoding="utf-8")
    loaded = AnalysisJob.load(p)
    assert loaded.artifacts[0].root == "run_dir"
    assert loaded.primary_mztab_files[0].root == "run_dir"
    assert loaded.save_project is False
    assert loaded.timeout_s == 3600


def test_load_rejects_unknown_schema(tmp_path):
    import json
    import pytest
    from lipidmix.handoff.schema import AnalysisJob
    p = tmp_path / "analysis-job.json"
    p.write_text(json.dumps({"schema": "analysis-job.v99"}), encoding="utf-8")
    with pytest.raises(ValueError):
        AnalysisJob.load(p)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_handoff_schema.py -q`
Expected: FAIL（`SCHEMA_VERSION` が v1、`Artifact` に `root` が無い）

- [x] **Step 3: Implement**

`lipidmix/handoff/schema.py` の `SCHEMA_VERSION = "analysis-job.v1"` の行を差し替える。

```python
# v2: 生成物が 2 つのルート（run_dir と dataset_root）に分かれる事実を
# Artifact.root / MztabEntry.root で表現し、AnalysisJob に save_project /
# timeout_s を足した。MS-DIAL Console は -o にエクスポートだけを出し、
# .pai2 / .dcl / .arf / .arf2 / .EIC.aef は生データフォルダへ出す
# （docs/HISTRY.md 2026-09-03(6) の実走で確認）。
SCHEMA_VERSION = "analysis-job.v2"
# 読み込みは v1 も受ける。v1 は生成物がすべて run_dir 側にある前提で
# 書かれているので root="run_dir" として解釈する。書き出しは常に v2。
SUPPORTED_SCHEMA_VERSIONS = frozenset({"analysis-job.v1", SCHEMA_VERSION})

ArtifactRoot = Literal["run_dir", "dataset_root"]
```

`MztabEntry` と `Artifact` を差し替える。

```python
@dataclass
class MztabEntry:
    path: str
    polarity: Polarity
    measure: MeasureType
    sha256: str
    validation: dict = field(default_factory=dict)
    # path はこのルートからの相対パス。dataset_load が絶対パスへ戻すのに使う。
    root: ArtifactRoot = "run_dir"


@dataclass
class Artifact:
    path: str
    role: str
    format: str
    sha256: str
    root: ArtifactRoot = "run_dir"
```

`AnalysisJob` の最後のフィールド `error: str | None = None` の直後に追加する。

```python
    # console_plan が宣言し console_run が使う実行オプション。
    save_project: bool = False
    timeout_s: int = 3600
```

`AnalysisJob.load` の版チェックを差し替える。

```python
        if data.get("schema") not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"analysis-job schema mismatch: expected one of "
                f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}, got {data.get('schema')!r}"
            )
```

`_to_dict` の `primary_mztab_files` / `artifacts` を差し替える。

```python
        "primary_mztab_files": [
            {
                "path": e.path,
                "polarity": e.polarity,
                "measure": e.measure,
                "sha256": e.sha256,
                "validation": e.validation,
                "root": e.root,
            }
            for e in job.primary_mztab_files
        ],
        "artifacts": [
            {"path": a.path, "role": a.role, "format": a.format,
             "sha256": a.sha256, "root": a.root}
            for a in job.artifacts
        ],
```

`_to_dict` の `"warnings": job.warnings,` の直前に追加する。

```python
        "execution": {
            "save_project": job.save_project,
            "timeout_s": job.timeout_s,
        },
```

`_from_dict` の `mztab` / `artifacts` 構築を差し替え、`execution` を読む。

```python
    mztab = [
        MztabEntry(
            path=e["path"],
            polarity=e["polarity"],
            measure=e["measure"],
            sha256=e.get("sha256", ""),
            validation=e.get("validation", {}),
            root=e.get("root", "run_dir"),
        )
        for e in d.get("primary_mztab_files", [])
    ]
    artifacts = [
        Artifact(path=a["path"], role=a["role"], format=a["format"],
                 sha256=a.get("sha256", ""), root=a.get("root", "run_dir"))
        for a in d.get("artifacts", [])
    ]
    execution = d.get("execution", {})
```

`_from_dict` 末尾の `AnalysisJob(...)` の `error=d.get("error"),` の直後に追加する。

```python
        save_project=execution.get("save_project", False),
        timeout_s=execution.get("timeout_s", 3600),
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_handoff_schema.py tests/test_mztab_tools.py -q`
Expected: PASS（`tests/test_mztab_tools.py` は `SCHEMA_VERSION` 定数を使っているので自動追従する）

- [x] **Step 5: Commit**

```bash
git add lipidmix/handoff/schema.py tests/test_handoff_schema.py
git commit -m "feat(handoff): analysis-job を v2 に上げ生成物のルートと実行オプションを持たせる"
```

---

### Task 7: `collect_artifacts` を複数ルート対応にする

MS-DIAL Console は `-o` にエクスポートだけを出し、`.pai2` / `.dcl` / `.arf` / `.arf2` / `.EIC.aef` / `_tags.xml` / `.mddata` は**生データフォルダ**へ出す。現行の収集は `run_dir` しか見ないため、実走した `console_run` は `.pai2` が 4 本あるのに「1 つも生成されていません」と誤った warning を出した。

**Files:**
- Modify: `lipidmix/console/output_collector.py`
- Test: `tests/test_console_runner.py`

**Interfaces:**
- Consumes: `Artifact.root` / `MztabEntry.root`（Task 6）、`_ROLE_MAP`（Task 5）
- Produces: `snapshot(directory: Path, exclude_dir_names: frozenset[str] | set[str] = frozenset()) -> dict[str, int]`
- Produces: `collect_artifacts(roots: dict[str, Path], befores: dict[str, dict[str, int]], *, declared_polarity: str | None = None, declared_measure: str | None = None) -> tuple[list[MztabEntry], list[Artifact]]`
  - `roots` のキーは `"run_dir"` / `"dataset_root"`。返る `Artifact.root` / `MztabEntry.root` がそのキーになる。
- Produces: `RUNS_SUBDIR = "runs"`（モジュール定数）

- [x] **Step 1: Write the failing tests**

```python
def test_snapshot_excludes_named_dirs(tmp_path):
    (tmp_path / "runs" / "job1").mkdir(parents=True)
    (tmp_path / "runs" / "job1" / "analysis-job.json").write_text("{}", encoding="utf-8")
    (tmp_path / "S1.pai2").write_bytes(b"x")
    snap = snapshot(tmp_path, exclude_dir_names={"runs"})
    assert "S1.pai2" in snap
    assert not any("runs" in k for k in snap)


def test_collect_artifacts_tags_root_per_source(tmp_path):
    """-o のエクスポートと生データフォルダの生成物を 1 回で集め、出所を刻む。"""
    run_dir = tmp_path / "runs" / "job1"
    (run_dir / "msdial").mkdir(parents=True)
    (run_dir / "msdial" / "AlignResult-1.mzTab").write_text("MTD\n", encoding="utf-8")
    (run_dir / "msdial" / "S1.mdpeak").write_bytes(b"a")
    (tmp_path / "S1_1.pai2").write_bytes(b"b")
    (tmp_path / "S1_1.dcl").write_bytes(b"c")

    roots = {"run_dir": run_dir, "dataset_root": tmp_path}
    befores = {"run_dir": {}, "dataset_root": {}}
    entries, artifacts = collect_artifacts(
        roots, befores, declared_polarity="negative", declared_measure="peak_height")

    assert [(e.path, e.root) for e in entries] == [
        (str(Path("msdial") / "AlignResult-1.mzTab"), "run_dir")]
    by_role = {a.role: a for a in artifacts}
    assert by_role["sample_peak_table"].root == "run_dir"
    assert by_role["sample_peaks"].root == "dataset_root"
    assert by_role["msms_evidence"].root == "dataset_root"


def test_collect_artifacts_does_not_double_count_run_dir(tmp_path):
    """run_dir は dataset_root の配下にある。同じファイルを 2 回集めない。"""
    run_dir = tmp_path / "runs" / "job1"
    (run_dir / "msdial").mkdir(parents=True)
    (run_dir / "msdial" / "S1.mdpeak").write_bytes(b"a")
    roots = {"run_dir": run_dir, "dataset_root": tmp_path}
    befores = {"run_dir": {}, "dataset_root": snapshot(tmp_path, exclude_dir_names={"runs"})}
    _, artifacts = collect_artifacts(roots, befores)
    assert [a.path for a in artifacts] == [str(Path("msdial") / "S1.mdpeak")]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q -k "excludes_named_dirs or tags_root or double_count"`
Expected: FAIL（`snapshot` に `exclude_dir_names` が無い、`collect_artifacts` が単一ルートしか受けない）

- [x] **Step 3: Implement**

`lipidmix/console/output_collector.py` の import 群の直後（`_OPERATIONAL_FILES` の定義の前）に追加する。

```python
# dataset_root を撮るときに降りないディレクトリ名。job_manager の RUNS_SUBDIR と
# 同じ値だが、output_collector に job_manager を知らせないため定数を複製する
# （収集層はジョブ管理を知らないままにしておく）。
RUNS_SUBDIR = "runs"
```

`snapshot` を差し替える。

```python
def snapshot(directory: Path, exclude_dir_names: frozenset[str] | set[str] = frozenset()) -> dict[str, int]:
    """ディレクトリ以下の全ファイルを {相対パス文字列: サイズ} で返す。

    exclude_dir_names: 降りないディレクトリ名。dataset_root を撮るときに
        `{RUNS_SUBDIR}` を渡す。ランディレクトリは dataset_root の配下にあるので、
        除外しないと同じファイルを 2 つのルートで二重に数える。
    """
    result: dict[str, int] = {}
    if not directory.exists():
        return result
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dir_names]
        for name in files:
            fp = Path(root) / name
            try:
                result[str(fp.relative_to(directory))] = fp.stat().st_size
            except (OSError, ValueError):
                pass
    return result
```

`collect_artifacts` を差し替える。

```python
def collect_artifacts(
    roots: dict[str, Path],
    befores: dict[str, dict[str, int]],
    *,
    declared_polarity: str | None = None,
    declared_measure: str | None = None,
) -> tuple[list[MztabEntry], list[Artifact]]:
    """複数ルートを before スナップショットと比較し、新規・変化ファイルを収集する。

    MS-DIAL Console は `-o`（run_dir）にエクスポート（.mdpeak / .mdmsp /
    .mdalign / .mzTab / .qa.tsv / .mdproject）だけを出し、.pai2 / .dcl /
    _tags.xml / .arf / .arf2 / .EIC.aef / .mddata は**生データフォルダ**へ出す
    （docs/HISTRY.md 2026-09-03(6) の実走で確認）。片方だけ見ると MS/MS 根拠の
    経路が丸ごと空になるので、両方を 1 回で集めて出所を root に刻む。

    Parameters
    ----------
    roots: {"run_dir": Path, "dataset_root": Path} のようなルート名 → パス。
    befores: 同じキーの、実行前スナップショット。
    declared_polarity / declared_measure: analysis-job.json の project 値。

    Returns
    -------
    (mztab_entries, other_artifacts)
    """
    mztab_entries: list[MztabEntry] = []
    other_artifacts: list[Artifact] = []

    for root_name, root_path in roots.items():
        before = befores.get(root_name, {})
        exclude = {RUNS_SUBDIR} if root_name == "dataset_root" else frozenset()
        after = snapshot(root_path, exclude_dir_names=exclude)
        new_or_changed = {
            rel: size
            for rel, size in after.items()
            if (rel not in before or before[rel] != size)
            and Path(rel).name not in _OPERATIONAL_FILES
        }

        for rel_str in sorted(new_or_changed):
            fp = root_path / rel_str
            if not fp.is_file():
                continue
            role, fmt = _assign_role(rel_str)
            # role の付かないファイルはハッシュしない。生データフォルダには
            # .msp2.dbs（実測 146MB）のような大きな副産物がある。
            checksum = sha256_file(fp) if role != "unknown" else ""

            if fmt == "mztab":
                if _NORMALIZED_PREFIX_RE.match(fp.name):
                    other_artifacts.append(Artifact(
                        path=rel_str, role=UNSUPPORTED_MZTAB_ROLE, format=fmt,
                        sha256=checksum, root=root_name,
                    ))
                    continue
                polarity, measure, validation = _resolve_mztab_meta(
                    fp.name, declared_polarity, declared_measure)
                mztab_entries.append(MztabEntry(
                    path=rel_str, polarity=polarity, measure=measure,
                    sha256=checksum, validation=validation, root=root_name,
                ))
            else:
                other_artifacts.append(Artifact(
                    path=rel_str, role=role, format=fmt,
                    sha256=checksum, root=root_name,
                ))

    return mztab_entries, other_artifacts
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q`
Expected: 最初は FAIL。旧シグネチャ `collect_artifacts(run_dir, before, ...)` で呼ぶ既存テスト（Task 5 で足した `test_collect_artifacts_skips_hash_for_unknown_role` を含む）を、次の形に書き換えてから再実行して PASS にする。

```python
    _, artifacts = collect_artifacts({"run_dir": tmp_path}, {"run_dir": {}})
```

- [x] **Step 5: Commit**

```bash
git add lipidmix/console/output_collector.py tests/test_console_runner.py
git commit -m "feat(console): 生成物を run_dir と dataset_root の両方から集め出所を刻む"
```

---

### Task 8: `console_run` が両ルートを撮り、実行オプションを使う

Task 7 の収集をツール層に配線し、`console_plan` が宣言した `save_project` / `timeout_s` を実行に渡す。`.pai2` 不在の warning が誤発火しないことをテストで固定する。

**Files:**
- Modify: `lipidmix/tools/console_tools.py`
- Test: `tests/test_console_runner.py`

**Interfaces:**
- Consumes: `collect_artifacts(roots, befores, ...)` / `snapshot(dir, exclude_dir_names)` / `RUNS_SUBDIR`（Task 7）、`AnalysisJob.save_project` / `.timeout_s`（Task 6）、`run_msdial(..., save_project=)`（Task 1）
- Produces: `console_plan(dataset_root, method_file, polarity="positive", measure="peak_height", omics="lipidomics", save_project=True, timeout_s=21600) -> str`

- [x] **Step 1: Write the failing tests**

```python
def _planned_job(tmp_path, monkeypatch, **kwargs):
    """console_plan を通してジョブを 1 件作り、job_path を返す。"""
    import json as _json
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    (tmp_path / "S1.wiff").touch()
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height", **kwargs))
    return Path(parsed["job_path"])


def test_console_run_collects_dataset_root_outputs(tmp_path, monkeypatch):
    """.pai2 は生データフォルダ側に出る。誤った不在 warning を出さない。"""
    import json as _json
    job_path = _planned_job(tmp_path, monkeypatch)

    def fake_run_msdial(method_file, dataset_root, run_dir, timeout_s=3600,
                        exe_path=None, save_project=False):
        out = Path(run_dir) / "msdial"
        out.mkdir(parents=True, exist_ok=True)
        (out / "AlignResult-1.mzTab").write_text("MTD\n", encoding="utf-8")
        (out / "S1.mdpeak").write_bytes(b"a")
        Path(dataset_root, "S1_1.pai2").write_bytes(b"b")
        Path(dataset_root, "S1_1.dcl").write_bytes(b"c")
        return 0

    monkeypatch.setattr("lipidmix.console.runner.run_msdial", fake_run_msdial)
    from lipidmix.tools.console_tools import console_run
    parsed = _json.loads(console_run(str(job_path)))
    assert parsed["status"] == "completed"
    assert not any(".pai2" in w for w in parsed["warnings"])

    from lipidmix.console.job_manager import load_job
    job = load_job(job_path)
    roots = {a.role: a.root for a in job.artifacts}
    assert roots["sample_peaks"] == "dataset_root"
    assert roots["msms_evidence"] == "dataset_root"
    assert roots["sample_peak_table"] == "run_dir"


def test_console_run_warns_when_no_sample_files(tmp_path, monkeypatch):
    import json as _json
    job_path = _planned_job(tmp_path, monkeypatch)

    def fake_run_msdial(method_file, dataset_root, run_dir, timeout_s=3600,
                        exe_path=None, save_project=False):
        out = Path(run_dir) / "msdial"
        out.mkdir(parents=True, exist_ok=True)
        (out / "AlignResult-1.mzTab").write_text("MTD\n", encoding="utf-8")
        return 0

    monkeypatch.setattr("lipidmix.console.runner.run_msdial", fake_run_msdial)
    from lipidmix.tools.console_tools import console_run
    parsed = _json.loads(console_run(str(job_path)))
    assert any(".pai2" in w for w in parsed["warnings"])


def test_console_run_passes_job_execution_options(tmp_path, monkeypatch):
    job_path = _planned_job(tmp_path, monkeypatch, save_project=True, timeout_s=1234)
    seen = {}

    def fake_run_msdial(method_file, dataset_root, run_dir, timeout_s=3600,
                        exe_path=None, save_project=False):
        seen["timeout_s"] = timeout_s
        seen["save_project"] = save_project
        out = Path(run_dir) / "msdial"
        out.mkdir(parents=True, exist_ok=True)
        (out / "AlignResult-1.mzTab").write_text("MTD\n", encoding="utf-8")
        return 0

    monkeypatch.setattr("lipidmix.console.runner.run_msdial", fake_run_msdial)
    from lipidmix.tools.console_tools import console_run
    console_run(str(job_path))
    assert seen == {"timeout_s": 1234, "save_project": True}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q -k "dataset_root_outputs or no_sample_files or execution_options"`
Expected: FAIL（`console_plan` が `save_project` / `timeout_s` を受け取らず TypeError）

- [x] **Step 3: Implement**

`lipidmix/tools/console_tools.py` の `console_plan` のシグネチャを差し替える。

```python
def console_plan(
    dataset_root: str,
    method_file: str,
    polarity: str = "positive",
    measure: str = "peak_height",
    omics: str = "lipidomics",
    save_project: bool = True,
    timeout_s: int = 21600,
) -> str:
```

docstring の `omics` の説明の後に追記する。

```python
    save_project:
        True（既定）なら MS-DIAL Console に -p を渡し、GUI で開ける
        .mdproject を出力フォルダに生成させます。
    timeout_s:
        MS-DIAL Console のタイムアウト秒数（既定 21600 ＝ 6 時間）。
        実測では 4 サンプルで約 3 分。60 サンプル規模では 1 時間を超え得ます。
```

`create_job(...)` の直後、`session_state.session.current_job_path` への代入の前に追加する。

```python
    from lipidmix.console.job_manager import save_job
    job.save_project = save_project
    job.timeout_s = timeout_s
    save_job(job, job_path)
```

`json_payload` の辞書の `"input_count": input_count,` の直後に追加する。

```python
        "save_project": save_project,
        "timeout_s": timeout_s,
```

`console_run` のスナップショット取得ブロックを差し替える。

```python
    run_dir = Path(job.run_dir)
    dataset_root = Path(job.dataset_root)
    from lipidmix.console.output_collector import snapshot, RUNS_SUBDIR
    # MS-DIAL Console は -o にエクスポートだけを出し、.pai2 / .dcl / .arf /
    # .arf2 / .EIC.aef は生データフォルダへ出す。両方を撮らないと MS/MS 根拠の
    # 経路が丸ごと空になる（docs/HISTRY.md 2026-09-03(6)）。
    # run_dir は dataset_root の配下にあるので、dataset_root 側では runs/ を除く。
    befores = {
        "run_dir": snapshot(run_dir),
        "dataset_root": snapshot(dataset_root, exclude_dir_names={RUNS_SUBDIR}),
    }
```

`run_msdial(...)` 呼び出しを差し替える。

```python
        run_msdial(
            method_file=Path(job.method_file),
            dataset_root=Path(job.dataset_root),
            run_dir=run_dir,
            timeout_s=job.timeout_s,
            save_project=job.save_project,
        )
```

`collect_artifacts(...)` 呼び出しを差し替える。

```python
        from lipidmix.console.output_collector import collect_artifacts
        mztab_entries, other_artifacts = collect_artifacts(
            {"run_dir": run_dir, "dataset_root": dataset_root},
            befores,
            declared_polarity=job.polarity,
            declared_measure=job.measure,
        )
```

`_missing_per_sample_output_warnings` の docstring を実測に合わせて更新する（本体は変更なし）。

```python
def _missing_per_sample_output_warnings(artifacts) -> list[str]:
    """サンプル別ファイル（.pai2）が 1 つも出ていないことを伝える。

    MS-DIAL Console は .pai2 を**生データフォルダ側**に書く（-o ではない。
    docs/HISTRY.md 2026-09-03(6) の実走で確認）。collect_artifacts が両ルートを
    見るようになったので、この検査は「本当に出ていない」ときだけ発火する。
    .pai2 が無いと pai2_parser / dcl_find_msms が読むものが無く、MS/MS 根拠の
    経路が丸ごと空になる。アライメント結果だけは出ているので実行は成功扱いの
    まま、「後で MS/MS を辿れない」ことだけ先に知らせる。
    """
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_console_runner.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add lipidmix/tools/console_tools.py tests/test_console_runner.py
git commit -m "feat(console): console_run が両ルートを収集し実行オプションを渡す"
```

---

### Task 9: `dataset_load` が root を見て絶対パスへ戻す

Task 6/7 で生成物の相対パスが 2 つのルートに分かれたので、`job.run_dir` 決め打ちの結合を root ごとの結合に直す。直さないと `artifact_paths` が存在しないパスを指す。

**Files:**
- Modify: `lipidmix/tools/mztab_tools.py`
- Test: `tests/test_mztab_tools.py`

**Interfaces:**
- Consumes: `Artifact.root` / `MztabEntry.root`（Task 6）
- Produces: `_artifact_abs_path(job, root: str, rel: str) -> Path`（`mztab_tools` 内部ヘルパ）

- [x] **Step 1: Write the failing test**

`tests/test_mztab_tools.py` の先頭付近に最小の mzTab 本文を定義する。

```python
_MINIMAL_MZTAB = (
    "MTD\tmzTab-version\t2.0.0-M\n"
    "MTD\tassay[1]-ms_run_ref\tms_run[1]\n"
    "MTD\tassay[1]\tS1\n"
    "SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]\n"
    "SMF\t1\tnull\t786.6\t300.0\t100.0\n"
)
```

同ファイルに次のテストを追記する。

```python
def test_dataset_load_resolves_dataset_root_artifacts(tmp_path):
    """dataset_root 側の .pai2 は run_dir ではなく dataset_root から解決する。"""
    import json
    from pathlib import Path
    from lipidmix.handoff.schema import SCHEMA_VERSION
    from lipidmix.tools.mztab_tools import dataset_load
    from lipidmix.core import session_state

    run_dir = tmp_path / "runs" / "job1"
    (run_dir / "msdial").mkdir(parents=True)
    (run_dir / "msdial" / "AlignResult-1.mzTab").write_text(_MINIMAL_MZTAB, encoding="utf-8")
    pai2 = tmp_path / "S1_1.pai2"
    pai2.write_bytes(b"x")

    job_path = run_dir / "analysis-job.json"
    job_path.write_text(json.dumps({
        "schema": SCHEMA_VERSION, "job_id": "j1", "status": "completed",
        "created_at": "t", "updated_at": "t",
        "source": {"dataset_root": str(tmp_path), "input_count": 1},
        "software": {"name": "MS-DIAL", "version": "", "execution_mode": "console",
                     "method_file": "m.txt"},
        "project": {"omics": "lipidomics", "polarity": "negative", "measure": "peak_height"},
        "run_dir": str(run_dir),
        "primary_mztab_files": [{"path": str(Path("msdial") / "AlignResult-1.mzTab"),
                                 "polarity": "negative", "measure": "peak_height",
                                 "sha256": "", "validation": {}, "root": "run_dir"}],
        "artifacts": [{"path": "S1_1.pai2", "role": "sample_peaks", "format": "pai2",
                       "sha256": "", "root": "dataset_root"}],
        "execution": {"save_project": True, "timeout_s": 60},
        "warnings": [], "error": None,
    }, ensure_ascii=False), encoding="utf-8")

    session_state.session = session_state.AnalysisSession()
    out = dataset_load(job_path=str(job_path))
    assert "dataset_load 完了" in out
    ds = session_state.session.dataset
    assert ds.artifact_paths["sample_peaks"] == [str(pai2.resolve())]
```

- [x] **Step 2: Run test to verify it fails**

Run: `C:/Python314/python.exe -m pytest tests/test_mztab_tools.py -q -k "dataset_root_artifacts"`
Expected: FAIL（`artifact_paths` が `run_dir / "S1_1.pai2"` を指す）

- [x] **Step 3: Implement**

`lipidmix/tools/mztab_tools.py` の内部ヘルパ節に追加する。

```python
def _artifact_abs_path(job, root: str, rel: str) -> Path:
    """生成物の相対パスを、記録された出所ルートから絶対パスへ戻す。

    MS-DIAL Console は -o（run_dir）と生データフォルダの両方へ生成物を出すため、
    analysis-job.v2 は各エントリに root を持つ。run_dir 決め打ちで結合すると
    dataset_root 側のファイルが存在しないパスになる。
    """
    base = Path(job.dataset_root) if root == "dataset_root" else Path(job.run_dir)
    return (base / rel).resolve()
```

`_load_from_job` の mzTab 絶対パス算出を差し替える。

```python
    mztab_abs = _artifact_abs_path(job, getattr(entry, "root", "run_dir"), entry.path)
```

`_load_from_job` の artifact ループを差し替える。

```python
    for art in job.artifacts:
        abs_p = str(_artifact_abs_path(job, getattr(art, "root", "run_dir"), art.path))
        ds.artifact_paths.setdefault(art.role, []).append(abs_p)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_mztab_tools.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add lipidmix/tools/mztab_tools.py tests/test_mztab_tools.py
git commit -m "fix(mztab): 生成物の絶対パスを記録された出所ルートから解決する"
```

---

### Task 10: mzTab の assay custom CV term（注入順・バッチ）を読む

Console 出力にも GUI 出力にも `MTD assay[N]-custom[1] = [MS,MS:4000088,batch label,1]` と `custom[2] = [MS,MS:4000089,injection sequence label,N]` が実在する（実測）。まず `DatasetState` に取り込む。ここでは読むだけで、解析への配線は Task 11 で行う。

**Files:**
- Modify: `lipidmix/mztab/dataset_state.py`
- Test: `tests/test_dataset_state.py`

**Interfaces:**
- Consumes: なし
- Produces: `DatasetState.sample_assay_ids: list[str]`（`sample_names` と同じ並びの `"assay[N]"`）
- Produces: `ds.assay_metadata["assay[N]"]["run_order"]: int | None`
- Produces: `ds.assay_metadata["assay[N]"]["batch"]: str | None`
- Produces: `_parse_cv_term(value: str | None) -> tuple[str | None, str | None]`（accession, value）
- Produces: `_resolve_sample_names(abundance_cols, assay_metadata) -> tuple[list[str], list[str], list[str]]`（names, assay_ids, warnings）

- [x] **Step 1: Write the failing tests**

`tests/test_dataset_state.py` に追記する。

```python
_MZTAB_WITH_CUSTOM = textwrap.dedent("""\n    MTD	mzTab-version	2.0.0-M
    MTD	assay[1]	S_first
    MTD	assay[1]-ms_run_ref	ms_run[1]
    MTD	assay[1]-custom[1]	[MS,MS:4000088,batch label,B1]
    MTD	assay[1]-custom[2]	[MS,MS:4000089,injection sequence label,7]
    MTD	assay[2]	S_second
    MTD	assay[2]-ms_run_ref	ms_run[2]
    MTD	assay[2]-custom[1]	[MS,MS:4000088,batch label,B2]
    MTD	assay[2]-custom[2]	[MS,MS:4000089,injection sequence label,3]
    SFH	SMF_ID	SME_ID_REFS	exp_mass_to_charge	retention_time_in_seconds	abundance_assay[1]	abundance_assay[2]
    SMF	1	null	786.6	300.0	10.0	20.0
""")


@pytest.fixture
def mztab_with_custom(tmp_path):
    p = tmp_path / "AlignResult-1.mzTab"
    p.write_text(_MZTAB_WITH_CUSTOM, encoding="utf-8")
    return p


def test_parse_cv_term_extracts_accession_and_value():
    from lipidmix.mztab.dataset_state import _parse_cv_term
    assert _parse_cv_term("[MS,MS:4000089,injection sequence label,7]") == ("MS:4000089", "7")


def test_parse_cv_term_returns_none_for_garbage():
    from lipidmix.mztab.dataset_state import _parse_cv_term
    assert _parse_cv_term("not a cv term") == (None, None)
    assert _parse_cv_term("[MS,MS:4000089]") == (None, None)
    assert _parse_cv_term(None) == (None, None)


def test_build_dataset_state_reads_injection_order(mztab_with_custom):
    pr = parse_mztab(mztab_with_custom)
    ds = build_dataset_state(pr, mztab_with_custom.name, str(mztab_with_custom))
    assert ds.assay_metadata["assay[1]"]["run_order"] == 7
    assert ds.assay_metadata["assay[2]"]["run_order"] == 3


def test_build_dataset_state_reads_batch_label(mztab_with_custom):
    pr = parse_mztab(mztab_with_custom)
    ds = build_dataset_state(pr, mztab_with_custom.name, str(mztab_with_custom))
    assert ds.assay_metadata["assay[1]"]["batch"] == "B1"
    assert ds.assay_metadata["assay[2]"]["batch"] == "B2"


def test_build_dataset_state_records_sample_assay_ids(mztab_with_custom):
    """sample_names と同じ並びで assay id を持つ。列順を変えずに紐付けるため。"""
    pr = parse_mztab(mztab_with_custom)
    ds = build_dataset_state(pr, mztab_with_custom.name, str(mztab_with_custom))
    assert ds.sample_names == ["S_first", "S_second"]
    assert ds.sample_assay_ids == ["assay[1]", "assay[2]"]


def test_build_dataset_state_without_custom_terms_has_none(mztab_file):
    """custom[] を持たないファイルでは None のままにする（既定値をでっち上げない）。"""
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert ds.assay_metadata["assay[1]"].get("run_order") is None
    assert ds.assay_metadata["assay[1]"].get("batch") is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q -k "cv_term or injection_order or batch_label or sample_assay_ids or without_custom"`
Expected: FAIL with `ImportError: cannot import name '_parse_cv_term'`

- [x] **Step 3: Implement**

`lipidmix/mztab/dataset_state.py` の正規表現定義群（`_ABUNDANCE_ASSAY_RE` の下）に追加する。

```python
# assay[N]-custom[M] が運ぶ CV term。実データ（MS-DIAL の Console 出力・GUI 出力の
# 両方）で確認済みの形: [MS,MS:4000089,injection sequence label,3]
_CV_TERM_RE = re.compile(r"^\[([^\]]*)\]$")
_ACCESSION_INJECTION_ORDER = "MS:4000089"   # injection sequence label
_ACCESSION_BATCH = "MS:4000088"             # batch label


def _parse_cv_term(value: str | None) -> tuple[str | None, str | None]:
    """`[CV,ACCESSION,name,value]` から (accession, value) を取り出す。

    形が違えば (None, None) を返す。**推測で埋めない**——注入順やバッチを
    でっち上げると、ドリフト補正と交絡判定が黙って嘘の前提で走る。
    """
    m = _CV_TERM_RE.match((value or "").strip())
    if not m:
        return None, None
    parts = [p.strip() for p in m.group(1).split(",")]
    if len(parts) < 4:
        return None, None
    return parts[1], parts[3]
```

`DatasetState.__init__` の `self.assay_metadata` の直後に追加する。

```python
        # sample_assay_ids: sample_names と同じ並びの "assay[N]"。
        # assay_metadata（注入順・バッチ）へ列順を崩さず紐付けるために持つ。
        self.sample_assay_ids: list[str] = []
```

`build_dataset_state` の assay メタデータ収集ループを差し替える。

```python
    meta = parse_result.get("metadata", {})
    for k, v in meta.items():
        m = _ASSAY_SUFFIX_RE.match(k)
        if m:
            aid = f"assay[{m.group(1)}]"
            suffix = m.group(2)
            ds.assay_metadata.setdefault(aid, {})[suffix] = v
            # custom[N] は CV term を運ぶ。注入順とバッチは解析の前提を変えるので
            # 正規化したキーへ写しておく（生の suffix も残す）。
            if suffix.startswith("custom["):
                accession, term_value = _parse_cv_term(v)
                if accession == _ACCESSION_INJECTION_ORDER:
                    try:
                        ds.assay_metadata[aid]["run_order"] = int(str(term_value).strip())
                    except (TypeError, ValueError):
                        ds.assay_metadata[aid]["run_order"] = None
                elif accession == _ACCESSION_BATCH:
                    ds.assay_metadata[aid]["batch"] = term_value
            continue
        m = _ASSAY_BARE_RE.match(k)
        if m:
            aid = f"assay[{m.group(1)}]"
            # "name" キーで保持する。dataset_status など将来の呼び出し元は
            # assay_metadata[aid]["name"] を見れば表示名に到達できる。
            ds.assay_metadata.setdefault(aid, {})["name"] = v
```

`_resolve_sample_names` の呼び出しを差し替える。

```python
    ds.sample_names, ds.sample_assay_ids, name_warnings = _resolve_sample_names(
        ds.sample_names, ds.assay_metadata)
```

`_resolve_sample_names` の定義を差し替える。

```python
def _resolve_sample_names(abundance_cols: list[str], assay_metadata: dict) -> tuple[list[str], list[str], list[str]]:
    """abundance_assay[N] 列名を assay[N] の表示名へ解決する。

    戻り値: (sample_names, assay_ids, warnings)。assay_ids は sample_names と
    同じ並びで、注入順・バッチを列順を崩さずに引くために返す。

    表示名が無い assay は列識別子のままフォールバックする——意味のある名前が
    無いより、一意で追跡可能な旧識別子を残すほうが安全。
    表示名が複数 assay で重複する不正ファイルは、置き換え自体は行いつつ
    warning を返す（例外にはしない。読み込み自体を止めるほどではなく、
    群選択が曖昧になり得ることだけ呼び出し元に伝えれば足りる）。
    """
    resolved: list[str] = []
    assay_ids: list[str] = []
    cols_by_name: dict[str, list[str]] = {}
    for col in abundance_cols:
        m = _ABUNDANCE_ASSAY_RE.search(col)
        name = None
        aid = ""
        if m:
            aid = f"assay[{m.group(1)}]"
            name = (assay_metadata.get(aid) or {}).get("name")
        resolved_name = name if name else col
        resolved.append(resolved_name)
        assay_ids.append(aid)
        cols_by_name.setdefault(resolved_name, []).append(col)

    warnings = [
        f"assay 表示名が重複しています（{name!r}）: {', '.join(cols)}。"
        "sample_names での群選択が意図しないアッセイを指す恐れがあります。"
        for name, cols in cols_by_name.items() if len(cols) > 1
    ]
    return resolved, assay_ids, warnings
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add lipidmix/mztab/dataset_state.py tests/test_dataset_state.py
git commit -m "feat(mztab): assay の注入順・バッチ CV term を DatasetState に取り込む"
```

---

### Task 11: 注入順・バッチを前処理に配線する

`build_dataset_pp_inputs` は `run_order` を全件 `None`、`batch` をファイル名の日付から推定している。Task 10 で読んだ値を使う。ただし **MS-DIAL のバッチラベルは CSV インポートで指定しない限り全件 `1`** になるので、mzTab のバッチは**2 値以上あるときだけ**採用し、そうでなければ従来のファイル名日付にフォールバックする（既定値 1 を採ると、日付で分かれていた交絡が検出できなくなる）。注入順は MS-DIAL のファイル読み込み順に由来する既定値の可能性があるため、ドリフト補正を要求されたときに caveat で明示する。

**Files:**
- Modify: `lipidmix/analysis/dataset_analysis.py`
- Test: `tests/test_dataset_analysis.py`

**Interfaces:**
- Consumes: `ds.sample_assay_ids` / `ds.assay_metadata[aid]["run_order" | "batch"]`（Task 10）
- Produces: `sample_meta[name] = {"role", "batch", "batch_source", "run_order", "run_order_source"}`
  - `batch_source` は `"mztab_batch_label"` / `"filename_date"` / `None`
  - `run_order_source` は `"mztab_injection_sequence"` / `None`

- [x] **Step 1: Write the failing tests**

`tests/test_dataset_analysis.py` に追記する。

```python
def _ds_with_assay_meta(names, assay_meta):
    """sample_names と assay_metadata を持つ最小の DatasetState を作る。"""
    import numpy as np
    from lipidmix.mztab.dataset_state import DatasetState
    ds = DatasetState()
    ds.feature_matrix = np.arange(3 * len(names), dtype=float).reshape(3, len(names)) + 1.0
    ds.sample_names = list(names)
    ds.feature_ids = ["1", "2", "3"]
    ds.sample_assay_ids = [f"assay[{i + 1}]" for i in range(len(names))]
    ds.assay_metadata = assay_meta
    return ds


def test_build_dataset_pp_inputs_uses_mztab_injection_order():
    from lipidmix.analysis.dataset_analysis import build_dataset_pp_inputs
    ds = _ds_with_assay_meta(["s1", "s2"], {
        "assay[1]": {"run_order": 7, "batch": "1"},
        "assay[2]": {"run_order": 3, "batch": "1"},
    })
    _, _, _, _, sample_meta = build_dataset_pp_inputs(ds)
    assert sample_meta["s1"]["run_order"] == 7
    assert sample_meta["s2"]["run_order"] == 3
    assert sample_meta["s1"]["run_order_source"] == "mztab_injection_sequence"


def test_build_dataset_pp_inputs_prefers_filename_date_when_batch_is_constant():
    """MS-DIAL のバッチラベルは既定で全件 1。定数なら情報が無いのでファイル名日付を使う。"""
    from lipidmix.analysis.dataset_analysis import build_dataset_pp_inputs
    ds = _ds_with_assay_meta(["20220901_a", "20220902_b"], {
        "assay[1]": {"run_order": 1, "batch": "1"},
        "assay[2]": {"run_order": 2, "batch": "1"},
    })
    _, _, _, _, sample_meta = build_dataset_pp_inputs(ds)
    assert sample_meta["20220901_a"]["batch"] == "20220901"
    assert sample_meta["20220901_a"]["batch_source"] == "filename_date"


def test_build_dataset_pp_inputs_uses_mztab_batch_when_it_varies():
    from lipidmix.analysis.dataset_analysis import build_dataset_pp_inputs
    ds = _ds_with_assay_meta(["20220901_a", "20220901_b"], {
        "assay[1]": {"run_order": 1, "batch": "B1"},
        "assay[2]": {"run_order": 2, "batch": "B2"},
    })
    _, _, _, _, sample_meta = build_dataset_pp_inputs(ds)
    assert sample_meta["20220901_a"]["batch"] == "B1"
    assert sample_meta["20220901_b"]["batch"] == "B2"
    assert sample_meta["20220901_a"]["batch_source"] == "mztab_batch_label"


def test_run_dataset_preprocess_caveats_injection_order_provenance():
    """注入順が読めた場合は「読めない」ではなく出所の注意に変わる。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_preprocess
    ds = _ds_with_assay_meta(["s1", "s2"], {
        "assay[1]": {"run_order": 1, "batch": "1"},
        "assay[2]": {"run_order": 2, "batch": "1"},
    })
    *_, report = run_dataset_preprocess(ds, {"normalize": "none", "drift_correct": True,
                                             "impute": "half_min"})
    caveats = " ".join(report["caveats"])
    assert "読み取っていない" not in caveats
    assert "ファイル読み込み順" in caveats
```

既存の `test_run_dataset_preprocess_warns_no_run_order` を次のように書き換える（文面が変わるため）。

```python
def test_run_dataset_preprocess_warns_no_run_order():
    """注入順を持たない mzTab では drift_correct は必ず skipped になる。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_preprocess
    ds = _ds_with_assay_meta(["s1", "s2"], {"assay[1]": {}, "assay[2]": {}})
    *_, report = run_dataset_preprocess(ds, {"normalize": "none", "drift_correct": True,
                                             "impute": "half_min"})
    assert any("注入順" in c for c in report["caveats"])
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_analysis.py -q`
Expected: FAIL（`run_order` が None のまま、`run_order_source` キーが無い）

- [x] **Step 3: Implement**

`lipidmix/analysis/dataset_analysis.py` の `build_dataset_pp_inputs` の `sample_meta` 構築部（`sample_meta: dict = {}` から `return` まで）を差し替える。

```python
    # 注入順とバッチは mzTab の MTD assay[N]-custom[...] が運ぶ
    # （MS:4000089 injection sequence label / MS:4000088 batch label）。
    # 実データの Console 出力・GUI 出力の両方に存在することを確認済み。
    assay_ids = list(getattr(ds, "sample_assay_ids", []) or [])
    assay_meta = getattr(ds, "assay_metadata", {}) or {}

    def _assay_field(index: int, key: str):
        aid = assay_ids[index] if index < len(assay_ids) else None
        return (assay_meta.get(aid) or {}).get(key) if aid else None

    mztab_batches = [_assay_field(i, "batch") for i in range(len(sample_names))]
    # MS-DIAL のバッチラベルは CSV インポートで指定しない限り全件 "1" になる。
    # 定数のラベルは情報を持たないので、その場合はファイル名日付の推定に戻す。
    # 定数を採ると、日付で分かれていた交絡が検出できなくなる。
    use_mztab_batch = len({b for b in mztab_batches if b is not None}) > 1

    sample_meta: dict = {}
    for i, name in enumerate(sample_names):
        m = _DATE_RE.search(name)
        if use_mztab_batch and mztab_batches[i] is not None:
            batch, batch_source = mztab_batches[i], "mztab_batch_label"
        elif m:
            batch, batch_source = m.group(1), "filename_date"
        else:
            batch, batch_source = None, None

        run_order = _assay_field(i, "run_order")
        sample_meta[name] = {
            "role": roles.get(name, "sample"),
            "batch": batch,
            "batch_source": batch_source,
            "run_order": run_order,
            "run_order_source": "mztab_injection_sequence" if run_order is not None else None,
        }
    return matrix, sample_names, feature_names, roles, sample_meta
```

`run_dataset_preprocess` の `run_order` 生成行を差し替える。

```python
    run_order = {n: sample_meta[n]["run_order"] for n in sample_names}
```

`run_dataset_preprocess` の末尾にある無条件 caveat（「現在の実装は mzTab-M から注入順（run order）を読み取っていないため…」の `report.setdefault("caveats", []).append(...)`）を、次に差し替える。

```python
    has_run_order = any(sample_meta[n]["run_order"] is not None for n in sample_names)
    if not has_run_order:
        report.setdefault("caveats", []).append(
            "この mzTab-M は注入順（MTD assay[N]-custom[...] の "
            "MS:4000089 injection sequence label）を持たないため、QC ドリフト補正は"
            "実施できません。注入順に依存する品質評価が必要なら ARF 経路"
            "（arf_preprocess）を使ってください。")
    elif recipe.get("drift_correct"):
        report.setdefault("caveats", []).append(
            "注入順は mzTab-M の injection sequence label から取得しました。"
            "MS-DIAL は CSV インポートで実注入順を与えない場合、**ファイル読み込み順**を"
            "そのまま注入順として書き出します。QC の挿入位置（drift_correct の "
            "qc_interspersion）で妥当性を確認してください。")
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_analysis.py tests/test_dataset_state.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add lipidmix/analysis/dataset_analysis.py tests/test_dataset_analysis.py
git commit -m "feat(dataset): 注入順とバッチを mzTab から読み前処理に配線する"
```

---

### Task 12: RDKit 不在を明示する

実データの InChIKey は **401/714 すべてが `smiles_derived`**、`database_identifier` 由来は 0 件だった（Console 出力でも 162/271 が全て `smiles_derived`）。`derive_inchikey` は `except (ImportError, Exception): pass` で RDKit 不在を黙って握り潰すため、RDKit の無い環境では InChIKey が 0 件になり `dataset_export_differential` が「InChIKey が付いた特徴が 0 件」で書き出しを拒否する。下流への受け渡しが警告なしで環境依存に落ちる。

**Files:**
- Modify: `lipidmix/mztab/identity.py`
- Modify: `lipidmix/mztab/dataset_state.py`
- Test: `tests/test_dataset_state.py`

**Interfaces:**
- Consumes: `_resolve_sample_names` の戻り値 `name_warnings`（Task 10）
- Produces: `rdkit_available() -> bool`（`lipidmix/mztab/identity.py`）
- Produces: `ds.inchikey_coverage["rdkit_available"]: bool`

- [x] **Step 1: Write the failing tests**

```python
def test_rdkit_available_reports_bool():
    from lipidmix.mztab.identity import rdkit_available
    assert isinstance(rdkit_available(), bool)


def test_inchikey_coverage_reports_rdkit_availability(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert "rdkit_available" in ds.inchikey_coverage


def test_dataset_state_warns_when_rdkit_missing(mztab_file, monkeypatch):
    """RDKit が無いと SMILES 由来の InChIKey が全滅し、export が拒否される。

    実データでは InChIKey の 100% が smiles_derived だった。黙って 0 件に
    なると「同定が無いデータ」と誤読される。
    """
    monkeypatch.setattr("lipidmix.mztab.identity.rdkit_available", lambda: False)
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert ds.inchikey_coverage["rdkit_available"] is False
    assert any("RDKit" in w for w in ds.validation_result["warnings"])
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q -k "rdkit"`
Expected: FAIL with `ImportError: cannot import name 'rdkit_available'`

- [x] **Step 3: Implement**

`lipidmix/mztab/identity.py` の末尾に追加する。

```python
def rdkit_available() -> bool:
    """RDKit で SMILES / InChI から InChIKey を導出できるかを返す。

    derive_inchikey は RDKit 不在を黙って握り潰す（同定が無いだけの特徴と
    区別せずに None を返す）。実データでは InChIKey の 100% が SMILES 由来
    だったため、RDKit が無いと下流へ渡す背景集合が丸ごと空になる。
    その差を呼び出し側が見えるようにするための問い合わせ口。
    """
    try:
        from rdkit import Chem  # noqa: F401
        from rdkit.Chem.inchi import InchiToInchiKey  # noqa: F401
    except ImportError:
        return False
    return True
```

`lipidmix/mztab/dataset_state.py` の identity import 行を差し替える。

```python
from lipidmix.mztab import identity as mztab_identity
from lipidmix.mztab.identity import derive_inchikey
```

`build_dataset_state` の `inchikey_coverage` 構築を差し替える。

```python
    with_ik = sum(v for k, v in by_source.items() if k != "none")
    has_rdkit = mztab_identity.rdkit_available()
    ds.inchikey_coverage = {
        "total_features": len(smf_rows),
        "with_inchikey": with_ik,
        "by_source": by_source,
        "rdkit_available": has_rdkit,
    }
```

`_resolve_sample_names` の呼び出しで `name_warnings` を受けた直後（`if name_warnings:` の前）に追加する。

```python
    if not has_rdkit:
        # RDKit 不在は「同定が無い」と見分けがつかない形で InChIKey を全滅させる。
        # 実データでは InChIKey の 100% が SMILES 由来だった。
        name_warnings.insert(0, (
            "RDKit が利用できないため、SMILES / InChI からの InChIKey 導出が"
            "行われていません。mzTab-M の database_identifier が InChIKey 形式で"
            "無い場合、InChIKey は 0 件になり dataset_export_differential は"
            "書き出しを拒否します（『同定が無い』のではなく『導出できていない』）。"))
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add lipidmix/mztab/identity.py lipidmix/mztab/dataset_state.py tests/test_dataset_state.py
git commit -m "feat(mztab): RDKit 不在を inchikey_coverage と warning で明示する"
```

---

### Task 13: 全体テストと記録の更新

全タスク完了後に全体を通し、CLAUDE.md のテスト件数と開発ログを実測に合わせる。

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/HISTRY.md`
- Modify: `docs/task.md`

**Interfaces:**
- Consumes: Task 1〜12 のすべて
- Produces: なし（記録のみ）

- [x] **Step 1: 全体テストを実行して件数を実測する**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS（0 failed）。出力末尾の `N passed` の N を控える。

- [x] **Step 2: 登録面が変わっていないことを確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_server_registration.py tests/test_workflow_docs.py tests/test_package_layout.py -q`
Expected: PASS。この計画ではツールもリソースも増減していないので、登録数・`ToolAnnotations`・ワークフロー文書の照合はすべて現状のまま通るはず。落ちたらツールを増やしてしまっている。

- [x] **Step 3: CLAUDE.md のテスト件数を実測値に直す**

`CLAUDE.md` の「- 全 **762 件**（…」の 762 を Step 1 で控えた N に置き換える。`unittest discover` 側の実測値（現在 580）も再測して直す。

Run: `C:/Python314/python.exe -m unittest discover -s tests -t . 2>&1 | tail -3`

- [x] **Step 4: 開発ログと進捗を更新する**

`docs/HISTRY.md` の先頭に新しいエントリを追加する。書く内容:

- 実施した 12 タスクの一覧と、それぞれが直した実測上の症状
- `analysis-job` を v1 → v2 に上げたこと、v1 は読めるままにしたこと
- `sample_meta` に `run_order_source` / `batch_source` が入り、mzTab のバッチラベルは**2 値以上あるときだけ**採用する規則にしたこと（既定の全件 "1" を採るとファイル名日付で検出できていた交絡を落とすため）
- 注入順は MS-DIAL のファイル読み込み順に由来し得るため、drift_correct 要求時に出所の caveat を出すようにしたこと
- テスト件数 762 → N

`docs/task.md` の「Console 適合の是正（実走で確定）」「mzTab-M の注入順・バッチを読む」を DONE にする。残件（`.qa.tsv` の読み取り、Console 層の設計指摘8件、DatasetState 経路の図保存未対応、significance 判定ロジックの重複）は TODO のまま残す。

- [x] **Step 5: Commit**

```bash
git add CLAUDE.md docs/HISTRY.md docs/task.md
git commit -m "docs: Console 適合の是正を記録しテスト件数を実測に合わせる"
```

---

## 実機での最終確認（任意・ユーザーの承認が要る）

すべてのタスクが green になったら、実 Console で 1 回だけ通しを再実行して回帰が無いことを確かめられる。**MS-DIAL の実行はデータフォルダへの書き込みを伴うので、ユーザーの明示的な承認を得てから行うこと。**

手順:

1. 生データを 1 種類の拡張子だけにしたフォルダを用意する（`.wiff2` を混ぜない）。
2. `MSDIAL_EXE` にラボの Console 実行体を設定する: `C:\Users\yuu18\source\repos\MsdialWorkbench\tests\MSDIAL5\MsdialCoreTestApp\bin\Debug\net48\MSDIALCUI.exe`
3. `console_plan` → `console_run` → `dataset_load(job_path=...)` → `dataset_status` → `dataset_preprocess` を順に実行する。

期待する結果:

- `console_run` の `warnings` に「.pai2 が 1 つも生成されていません」が**出ない**
- `console_status` の `artifacts` に `sample_peaks` / `msms_evidence` / `spot_catalog` / `chromatogram` / `peak_matrix_source` が並び、`role="unknown"` がほぼ消えている
- `dataset_status` の `samples` が assay 表示名で並ぶ
- `dataset_preprocess` の caveat が「注入順を読み取っていない」ではなく、注入順の出所に関する注意に変わっている

## 実行記録（2026-09-03）

- 開始時点: `feat/dataset-analysis-layer`、`3171aa4`。Python 3.14 の全体テストは **766 passed / 750 subtests passed**。既存の `preprocessing.py` の `Mean of empty slice` 警告が1件。
- Task 1 の flush 回帰テストは、親のテキストバッファを共有する `stdout.write` から、子の書き込みを再現する `os.write(stdout.fileno(), ...)` へ補正する。
- Task 2 の実行体チェックは `console_run` の実行直前にも適用し、保存済みジョブや環境変数変更による迂回を防ぐ。
- Task 6 は v1 読み込み後の再保存も v2 にする。Task 7 は全体テストを維持するため本番呼び出しも新しい辞書引数へ移行する。
- Task 8 はタイムアウト時の生成物保持（生成物があれば `partial`）、実行オプションの検証、`console_status` の生成物・出所ルート表示を補う。
- Task 11 は人工データでドリフト補正が実際に適用されることと ARF 経路との数値一致も確認する。
- Task 13 は公開説明 `USAGE.md` も更新する。`docs/HISTRY.md` と `docs/task.md` はローカル記録の扱いを維持し、追跡対象に追加しない。
- 同時作業の `fc31c79` で `CLAUDE.md` は「テスト件数を転記せず pytest 出力を正準にする」規約へ更新された。Task 13 の実測は実施し、件数は本記録・HISTRY に残す。CLAUDE へ固定件数を戻さない。
- 任意の実 Console 再実行は今回の自動検証には含めない。

### 実行時の判断と、誤っていた場合の影響

| 順 | 判断 | 誤っていた場合の影響 |
|---|---|---|
| 1 | 指定された既存 feature ブランチで実行し、別 worktree・merge・push は作業に含めない。 | 同時作業との競合があり得るため、担当ファイルを限定した commit と差分確認が必要。 |
| 2 | HISTRY/task の ignore を維持し、ローカル記録として更新する。 | これらの記録は clone 先へ伝わらない。 |
| 3 | flush 回帰テストは os.write で子の fd 書き込みを再現する。 | テスト方法が実動作と異なる場合、ログ順序の回帰を取り逃がす。 |
| 4 | role を持つ .msp2.dbs もハッシュ対象とし、unknown/raw のみ省略する。 | 大きなキャッシュのハッシュに時間がかかる。 |
| 5 | v1 を読んだ後の再保存も v2 とする。 | v1 専用の外部 reader は再保存したジョブを読めない可能性がある。 |
| 6 | Task7 で呼出側を新APIへ最小移行し、Task8 で二重ルートを接続する。 | タスクごとの変更範囲が計画例と少し異なる。 |
| 7 | タイムアウト時も生成物を保存し、あれば partial、なければ failed。status には role/root/options を含める。 | partial を扱わない外部クライアントには状態分岐の追加が必要。 |
| 8 | save_project は bool、timeout_s は bool を除く正の int として保存前・実行前に検証する。 | float 秒など従来明示されていない入力は拒否される。 |
| 9 | 基準は計画中の762でなく開始時実測766とする。任意の実Console再実行は含めない。 | 実機上の回帰確認は別途残る。 |
| 10 | USAGE.md も実装に合わせて更新し、新規 workflow/tool は追加しない。 | 公開説明の変更範囲が計画例より広がる。 |
| 11 | Task2–4 は単一担当で順次実装し、各タスクでRED/全体テスト/commit後に一括レビューする。 | 個別レビューと比較して、共通箇所の見落としがまとめて発見される。 |
| 12 | Task11 にQC補正の実適用とARF経路との数値一致テストを追加し、数値処理本体は変更しない。 | 人工データの一致だけでは実データ全般の妥当性は証明しない。 |
| 13 | 実行直前も現在のConsole実行体を確認し、確認済みパスを子プロセスへ渡す。 | 起動前のhelp呼出しの時間が追加される。 |
| 14 | Task5–6 は単一担当で順次実装し、各タスクでRED/全体テスト/commit後に一括レビューする。 | 共通項目の欠陥がレビュー段階でまとめて発見される。 |
| 15 | Task10→12→11 の順にし、独立したメタデータ追加10/12を一括レビューする。 | 実装順が計画の番号順と異なるが、依存関係は維持する。 |
| 16 | 同時作業で更新されたCLAUDEの規約を優先し、固定テスト件数を戻さずHISTRY/本記録へ実測を残す。 | CLAUDE単体では今回の実測件数を確認できない。 |
| 17 | v1ジョブの既定値は計画通り save_project=False/timeout_s=3600 を維持し、新規planだけTrue/21600とする。 | 古い保存ジョブには新しい既定値が自動適用されない。 |

### 完了時の実測（2026-09-04）

- `C:/Python314/python.exe -m pytest tests -q` → **844 passed / 754 subtests passed**
  （0 failed。既存の `preprocessing.py` の `Mean of empty slice` 警告 1 件のみ）。
  開始時 766 → Task 1〜12 で 782 → レビュー指摘の修正で 844。
- `-m unittest discover -s tests -t .` → 588（pytest 関数形式のファイルを拾わないため少ない）。
- 登録面（`test_server_registration.py` / `test_workflow_docs.py` / `test_package_layout.py` /
  `test_readme_links.py`）は PASS。ツール・リソースの増減なし。
- CLAUDE.md へのテスト件数の書き戻しは**しない**（`fc31c79` で「数量は書かず pytest 出力を正準に
  する」規約へ変わったため）。実測値は本節と `docs/HISTRY.md` 2026-09-04 に残す。
- Task 13 の一環として `USAGE.md` の Console 4 ツールと `dataset_load` / `dataset_preprocess` の
  説明を実装に合わせて更新した。
- 実 Console での再実行による回帰確認は未実施（データフォルダへ書き込むためユーザーの承認が要る）。
