# DatasetState 解析層 + Phase 4 メタボロミクス準備 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DatasetState（mzTab-M ロード後の正準モデル）に対して前処理・PCA・差次的解析・エクスポートを実行できる MCP ツール群を追加し、`console_plan → console_run → dataset_load(job_path=...) → dataset_preprocess → dataset_pca → dataset_differential → dataset_export_differential` というメタボロミクス/リピドミクス共通の解析パイプラインを完成させる。

**Architecture:** ARF 経路（`arf_preprocess` → `arf_pca_preprocessed` → `arf_differential` → `arf_export_differential`）と**同じ純関数群を共有し、同じ結果が出る**ことを設計の中心に置く。そのために:

1. `run_pca` を `lipidmix/arf/reader.py` から `lipidmix/analysis/pca.py` へ移す（形式非依存の数値処理は `analysis/` に置くという CLAUDE.md の層序を守るため。`analysis/` が `arf/` を import する逆転を作らない）。
2. エクスポート契約（列定義・contract_version・数値書式）を `lipidmix/analysis/export_contract.py` へ切り出し、ARF 側と DatasetState 側の両方が同一定義を参照する。
3. 新規 `lipidmix/analysis/dataset_analysis.py` が「DatasetState → `analysis/` の純関数」の変換（**転置**とロール推定）だけを担い、MCP ツール層 `lipidmix/tools/dataset_analysis_tools.py` に純ロジックを提供する。
4. ARF ツールの**外形は変えない**（`run_pca` の移動と契約定数の import 元変更のみ。既存テストで回帰を縛る）。

**Tech Stack:** Python 3.14、NumPy、scikit-learn、既存 `lipidmix/analysis/preprocessing.py`（`detect_sample_roles` / `preprocess` / `drop_samples_by_role`）、`lipidmix/analysis/differential.py`（`two_group_test` / `add_fdr` / `summarize_two_group` / `volcano_data`）、FastMCP、`lipidmix/mztab/dataset_state.py`（DatasetState）

**Spec:** `docs/superpowers/specs/2026-09-02-single-repo-msdial-omics-design.md` §18 Phase 3〜Phase 4

---

## 改訂履歴

**2026-09-03 改訂（レビュー反映）。** 初版は `preprocessing.py` の API を推測で書いており、そのままでは実装できなかった。実コードを読んで確定した内容に差し替えたほか、以下を変更した。

| 変更 | 理由 |
|---|---|
| 新 Task 1 に `run_pca` の `analysis/pca.py` 抽出を追加 | 初版は `preprocessing.run_pca` が存在すると仮定していた。実体は `arf/reader.py`。`analysis/` から `arf/` を import すると層序が逆転する |
| 「実 API 早見表」を追加 | 初版の関数名・シグネチャ・戻り値がすべて実体と食い違っていた。推測を持ち込ませないため、確認済みの事実を1箇所に固定する |
| `dataset_export_differential` を既存契約準拠に変更 | 初版の 5 列 TSV は下流（massbank-context `load_differential`）が読めず、かつ `pval`/`padj`/`significant` は `two_group_test` に存在しないキーで全行空欄になった |
| `dataset_differential` の戻り値を summary + top のみに変更 | 初版は全特徴量の `results` を戻り値に載せていた。CLAUDE.md の戻り値肥大禁止に違反し、`arf_differential` の設計（全量はセッション保持）と非対称 |
| annotations を全件 READ_ONLY / LOCAL_WRITE に修正 | `tests/test_tool_annotations.py` の冒頭が「セッション状態の更新は副作用に数えない」と明記している。ARF の同一操作も READ_ONLY |
| 失敗時の封筒を `missing_state` の乱用から分離 | 初版は前処理失敗・PCA 次元不足まで `missing_state("dataset", ["dataset_load"])` に潰していた（自己参照でリプレイループ、真の原因も消える） |
| 旧 Task 6（未使用 `omics` 引数の追加）を削除 | 計画書自身が「使わない」「Console のモードは取得方式で決まるので軸が違う」と書いていた。`AnalysisJob.omics` が既に情報を保持しており失われるものは無い |
| 旧 Task 7（feature-qc.tsv）に配線 Step を追加 | 初版は Files/Interfaces で `console_run` からの呼び出しと `AnalysisJob.artifacts` 登録を約束しつつ、どの Step もそれをやらず未使用関数が残る構成だった |
| 新 Task 0（Console 実行層のブロッカー修正）を追加 | 既定データディレクトリ `<repo>/data` が `_assert_not_in_repo` に弾かれ、Goal の end-to-end 経路が現状通らない |
| ツール数・テスト件数の期待値を実測に修正 | 初版「46 → 50」は誤り（現在 47 登録なので 47 → 51）。テストのベースラインは 674 件で正しい |

---

## Global Constraints

- Python は `C:/Python314/python.exe` を使う。テストはリポジトリルートから `C:/Python314/python.exe -m pytest tests -q`。**ベースライン 674 件**。
- MCP ツールは必ず `structured_output=False` + `ToolAnnotations` を付ける。
- **annotations の判定基準**（`tests/test_tool_annotations.py` 冒頭の規約）: `readOnlyHint` は「サーバの**外**に副作用が無い」＝ファイルとネットワークを変更しない、の意。**サーバ自身のセッション状態の更新は副作用に数えない**。したがって `dataset_preprocess` / `dataset_pca` / `dataset_differential` は `READ_ONLY`（ARF の同一操作もそう）、ファイルを書く `dataset_export_differential` だけが `LOCAL_WRITE`。
- 戻り値は `json_payload()` または markdown 文字列（`json.dumps(..., indent=2)` 禁止）。
- **戻り値に全特徴量の行を載せない**。全量は `session.dataset` に保持し、戻り値は要約 + 上位のみ。`arf_differential` がこの形（`results` はセッション、payload は `summary` + `top`）。
- セッション状態の正準は `lipidmix.core.session_state` の `session` シングルトン。`from ... import session` のスナップショット束縛を書かない。
- `lipidmix.core.mcp_core` は leaf。ここから `tools_*` を import しない。
- **`lipidmix/analysis/` は `lipidmix/arf/` `lipidmix/mztab/` `lipidmix/tools/` を import しない**（形式非依存層。DatasetState は引数として受け取るだけで型 import もしない）。
- **前提状態が無いときだけ** `missing_state()` を返す。引数エラー・計算失敗・次元不足は `missing_state` ではない（`mcp_errors.py` の docstring がこの線引きを明記している）。`required_tools` に**自分自身を入れない**（リプレイループになる）。
- `server.py` への追加は `from lipidmix.tools.dataset_analysis_tools import *` 1 行。

### テスト件数の扱い

各 Task に「+N 件 / 累計 M 件」を書くが、これは**ガードであって目標ではない**。数字が合わないときに期待値を書き換えるのは禁止。まず「別のテストが壊れて収集数が変わった」を疑い、`-q` の内訳を見て原因を特定する。

---

## 実 API 早見表（推測禁止・ここが正解）

初版の破綻はすべてここの取り違えだった。実装中はこの表だけを信じ、迷ったら実ファイルを読む。

### `lipidmix/analysis/preprocessing.py`

```python
detect_sample_roles(sample_names, class_ids=None, config=None) -> dict[str, str]
# 戻り値: {sample_name: "sample" | "qc" | "blank"}

preprocess(matrix, sample_names, roles, run_order, recipe) -> tuple
# 引数は 5 個・すべて位置。matrix は (n_samples, n_features)。
#   roles     : detect_sample_roles の戻り値
#   run_order : dict {sample_name: int | None}（list ではない）
#   recipe    : 認識するキーは normalize / blank_min_fold / drift_correct /
#               max_qc_rsd / impute のみ。未知キーは黙って無視される
# 戻り値: (matrix, kept_idx, report)  ← dict ではなくタプル
#   kept_idx : 残った特徴量の**インデックス列**（bool マスクではない）
#   report   : {recipe_applied, steps, caveats, features_before,
#               features_after, features_removed_total}
# 注: 特徴量（列）は落とすが、サンプル（行）は落とさない

drop_samples_by_role(matrix, sample_names, roles, drop_roles=("blank",)) -> tuple
# 戻り値: (matrix, kept_names, dropped)   dropped は {role: [name, ...]}

normalize(matrix, method, roles=None, sample_names=None)
# method は "none" | "tic" | "median" | "pqn" のみ。他は ValueError
impute(matrix, method="half_min")
# method は "half_min" | "knn" | "column_mean" | "none"
```

### `lipidmix/analysis/pca.py`（Task 1 で新設。現 `lipidmix/arf/reader.py`）

```python
run_pca(matrix, n_components=None, log_transform=False) -> dict
# matrix は (n_samples, n_features)。StandardScaler + sklearn PCA。
# max_components = min(n_samples, n_features) < 2 なら ValueError を raise
# 戻り値のキー: components / explained_variance_ratio / singular_values / loadings
#   ★ "scores" ではなく "components"（(n_samples, n_components) の list）
```

### `lipidmix/analysis/differential.py`

```python
two_group_test(matrix, feature_names, group_labels, group_a, group_b,
               *, log2=True, pseudo_count=1.0, log_transform=False) -> list[dict]
# group_labels は各行（サンプル）のラベル列。group_a / group_b はそのラベル値（文字列）。
#   ★ サンプル名リストではない
# 戻り値: 特徴量ごとの dict の**リスト**。キーは feature / mean_a / mean_b / log2fc / t / p
#   ★ "pval" も "padj" も "significant" も無い
# log2fc は正なら group_b が高い

add_fdr(results) -> list[dict]          # 各要素に "q" を付与（破壊的・同じリストを返す）
summarize_two_group(results, q_thr=0.05, log2fc_thr=1.0, top_n=15) -> dict
# 戻り値: {n_tested, n_significant, n_up, n_down, top}
volcano_data(results, q_thr=0.05, log2fc_thr=1.0) -> list[dict]
# 戻り値: {feature, log2fc, neg_log10_p, sig}
```

### 既存エクスポート契約（`lipidmix/arf/tools.py`。Task 5 で `analysis/export_contract.py` へ移す）

```python
_EXPORT_COLUMNS = ["spot_id", "name", "name_source", "ontology", "inchikey",
                   "inchikey_source", "msi_level", "mz", "rt", "log2fc",
                   "p_value", "q_value", "mean_a", "mean_b", "significant"]
_DIFFERENTIAL_CONTRACT_VERSION = 1
_LOG2FC_SIGN = "positive means group_b is higher"
# 本文の前に `# key = value` 形式のメタ行ブロックを置く（contract_version /
# exported_at / group_a,n_a / group_b,n_b / log2fc_sign / 閾値 / 件数）
```

### `DatasetState`（`lipidmix/mztab/dataset_state.py`）

```python
feature_matrix   : np.ndarray | None   # (n_features, n_samples) ★転置が必要
sample_names     : list[str]           # abundance 列名（= assay 識別子）
feature_ids      : list[str]           # SMF_ID
feature_metadata : dict                # smf_id -> {name, mz, rt, inchikey,
                                       #            inchikey_source, smiles, inchi}
inchikey_coverage: dict                # {total_features, with_inchikey, by_source}
job_path         : str | None
artifact_paths   : dict[str, list[str]]
```

### 参考にすべき既存実装

- 前処理の呼び出し順・caveat の付け方 → `lipidmix/arf/tools.py` の `arf_preprocess`
- 差次的解析の payload 構造（全量セッション / 要約のみ返す）→ 同 `arf_differential`
- エクスポートの行組み立て・拒否条件 → 同 `arf_export_differential`

**この 3 つを読まずに Task 3〜5 を書き始めてはいけない。**

---

## ファイル構成

### 新規作成

| ファイル | 責務 |
|---|---|
| `lipidmix/analysis/pca.py` | `run_pca`（`arf/reader.py` から移設。形式非依存） |
| `lipidmix/analysis/export_contract.py` | 差次的エクスポートの列定義・contract_version・数値書式・メタ行組み立て |
| `lipidmix/analysis/dataset_analysis.py` | DatasetState ↔ `analysis/` の変換アダプタ（純関数・MCP 非依存） |
| `lipidmix/tools/dataset_analysis_tools.py` | MCP ツール 4 件 |
| `lipidmix/console/sidecar.py` | `feature-qc.tsv` 生成（Task 7） |
| `docs/workflow/dataset_analysis.md` | 腐敗防止テスト対象のワークフロー文書 |
| `tests/test_dataset_analysis.py` | `dataset_analysis.py` の単体テスト |
| `tests/test_dataset_analysis_tools.py` | MCP ツール層のテスト |
| `tests/test_export_contract.py` | 契約モジュールの単体テスト |

### 変更

| ファイル | 変更内容 |
|---|---|
| `lipidmix/arf/reader.py` | `run_pca` を移設し再エクスポートに置換。未使用になる sklearn import を削除 |
| `lipidmix/arf/tools.py` | 契約定数を `analysis/export_contract.py` から import |
| `lipidmix/mztab/dataset_state.py` | 解析状態フィールドを追加 |
| `lipidmix/console/job_manager.py` | `_assert_not_in_repo` の判定を修正（Task 0） |
| `lipidmix/console/output_collector.py` | 収集対象から運用ファイルを除外（Task 0） |
| `lipidmix/tools/console_tools.py` | 例外処理の穴を埋める + sidecar 呼び出し（Task 0, 7） |
| `server.py` | `from lipidmix.tools.dataset_analysis_tools import *` を追加 |
| `docs/workflow/arf.md` | `run_pca()` の参照先を `lipidmix/analysis/pca.py` に修正（Task 1） |
| `tests/test_server_registration.py` | `EXPECTED_TOOLS` に 4 件追加（47 → 51） |
| `tests/test_tool_annotations.py` | `EXPECTED_ANNOTATIONS` に 4 件追加 |
| `tests/test_workflow_docs.py` | `IN_SCOPE["dataset_analysis.md"]` に 4 ツール追加（合計 31 → 35） |

---

## Task 0: Console 実行層のブロッカー修正

> **このタスクは分離可能。** DatasetState 解析層（Task 1〜6）は `dataset_load(mztab_path=...)` 経路だけで完結するので、Task 0 を別ブランチに回しても Task 1 以降は進められる。ただし **Goal の end-to-end 経路（`console_plan` から始まる列）は Task 0 なしでは通らない**ので、Phase を「完成」と宣言する前に必要。
>
> 2026-09-03 のレビューで挙がった Console 層の他の指摘（`JOB_NOT_PLANNED` の入力バリデーションへの流用、`CONSOLE_ERROR_CODES` が未使用、`_job_id` の秒精度衝突、サイズのみの差分検出、`console_run` 成功パスのテスト欠如、`tests/test_console_runner.py` のセッション差し替えが fixture で復元されない）は、いずれも本計画の Goal を塞がないため**別課題として `docs/task.md` に起票する**。ここでは「黙って誤った成功を報告する」3 件だけを直す。

**Files:**
- Modify: `lipidmix/console/job_manager.py`
- Modify: `lipidmix/console/output_collector.py`
- Modify: `lipidmix/tools/console_tools.py`
- Test: `tests/test_console_runner.py`（既存ファイルに追記）

**Interfaces:**
- Produces: `console_plan` が既定データディレクトリ（`<repo>/data/...`）を受け付ける
- Produces: `console_run` が「出力ゼロ」を `NO_JOB_OUTPUT` として検出する
- Produces: `console_run` が予期しない例外でジョブを `running` に固着させない

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_console_runner.py` に追記:

```python
# ---------- Task 0: ブロッカー回帰 ----------

def test_create_job_allows_default_data_dir(tmp_path, monkeypatch):
    """既定データディレクトリ（<repo>/data 配下）を dataset_root にできる。"""
    from lipidmix.core import mcp_core
    from lipidmix.console.job_manager import create_job
    # リポジトリ直下の data/ を模す: BASE_DIR 配下だが DATA_DIR 配下でもある
    fake_repo = tmp_path / "repo"
    data_root = fake_repo / "data" / "study-001"
    data_root.mkdir(parents=True)
    method = tmp_path / "params.msdial"
    method.touch()
    monkeypatch.setattr(mcp_core, "BASE_DIR", fake_repo)
    monkeypatch.setenv("LIPIDMIX_DATA_DIR", str(fake_repo / "data"))

    job, job_path = create_job(dataset_root=data_root, method_file=method,
                              polarity="positive", measure="peak_height")
    assert job_path.is_file()


def test_create_job_still_rejects_source_tree(tmp_path, monkeypatch):
    """データディレクトリ外のリポジトリ内パスは従来どおり拒否する。"""
    from lipidmix.core import mcp_core
    from lipidmix.console.job_manager import create_job
    fake_repo = tmp_path / "repo"
    (fake_repo / "lipidmix").mkdir(parents=True)
    (fake_repo / "data").mkdir(parents=True)
    method = tmp_path / "params.msdial"
    method.touch()
    monkeypatch.setattr(mcp_core, "BASE_DIR", fake_repo)
    monkeypatch.setenv("LIPIDMIX_DATA_DIR", str(fake_repo / "data"))

    with pytest.raises(ValueError):
        create_job(dataset_root=fake_repo / "lipidmix", method_file=method,
                   polarity="positive", measure="peak_height")


def test_collect_artifacts_excludes_operational_files(tmp_path):
    """msdial.log / analysis-job.json は生成物として数えない。"""
    from lipidmix.console.output_collector import collect_artifacts, snapshot
    before = snapshot(tmp_path)
    (tmp_path / "msdial.log").write_text("CMD: fake\n")
    (tmp_path / "analysis-job.json").write_text("{}")
    mztabs, others = collect_artifacts(tmp_path, before)
    assert mztabs == []
    assert others == []


def test_console_run_reports_no_output(tmp_path, monkeypatch):
    """MS-DIAL が終了コード 0 で何も出力しなければ NO_JOB_OUTPUT になる。"""
    import json as _json
    from unittest.mock import MagicMock, patch
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(dataset_root=tmp_path, method_file=method,
                            polarity="positive", measure="peak_height")
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result):
        result = console_run(str(job_path))
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "NO_JOB_OUTPUT"
    assert load_job(job_path).status == "failed"


def test_console_run_unexpected_exception_marks_failed(tmp_path, monkeypatch):
    """exe が実在しない等の想定外例外でも封筒を返し、running に固着させない。"""
    import json as _json
    from unittest.mock import patch
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "definitely_not_here.exe")
    method = tmp_path / "params.msdial"
    method.touch()
    _, job_path = create_job(dataset_root=tmp_path, method_file=method,
                            polarity="positive", measure="peak_height")
    with patch("subprocess.run", side_effect=FileNotFoundError("exe not found")):
        result = console_run(str(job_path))
    parsed = _json.loads(result)
    assert parsed["error"]["code"] == "MSDIAL_EXE_NOT_FOUND"
    assert load_job(job_path).status == "failed"
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_console_runner.py -q
```

Expected: 5 FAIL（既存 32 件は PASS）

- [ ] **Step 3: `_assert_not_in_repo` を「ソースツリー内禁止」に絞る**

`lipidmix/console/job_manager.py`。現行は「リポジトリ配下すべてを拒否」で、既定データディレクトリ `<repo>/data` を巻き込んでいた。加えて、判定の再 raise を日本語メッセージの部分一致で決めており、文面を直すとガードが無効化する構造だった。両方を直す。

```python
def _assert_not_in_repo(path: Path) -> None:
    """path が「リポジトリ内かつデータディレクトリ外」でないことを確認する。

    ランディレクトリを版管理下のソースツリーに掘らせないためのガード。
    ただし既定のデータディレクトリは <repo>/data（data_config.DEFAULT_DATA_DIR）で
    あり、これはリポジトリ配下だが .gitignore 済みの運用領域なので許可する。
    「リポジトリ配下すべて禁止」にすると既定構成が丸ごと使えなくなる。
    """
    from lipidmix.core import mcp_core
    from lipidmix.core.data_config import get_data_dir

    target = path.resolve()
    repo_root = mcp_core.BASE_DIR.resolve()
    if not _is_relative_to(target, repo_root):
        return  # リポジトリ外。何も言わない
    data_dir = get_data_dir().resolve()
    if _is_relative_to(target, data_dir):
        return  # <repo>/data 配下は運用領域として許可
    raise ValueError(
        f"ランディレクトリをリポジトリのソースツリー内 ({repo_root}) に作成しようとしました。"
        f"dataset_root はリポジトリ外か、データディレクトリ ({data_dir}) 配下を"
        f"指定してください: {path}"
    )


def _is_relative_to(child: Path, parent: Path) -> bool:
    """child が parent 配下かを bool で返す（例外を制御フローに使わない）。"""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
```

> `except Exception: return` で `mcp_core` の import 失敗を飲み込む構造も外した。import できない状況はガードを黙って無効化する理由にならない（そのときは例外を上げて気付かせるべき）。

同ファイル冒頭の未使用 import（`os`、`sha256_file`）も外す。

- [ ] **Step 4: 運用ファイルを収集対象から外す**

`lipidmix/console/output_collector.py`。`run_msdial` が必ず書く `msdial.log` と、`update_status` が実行中に書き換える `analysis-job.json` が「生成物」として拾われていた。前者があるため `NO_JOB_OUTPUT` は到達不能だった。

```python
# ジョブ運用のためにランディレクトリへ書かれるファイル。MS-DIAL の生成物ではない。
# msdial.log は run_msdial が必ず作るため、除外しないと「出力ゼロ」を検出できない。
# analysis-job.json は実行中に status 遷移で書き換わるため、差分に混入する。
_OPERATIONAL_FILES = frozenset({"msdial.log", "analysis-job.json"})


def collect_artifacts(run_dir, before):
    after = snapshot(run_dir)
    new_or_changed = {
        rel: size
        for rel, size in after.items()
        if (rel not in before or before[rel] != size)
        and Path(rel).name not in _OPERATIONAL_FILES
    }
    ...  # 以降は現行のまま
```

- [ ] **Step 5: `console_run` の例外処理の穴を埋める**

`lipidmix/tools/console_tools.py`。`run_msdial` は `MSDIAL_EXE` が実在しないパスのとき `subprocess.run` から `FileNotFoundError` を受け、これが `Msdial*Error` のどれにも当たらず素通りしていた。素通りするとジョブが `running` のまま残り、`status != "planned"` ガードで永久に再実行できなくなる。

```python
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
```

`MsdialExeNotFoundError` は `EnvironmentError`（= `OSError`）の派生なので、**既存の `except MsdialExeNotFoundError` 節を `except OSError` より前に置く**こと（順序を逆にすると専用メッセージが出なくなる）。`import os` を関数冒頭ではなくモジュール先頭に足す。

- [ ] **Step 6: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_console_runner.py -q
```

Expected: 37 PASS（32 + 5）

- [ ] **Step 7: 全テストが壊れていないことを確認**

```
C:/Python314/python.exe -m pytest tests -q
```

Expected: 679 passed（674 + 5）

- [ ] **Step 8: コミット**

```
git add lipidmix/console/job_manager.py lipidmix/console/output_collector.py \
        lipidmix/tools/console_tools.py tests/test_console_runner.py
git commit -m "fix(console): 既定データディレクトリを許可し、出力ゼロと想定外例外を検出する"
```

---

## Task 1: `run_pca` を `lipidmix/analysis/pca.py` へ抽出

**Files:**
- Create: `lipidmix/analysis/pca.py`
- Modify: `lipidmix/arf/reader.py`
- Modify: `docs/workflow/arf.md`
- Test: `tests/test_analysis_pca.py`（新規）

**Interfaces:**
- Produces: `lipidmix.analysis.pca.run_pca(matrix, n_components=None, log_transform=False) -> dict`
- Preserves: `lipidmix.arf.reader.run_pca` が同じ関数を指す（module 属性としての束縛を残す）

> **なぜ再エクスポートを残すのか。** 3 つの現行依存がこの束縛を見ている。
> 1. `tests/test_factor_tools.py` と `tests/test_server_class_filter.py` が `patch.object(server.arf_reader, "run_pca", fake_run_pca)` でモジュール属性を差し替える。
> 2. `lipidmix/arf/tools.py:411,515` は**関数本体の中で** `from lipidmix.arf.reader import run_pca` するので、呼び出し時に `arf_reader.run_pca` を読む（＝ 1 のパッチが効く）。
> 3. `lipidmix/arf/reader.py:966` の CLI がモジュールスコープの名前として使う。
>
> `from lipidmix.analysis.pca import run_pca` を reader.py のトップレベルに置けば 3 つとも壊れない。逆に、この行を消したり `import lipidmix.analysis.pca as _pca` に変えると 1 が黙って効かなくなり、ARF テストが実物の sklearn PCA を走らせ始める。

- [ ] **Step 1: 失敗するテストを書く**

新規 `tests/test_analysis_pca.py`:

```python
import numpy as np
import pytest


def test_run_pca_importable_from_analysis():
    from lipidmix.analysis.pca import run_pca
    rng = np.random.default_rng(0)
    result = run_pca(rng.random((6, 10)), n_components=2)
    assert set(result) == {"components", "explained_variance_ratio",
                           "singular_values", "loadings"}
    assert len(result["components"]) == 6
    assert len(result["explained_variance_ratio"]) == 2


def test_arf_reader_reexports_the_same_object():
    """arf/reader.py の束縛が analysis/pca.py の関数と同一であること。

    既存 ARF テストが patch.object(server.arf_reader, "run_pca", ...) で
    差し替えるため、この束縛が消えるとモックが効かなくなる。
    """
    from lipidmix.analysis.pca import run_pca as canonical
    from lipidmix.arf.reader import run_pca as reexported
    assert reexported is canonical


def test_run_pca_rejects_degenerate_matrix():
    from lipidmix.analysis.pca import run_pca
    with pytest.raises(ValueError):
        run_pca(np.array([[1.0, 2.0, 3.0]]))  # n_samples=1
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_analysis_pca.py -v
```

Expected: FAIL（`ModuleNotFoundError: lipidmix.analysis.pca`）

- [ ] **Step 3: `lipidmix/analysis/pca.py` を作り、`run_pca` を移設する**

新規ファイルの冒頭:

```python
"""PCA（入力形式に依存しない数値処理）。

もとは lipidmix/arf/reader.py にあった run_pca をここへ移した。ARF と
DatasetState（mzTab-M）の双方から**同一実装**を呼ぶため、形式非依存の
analysis/ 層に置く。analysis/ が arf/ を import する層序の逆転を作らない。

lipidmix/arf/reader.py はトップレベルで同名を再エクスポートしている。
既存 ARF テストが patch.object(server.arf_reader, "run_pca", ...) で
モジュール属性を差し替えるため、その束縛は消してはならない。

依存は numpy + sklearn のみ。lipidmix.* を import しない leaf。
"""
from __future__ import annotations

import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
```

続けて `lipidmix/arf/reader.py` の `run_pca` 定義（`def run_pca(...)` から `}` で閉じる `return` まで）を**1 文字も変えずに**移す。標準化・log10 クリップ・`max_components < 2` の `ValueError` を含めて挙動を変えないこと。既存 ARF テストがこの挙動を縛っている。

- [ ] **Step 4: `arf/reader.py` を再エクスポートに置換**

1. `def run_pca(...)` の定義本体を削除する。
2. 他の `lipidmix.*` import と同じ位置（`from lipidmix.core.data_config import get_data_dir` の並び）に追加:

```python
from lipidmix.analysis.pca import run_pca  # 後方互換の再エクスポート。消すと ARF テストのモックが効かなくなる
```

3. 冒頭の `from sklearn.decomposition import PCA` と `from sklearn.preprocessing import StandardScaler` を削除する（`run_pca` 以外に使用箇所は無い。削除後に `grep -n "StandardScaler\|PCA(" lipidmix/arf/reader.py` が空になることを確認する）。

`plot_pca` / `plot_pca_scores_by_sample` / `get_pca_loading_features` は `pca_result["components"]` を読むだけなので変更不要。

- [ ] **Step 5: ワークフロー文書の参照先を直す**

`tests/test_workflow_docs.py` の `_defined_names()` は AST のトップレベル `FunctionDef` / `ClassDef` / `Assign` しか集めない。**`ImportFrom` は「定義」に数えない**ので、再エクスポートしただけでは `docs/workflow/arf.md` の参照が落ちる。

`docs/workflow/arf.md` の 2 箇所（`## arf_pca_preprocessed` と `## save_pca_figure` 相当の連鎖）を書き換える:

```
- 10. └─ lipidmix/arf/reader.py  run_pca()
+ 10. └─ lipidmix/analysis/pca.py  run_pca()
```

`grep -n "run_pca" docs/workflow/` で残りが無いことを確認する。

- [ ] **Step 6: 回帰を確認（このタスクの本番）**

ARF 側の PCA 経路が無傷であることを、モックを使うテストを含めて確かめる。

```
C:/Python314/python.exe -m pytest tests/test_analysis_pca.py tests/test_factor_tools.py tests/test_server_class_filter.py tests/test_workflow_docs.py tests/test_package_layout.py -q
```

Expected: 全 PASS。`test_factor_tools.py` / `test_server_class_filter.py` が落ちたら Step 4 の再エクスポートが機能していない。

- [ ] **Step 7: 全テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests -q
```

Expected: 682 passed（679 + 3）

- [ ] **Step 8: コミット**

```
git add lipidmix/analysis/pca.py lipidmix/arf/reader.py docs/workflow/arf.md tests/test_analysis_pca.py
git commit -m "refactor(analysis): run_pca を形式非依存の analysis/pca.py へ移す"
```

---

## Task 2: DatasetState に解析状態フィールドを追加

**Files:**
- Modify: `lipidmix/mztab/dataset_state.py`
- Test: `tests/test_dataset_state.py`（既存ファイルに追記）

**Interfaces:**
- Produces: `DatasetState.pp_matrix` / `.pp_sample_names` / `.pp_feature_names` / `.roles` / `.sample_meta` / `.preprocessing_recipe` / `.last_pca` / `.last_differential`
- 注意: `feature_matrix` は (n_features, n_samples) のまま変えない（既存テストが参照）。`pp_matrix` は転置済み (n_samples, n_features)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_dataset_state.py` に追記:

```python
def test_dataset_state_has_analysis_fields():
    from lipidmix.mztab.dataset_state import DatasetState
    ds = DatasetState()
    assert ds.pp_matrix is None
    assert ds.pp_sample_names == []
    assert ds.pp_feature_names == []
    assert ds.roles == {}
    assert ds.sample_meta == {}
    assert ds.preprocessing_recipe == {}
    assert ds.last_pca is None
    assert ds.last_differential is None
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_dataset_state.py::test_dataset_state_has_analysis_fields -v
```

Expected: FAIL（`AttributeError: 'DatasetState' object has no attribute 'pp_matrix'`）

- [ ] **Step 3: `DatasetState.__init__` 末尾に追記**

```python
        # --- 解析状態フィールド（dataset_preprocess / dataset_pca / dataset_differential が設定） ---
        # pp_matrix: 前処理済み行列。shape は (n_samples, n_features) — feature_matrix の転置。
        # ARF 側の session.arf.feature_matrix / pp_sample_names / pp_feature_names と
        # 同じ役割で、スロットだけが独立している。
        self.pp_matrix = None
        self.pp_sample_names: list[str] = []
        self.pp_feature_names: list[str] = []
        # roles: {sample_name: "sample"|"qc"|"blank"}。preprocess() と差次的解析の群構成で使う。
        self.roles: dict[str, str] = {}
        # sample_meta: {sample_name: {role, batch, batch_source}}。feature-qc.tsv の元。
        self.sample_meta: dict = {}
        self.preprocessing_recipe: dict = {}
        # last_pca / last_differential: 直近結果の全量。戻り値には要約だけを載せ、
        # 全量はここに置く（CLAUDE.md の戻り値肥大禁止）。
        # 現時点で読むのは dataset_export_differential のみ。図の保存ツール
        # （save_pca_figure / save_volcano_figure）は session.arf 側を見ており、
        # DatasetState 経路には未対応（次フェーズ）。
        self.last_pca = None
        self.last_differential = None
```

> 初版はここに `last_pca_plot  # save_pca_figure が参照` / `last_differential  # dataset_plot_volcano が参照` と書いていたが、`save_pca_figure` は `session.arf.last_pca_plot` を読み、`dataset_plot_volcano` は存在しない。**存在しない配線をコメントで主張しない。**

- [ ] **Step 4: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q
```

Expected: PASS

- [ ] **Step 5: 全テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests -q
```

Expected: 683 passed（682 + 1）

- [ ] **Step 6: コミット**

```
git add lipidmix/mztab/dataset_state.py tests/test_dataset_state.py
git commit -m "feat(dataset): DatasetState に解析状態フィールドを追加"
```

---

## Task 3: `dataset_analysis.py`（純ロジック層）

**Files:**
- Create: `lipidmix/analysis/dataset_analysis.py`
- Create: `tests/test_dataset_analysis.py`

**Interfaces:**
- Consumes: 「実 API 早見表」の `preprocessing` / `differential` / `pca` の各関数（**シグネチャはあの表のとおり**）
- Produces:
  - `build_dataset_pp_inputs(ds) -> (matrix, sample_names, feature_names, roles, sample_meta)` — matrix は (n_samples, n_features)
  - `run_dataset_preprocess(ds, recipe) -> (pp_matrix, pp_sample_names, pp_feature_names, roles, sample_meta, report)`
  - `run_dataset_pca(ds, n_components, log_transform) -> dict`
  - `run_dataset_differential(ds, group_a_samples, group_b_samples, ...) -> dict`
- **失敗の伝え方**: 前提が足りない/計算できない場合は `None` を返さず `PreconditionError` を上げる。呼び出し側（MCP ツール層）が理由を読んで適切な封筒に振り分ける。初版のように `None` へ潰すと理由が失われ、上位で `missing_state` に一括変換されてリプレイループになる。

- [ ] **Step 1: 失敗するテストを書く**

新規 `tests/test_dataset_analysis.py`:

```python
import numpy as np
import pytest

from lipidmix.mztab.dataset_state import DatasetState


def _make_ds(n_features=20, n_samples=8, with_blank=False):
    """最小限の DatasetState。ロールは dataset_analysis がサンプル名から推定する。"""
    rng = np.random.default_rng(42)
    ds = DatasetState()
    names = [f"ctrl_{i}" for i in range(n_samples // 2)]
    names += [f"treat_{i}" for i in range(n_samples - len(names))]
    if with_blank:
        names[-1] = "blank_1"
    ds.feature_matrix = rng.random((n_features, n_samples)) * 1000.0
    ds.sample_names = names
    ds.feature_ids = [f"f{i}" for i in range(n_features)]
    return ds


def _preprocessed(ds, recipe=None):
    from lipidmix.analysis.dataset_analysis import run_dataset_preprocess
    (ds.pp_matrix, ds.pp_sample_names, ds.pp_feature_names,
     ds.roles, ds.sample_meta, report) = run_dataset_preprocess(ds, recipe or {})
    return report


# ---------- build_dataset_pp_inputs ----------

def test_build_dataset_pp_inputs_transposes():
    from lipidmix.analysis.dataset_analysis import build_dataset_pp_inputs
    ds = _make_ds(n_features=20, n_samples=8)
    matrix, sample_names, feature_names, roles, sample_meta = build_dataset_pp_inputs(ds)
    assert matrix.shape == (8, 20)          # (n_samples, n_features)
    assert sample_names == ds.sample_names
    assert feature_names == ds.feature_ids
    assert set(roles) == set(ds.sample_names)
    assert set(sample_meta) == set(ds.sample_names)


def test_build_dataset_pp_inputs_detects_blank_role():
    from lipidmix.analysis.dataset_analysis import build_dataset_pp_inputs
    ds = _make_ds(with_blank=True)
    _, _, _, roles, _ = build_dataset_pp_inputs(ds)
    assert roles["blank_1"] == "blank"


def test_build_dataset_pp_inputs_rejects_empty_matrix():
    from lipidmix.analysis.dataset_analysis import (
        PreconditionError, build_dataset_pp_inputs,
    )
    ds = DatasetState()
    with pytest.raises(PreconditionError):
        build_dataset_pp_inputs(ds)


# ---------- run_dataset_preprocess ----------

def test_run_dataset_preprocess_keeps_feature_count():
    ds = _make_ds(n_features=20, n_samples=8)
    report = _preprocessed(ds)
    assert ds.pp_matrix.shape == (8, 20)
    assert len(ds.pp_feature_names) == 20
    assert report["features_before"] == 20


def test_run_dataset_preprocess_drops_blank_samples():
    """ブランクは背景除去の参照として使った後、解析行列から外す（ARF と同じ）。"""
    ds = _make_ds(n_features=20, n_samples=8, with_blank=True)
    report = _preprocessed(ds)
    assert "blank_1" not in ds.pp_sample_names
    assert report["excluded_from_matrix"]["blank"] == ["blank_1"]


def test_run_dataset_preprocess_warns_no_run_order():
    """mzTab-M に注入順が無いため drift_correct は必ず skipped になる。"""
    ds = _make_ds()
    report = _preprocessed(ds, {"drift_correct": True})
    assert report["steps"]["drift_correct"]["status"] == "skipped"
    assert any("注入順" in c for c in report["caveats"])


def test_run_dataset_preprocess_rejects_unknown_normalize():
    from lipidmix.analysis.dataset_analysis import PreconditionError
    ds = _make_ds()
    with pytest.raises(PreconditionError):
        _preprocessed(ds, {"normalize": "not_a_method"})


# ---------- run_dataset_pca ----------

def test_run_dataset_pca_requires_preprocess():
    from lipidmix.analysis.dataset_analysis import PreconditionError, run_dataset_pca
    ds = _make_ds()
    with pytest.raises(PreconditionError):
        run_dataset_pca(ds, n_components=2)


def test_run_dataset_pca_returns_scores_per_sample():
    from lipidmix.analysis.dataset_analysis import run_dataset_pca
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    result = run_dataset_pca(ds, n_components=2)
    assert len(result["explained_variance_ratio"]) == 2
    assert [s["name"] for s in result["scores"]] == ds.pp_sample_names
    assert "PC1" in result["scores"][0]


# ---------- run_dataset_differential ----------

def test_run_dataset_differential_summarizes():
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    result = run_dataset_differential(
        ds,
        group_a_samples=[n for n in ds.pp_sample_names if n.startswith("ctrl")],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )
    assert result["summary"]["n_tested"] > 0
    assert len(result["results"]) == 20
    assert "q" in result["results"][0]
    assert result["contract_version"] == 1
    assert result["log2fc_sign"] == "positive means group_b is higher"


def test_run_dataset_differential_reports_unknown_samples():
    """存在しないサンプル名を黙って捨てず、caveat で名指しする。"""
    from lipidmix.analysis.dataset_analysis import run_dataset_differential
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    result = run_dataset_differential(
        ds,
        group_a_samples=["ctrl_0", "ctrl_1", "ctrl_2", "nope_1"],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )
    assert any("nope_1" in c for c in result["caveats"])
    assert result["n_a"] == 3


def test_run_dataset_differential_rejects_small_groups():
    from lipidmix.analysis.dataset_analysis import (
        PreconditionError, run_dataset_differential,
    )
    ds = _make_ds(n_features=20, n_samples=8)
    _preprocessed(ds)
    with pytest.raises(PreconditionError):
        run_dataset_differential(ds, group_a_samples=["ctrl_0"],
                                 group_b_samples=["treat_0"])
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_dataset_analysis.py -v
```

Expected: FAIL（`ModuleNotFoundError: lipidmix.analysis.dataset_analysis`）

- [ ] **Step 3: `dataset_analysis.py` を実装する**

**実装前に `lipidmix/arf/tools.py` の `arf_preprocess` と `arf_differential` を読むこと。** 呼び出し順・caveat の文面・`drop_samples_by_role` の位置をそこに揃える。ARF と DatasetState で違う答えが出るのが最悪の結果になる。

```python
"""DatasetState と analysis/ の純関数群をつなぐアダプタ（MCP 非依存）。

DatasetState.feature_matrix は (n_features, n_samples)。analysis/ の関数は
すべて (n_samples, n_features) を期待するため、ここで転置する。
呼び出し側はこの転置を意識しなくてよい。

処理順は lipidmix/arf/tools.py の arf_preprocess と同一に保つ:
  blank_filter → normalize → drift_correct → qc_rsd_filter → impute
  → drop_samples_by_role(blank)
ARF 経路と DatasetState 経路で違う数字が出ないことが、この層の存在意義。

依存は lipidmix.analysis.* のみ。arf/ mztab/ tools/ を import しない
（DatasetState は引数として受け取るだけで型 import もしない）。
"""
from __future__ import annotations

import re

import numpy as np

from lipidmix.analysis import differential, preprocessing
from lipidmix.analysis.pca import run_pca

_DATE_RE = re.compile(r"(\d{8})")

# 差次的解析の群に混ぜてはいけないロール。
_NON_SAMPLE_ROLES = ("qc", "blank")

_CONTRACT_VERSION = 1
_LOG2FC_SIGN = "positive means group_b is higher"


class PreconditionError(Exception):
    """前提が満たされないことを、理由付きで呼び出し側へ返す。

    MCP ツール層がこの例外を捕らえ、`kind` を見て封筒を選ぶ:
      kind="missing_state"  → missing_state() エンベロープ（リプレイで回復可能）
      kind="bad_request"    → 引数エラー。リプレイしても直らない
    None を返して理由を捨てると、上位が一律 missing_state に変換して
    クライアントを無限リプレイに落とす。
    """

    def __init__(self, kind: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details or {}


def build_dataset_pp_inputs(ds):
    """DatasetState から preprocessing.preprocess() の引数を組む。

    Returns:
        matrix        : (n_samples, n_features) — feature_matrix の転置
        sample_names  : list[str]
        feature_names : list[str]（SMF_ID）
        roles         : {sample_name: "sample"|"qc"|"blank"} — preprocess の第3引数
        sample_meta   : {sample_name: {role, batch, batch_source}} — feature-qc.tsv 用
    """
    if ds.feature_matrix is None or not ds.sample_names or not ds.feature_ids:
        raise PreconditionError(
            "missing_state",
            "DatasetState に定量行列がありません。dataset_load を先に実行してください。",
        )

    matrix = np.asarray(ds.feature_matrix, dtype=float).T.copy()
    sample_names = list(ds.sample_names)
    feature_names = list(ds.feature_ids)

    # class_ids は mzTab-M に対応物が無いので渡さない（既定 None）。
    roles = preprocessing.detect_sample_roles(sample_names)

    sample_meta: dict = {}
    for name in sample_names:
        m = _DATE_RE.search(name)
        sample_meta[name] = {
            "role": roles.get(name, "sample"),
            "batch": m.group(1) if m else None,
            "batch_source": "filename_date" if m else None,
            # mzTab-M は注入順を持たない。ARF の sample_meta と同じキーを立てて
            # おき、値が None であることを下流（drift_correct）に伝える。
            "run_order": None,
        }
    return matrix, sample_names, feature_names, roles, sample_meta


def run_dataset_preprocess(ds, recipe: dict):
    """DatasetState の feature_matrix に前処理を適用する。

    recipe は preprocessing.preprocess() と同じキー（normalize / blank_min_fold /
    drift_correct / max_qc_rsd / impute）。未知キーは preprocess が無視する。

    Returns: (pp_matrix, pp_sample_names, pp_feature_names, roles, sample_meta, report)
    """
    matrix, sample_names, feature_names, roles, sample_meta = build_dataset_pp_inputs(ds)

    # preprocess の run_order は {name: int | None} の dict。mzTab-M には注入順が
    # 無いので全件 None になり、drift_correct は status="skipped" + caveat を返す。
    run_order = {n: None for n in sample_names}

    try:
        matrix, kept_idx, report = preprocessing.preprocess(
            matrix, sample_names, roles, run_order, recipe,
        )
    except ValueError as exc:
        # normalize の未知メソッド等。引数由来なのでリプレイでは直らない。
        raise PreconditionError(
            "bad_request", f"前処理レシピが不正です: {exc}", {"recipe": recipe},
        ) from exc

    pp_feature_names = [feature_names[i] for i in kept_idx]

    # ブランクは blank_filter の参照として使い終えたので解析行列から外す。
    # 残すと総強度が桁違いに低い行が PCA の PC1 を支配する（arf_preprocess と同じ理由）。
    # QC は残す——QC クラスタの締まり具合を PCA で見るのは品質確認の定番手段。
    matrix, pp_sample_names, dropped = preprocessing.drop_samples_by_role(
        matrix, sample_names, roles, drop_roles=("blank",),
    )
    report["excluded_from_matrix"] = dropped
    if dropped.get("blank"):
        report.setdefault("caveats", []).append(
            f"ブランク {len(dropped['blank'])} 件（{', '.join(dropped['blank'])}）は背景除去に"
            "使用後、解析行列（PCA/差次的解析）から除外しました。QC は PCA での品質確認の"
            "ため残しています。"
        )

    # 層別プール QC の警告（arf_preprocess と同じ材料）
    qc_strata = preprocessing.detect_qc_strata(sample_names, roles)
    if len(qc_strata) > 1:
        labels = ", ".join(sorted(s for s in qc_strata if s))
        report.setdefault("caveats", []).append(
            f"プールQC が層別（{len(qc_strata)} サブグループ"
            f"{f': {labels}' if labels else ''}）と検出されました。"
            "全 QC を1系列として扱うドリフト補正/RSD フィルタは近似です。"
        )
    report.setdefault("caveats", []).append(
        "mzTab-M は注入順（run order）を持たないため、QC ドリフト補正は実施できません。"
        "注入順に依存する品質評価が必要なら ARF 経路（arf_preprocess）を使ってください。"
    )

    return matrix, pp_sample_names, pp_feature_names, roles, sample_meta, report


def run_dataset_pca(ds, n_components: int = 5, log_transform: bool = False) -> dict:
    """DatasetState の pp_matrix に PCA を実行する。"""
    matrix = _require_pp_matrix(ds)
    n_samples, n_features = matrix.shape
    if min(n_samples, n_features) < 2:
        raise PreconditionError(
            "bad_request",
            f"PCA には 2 以上のサンプルと特徴量が必要です"
            f"（現在: サンプル={n_samples}, 特徴量={n_features}）。"
            "前処理のフィルタ閾値が厳しすぎる可能性があります。",
            {"n_samples": n_samples, "n_features": n_features},
        )

    pca = run_pca(matrix, n_components=n_components, log_transform=log_transform)
    components = np.asarray(pca["components"], dtype=float)
    n_pc = components.shape[1]

    scores = []
    for i, name in enumerate(ds.pp_sample_names):
        row = {"name": name, "role": ds.roles.get(name, "sample")}
        for pc in range(n_pc):
            row[f"PC{pc + 1}"] = round(float(components[i, pc]), 4)
        scores.append(row)

    return {
        "explained_variance_ratio": [round(float(v), 4)
                                     for v in pca["explained_variance_ratio"]],
        "scores": scores,
        "n_samples": n_samples,
        "n_features": n_features,
        "log_transform": log_transform,
        # loadings は特徴量数 × 主成分数で巨大になる。要約には載せず、
        # 呼び出し側がセッションに保持する分にだけ含める。
        "loadings": pca["loadings"],
    }


def run_dataset_differential(
    ds,
    group_a_samples: list[str],
    group_b_samples: list[str],
    *,
    q_threshold: float = 0.05,
    log2fc_threshold: float = 1.0,
    log_transform: bool = True,
    group_a_label: str = "group_a",
    group_b_label: str = "group_b",
) -> dict:
    """前処理済み DatasetState で 2 群比較を実行する。

    group_a_samples / group_b_samples は pp_sample_names に含まれるサンプル名。
    未知の名前・非 sample ロール・両群への重複指定はすべて caveat で名指しする
    （黙って落とすと群サイズが縮んだことに気付けない）。
    """
    matrix = _require_pp_matrix(ds)
    available = list(ds.pp_sample_names)
    caveats: list[str] = []

    idx_a, names_a = _resolve_group(group_a_samples, available, ds.roles,
                                    group_a_label, caveats)
    idx_b, names_b = _resolve_group(group_b_samples, available, ds.roles,
                                    group_b_label, caveats)

    overlap = sorted(set(names_a) & set(names_b))
    if overlap:
        raise PreconditionError(
            "bad_request",
            f"両群に同じサンプルが指定されています: {', '.join(overlap)}。"
            "群定義を見直してください。",
            {"overlap": overlap},
        )
    if len(idx_a) < 2 or len(idx_b) < 2:
        raise PreconditionError(
            "bad_request",
            f"群サイズ不足（{group_a_label}={len(idx_a)}, {group_b_label}={len(idx_b)}）: "
            "各群 n>=2 が必要です。群名の誤り、または前処理での試料脱落の可能性があります。",
            {"n_a": len(idx_a), "n_b": len(idx_b),
             "available_samples": available, "caveats": caveats},
        )

    # two_group_test はラベル列で群を切る（サンプル名リストではない）。
    # 両群のどちらにも属さない行は検定対象から外すため、行を抜いてラベルを組む。
    keep_idx = idx_a + idx_b
    sub_matrix = matrix[keep_idx, :]
    group_labels = [group_a_label] * len(idx_a) + [group_b_label] * len(idx_b)

    results = differential.two_group_test(
        sub_matrix, ds.pp_feature_names, group_labels,
        group_a_label, group_b_label, log_transform=log_transform,
    )
    results = differential.add_fdr(results)
    summary = differential.summarize_two_group(results, q_threshold, log2fc_threshold)
    volcano = differential.volcano_data(results, q_threshold, log2fc_threshold)

    n_tested = summary["n_tested"]
    if n_tested == 0:
        caveats.append(
            "検定可能な特徴が0件（全特徴で p=NaN）。群が空・分散0・または正規化で試料が"
            "NaN化した可能性があります。『有意0件』を『群間差なし』と解釈しないでください。")
    elif n_tested < 0.2 * len(ds.pp_feature_names):
        caveats.append(
            f"検定できた特徴は {n_tested}/{len(ds.pp_feature_names)} 件のみ（多くが p=NaN）。"
            "群内 n 不足・分散0・欠損が多い可能性があります（前処理の見直しを検討）。")
    if min(len(idx_a), len(idx_b)) < 4:
        caveats.append(
            f"小n（{group_a_label}={len(idx_a)}, {group_b_label}={len(idx_b)}）につき"
            "検出力が限られます。")
    caveats.append(
        f"log2FC の向き: 正なら {group_b_label} が高い（上昇）、負なら {group_a_label} が高い（低下）。")

    return {
        "kind": "two_group",
        "a": group_a_label,
        "b": group_b_label,
        "samples_a": names_a,
        "samples_b": names_b,
        "n_a": len(idx_a),
        "n_b": len(idx_b),
        "q_threshold": q_threshold,
        "log2fc_threshold": log2fc_threshold,
        "log_transform": log_transform,
        "contract_version": _CONTRACT_VERSION,
        "log2fc_sign": _LOG2FC_SIGN,
        "summary": summary,
        "caveats": caveats,
        # 全量。呼び出し側はこれをセッションに保持し、戻り値には載せない。
        "results": results,
        "volcano": volcano,
    }


# ---------- 内部ヘルパ ----------

def _require_pp_matrix(ds):
    if ds.pp_matrix is None:
        raise PreconditionError(
            "missing_state",
            "前処理済み行列がありません。dataset_preprocess を先に実行してください。",
        )
    return np.asarray(ds.pp_matrix, dtype=float)


def _resolve_group(requested, available, roles, label, caveats):
    """指定サンプル名を pp_sample_names の位置に解決し、落ちた分を caveat に残す。"""
    index_of = {name: i for i, name in enumerate(available)}
    idx: list[int] = []
    names: list[str] = []
    unknown: list[str] = []
    non_sample: list[str] = []
    for name in requested:
        if name not in index_of:
            unknown.append(name)
            continue
        role = roles.get(name, "sample")
        if role in _NON_SAMPLE_ROLES:
            non_sample.append(f"{name}({role})")
            continue
        idx.append(index_of[name])
        names.append(name)
    if unknown:
        caveats.append(
            f"{label} に指定されたサンプルのうち {len(unknown)} 件は前処理済み行列に"
            f"存在しないため除外しました: {', '.join(unknown)}。"
            "名前の誤り、または前処理で脱落した可能性があります。")
    if non_sample:
        caveats.append(
            f"{label} から QC/ブランクを除外しました: {', '.join(non_sample)}。"
            "群に混ぜると比較が壊れるため、生体試料のみで検定します。")
    return idx, names
```

- [ ] **Step 4: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_dataset_analysis.py -v
```

Expected: 12 PASS

- [ ] **Step 5: 全テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests -q
```

Expected: 695 passed（683 + 12）

- [ ] **Step 6: コミット**

```
git add lipidmix/analysis/dataset_analysis.py tests/test_dataset_analysis.py
git commit -m "feat(analysis): DatasetState アダプタ dataset_analysis.py を追加"
```

---

## Task 4: MCP ツール 3 件（preprocess / pca / differential）

**Files:**
- Create: `lipidmix/tools/dataset_analysis_tools.py`
- Create: `tests/test_dataset_analysis_tools.py`
- Modify: `lipidmix/core/mcp_errors.py`（`DATASET_BAD_REQUEST` を追加）
- Modify: `tests/test_mcp_errors.py`（コード表のテストを追記）

**Interfaces:**
- Consumes: Task 3 の `run_dataset_preprocess` / `run_dataset_pca` / `run_dataset_differential` / `PreconditionError`
- Consumes: `session_state.session.dataset`（`DatasetState | None`）
- Produces: MCP tools `dataset_preprocess`, `dataset_pca`, `dataset_differential`（`dataset_export_differential` は Task 5）
- 引数名は ARF 版（`arf_preprocess`）と揃える: `normalize` / `blank_min_fold` / `drift_correct` / `max_qc_rsd` / `impute`

> **初版からの主な変更**
> - 引数を実 recipe キーに合わせた。初版の `blank_threshold` は `preprocess()` が読まないキーで、しかも意味が逆（`blank_min_fold` は下限 fold、既定 3.0）だったため、指定しても何も起きなかった。`drift_correct` / `max_qc_rsd` も届いていなかった。
> - annotations を全件 `READ_ONLY` にした（セッション状態の更新は副作用に数えない、が本リポの規約）。
> - `dataset_differential` の戻り値から全特徴量の `results` を外し、`summary` + 上位のみにした。全量はセッション（`ds.last_differential`）に置く。
> - `PreconditionError.kind` で封筒を振り分ける。`missing_state` の `required_tools` に自分自身を入れない。

- [ ] **Step 1: 失敗するテストを書く**

新規 `tests/test_dataset_analysis_tools.py`:

```python
import json

import numpy as np
import pytest

from lipidmix.core import session_state
from lipidmix.core.mcp_errors import MISSING_STATE
from lipidmix.mztab.dataset_state import DatasetState


@pytest.fixture(autouse=True)
def reset_session():
    session_state.session = session_state.AnalysisSession()
    yield
    session_state.session = session_state.AnalysisSession()


def _load_ds(n_features=20, n_samples=8):
    rng = np.random.default_rng(0)
    ds = DatasetState()
    ds.feature_matrix = rng.random((n_features, n_samples)) * 1000.0
    ds.sample_names = ([f"ctrl_{i}" for i in range(n_samples // 2)]
                       + [f"treat_{i}" for i in range(n_samples - n_samples // 2)])
    ds.feature_ids = [f"f{i}" for i in range(n_features)]
    ds.feature_metadata = {
        f"f{i}": {"name": f"Compound {i}", "mz": 100.0 + i, "rt": 1.0 + i * 0.1,
                  "inchikey": f"AAAAAAAAAAAAAA-BBBBBBBBFB-{i%10}",
                  "inchikey_source": "database_identifier"}
        for i in range(n_features)
    }
    ds.validation_result = {"ok": True, "errors": [], "warnings": []}
    session_state.session.dataset = ds
    return ds


def _groups(ds):
    return ([n for n in ds.pp_sample_names if n.startswith("ctrl")],
            [n for n in ds.pp_sample_names if n.startswith("treat")])


# ---------- dataset_preprocess ----------

def test_dataset_preprocess_without_dataset_returns_missing_state():
    from lipidmix.tools.dataset_analysis_tools import dataset_preprocess
    parsed = json.loads(dataset_preprocess())
    assert parsed["error"]["code"] == MISSING_STATE
    assert parsed["error"]["required_tools"] == ["dataset_load"]


def test_dataset_preprocess_success_sets_pp_matrix():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_preprocess
    parsed = json.loads(dataset_preprocess())
    assert parsed["status"] == "success"
    assert parsed["n_samples"] == 8
    assert parsed["n_features"] == 20
    ds = session_state.session.dataset
    assert ds.pp_matrix is not None
    assert ds.preprocessing_recipe["normalize"] == "none"


def test_dataset_preprocess_bad_recipe_is_not_missing_state():
    """引数エラーは missing_state ではない（リプレイしても直らない）。"""
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_preprocess
    parsed = json.loads(dataset_preprocess(normalize="not_a_method"))
    assert parsed["error"]["code"] != MISSING_STATE
    assert "not_a_method" in json.dumps(parsed, ensure_ascii=False)


# ---------- dataset_pca ----------

def test_dataset_pca_requires_preprocess():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_pca
    parsed = json.loads(dataset_pca())
    assert parsed["error"]["code"] == MISSING_STATE
    assert parsed["error"]["required_tools"] == ["dataset_preprocess"]


def test_dataset_pca_success_omits_loadings_from_payload():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_pca, dataset_preprocess
    dataset_preprocess()
    parsed = json.loads(dataset_pca(n_components=2))
    assert len(parsed["explained_variance_ratio"]) == 2
    assert len(parsed["scores"]) == 8
    assert "loadings" not in parsed          # 全量は戻り値に載せない
    assert session_state.session.dataset.last_pca["loadings"]  # セッションには保持


# ---------- dataset_differential ----------

def test_dataset_differential_requires_preprocess():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_differential
    parsed = json.loads(dataset_differential(group_a=["ctrl_0"], group_b=["treat_0"]))
    assert parsed["error"]["code"] == MISSING_STATE
    assert parsed["error"]["required_tools"] == ["dataset_preprocess"]


def test_dataset_differential_success_returns_summary_only():
    ds = _load_ds()
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_preprocess,
    )
    dataset_preprocess()
    a, b = _groups(session_state.session.dataset)
    parsed = json.loads(dataset_differential(group_a=a, group_b=b))
    assert parsed["status"] == "success"
    assert "n_tested" in parsed["summary"]
    assert len(parsed["summary"]["top"]) <= 15
    assert "results" not in parsed           # 全量は戻り値に載せない
    assert "volcano" not in parsed
    stored = session_state.session.dataset.last_differential
    assert len(stored["results"]) == 20      # セッションには全量


def test_dataset_differential_small_group_is_bad_request():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_preprocess,
    )
    dataset_preprocess()
    parsed = json.loads(dataset_differential(group_a=["ctrl_0"], group_b=["treat_0"]))
    assert parsed["error"]["code"] != MISSING_STATE
    assert "群サイズ" in parsed["error"]["message"]


def test_dataset_differential_does_not_touch_arf_slot():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_preprocess,
    )
    dataset_preprocess()
    a, b = _groups(session_state.session.dataset)
    dataset_differential(group_a=a, group_b=b)
    assert session_state.session.arf.feature_matrix is None
    assert getattr(session_state.session.arf, "last_differential", None) is None
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_dataset_analysis_tools.py -v
```

Expected: FAIL（ImportError）

- [ ] **Step 3: `dataset_analysis_tools.py` を実装する**

```python
"""DatasetState に対する解析 MCP ツール。

dataset_load → dataset_preprocess → dataset_pca / dataset_differential
→ dataset_export_differential の順に実行する。ARF 経路
（arf_preprocess → arf_pca_preprocessed → arf_differential →
arf_export_differential）と同じ純関数を共有しており、同じ入力からは
同じ数字が出る。

戻り値には要約だけを載せ、全量（PCA の loadings・差次的の results/volcano）は
session.dataset に保持する（CLAUDE.md の戻り値肥大禁止）。
"""
from __future__ import annotations

from mcp.types import ToolAnnotations

from lipidmix.analysis.dataset_analysis import PreconditionError
from lipidmix.core import session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.mcp_errors import missing_state, mztab_error
from lipidmix.core.serialization import json_payload

__all__ = [
    "dataset_preprocess",
    "dataset_pca",
    "dataset_differential",
    # dataset_export_differential は Task 5 で追加する
]

# PreconditionError.kind="missing_state" のとき、どのツールが状態を作れるか。
# 自分自身は入れない（クライアントが同じ呼び出しを繰り返すループになる）。
_RECOVERY_TOOLS = {
    "dataset": ["dataset_load"],
    "dataset_preprocessed": ["dataset_preprocess"],
    "dataset_differential_result": ["dataset_differential"],
}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_preprocess(
    normalize: str = "none",
    blank_min_fold: float | None = None,
    drift_correct: bool = False,
    max_qc_rsd: float | None = None,
    impute: str = "half_min",
) -> str:
    """DatasetState の定量行列に前処理レシピを適用する。

    引数は arf_preprocess と同一。以降の dataset_pca / dataset_differential は
    ここで作った前処理済み行列を消費する。

    normalize: "none"（既定）/ "tic"（行総和）/ "median"（行中央値）/ "pqn"。
    blank_min_fold: 生体試料平均がブランク平均のこの倍数未満の特徴量を背景として
        除去する（例 3.0）。None（既定）でブランク除去なし。
    drift_correct: QC 注入順ドリフト補正。**mzTab-M は注入順を持たないため常に
        未実施になる**（caveat で報告する）。注入順が必要なら ARF 経路を使う。
    max_qc_rsd: QC 群の RSD がこの値を超える特徴量を除去する（例 0.30）。
    impute: "half_min"（既定）/ "knn" / "column_mean" / "none"。

    成功すると session.dataset.pp_matrix に前処理済み行列が設定される。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")

    from lipidmix.analysis.dataset_analysis import run_dataset_preprocess

    recipe = {
        "normalize": normalize,
        "blank_min_fold": blank_min_fold,
        "drift_correct": drift_correct,
        "max_qc_rsd": max_qc_rsd,
        "impute": impute,
    }
    try:
        (pp_matrix, pp_sample_names, pp_feature_names,
         roles, sample_meta, report) = run_dataset_preprocess(ds, recipe)
    except PreconditionError as exc:
        return _from_precondition(exc)

    ds.pp_matrix = pp_matrix
    ds.pp_sample_names = pp_sample_names
    ds.pp_feature_names = pp_feature_names
    ds.roles = roles
    ds.sample_meta = sample_meta
    ds.preprocessing_recipe = recipe

    return json_payload({
        "status": "success",
        "n_samples": len(pp_sample_names),
        "n_features": len(pp_feature_names),
        "features_before": report.get("features_before"),
        "features_removed_total": report.get("features_removed_total"),
        "recipe_applied": report.get("recipe_applied", []),
        "excluded_from_matrix": report.get("excluded_from_matrix", {}),
        "role_counts": _count_roles(roles, pp_sample_names),
        "steps": report.get("steps", {}),
        "caveats": report.get("caveats", []),
        "next": "dataset_pca または dataset_differential を実行してください",
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_pca(n_components: int = 5, log_transform: bool = False) -> str:
    """前処理済み DatasetState に PCA を実行する。

    dataset_preprocess を先に実行しておくこと。
    n_components: 主成分数（既定 5。サンプル数・特徴量数の小さい方で上限が決まる）。
    log_transform: 標準化の前に log10 変換を適用する（既定 False）。

    ローディング全量は戻り値に載せず session.dataset.last_pca に保持する。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")

    from lipidmix.analysis.dataset_analysis import run_dataset_pca

    try:
        result = run_dataset_pca(ds, n_components=n_components, log_transform=log_transform)
    except PreconditionError as exc:
        return _from_precondition(exc)

    ds.last_pca = result
    payload = {k: v for k, v in result.items() if k != "loadings"}
    payload["status"] = "success"
    payload["loadings_note"] = (
        "ローディング全量（特徴量数 × 主成分数）は本要約に非同梱。"
        "セッションに保持しており、寄与特徴量が必要になったら別途取得します。")
    return json_payload(payload)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_differential(
    group_a: list[str],
    group_b: list[str],
    q_threshold: float = 0.05,
    log2fc_threshold: float = 1.0,
    log_transform: bool = True,
    group_a_label: str = "group_a",
    group_b_label: str = "group_b",
) -> str:
    """前処理済み DatasetState で 2 群の差次的解析（Welch t 検定 + BH-FDR）を実行する。

    group_a / group_b: サンプル名のリスト。dataset_preprocess の戻り値
        （role_counts）と dataset_status で名前を確認できる。前処理済み行列に無い
        名前、QC/ブランクは除外し、caveat で名指しする。
    q_threshold: BH-FDR 補正後の有意水準（既定 0.05）。
    log2fc_threshold: この絶対値以上の log2FC を有意として数える（既定 1.0）。
    log_transform: log2(x + 1) 空間で検定する（既定 True。MS 強度は対数正規に近い）。

    **log2FC は正なら group_b が高い（上昇）。** group_a が基準（対照）。
    全特徴量の結果と volcano 点列は戻り値に載せず session.dataset.last_differential
    に保持する。エクスポートは dataset_export_differential を使う。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")

    from lipidmix.analysis.dataset_analysis import run_dataset_differential

    try:
        result = run_dataset_differential(
            ds,
            group_a_samples=group_a,
            group_b_samples=group_b,
            q_threshold=q_threshold,
            log2fc_threshold=log2fc_threshold,
            log_transform=log_transform,
            group_a_label=group_a_label,
            group_b_label=group_b_label,
        )
    except PreconditionError as exc:
        return _from_precondition(exc)

    ds.last_differential = result

    payload = {k: v for k, v in result.items() if k not in ("results", "volcano")}
    payload["status"] = "success"
    payload["differential_contract_version"] = result["contract_version"]
    payload["results_note"] = (
        "全特徴の結果と volcano 点列は本要約に非同梱（セッション保持）。"
        "InChIKey 付きの全行が必要なら dataset_export_differential を実行してください。")
    return json_payload(payload)


# ---------- 内部ヘルパ ----------

def _missing(state: str, message: str) -> str:
    return missing_state(state, _RECOVERY_TOOLS[state], message)


def _from_precondition(exc: PreconditionError) -> str:
    """PreconditionError を kind に応じた封筒へ振り分ける。

    missing_state は「先に別のツールを呼べば直る」場合だけに使う。引数エラーを
    missing_state にすると、契約に従うクライアントが同じ呼び出しを繰り返す。
    """
    if exc.kind == "missing_state":
        return _missing(_state_for(exc.message), exc.message)
    return mztab_error("DATASET_BAD_REQUEST", exc.message, exc.details or None)


def _state_for(message: str) -> str:
    return "dataset_preprocessed" if "前処理済み行列" in message else "dataset"


def _count_roles(roles: dict, sample_names: list[str]) -> dict:
    counts: dict[str, int] = {}
    for name in sample_names:
        role = roles.get(name, "sample")
        counts[role] = counts.get(role, 0) + 1
    return counts
```

- [ ] **Step 3b: `DATASET_BAD_REQUEST` を `mcp_errors.py` に新設する**

引数エラー専用のコードを足す。既存の `SAMPLE_DESIGN_MISSING` を流用すると、群指定の不備と「未知の normalize メソッド」が同じコードになり、クライアントが区別できない。

`lipidmix/core/mcp_errors.py` の `MZTAB_ERROR_CODES` に追加:

```python
MZTAB_ERROR_CODES = frozenset({
    ...
    "POLARITY_MISMATCH",
    # DatasetState 解析層の引数エラー。missing_state と違い、別のツールを先に
    # 呼んでも直らない（引数を直して呼び直すしかない）。
    "DATASET_BAD_REQUEST",
})
```

`mztab_error()` 自体は変更しない（コード検証はしていない。frozenset は現状ドキュメント）。

`tests/test_mcp_errors.py` に追記:

```python
class TestDatasetBadRequest(unittest.TestCase):
    def test_code_is_registered(self):
        from lipidmix.core.mcp_errors import MZTAB_ERROR_CODES
        self.assertIn("DATASET_BAD_REQUEST", MZTAB_ERROR_CODES)

    def test_envelope_carries_details(self):
        import json
        from lipidmix.core.mcp_errors import mztab_error
        parsed = json.loads(mztab_error(
            "DATASET_BAD_REQUEST", "群サイズ不足", {"n_a": 1, "n_b": 1}))
        self.assertEqual(parsed["error"]["code"], "DATASET_BAD_REQUEST")
        self.assertEqual(parsed["error"]["message"], "群サイズ不足")
        self.assertEqual(parsed["error"]["details"], {"n_a": 1, "n_b": 1})

    def test_is_not_missing_state(self):
        """引数エラーを missing_state と混同しないことを固定する。"""
        import json
        from lipidmix.core.mcp_errors import MISSING_STATE, mztab_error
        parsed = json.loads(mztab_error("DATASET_BAD_REQUEST", "x"))
        self.assertNotEqual(parsed["error"]["code"], MISSING_STATE)
        self.assertNotIn("required_tools", parsed["error"])
```

- [ ] **Step 4: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_dataset_analysis_tools.py tests/test_mcp_errors.py -v
```

Expected: `test_dataset_analysis_tools.py` 10 PASS、`test_mcp_errors.py` の新規 3 件 PASS

- [ ] **Step 5: 全テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests -q
```

Expected: 708 passed（695 + 13）

> `tests/test_server_registration.py` / `tests/test_tool_annotations.py` はまだ落ちない。`server.py` に import を足していないため 3 ツールは未登録。登録は Task 6。

- [ ] **Step 6: コミット**

```
git add lipidmix/tools/dataset_analysis_tools.py lipidmix/core/mcp_errors.py \
        tests/test_dataset_analysis_tools.py tests/test_mcp_errors.py
git commit -m "feat(tools): dataset_preprocess / dataset_pca / dataset_differential を追加"
```

---

## Task 5: エクスポート契約の共有化 + `dataset_export_differential`

**Files:**
- Create: `lipidmix/analysis/export_contract.py`
- Create: `tests/test_export_contract.py`
- Modify: `lipidmix/arf/tools.py`（契約定数を import に置換）
- Modify: `lipidmix/tools/dataset_analysis_tools.py`（ツール追加）
- Modify: `tests/test_dataset_analysis_tools.py`（テスト追記）
- Modify: `tests/test_export_differential.py`（メタ行の順序固定テストを追記）

**Interfaces:**
- Produces: `export_contract.EXPORT_COLUMNS` / `CONTRACT_VERSION` / `LOG2FC_SIGN` / `format_number()` / `build_meta()` / `format_row()`
- Produces: MCP tool `dataset_export_differential`
- Preserves: `arf_export_differential` の出力バイト列が**1 バイトも変わらない**

> **なぜ既存契約に寄せるのか。** `arf_export_differential` の出力は別リポ（`massbank-context` の `load_differential` → `pathway_activity`）が読む契約で、15 列 + `# contract_version = 1` のメタ行が前提になっている。DatasetState 側が独自の列で書くと、名前がほぼ同じツールから**下流が読めないファイル**が出る。しかも初版の `pval`/`padj`/`significant` は `two_group_test` に存在しないキーなので、統計量が全行空欄のファイルが「正常に書けた」として返っていた。
>
> mzTab-M 側の利点として、InChIKey は `.arf2` との結合（`identity_join`）を必要とせず `DatasetState.feature_metadata` から直接取れる。`inchikey_source` も `database_identifier` / `inchi_derived` / `smiles_derived` の実値が入る。

- [ ] **Step 1: 失敗するテストを書く**

新規 `tests/test_export_contract.py`:

```python
def test_contract_constants_are_shared_with_arf():
    """ARF 側が契約モジュールの定数を参照していること（二重定義を作らない）。"""
    from lipidmix.analysis import export_contract
    from lipidmix.arf import tools as arf_tools
    assert arf_tools._EXPORT_COLUMNS is export_contract.EXPORT_COLUMNS
    assert arf_tools._DIFFERENTIAL_CONTRACT_VERSION == export_contract.CONTRACT_VERSION
    assert arf_tools._LOG2FC_SIGN == export_contract.LOG2FC_SIGN


def test_export_columns_are_frozen():
    """列と順序は下流との契約。変更は contract_version の上げ方とセット。"""
    from lipidmix.analysis.export_contract import EXPORT_COLUMNS
    assert EXPORT_COLUMNS == [
        "spot_id", "name", "name_source", "ontology", "inchikey",
        "inchikey_source", "msi_level", "mz", "rt", "log2fc",
        "p_value", "q_value", "mean_a", "mean_b", "significant",
    ]


def test_format_number_blanks_non_finite():
    from lipidmix.analysis.export_contract import format_number
    assert format_number(None, ".4f") == ""
    assert format_number(float("nan"), ".4f") == ""
    assert format_number(float("inf"), ".4f") == ""
    assert format_number(1.23456, ".4f") == "1.2346"


def _meta(**over):
    from lipidmix.analysis.export_contract import build_meta
    kwargs = dict(group_a="ctrl", n_a=4, group_b="treat", n_b=4,
                  q_threshold=0.05, log2fc_threshold=1.0, log_transform=True,
                  n_features_total=100, n_with_inchikey=80, n_unannotated=20,
                  msi_note="# msi_level は注釈確度。MS/MS の有無ではない")
    kwargs.update(over)
    return build_meta(**kwargs)


def test_build_meta_has_mandatory_lines():
    lines = _meta()
    joined = "\n".join(lines)
    assert "# contract_version = 1" in joined
    assert "# log2fc_sign = positive means group_b is higher" in joined
    assert "n_a = 4" in joined and "n_b = 4" in joined
    assert "n_unannotated = 20" in joined
    assert all(line.startswith("#") for line in lines)


def test_build_meta_places_optional_slots_in_contract_order():
    """行の順序も契約。source は exported_at の直後、preprocess は閾値の直後。"""
    lines = _meta(source_lines=["# source_arf = a.arf", "# source_arf2 = a.arf2"],
                  preprocess_line="# preprocess = {'normalize': 'none'}")
    keys = [l.split(" = ")[0].split("\t")[0] for l in lines]
    assert keys == [
        "# contract_version", "# exported_at",
        "# source_arf", "# source_arf2",
        "# group_a", "# group_b", "# log2fc_sign", "# q_threshold",
        "# preprocess", "# n_features_total",
        "# msi_level は注釈確度。MS/MS の有無ではない",
    ]


def test_build_meta_omits_preprocess_when_absent():
    assert not any("preprocess" in l for l in _meta())
```

`tests/test_dataset_analysis_tools.py` に追記:

```python
# ---------- dataset_export_differential ----------

def test_dataset_export_requires_differential(tmp_path):
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_export_differential
    parsed = json.loads(dataset_export_differential(str(tmp_path / "out.tsv")))
    assert parsed["error"]["code"] == MISSING_STATE
    assert parsed["error"]["required_tools"] == ["dataset_differential"]


def test_dataset_export_writes_contract_format(tmp_path):
    from lipidmix.analysis.export_contract import CONTRACT_VERSION, EXPORT_COLUMNS
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_export_differential, dataset_preprocess,
    )
    dataset_preprocess()
    a, b = _groups(session_state.session.dataset)
    dataset_differential(group_a=a, group_b=b)

    out = tmp_path / "diff.tsv"
    parsed = json.loads(dataset_export_differential(str(out)))
    assert parsed["status"] == "success"
    assert parsed["contract_version"] == CONTRACT_VERSION

    lines = out.read_text(encoding="utf-8").splitlines()
    meta = [l for l in lines if l.startswith("#")]
    assert f"# contract_version = {CONTRACT_VERSION}" in meta
    assert any("id_space = mztab_smf_id" in l for l in meta)
    header = next(l for l in lines if not l.startswith("#"))
    assert header.split("\t") == EXPORT_COLUMNS
    body = [l for l in lines if not l.startswith("#")][1:]
    assert len(body) == 20
    # p_value / q_value が空欄でないこと（初版の欠陥の回帰テスト）
    cols = body[0].split("\t")
    assert cols[EXPORT_COLUMNS.index("p_value")] != ""
    assert cols[EXPORT_COLUMNS.index("q_value")] != ""
    assert cols[EXPORT_COLUMNS.index("inchikey")] != ""


def test_dataset_export_refuses_without_inchikey(tmp_path):
    """InChIKey が 0 件なら書かずに拒否する（arf_export_differential と同じ理由）。"""
    ds = _load_ds()
    ds.feature_metadata = {fid: {"name": None, "mz": None, "rt": None,
                                 "inchikey": None, "inchikey_source": "none"}
                           for fid in ds.feature_ids}
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_export_differential, dataset_preprocess,
    )
    dataset_preprocess()
    a, b = _groups(session_state.session.dataset)
    dataset_differential(group_a=a, group_b=b)
    out = tmp_path / "diff.tsv"
    parsed = json.loads(dataset_export_differential(str(out)))
    assert parsed["status"] == "error"
    assert not out.exists()
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_export_contract.py tests/test_dataset_analysis_tools.py -v
```

Expected: 契約テスト 4 件 + エクスポートテスト 3 件が FAIL

- [ ] **Step 3: `export_contract.py` を作る**

`lipidmix/arf/tools.py` の `_EXPORT_COLUMNS` / `_DIFFERENTIAL_CONTRACT_VERSION` / `_LOG2FC_SIGN` / `_format_export_number` と、メタ行の共通部分をここへ移す。

```python
"""差次的エクスポートの契約（列定義・版・数値書式・メタ行）。

このファイルの EXPORT_COLUMNS と CONTRACT_VERSION は**別リポとの契約**。
massbank-context の load_differential → pathway_activity がこの形を前提に読む。
列の増減・改名・順序変更は CONTRACT_VERSION の引き上げと下流の同時更新なしに
やってはいけない。

ARF 経路（arf_export_differential）と DatasetState 経路
（dataset_export_differential）の両方がここを参照する。同じ契約の実装が
2 箇所にあると、片方だけ直った状態で下流が壊れる。

依存は stdlib のみ。lipidmix.* を import しない leaf。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

EXPORT_COLUMNS = [
    "spot_id", "name", "name_source", "ontology", "inchikey",
    "inchikey_source", "msi_level", "mz", "rt", "log2fc",
    "p_value", "q_value", "mean_a", "mean_b", "significant",
]
CONTRACT_VERSION = 1
LOG2FC_SIGN = "positive means group_b is higher"


def format_number(value, format_spec: str) -> str:
    """有限の数値だけを書き出し、欠測・NaN・inf は空欄にする。"""
    if value is None:
        return ""
    number = float(value)
    if not math.isfinite(number):
        return ""
    return format(number, format_spec)


def build_meta(*, group_a, n_a, group_b, n_b,
               q_threshold, log2fc_threshold, log_transform,
               n_features_total, n_with_inchikey, n_unannotated,
               msi_note, source_lines=(), preprocess_line=None) -> list[str]:
    """メタ行ブロックを契約の順序で組む。

    行の**順序も契約の一部**なので、経路ごとに固有の行（source 系・preprocess）は
    自由な位置に足させず、決められたスロットに差す:

        1. contract_version
        2. exported_at
        3. *source_lines        ← 経路固有（# source_arf / # source_mztab など）
        4. group_a / n_a
        5. group_b / n_b
        6. log2fc_sign
        7. q_threshold / log2fc_threshold / log_transform
        8. preprocess_line      ← 経路固有（省略可）
        9. n_features_total / n_with_inchikey / n_unannotated
       10. msi_note             ← 文面が経路で違う（.arf2 由来 か mzTab-M か）

    この順序は arf_export_differential の現行出力と一致させてある。
    変えると既存ファイルとの差分が出るので、CONTRACT_VERSION を上げずに
    順序を触ってはいけない。
    """
    lines = [
        f"# contract_version = {CONTRACT_VERSION}",
        f"# exported_at = {datetime.now(timezone.utc).isoformat()}",
        *source_lines,
        f"# group_a = {group_a}\tn_a = {n_a}",
        f"# group_b = {group_b}\tn_b = {n_b}",
        f"# log2fc_sign = {LOG2FC_SIGN}",
        f"# q_threshold = {q_threshold}\tlog2fc_threshold = {log2fc_threshold}"
        f"\tlog_transform = {str(bool(log_transform)).lower()}",
    ]
    if preprocess_line is not None:
        lines.append(preprocess_line)
    lines += [
        f"# n_features_total = {n_features_total}"
        f"\tn_with_inchikey = {n_with_inchikey}"
        f"\tn_unannotated = {n_unannotated}",
        msi_note,
    ]
    return lines


def format_row(row: dict) -> str:
    """EXPORT_COLUMNS の順に 1 行を組む。row は 15 列すべてのキーを持つこと。"""
    return "\t".join([
        str(row["spot_id"]),
        row["name"] or "",
        row["name_source"] or "",
        row["ontology"] or "",
        row["inchikey"] or "",
        row["inchikey_source"] or "",
        str(row["msi_level"]) if row["msi_level"] is not None else "",
        format_number(row["mz"], ".4f"),
        format_number(row["rt"], ".4f"),
        format_number(row["log2fc"], ".6f"),
        format_number(row["p_value"], ".6g"),
        format_number(row["q_value"], ".6g"),
        format_number(row["mean_a"], ".6g"),
        format_number(row["mean_b"], ".6g"),
        "true" if row["significant"] else "false",
    ])
```

- [ ] **Step 4: `arf/tools.py` を契約モジュール参照に置き換える**

`_EXPORT_COLUMNS` / `_DIFFERENTIAL_CONTRACT_VERSION` / `_LOG2FC_SIGN` / `_format_export_number` の定義を削除し、後方互換の別名束縛にする（`tests/test_export_contract.py` が `is` で同一性を見る）。

```python
from lipidmix.analysis import export_contract

_EXPORT_COLUMNS = export_contract.EXPORT_COLUMNS
_DIFFERENTIAL_CONTRACT_VERSION = export_contract.CONTRACT_VERSION
_LOG2FC_SIGN = export_contract.LOG2FC_SIGN
_format_export_number = export_contract.format_number
```

`arf_export_differential` の本体（メタ行の組み立てと行の書き出し）は Step 6 で寄せる。この Step では定数の差し替えだけに留め、出力が 1 バイトも変わらないことを Step 5 で確認する。

- [ ] **Step 5: ARF 側の回帰を確認し、出力をバイト列で固定する**

まず既存テストが素通しであることを確認する。

```
C:/Python314/python.exe -m pytest tests/test_export_differential.py tests/test_export_contract.py -q
```

Expected: 全 PASS。`tests/test_export_differential.py` が 1 件でも落ちたら契約定数の差し替えを間違えている。

次に **Step 6 の共通ヘルパ化で出力が変わらないことを縛るテスト**を `tests/test_export_differential.py` に追記する。既存テストがどう fixture を組んでいるかを読み、同じ流儀で `arf_export_differential` を 1 回走らせて全文を取り、`exported_at` 行だけを伏せて比較する。

```python
_EXPECTED_META_KEYS = [
    "# contract_version", "# exported_at",
    "# source_arf", "# source_arf2",
    "# group_a", "# group_b", "# log2fc_sign", "# q_threshold",
    "# preprocess", "# n_features_total",
    "# msi_level は .arf2 由来の注釈確度。MS/MS の有無ではない",
]


def test_arf_export_meta_line_order_is_frozen(<既存 fixture と同じ引数>):
    """メタ行の順序と文面を固定する。

    Task 5 Step 6 で export_contract.build_meta() へ寄せるとき、この順序が
    変わっていないことを保証する。行の順序も下流との契約の一部。
    """
    ...  # 既存テストと同じ手順で arf_export_differential を実行し out を得る
    meta = [l for l in out.read_text(encoding="utf-8").splitlines()
            if l.startswith("#")]
    keys = [l.split(" = ")[0].split("\t")[0] for l in meta]
    assert keys == _EXPECTED_META_KEYS
```

このテストを**先に緑にしてからコミットし**、それから Step 6 に進む。順序を固定していない状態で寄せると、壊しても気付けない。

- [ ] **Step 6: `arf_export_differential` を `build_meta` / `format_row` へ寄せる**

`build_meta` のスロット設計（Step 3 の docstring）は ARF の現行順序に合わせてある。`# source_arf` / `# source_arf2` を `source_lines` に、`# preprocess` を `preprocess_line` に、`.arf2` 由来の文面を `msi_note` に渡す。

```python
    meta = export_contract.build_meta(
        group_a=last["a"], n_a=last["n_a"],
        group_b=last["b"], n_b=last["n_b"],
        q_threshold=q_threshold, log2fc_threshold=log2fc_threshold,
        log_transform=last.get("log_transform"),
        n_features_total=report["n_features_total"],
        n_with_inchikey=report["n_with_inchikey"],
        n_unannotated=report["n_unannotated"],
        msi_note="# msi_level は .arf2 由来の注釈確度。MS/MS の有無ではない",
        source_lines=[
            f"# source_arf = {getattr(session_state.session.arf, 'current_file_path', '')}",
            f"# source_arf2 = {arf2_path}",
        ],
        preprocess_line=f"# preprocess = {getattr(session_state.session.arf, 'preprocessing_recipe', None)}",
    )
```

行の書き出しも `export_contract.format_row()` に寄せる。ARF 側は `spot_id` / `name_source="arf2"` / `ontology` / `inchikey_source="arf2"` / `msi_level=identity["msi"]["level"]` を dict に詰めて渡す形になる。**`format_row` は 15 キーすべてを要求する**（`.get()` で黙って空欄にしない設計）。

```python
    for row in rows:
        identity_name = row["name"]
        if identity_name.strip().lower() == "unknown":
            identity_name = ""
        identity = lipid_identity.build_identity_block(
            {"name": identity_name, "ontology": row["ontology"], "has_msms": False},
            identity_tables, mass_error_band="UNKNOWN", adduct_band="UNKNOWN",
        )
        lines.append(export_contract.format_row({
            "spot_id": row["spot_id"],
            "name": row["name"],
            "name_source": "arf2",
            "ontology": row["ontology"],
            "inchikey": row["inchikey"],
            "inchikey_source": "arf2",
            "msi_level": identity["msi"]["level"],
            "mz": row["mz"], "rt": row["rt"],
            "log2fc": row["log2fc"],
            "p_value": row["p_value"], "q_value": row["q_value"],
            "mean_a": row["mean_a"], "mean_b": row["mean_b"],
            "significant": _is_significant(row),
        }))
```

> **注意**: 現行の行組み立ては `row["name"]` を `str()` せずそのまま書き、`identity_name` は `build_identity_block` に渡すだけで**出力には使っていない**（`name` 列には `row["name"]` が入る）。`format_row` に寄せるときこの挙動を変えないこと。`format_row` の `row["name"] or ""` は `None` を空欄にするだけで、既存の文字列はそのまま通る。

確認:

```
C:/Python314/python.exe -m pytest tests/test_export_differential.py -q
```

Expected: 全 PASS（Step 5 で追加した順序固定テストを含む）。**1 件でも落ちたら寄せ方が間違っている。** 出力の差分が意図的でない限り Step 6 を revert して Step 4 の別名束縛のまま次へ進み、`docs/task.md` に起票する。

- [ ] **Step 7: `dataset_export_differential` を実装する**

`lipidmix/tools/dataset_analysis_tools.py` に追加し、`__all__` に載せる。

```python
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True),
    structured_output=False)
def dataset_export_differential(output_path: str) -> str:
    """直近の差次的結果を InChIKey 付きの 1 ファイルへ書き出す。

    先に dataset_preprocess → dataset_differential を実行しておくこと。
    出力は arf_export_differential と**同一の契約**（15 列 + contract_version
    メタ行）なので、下流のパスウェイ解析にそのまま渡せる。

    InChIKey は DatasetState.feature_metadata から取る（mzTab-M の
    database_identifier / InChI / SMILES 由来。.arf2 との結合は不要）。
    濃縮解析の背景を保つため、有意な行だけでなく InChIKey が付いた全行を出す。
    ontology と msi_level は mzTab-M に対応物が無いため空欄で、その旨をメタ行に
    書く（空欄を「該当なし」と読み違えさせない）。
    """
    ds = session_state.session.dataset
    if ds is None:
        return _missing("dataset", "DatasetState がありません。先に dataset_load を実行してください。")
    last = ds.last_differential
    if not last or last.get("kind") != "two_group":
        return _missing(
            "dataset_differential_result",
            "先に dataset_preprocess → dataset_differential（2群）を実行してください。")
    if (last.get("contract_version") != export_contract.CONTRACT_VERSION
            or last.get("log2fc_sign") != export_contract.LOG2FC_SIGN):
        return _missing(
            "dataset_differential_result",
            "直近の差次的結果は現行エクスポート契約と互換性がありません。"
            "dataset_differential を再実行してください。")

    q_threshold = last["q_threshold"]
    log2fc_threshold = last["log2fc_threshold"]

    rows: list[dict] = []
    n_unannotated = 0
    for result in last["results"]:
        fid = result["feature"]
        meta = ds.feature_metadata.get(fid, {})
        inchikey = str(meta.get("inchikey") or "").strip()
        if not inchikey:
            n_unannotated += 1
            continue
        rows.append({
            "spot_id": fid,                     # mzTab-M の SMF_ID（メタ行で id_space を宣言）
            "name": (meta.get("name") or "").strip(),
            "name_source": "mztab_smf",
            "ontology": "",                     # mzTab-M に対応物なし
            "inchikey": inchikey,
            "inchikey_source": meta.get("inchikey_source") or "",
            "msi_level": None,                  # 同上（.arf2 由来の注釈確度が無い）
            "mz": meta.get("mz"),
            "rt": meta.get("rt"),
            "log2fc": result.get("log2fc"),
            "p_value": result.get("p"),
            "q_value": result.get("q"),
            "mean_a": result.get("mean_a"),
            "mean_b": result.get("mean_b"),
            "significant": _is_significant(result, q_threshold, log2fc_threshold),
        })

    n_total = len(last["results"])
    if not rows:
        return json_payload({
            "status": "error",
            "message": ("InChIKey が付いた特徴が 0 件のため書き出しません。"
                        "下流のパスウェイ解析に使える背景集合がありません。"),
            "n_features_total": n_total,
            "n_with_inchikey": 0,
            "n_unannotated": n_unannotated,
        })

    meta_lines = export_contract.build_meta(
        group_a=last["a"], n_a=last["n_a"],
        group_b=last["b"], n_b=last["n_b"],
        q_threshold=q_threshold, log2fc_threshold=log2fc_threshold,
        log_transform=last.get("log_transform"),
        n_features_total=n_total, n_with_inchikey=len(rows),
        n_unannotated=n_unannotated,
        # mzTab-M に .arf2 由来の注釈確度が無いことを、空欄の意味とあわせて宣言する。
        msi_note=("# ontology / msi_level は mzTab-M に対応物が無いため空欄"
                  "（『該当なし』ではなく『この経路では取得していない』）"),
        source_lines=[
            f"# source_mztab = {'; '.join(ds.source_files) or ''}",
            f"# source_job = {ds.job_path or ''}",
            "# id_space = mztab_smf_id",
        ],
        preprocess_line=f"# preprocess = {ds.preprocessing_recipe}",
    )
    lines = [*meta_lines, "\t".join(export_contract.EXPORT_COLUMNS)]
    lines += [export_contract.format_row(r) for r in rows]

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return json_payload({
        "status": "success",
        "output_path": str(out),
        "contract_version": export_contract.CONTRACT_VERSION,
        "group_a": last["a"],
        "group_b": last["b"],
        "n_features_total": n_total,
        "n_with_inchikey": len(rows),
        "n_unannotated": n_unannotated,
        "log2fc_sign": "log2fc は正なら group_b が高い（上昇）。",
        "note": ("n_unannotated は InChIKey が付かず書き出さなかった行数です。"
                 "「変化が無かった」ではなく「調べていない」行です。"),
    })


def _is_significant(result: dict, q_threshold: float, log2fc_threshold: float) -> bool:
    q = result.get("q")
    fc = result.get("log2fc")
    if q is None or fc is None:
        return False
    if not (math.isfinite(q) and math.isfinite(fc)):
        return False
    return q <= q_threshold and abs(fc) >= log2fc_threshold
```

必要な import を追加する: `import math`、`from pathlib import Path`、`from lipidmix.analysis import export_contract`。

- [ ] **Step 8: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_export_contract.py tests/test_dataset_analysis_tools.py tests/test_export_differential.py -q
```

Expected: 全 PASS

- [ ] **Step 9: 全テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests -q
```

Expected: 718 passed（708 + 10）

- [ ] **Step 10: コミット**

```
git add lipidmix/analysis/export_contract.py lipidmix/arf/tools.py \
        lipidmix/tools/dataset_analysis_tools.py \
        tests/test_export_contract.py tests/test_dataset_analysis_tools.py \
        tests/test_export_differential.py
git commit -m "feat(export): 差次的エクスポート契約を共有化し dataset_export_differential を追加"
```

---

## Task 6: `server.py` への登録 + 腐敗防止テスト更新

**Files:**
- Modify: `server.py`
- Create: `docs/workflow/dataset_analysis.md`
- Modify: `docs/workflow/index.md`
- Modify: `tests/test_server_registration.py`
- Modify: `tests/test_tool_annotations.py`
- Modify: `tests/test_workflow_docs.py`

**Interfaces:**
- Produces: 4 ツールが MCP に登録された状態（`asyncio.run(server.mcp.list_tools())` で 51 件）

- [ ] **Step 1: `server.py` に import を追加し、腐敗防止テストが落ちることを確認**

既存の `from lipidmix.tools.console_tools import *` の直後に追加:

```python
from lipidmix.tools.dataset_analysis_tools import *  # dataset_preprocess, dataset_pca, dataset_differential, dataset_export_differential
```

```
C:/Python314/python.exe -m pytest tests/test_server_registration.py tests/test_tool_annotations.py tests/test_workflow_docs.py -v
```

Expected: FAIL（ツール数 **47 → 51**、`EXPECTED_TOOLS` / `EXPECTED_ANNOTATIONS` / 範囲分類の不一致）

- [ ] **Step 2: `tests/test_server_registration.py` を更新**

`EXPECTED_TOOLS` に追加（アルファベット順は問わない。`sorted()` で包まれている）:

```python
    "dataset_differential",
    "dataset_export_differential",
    "dataset_pca",
    "dataset_preprocess",
```

- [ ] **Step 3: `tests/test_tool_annotations.py` を更新**

```python
    # --- DatasetState 解析層 ---
    # readOnlyHint はファイル/ネットワークへの副作用の有無。セッション状態の
    # 更新は数えない（このファイル冒頭の規約）。ARF の同一操作も READ_ONLY。
    "dataset_preprocess": READ_ONLY,
    "dataset_pca": READ_ONLY,
    "dataset_differential": READ_ONLY,
    # ファイルを書き、同じ引数なら同じ内容で上書きする → 冪等
    "dataset_export_differential": LOCAL_WRITE,
```

> 初版は `dataset_preprocess` / `dataset_differential` を `LOCAL_WRITE_APPEND` にしていたが、`arf_preprocess` / `arf_differential` は `READ_ONLY`。同じ操作で annotations が違うと、annotations だけを見て承認要否を決めるクライアントが片方だけ毎回承認待ちにする。

- [ ] **Step 4: `tests/test_workflow_docs.py` を更新**

`IN_SCOPE` に追加:

```python
    "dataset_analysis.md": (
        "dataset_preprocess", "dataset_pca", "dataset_differential",
        "dataset_export_differential",
    ),
```

`test_scope_totals_match_registered_tool_count` の期待値を `31` → `35` に更新する。`OUT_OF_SCOPE` の Console 4 件はそのまま（Task 0 でワークフロー文書を作っていないため）。

- [ ] **Step 5: ワークフロー文書を作成**

新規 `docs/workflow/dataset_analysis.md`。**`_defined_names()` は AST のトップレベル定義しか見ない**ので、書いた `パス + 関数名` が本当にそのファイルで `def` されているかを目視で確認する（初版はここで存在しない `preprocessing.py run_pca()` / `run_differential()` を書いていた）。

```markdown
# DatasetState 解析ツール呼び出し連鎖

ARF 経路（`docs/workflow/arf.md`）と同じ純関数を共有している。同じ入力からは
同じ数字が出る。違いは入口（mzTab-M か .arf か）とセッションスロットだけ。

## dataset_preprocess

1. lipidmix/tools/dataset_analysis_tools.py  dataset_preprocess()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_preprocess()
3.    └─ lipidmix/analysis/dataset_analysis.py  build_dataset_pp_inputs()
4.       └─ lipidmix/analysis/preprocessing.py  detect_sample_roles()
5.    └─ lipidmix/analysis/preprocessing.py  preprocess()
6.    └─ lipidmix/analysis/preprocessing.py  drop_samples_by_role()
7.    └─ lipidmix/analysis/preprocessing.py  detect_qc_strata()

## dataset_pca

1. lipidmix/tools/dataset_analysis_tools.py  dataset_pca()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_pca()
3.    └─ lipidmix/analysis/pca.py  run_pca()

## dataset_differential

1. lipidmix/tools/dataset_analysis_tools.py  dataset_differential()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_differential()
3.    └─ lipidmix/analysis/differential.py  two_group_test()
4.    └─ lipidmix/analysis/differential.py  add_fdr()
5.    └─ lipidmix/analysis/differential.py  summarize_two_group()
6.    └─ lipidmix/analysis/differential.py  volcano_data()

## dataset_export_differential

1. lipidmix/tools/dataset_analysis_tools.py  dataset_export_differential()
2. └─ lipidmix/analysis/export_contract.py  build_meta()
3. └─ lipidmix/analysis/export_contract.py  format_row()
```

- [ ] **Step 5b: `docs/workflow/index.md` の目次を更新**

`test_all_expected_documents_exist` は**ファイル名の集合しか見ない**ので目次表は検証されておらず、現状すでにずれている。`mztab.md` の行が無く、末尾が「合計 28 ツール」「登録ツール総数は 40」のままになっている。今回の追加とあわせて実測に直す。

目次表に 2 行を足す（`mztab.md` の漏れも同時に埋める）:

```markdown
| [mztab.md](mztab.md) | mzTab-M — DatasetState への読み込み | 2 |
| [dataset_analysis.md](dataset_analysis.md) | DatasetState — 前処理・PCA・差次的解析・エクスポート | 4 |
```

末尾の集計文を実測に合わせる。

```
- 合計 28 ツール。……登録ツール総数は 40。
+ 合計 35 ツール。……登録ツール総数は 51。
```

対象外の内訳（文献探索・レポート記録系 12 + Console 実行層 4 = 16）も書き足す。`35 + 16 = 51` が `test_scope_totals_match_registered_tool_count` の主張と一致していることを確認する。

`CLAUDE.md` の「ツール 40・リソース 4」「`docs/workflow/`（8 文書…）」も同じ理由でずれている（実測 47 → 51、8 → 9 文書。テスト件数の「全 563 件」も現状 674）。**`CLAUDE.md` の更新は Task 9 でまとめて行う**（各 Task で触ると差分が散る）。

- [ ] **Step 6: 腐敗防止テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_server_registration.py tests/test_tool_annotations.py tests/test_workflow_docs.py -v
```

Expected: 全 PASS

- [ ] **Step 7: 全テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests -q
```

Expected: 718 passed（新規テストなし。既存テストの期待値更新のみ）

- [ ] **Step 8: コミット**

```
git add server.py docs/workflow/dataset_analysis.md \
        tests/test_server_registration.py \
        tests/test_tool_annotations.py \
        tests/test_workflow_docs.py
git commit -m "feat(server): dataset analysis ツール 4 件を MCP に登録する"
```

---

## Task 7: `feature-qc.tsv` 生成と `console_run` への配線

Phase 2 の積み残し。サンプルの役割・バッチをサイドカーファイルに書き出し、`analysis-job.json` の `artifacts` に登録する。**生成関数だけ作って呼ばないのは「積み残しを消した」ことにならない**ので、配線と登録まで含める。

**Files:**
- Create: `lipidmix/console/sidecar.py`
- Modify: `lipidmix/tools/console_tools.py`
- Test: `tests/test_console_runner.py`（既存ファイルに追記）

> ワークフロー文書は作らない。Console 系 4 ツールは `OUT_OF_SCOPE` のままなので、`docs/workflow/` に `## console_run` 節を書くと `test_out_of_scope_tools_have_no_section` が落ちる。

**Interfaces:**
- Produces: `sidecar.generate_feature_qc_tsv(sample_meta, output_path) -> None`
- Produces: `sidecar.build_sample_meta_from_names(sample_names) -> dict`
- Produces: `<run_dir>/sidecars/feature-qc.tsv` + `AnalysisJob.artifacts` への `role="sample_qc_sidecar"` 登録

> **sample_meta の出どころ。** ARF セッション（`session.arf.sample_meta`）は使わない。`console_run` の時点では ARF はまだ読まれていないし、DatasetState 経路を ARF スロットに再結合すると `session.arf` / `session.dataset` を分離した意味が消える。役割推定はサンプル名だけでできる（`preprocessing.detect_sample_roles`）ので、`console_run` が収集した生成物のファイル名から作る。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_console_runner.py` に追記:

```python
# ---------- sidecar ----------

def test_build_sample_meta_from_names():
    from lipidmix.console.sidecar import build_sample_meta_from_names
    meta = build_sample_meta_from_names(
        ["20260901_ctrl_1", "20260901_QC_1", "blank_1"])
    assert meta["20260901_QC_1"]["role"] == "qc"
    assert meta["blank_1"]["role"] == "blank"
    assert meta["20260901_ctrl_1"]["role"] == "sample"
    assert meta["20260901_ctrl_1"]["batch"] == "20260901"


def test_generate_feature_qc_tsv(tmp_path):
    from lipidmix.console.sidecar import generate_feature_qc_tsv
    sample_meta = {
        "ctrl_1": {"role": "sample", "batch": "20260901", "run_order": None},
        "blank_1": {"role": "blank", "batch": None, "run_order": None},
    }
    out_path = tmp_path / "feature-qc.tsv"
    generate_feature_qc_tsv(sample_meta, out_path)
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == ["sample_name", "role", "batch",
                                    "batch_source", "run_order"]
    assert len(lines) == 3


def test_console_run_registers_sidecar_artifact(tmp_path, monkeypatch):
    """console_run 成功後、feature-qc.tsv が生成され artifacts に登録される。"""
    import json as _json
    from unittest.mock import MagicMock, patch
    from lipidmix.console.job_manager import create_job, load_job
    from lipidmix.tools.console_tools import console_run
    monkeypatch.setenv("MSDIAL_EXE", "fake.exe")
    method = tmp_path / "params.msdial"
    method.touch()
    job, job_path = create_job(dataset_root=tmp_path, method_file=method,
                               polarity="positive", measure="peak_height")
    run_dir = Path(job.run_dir)

    def _fake_run(cmd, **kwargs):
        out = run_dir / "msdial"
        out.mkdir(parents=True, exist_ok=True)
        (out / "Height_AlignmentResult_ctrl_1.pai2").write_bytes(b"\x00")
        (out / "Height_AlignmentResult_QC_1.pai2").write_bytes(b"\x00")
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("subprocess.run", side_effect=_fake_run):
        result = console_run(str(job_path))
    parsed = _json.loads(result)
    assert parsed["status"] == "completed"

    sidecar = run_dir / "sidecars" / "feature-qc.tsv"
    assert sidecar.is_file()
    roles = {a.role for a in load_job(job_path).artifacts}
    assert "sample_qc_sidecar" in roles
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_console_runner.py -q
```

Expected: 3 FAIL

- [ ] **Step 3: `sidecar.py` を実装する**

新規 `lipidmix/console/sidecar.py`:

```python
"""サイドカーファイル生成（feature-qc.tsv）。

MS-DIAL Console 実行完了後に呼ぶ純関数群。MCP 非依存・セッション非依存。
役割はサンプル名から推定する（session.arf を参照しない。console_run の時点で
ARF はまだ読まれていないし、DatasetState 経路を ARF スロットに再結合しない）。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from lipidmix.analysis import preprocessing

SIDECAR_SUBDIR = "sidecars"
FEATURE_QC_FILENAME = "feature-qc.tsv"
ARTIFACT_ROLE = "sample_qc_sidecar"

COLUMNS = ["sample_name", "role", "batch", "batch_source", "run_order"]

_DATE_RE = re.compile(r"(\d{8})")


def build_sample_meta_from_names(sample_names) -> dict:
    """サンプル名から {name: {role, batch, batch_source, run_order}} を組む。

    ロール判定は lipidmix/analysis/preprocessing.py の detect_sample_roles に
    委ねる（ARF 経路・DatasetState 経路と同じ判定を使う）。
    run_order は MS-DIAL の出力から取れないため None。
    """
    names = list(sample_names)
    roles = preprocessing.detect_sample_roles(names)
    meta: dict = {}
    for name in names:
        m = _DATE_RE.search(name)
        meta[name] = {
            "role": roles.get(name, "sample"),
            "batch": m.group(1) if m else None,
            "batch_source": "filename_date" if m else None,
            "run_order": None,
        }
    return meta


def generate_feature_qc_tsv(sample_meta: dict, output_path: Path) -> None:
    """sample_meta から feature-qc.tsv を生成する。None は空欄で書く。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(COLUMNS)
        for name in sorted(sample_meta):
            meta = sample_meta[name]
            writer.writerow([
                name,
                meta.get("role") or "sample",
                meta.get("batch") or "",
                meta.get("batch_source") or "",
                "" if meta.get("run_order") is None else meta["run_order"],
            ])
```

- [ ] **Step 4: `console_run` に配線する**

`lipidmix/tools/console_tools.py`。生成物を収集して `status="completed"` を書く直前に挟む。**サイドカーの生成に失敗しても実行自体は成功として扱う**（副産物のためにジョブを失敗にしない）。

```python
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
```

内部ヘルパを追加:

```python
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
```

戻り値の payload に `"sidecar"` の有無を足す（クライアントが次の手を選べるように）:

```python
        "warnings": job.warnings,
```

- [ ] **Step 5: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_console_runner.py -q
```

Expected: 40 PASS（37 + 3）

- [ ] **Step 6: 全テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests -q
```

Expected: 721 passed（718 + 3）

- [ ] **Step 7: コミット**

```
git add lipidmix/console/sidecar.py lipidmix/tools/console_tools.py tests/test_console_runner.py
git commit -m "feat(console): feature-qc.tsv を生成し analysis-job.json に登録する"
```

---

## Task 8: 実データでの end-to-end 確認

単体テストは fixture の行列で通る。**実データで 1 回通すまでは「パイプライン完成」と言わない。**

**Files:** なし（検証のみ。`check.py` を使い、終わったら中身を消す）

- [ ] **Step 1: mzTab-M からの経路を確認**

手元の mzTab-M ファイル 1 つで、`dataset_load(mztab_path=...)` → `dataset_preprocess` → `dataset_pca` → `dataset_differential` → `dataset_export_differential` を順に呼ぶ。確認項目:

- `dataset_preprocess` の `caveats` に注入順の注意が出ている
- `dataset_pca` の戻り値に `loadings` が**入っていない**（`session.dataset.last_pca` には入っている）
- `dataset_differential` の戻り値に `results` / `volcano` が**入っていない**
- 各戻り値の文字数を数え、`arf_*` の対応ツールと同程度に収まっている（桁が違うなら要約が漏れている）
- エクスポートしたファイルのヘッダが `arf_export_differential` の出力と同一の 15 列

- [ ] **Step 2: ARF 経路との数値一致を確認**

同一アラインメント由来の `.arf` と mzTab-M が手元にあれば、両経路で同じレシピ・同じ群定義で `arf_differential` と `dataset_differential` を走らせ、`summary` の `n_tested` / `n_significant` / 上位特徴を比べる。ズレるなら行列構築（転置・定量種別・ブランク除去）のどこかが違う。

> 一致しない場合、**それは仕様どおりの差か、バグか**を切り分けて `docs/HISTRY.md` に書く。mzTab-M と .arf は粒度が違いうる（gap-fill の扱い・スポット代表値）ので、差が出ること自体は異常でない。差の**理由が説明できない**ことが異常。

- [ ] **Step 3: Console 経路を確認（Task 0 実施時のみ）**

`console_plan` → `console_run` → `dataset_load(job_path=...)` を既定データディレクトリ配下のフォルダで 1 回通し、`DATASET_ROOT_IN_REPO` が出ないこと、`analysis-job.json` の `artifacts` に `sample_qc_sidecar` が入ること、`msdial.log` が `artifacts` に**入っていない**ことを確認する。

- [ ] **Step 4: `check.py` を空に戻す**

---

## Task 9: `HISTRY.md` / `task.md` / `CLAUDE.md` 更新（Phase 完結の義務）

- [ ] **Step 1: `docs/HISTRY.md` に追記**

`YYYY-MM-DD: DatasetState 解析層 + Phase 4 メタボロミクス準備` の節を追加し、以下を記録する。

- `run_pca` を `analysis/pca.py` へ移した理由と、再エクスポートを残した理由（ARF テストのモックが module 属性を差し替えている）
- エクスポート契約を `analysis/export_contract.py` へ共有化したこと。mzTab-M 経路では `ontology` / `msi_level` が空欄になること
- Task 8 で確認した ARF 経路との数値差（あれば、その理由）
- 積み残し: 図の保存ツール（`save_pca_figure` / `save_volcano_figure`）は `session.arf` 側のみ対応で DatasetState 経路は未接続

- [ ] **Step 2: `docs/task.md` を更新**

- 「Phase 3 積み残し → DatasetState 解析層」を DONE に
- Task 0 で対応しなかった Console 層の指摘を TODO として起票する:
  - `JOB_NOT_PLANNED` を入力バリデーションに流用している（`console_plan` の polarity/omics/dataset_root）
  - `CONSOLE_ERROR_CODES` がコード検証に使われておらず、`console_plan` が集合外のコード（`UNSUPPORTED_AREA_CONSOLE`）を出す
  - `_resolve_job_path` が `missing_state` ではなく `console_error` を返している
  - `dataset_load` の `MZTAB_NOT_FOUND` が引数競合・job 解析失敗・mztab 不在の 3 つを兼ねている
  - `dataset_load()` 引数なしの `missing_state` が自分自身を `required_tools` に入れている
  - `_job_id` の秒精度による run_dir 衝突
  - `output_collector` の差分検出がサイズのみ
  - `tests/test_console_runner.py` のセッション差し替えが autouse fixture で復元されない
- Phase 4（メタボロミクス）の TODO を整備する。Console の解析モード切り替えは `omics` ではなく acquisition（`lcms` / `lcmsdda` / `gcms`）の軸で設計する旨を明記する

- [ ] **Step 3: `CLAUDE.md` の数値と地図を実測に合わせる**

`CLAUDE.md` は「全体スキャンの代わりに読む」ものなので、数字がずれていると次の作業者を誤らせる。本 Phase 開始時点ですでにずれていたものを含めて直す。

| 記述 | 現状 | 直す先 |
|---|---|---|
| 「ツール 40・リソース 4」 | 47 登録 | 51 |
| 「全 **563 件**」（pytest） | 674 件 | Task 8 完了時点の実測値 |
| 「`docs/workflow/`（8 文書・パーサ/プロット系 28 ツール分）」 | 9 文書・35 ツール | 実測値 |
| 構成図の `lipidmix/analysis/` の説明 | 「前処理/QC・差次的解析」 | PCA と エクスポート契約を追記 |

`lipidmix/console/` `lipidmix/handoff/` の 2 パッケージが構成図に無いので追加する。「触るときの鉄則」に 2 項を足す:

- `run_pca` の正準は `lipidmix/analysis/pca.py`。`lipidmix/arf/reader.py` の同名は後方互換の再エクスポートで、ARF テストが `patch.object(server.arf_reader, "run_pca", ...)` でこの束縛を差し替える。**消すとモックが効かなくなり実物の sklearn PCA が走り出す。**
- 差次的エクスポートの列定義は `lipidmix/analysis/export_contract.py` が唯一の正準。**別リポ（massbank-context）との契約**であり、列の増減・改名・順序変更は `CONTRACT_VERSION` の引き上げと下流の同時更新なしにやってはいけない。

> 数字は「直した瞬間から陳腐化する」ため、`pytest tests -q` の実測を貼るだけにして推測で書かない。

- [ ] **Step 4: コミット**

```
git add docs/HISTRY.md docs/task.md CLAUDE.md
git commit -m "docs: DatasetState 解析層 実装ログを更新し CLAUDE.md の数値を実測に合わせる"
```

---

## Self-Review

### Spec coverage チェック

| Spec 項目 | 対応タスク |
|---|---|
| DatasetState 経由の前処理 / PCA / 差次的解析 | Task 3（純ロジック）+ Task 4（MCP 層） |
| ARF 経路との数値一致 | Task 1（`run_pca` 共有）+ Task 3（処理順を `arf_preprocess` に合わせる）+ Task 8 Step 2（実測確認） |
| 下流パスウェイ解析への受け渡し | Task 5（契約共有 + `dataset_export_differential`） |
| `feature-qc.tsv` 生成 | Task 7（生成 + 配線 + artifacts 登録） |
| Phase 4 metabolomics 対応 | `AnalysisJob.omics` は Phase 2 で既に保持済み。Console の解析モード切り替えは acquisition 軸で別途設計（Task 9 Step 2 に起票） |
| DCL/EIC evidence index を DatasetState に接続 | 本計画の範囲外。`artifact_paths` は Phase 3 で追加済みで、使用側ツールは次フェーズ |
| 腐敗防止テスト追従 | Task 1 Step 5（`arf.md`）+ Task 6 |

### 判断済み（2026-09-03）

初版レビューで挙がった 3 件の保留は決着済み。実装中に再検討しない。

1. **引数エラーのエラーコード** → **`DATASET_BAD_REQUEST` を新設**する（Task 4 Step 3b）。`MZTAB_ERROR_CODES` に追加し `mztab_error()` で返す。`SAMPLE_DESIGN_MISSING` の流用はしない（群指定の不備と未知の normalize が同じコードになると区別できない）。
2. **`ontology` / `msi_level` の空欄** → **実ファイル確認後に判断**する。当面は空欄 + メタ行での理由宣言（`msi_note`）で進め、Task 8 Step 1 でエクスポートしたファイルを下流（`massbank-context load_differential`）に実際に渡して確かめる。落ちるなら SME 行の `reliability` から `msi_level` を導出する追加設計を別途起票する。**Task 1〜7 の作業はこの結果を待たない。**
3. **`arf_export_differential` の `build_meta` / `format_row` への寄せ** → **寄せる**（Task 5 Step 6。任意ではなく必須）。ただし出力バイト列を変えないことが条件で、Step 5 でメタ行の順序を固定するテストを先に緑にしてから着手する。`build_meta` のスロット設計（`source_lines` / `preprocess_line` / `msi_note`）は ARF の現行順序に合わせてある。寄せた結果 1 件でも落ちたら revert して別名束縛のまま次へ進み `docs/task.md` に起票する。

### 残る不確実性

- 上記 2 の下流互換性のみ。それ以外に未確認の事実は残っていない（「実 API 早見表」の全項目を実ファイルで確認済み）。

### 初版から持ち越した Placeholder はゼロか

- 「実 API 早見表」の全項目を実ファイルで確認済み（`preprocessing.py` / `differential.py` / `arf/reader.py` / `arf/tools.py`）。
- 初版にあった「実際のシグネチャは実装時に確認すること」という先送りの注記は、確認済みの事実に置き換えて削除した。**Task 5（旧「シグネチャ追従」タスク）も不要になったので削除した。**
- 残る「実装中に判断」は上記 3 件のみで、いずれも設計の選択であって未確認の事実ではない。

### 型整合性

- `run_dataset_preprocess` → 6-tuple `(pp_matrix, pp_sample_names, pp_feature_names, roles, sample_meta, report)`。Task 4 の `dataset_preprocess` が全要素を `session.dataset` に配る。
- `run_dataset_pca` → `dict`（`loadings` 込み）。ツール層がセッションに全量、payload から `loadings` を除く。
- `run_dataset_differential` → `dict`（`results` / `volcano` 込み）。ツール層がセッションに全量、payload から両方を除く。`dataset_export_differential` はセッション側を読む。
- 失敗経路は全て `PreconditionError(kind, message, details)`。`None` を返す経路は無い。
- `export_contract.format_row` は 15 キーすべてを要求する（欠けたら `KeyError` で気付く。`.get()` で黙って空欄にしない）。
