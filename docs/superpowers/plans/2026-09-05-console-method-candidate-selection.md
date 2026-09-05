# メソッドファイル候補の提示とユーザー選択 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生データフォルダだけを渡した `console_plan` が止まったとき、使えるメソッドファイルの候補を機械可読に提示し、ユーザーが選べるようにする。

**Architecture:** 探索の実装は `lipidmix/console/method_file.py` の 1 か所に集約したまま、範囲を「`dataset_root` 直下 → 兄弟フォルダ → 過去 run」へ広げる。`console_plan` / `console_method_template` / 新設 `console_method_candidates` が同じ候補集合を見る。サーバは JSON を返すところまでで、選択 UI はクライアント側（Claude チャットのアーティファクト、将来の手動実行 WebUI）が持つ。

**Tech Stack:** Python 3.14（`C:/Python314/python.exe`）、FastMCP（`mcp` 1.27.1 / `fastmcp` 3.3.0）、pytest。

**Spec:** [docs/superpowers/specs/2026-09-05-console-method-candidate-selection-design.md](../specs/2026-09-05-console-method-candidate-selection-design.md)

## Global Constraints

- テストは**リポジトリルートから** `C:/Python314/python.exe -m pytest tests -q` で走らせる。`.venv/` は無い。`.venv-1/` は使わない。
- 全 MCP ツールに `structured_output=False` を付ける（付けないと MCP が同じ内容を content と structuredContent の両方で送る）。
- JSON は `lipidmix.core.serialization.json_payload()` で返す。`json.dumps(..., indent=2)` を書かない。
- エラーは `lipidmix.core.mcp_errors.console_error(code, message, details, required_tools)` の封筒で返す。**新しい封筒形式を作らない。**
- 探索の再帰はしない。`dataset_root` 直下・兄弟フォルダ直下・`dataset_root/runs/*/analysis-job.json` の 3 か所だけ。
- **別極性の候補を自動採用しない。** `usable="needs_polarity_conversion"` を付けて `console_method_template` を経由させる。
- `key_params` のキー名は実ファイル由来の綴りをそのまま使う（`MS1 mass range begin` であって `Mass range begin` ではない）。
- **テストで `dataset_root` に `tmp_path` そのものを渡さない。** pytest の `tmp_path` は
  `pytest-of-<user>/pytest-<N>/<test_name>0` で、**親フォルダを同一セッションの他テストと共有する**。
  兄弟フォルダ探索を入れると他テストの一時フォルダにある `*_param_<ts>.txt` を拾い、
  テストが実行順に依存する。`root = tmp_path / "POS"` のように 1 段ネストして、
  兄弟が `tmp_path` の中だけになるようにする。
- コミット時に `.githooks/pre-commit` が全テストを走らせる（約 7 秒）。`--no-verify` は使わない。
- 作業後に `docs/HISTRY.md` へ追記し `docs/task.md` のステータスを更新する（**追記専用**。日付見出しで末尾に足し、既存の節は書き換えない）。

---

### Task 1: `key_params` の抽出と `MethodCandidate` の拡張

候補どうしの差を読むための少数キーを抜き出す。`Searched adduct ions` だけは実値が約 700 文字あるので要約する。

**Files:**
- Modify: `lipidmix/console/method_file.py`（`MethodCandidate` 定義の直後にキー定数と関数を足す）
- Test: `tests/test_console_method_file.py`（末尾に追記）

**Interfaces:**
- Consumes: `read_method_keys(path) -> dict[str, str]`（既存。キーは小文字化されている）
- Produces:
  - `KEY_PARAM_KEYS: tuple[str, ...]`（表示順を持つ 11 キー。綴りは実ファイル準拠）
  - `KEY_PARAMS_MAX_CANDIDATES: int = 10`
  - `extract_key_params(method_keys: dict[str, str]) -> dict[str, str]`
  - `MethodCandidate` に `origin: str = "same_dir"` / `usable: str = "direct"` / `key_params: dict[str, str] | None = None` を追加

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_console_method_file.py` の末尾に追記:

```python
# ---------- key_params ----------

def test_extract_key_params_keeps_only_the_decision_relevant_keys():
    keys = {"ion mode": "Positive", "target omics": "Lipidomics",
            "minimum peak height": "1000", "smoothing level": "3",
            "ms1 tolerance for centroid": "0.01"}
    out = extract_key_params(keys)
    assert out == {"Ion mode": "Positive", "Target omics": "Lipidomics",
                   "Minimum peak height": "1000",
                   "MS1 tolerance for centroid": "0.01"}


def test_extract_key_params_uses_the_spelling_from_the_real_param_file():
    """実ファイルの綴りは `MS1 mass range begin`。`Mass range begin` ではない。"""
    keys = {"ms1 mass range begin": "0", "ms1 mass range end": "2000",
            "retention time tolerance for alignment": "0.1",
            "ms1 tolerance for alignment": "0.015"}
    assert set(extract_key_params(keys)) == {
        "MS1 mass range begin", "MS1 mass range end",
        "Retention time tolerance for alignment", "MS1 tolerance for alignment"}


def test_extract_key_params_summarises_the_adduct_list():
    """POS の実値は 37 種・約 700 文字。候補 10 件で 7 KB になるので要約する。"""
    adducts = ",".join(["[M+H]+", "[M+NH4]+", "[M+Na]+"] + [f"[M+X{i}]+" for i in range(34)])
    out = extract_key_params({"searched adduct ions": adducts})
    assert out["Searched adduct ions"] == "37 種（先頭: [M+H]+, [M+NH4]+, [M+Na]+）"


def test_extract_key_params_omits_absent_keys():
    assert extract_key_params({"ion mode": "Negative"}) == {"Ion mode": "Negative"}


def test_method_candidate_defaults_keep_existing_callers_working():
    c = MethodCandidate(path="p", ion_mode="positive", omics="lipidomics",
                        has_lbm=False, mtime=0.0)
    assert (c.origin, c.usable, c.key_params) == ("same_dir", "direct", None)
```

同ファイル冒頭の import に `MethodCandidate` と `extract_key_params` を足す:

```python
from lipidmix.console.method_file import (
    LbmResolution,
    MethodCandidate,
    extract_key_params,
    find_lbm_files,
    find_method_candidates,
    read_method_keys,
    resolve_lbm,
    write_effective_method_file,
)
```

- [ ] **Step 2: 落ちることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_method_file.py -q`
Expected: `ImportError: cannot import name 'extract_key_params'` で収集エラー。

- [ ] **Step 3: 最小の実装**

`lipidmix/console/method_file.py` の `MethodCandidate` 定義を差し替え、直後に定数と関数を足す:

```python
@dataclass(frozen=True)
class MethodCandidate:
    """GUI が自動保存した既存パラメータファイル 1 件。"""

    path: str
    ion_mode: str | None
    omics: str | None
    has_lbm: bool
    mtime: float
    # どこで見つかったか。UI のグルーピングと、同じパスが二重に出たときの優先に使う。
    origin: str = "same_dir"          # same_dir | sibling | past_run | given
    # 求める極性に対してそのまま使えるか。別極性は console_method_template を経由させる。
    usable: str = "direct"            # direct | needs_polarity_conversion
    key_params: dict[str, str] | None = None


# 候補どうしの差を読むのに要る少数キー。全キー（実測 287 行 / 11.7 KB）を候補ごとに
# 返すと戻り値が肥大する。綴りは実ファイル（param_POS_generated.txt）準拠。
KEY_PARAM_KEYS: tuple[str, ...] = (
    "Ion mode",
    "Target omics",
    "Minimum peak height",
    "Retention time begin",
    "Retention time end",
    "MS1 mass range begin",
    "MS1 mass range end",
    "MS1 tolerance for centroid",
    "Retention time tolerance for alignment",
    "MS1 tolerance for alignment",
    "Searched adduct ions",
)

# これを超える候補数では key_params を付けない。比較表は絞ってから引き直す。
KEY_PARAMS_MAX_CANDIDATES = 10

_ADDUCT_PREVIEW = 3


def extract_key_params(method_keys: dict[str, str]) -> dict[str, str]:
    """判断に効くキーだけを、実ファイルの綴りで取り出す。

    `Searched adduct ions` は POS の実値が 37 種・約 700 文字あるので要約する。
    極性の違いは先頭 3 種で判別できる。
    """
    out: dict[str, str] = {}
    for key in KEY_PARAM_KEYS:
        value = method_keys.get(key.lower())
        if value is None or value == "":
            continue
        if key == ADDUCT_KEY:
            items = [t.strip() for t in value.split(",") if t.strip()]
            head = ", ".join(items[:_ADDUCT_PREVIEW])
            out[key] = f"{len(items)} 種（先頭: {head}）"
        else:
            out[key] = value
    return out
```

- [ ] **Step 4: 通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_method_file.py -q`
Expected: PASS（新規 5 件を含む全件）。

- [ ] **Step 5: コミット**

```bash
git add lipidmix/console/method_file.py tests/test_console_method_file.py
git commit -m "feat(console): メソッド候補の比較用に key_params を抽出する"
```

---

### Task 2: ディレクトリ走査を切り出す（振る舞いは変えない）

`find_method_candidates` の中の「1 フォルダを走査して候補を作る」部分を関数に切り出す。Task 4 の新しい入口が同じ走査を共有するため。**この Task で外から見た振る舞いは変わらない。**

**Files:**
- Modify: `lipidmix/console/method_file.py:find_method_candidates`
- Test: `tests/test_console_method_file.py`（既存の `find_method_candidates` テストがそのまま緑であることが検証）

**Interfaces:**
- Consumes: Task 1 の `MethodCandidate`
- Produces: `scan_dir_for_method_files(directory: Path, origin: str) -> list[MethodCandidate]`（`origin` をそのまま載せる。極性・omics での絞り込みはしない）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_console_method_file.py` の末尾に追記:

```python
def test_scan_dir_for_method_files_labels_the_origin(tmp_path):
    p = tmp_path / "Dataset_2026_05_15_10_12_46_param_202605151055.txt"
    p.write_text("Ion mode: Negative\nTarget omics: Lipidomics\n", encoding="ascii")
    found = scan_dir_for_method_files(tmp_path, "sibling")
    assert [c.origin for c in found] == ["sibling"]
    assert found[0].ion_mode == "negative"


def test_scan_dir_for_method_files_does_not_filter_by_polarity(tmp_path):
    """絞り込みは呼び出し側の仕事。ここで落とすと別極性の候補を提示できない。"""
    for name, ion in (("a_param_1.txt", "Positive"), ("b_param_2.txt", "Negative")):
        (tmp_path / name).write_text(f"Ion mode: {ion}\n", encoding="ascii")
    assert len(scan_dir_for_method_files(tmp_path, "same_dir")) == 2


def test_scan_dir_for_method_files_skips_unreadable_directory(tmp_path):
    assert scan_dir_for_method_files(tmp_path / "nope", "same_dir") == []
```

import に `scan_dir_for_method_files` を足す。

- [ ] **Step 2: 落ちることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_method_file.py -q`
Expected: `ImportError: cannot import name 'scan_dir_for_method_files'`。

- [ ] **Step 3: 最小の実装**

`find_method_candidates` の本体を書き換える:

```python
def scan_dir_for_method_files(directory: Path, origin: str) -> list[MethodCandidate]:
    """1 フォルダ直下の `*_param_<ts>.txt` を候補にする。絞り込みはしない。

    絞り込み（極性・omics）を呼び出し側に残すのは、別極性の候補を
    「使えないから消す」のではなく「変換が要る」と提示するため。
    """
    out: list[MethodCandidate] = []
    try:
        entries = sorted(Path(directory).iterdir())
    except OSError:
        return out
    for p in entries:
        if not p.is_file() or not _PARAM_FILENAME.search(p.name):
            continue
        keys = read_method_keys(p)
        if not keys:
            continue  # バイナリ／読めない
        out.append(MethodCandidate(
            path=str(p),
            ion_mode=(keys.get("ion mode") or "").strip().lower() or None,
            omics=(keys.get("target omics") or "").strip().lower() or None,
            has_lbm=bool((keys.get(LBM_KEY.lower()) or "").strip()),
            mtime=p.stat().st_mtime,
            origin=origin,
        ))
    return out


def find_method_candidates(
    directories, polarity: str | None = None, omics: str | None = None,
) -> list[MethodCandidate]:
    """既存の自動保存パラメータ（`*_param_<ts>.txt`）を新しい順に返す。

    GUI は解析のたびに `MethodModelBase.AutoParametersSave` でこれを
    プロジェクトフォルダへ書く。ASCII の `key: value` なので Console がそのまま読める。
    """
    seen: set[Path] = set()
    out: list[MethodCandidate] = []
    for directory in directories:
        for candidate in scan_dir_for_method_files(Path(directory), "same_dir"):
            resolved = Path(candidate.path).resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if polarity is not None and candidate.ion_mode != polarity:
                continue
            if omics is not None and candidate.omics != omics:
                continue
            out.append(candidate)
    out.sort(key=lambda c: c.mtime, reverse=True)
    return out
```

- [ ] **Step 4: 通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS（既存の `find_method_candidates` テストが緑のままであることが「振る舞いを変えていない」証拠）。

- [ ] **Step 5: コミット**

```bash
git add lipidmix/console/method_file.py tests/test_console_method_file.py
git commit -m "refactor(console): メソッド候補の 1 フォルダ走査を切り出す"
```

---

### Task 3: 探索先の列挙（兄弟フォルダと過去 run）

**Files:**
- Modify: `lipidmix/console/method_file.py`
- Test: `tests/test_console_method_file.py`

**Interfaces:**
- Produces:
  - `method_search_dirs(dataset_root: Path, search_dirs=None) -> list[tuple[Path, str]]`（`(directory, origin)` を優先順に。`origin` は `same_dir` / `sibling` / `given`）
  - `past_run_method_files(dataset_root: Path) -> list[Path]`

- [ ] **Step 1: 失敗するテストを書く**

```python
# ---------- 探索先の列挙 ----------

def test_method_search_dirs_lists_self_then_siblings(tmp_path):
    root = tmp_path / "POS"
    root.mkdir()
    (tmp_path / "NEG").mkdir()
    (tmp_path / "OTHER").mkdir()
    pairs = method_search_dirs(root)
    assert pairs[0] == (root, "same_dir")
    assert sorted(str(d.name) for d, o in pairs if o == "sibling") == ["NEG", "OTHER"]


def test_method_search_dirs_excludes_itself_from_the_siblings(tmp_path):
    root = tmp_path / "POS"
    root.mkdir()
    (tmp_path / "NEG").mkdir()
    assert [d for d, o in method_search_dirs(root) if o == "sibling"] == [tmp_path / "NEG"]


def test_method_search_dirs_appends_explicit_dirs_as_given(tmp_path):
    root = tmp_path / "POS"
    root.mkdir()
    extra = tmp_path / "elsewhere"
    extra.mkdir()
    pairs = method_search_dirs(root, [str(extra)])
    assert (extra, "given") in pairs


def test_method_search_dirs_does_not_recurse(tmp_path):
    """兄弟までで止める。深く掘ると無関係なパラメータが候補を濁らせる。"""
    root = tmp_path / "POS"
    root.mkdir()
    deep = tmp_path / "NEG" / "inner"
    deep.mkdir(parents=True)
    assert deep not in [d for d, _o in method_search_dirs(root)]


def test_past_run_method_files_reads_the_recorded_method(tmp_path):
    """analysis-job.json の software.method_file が実際に使ったメソッドを指す。"""
    import json
    used = tmp_path / "param_POS_generated.txt"
    used.write_text("Ion mode: Positive\n", encoding="ascii")
    run = tmp_path / "runs" / "job_1"
    run.mkdir(parents=True)
    (run / "analysis-job.json").write_text(
        json.dumps({"software": {"method_file": str(used)}}), encoding="utf-8")
    assert past_run_method_files(tmp_path) == [used]


def test_past_run_method_files_skips_records_pointing_at_a_deleted_file(tmp_path):
    import json
    run = tmp_path / "runs" / "job_1"
    run.mkdir(parents=True)
    (run / "analysis-job.json").write_text(
        json.dumps({"software": {"method_file": str(tmp_path / "gone.txt")}}), encoding="utf-8")
    assert past_run_method_files(tmp_path) == []


def test_past_run_method_files_ignores_a_broken_job_file(tmp_path):
    run = tmp_path / "runs" / "job_1"
    run.mkdir(parents=True)
    (run / "analysis-job.json").write_text("{not json", encoding="utf-8")
    assert past_run_method_files(tmp_path) == []
```

import に `method_search_dirs` と `past_run_method_files` を足す。

- [ ] **Step 2: 落ちることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_method_file.py -q`
Expected: `ImportError: cannot import name 'method_search_dirs'`。

- [ ] **Step 3: 最小の実装**

`method_file.py` の `find_method_candidates` の後に追記（`import json` をファイル冒頭の標準ライブラリ import に足す）:

```python
RUNS_SUBDIR_NAME = "runs"


def method_search_dirs(dataset_root: Path, search_dirs=None) -> list[tuple[Path, str]]:
    """メソッドファイルを探すフォルダを優先順に返す。

    `dataset_root` 直下 → 兄弟フォルダ直下 → 明示追加。**再帰しない** — 深く掘ると
    無関係なプロジェクトのパラメータが候補に混ざり、比較表が意味を失う。
    """
    root = Path(dataset_root).expanduser()
    pairs: list[tuple[Path, str]] = [(root, "same_dir")]
    try:
        siblings = sorted(p for p in root.parent.iterdir() if p.is_dir())
    except OSError:
        siblings = []
    for sibling in siblings:
        if sibling.resolve() == root.resolve():
            continue
        pairs.append((sibling, "sibling"))
    for extra in (search_dirs or []):
        pairs.append((Path(extra).expanduser(), "given"))
    return pairs


def past_run_method_files(dataset_root: Path) -> list[Path]:
    """過去 run が実際に使ったメソッドファイルを返す。

    `analysis-job.json` の `software.method_file` から引く。**ファイル名で拾わない** —
    `run_dir/effective-method.txt` は「LBM の解決元がメソッドファイル以外だったとき」
    だけ書かれるので、グロブでは取りこぼす。
    """
    runs = Path(dataset_root).expanduser() / RUNS_SUBDIR_NAME
    out: list[Path] = []
    try:
        entries = sorted(runs.iterdir())
    except OSError:
        return out
    for run_dir in entries:
        job = run_dir / "analysis-job.json"
        try:
            record = json.loads(job.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        declared = ((record.get("software") or {}).get("method_file") or "").strip()
        if not declared:
            continue
        path = Path(declared)
        if path.is_file():
            out.append(path)
    return out
```

- [ ] **Step 4: 通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS。

- [ ] **Step 5: コミット**

```bash
git add lipidmix/console/method_file.py tests/test_console_method_file.py
git commit -m "feat(console): メソッド候補の探索先に兄弟フォルダと過去 run を足す"
```

---

### Task 4: 候補の統合（`discover_method_candidates`）

**Files:**
- Modify: `lipidmix/console/method_file.py`
- Test: `tests/test_console_method_file.py`

**Interfaces:**
- Consumes: Task 1〜3 の `extract_key_params` / `scan_dir_for_method_files` / `method_search_dirs` / `past_run_method_files`
- Produces: `discover_method_candidates(dataset_root, *, polarity=None, omics="lipidomics", search_dirs=None) -> tuple[list[MethodCandidate], list[str]]`（候補と、探したフォルダ／ファイルの文字列一覧）

並び順は `usable="direct"` を先に、その中で mtime 降順。重複は `Path.resolve()` で排除し、**`past_run` を優先して残す**（出所の情報量が多いため。spec §9-1）。

- [ ] **Step 1: 失敗するテストを書く**

```python
# ---------- discover_method_candidates ----------

def _write_param(directory, name, ion="Positive", omics="Lipidomics"):
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(f"Ion mode: {ion}\nTarget omics: {omics}\n"
                 "Minimum peak height: 1000\nLbm file path: \n", encoding="ascii")
    return p


def test_discover_marks_the_other_polarity_as_needing_conversion(tmp_path):
    root = tmp_path / "POS"
    root.mkdir()
    _write_param(tmp_path / "NEG", "d_param_1.txt", ion="Negative")
    found, _searched = discover_method_candidates(root, polarity="positive")
    assert [(c.origin, c.usable) for c in found] == [("sibling", "needs_polarity_conversion")]


def test_discover_puts_directly_usable_candidates_first(tmp_path):
    root = tmp_path / "POS"
    _write_param(root, "own_param_2.txt", ion="Positive")
    _write_param(tmp_path / "NEG", "neg_param_1.txt", ion="Negative")
    found, _searched = discover_method_candidates(root, polarity="positive")
    assert [c.usable for c in found] == ["direct", "needs_polarity_conversion"]


def test_discover_attaches_key_params_for_a_small_candidate_set(tmp_path):
    root = tmp_path / "POS"
    _write_param(root, "own_param_1.txt")
    found, _searched = discover_method_candidates(root, polarity="positive")
    assert found[0].key_params["Minimum peak height"] == "1000"


def test_discover_omits_key_params_beyond_the_cap(tmp_path):
    root = tmp_path / "POS"
    for i in range(KEY_PARAMS_MAX_CANDIDATES + 1):
        _write_param(root, f"p{i}_param_{i}.txt")
    found, _searched = discover_method_candidates(root, polarity="positive")
    assert len(found) == KEY_PARAMS_MAX_CANDIDATES + 1
    assert all(c.key_params is None for c in found)


def test_discover_prefers_past_run_origin_for_a_duplicate_path(tmp_path):
    """過去 run のメソッドがユーザーのフォルダにある元ファイルを指すことがある。"""
    import json
    root = tmp_path / "POS"
    used = _write_param(root, "own_param_1.txt")
    run = root / "runs" / "job_1"
    run.mkdir(parents=True)
    (run / "analysis-job.json").write_text(
        json.dumps({"software": {"method_file": str(used)}}), encoding="utf-8")
    found, _searched = discover_method_candidates(root, polarity="positive")
    assert len(found) == 1
    assert found[0].origin == "past_run"


def test_discover_filters_by_omics(tmp_path):
    root = tmp_path / "POS"
    _write_param(root, "lip_param_1.txt", omics="Lipidomics")
    _write_param(root, "met_param_2.txt", omics="Metabolomics")
    found, _searched = discover_method_candidates(root, polarity="positive", omics="lipidomics")
    assert [Path(c.path).name for c in found] == ["lip_param_1.txt"]


def test_discover_reports_where_it_looked(tmp_path):
    root = tmp_path / "POS"
    root.mkdir()
    (tmp_path / "NEG").mkdir()
    _found, searched = discover_method_candidates(root, polarity="positive")
    assert str(root) in searched
    assert str(tmp_path / "NEG") in searched
```

import に `KEY_PARAMS_MAX_CANDIDATES` と `discover_method_candidates` を足す。

- [ ] **Step 2: 落ちることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_method_file.py -q`
Expected: `ImportError: cannot import name 'discover_method_candidates'`。

- [ ] **Step 3: 最小の実装**

```python
def discover_method_candidates(
    dataset_root,
    *,
    polarity: str | None = None,
    omics: str | None = "lipidomics",
    search_dirs=None,
) -> tuple[list[MethodCandidate], list[str]]:
    """候補を集めて `usable` と `key_params` を付ける。探した場所も返す。

    極性で**落とさない**。別極性は `needs_polarity_conversion` として提示し、
    console_method_template を経由させる（黙って別解析にしないため）。
    """
    root = Path(dataset_root).expanduser()
    by_path: dict[Path, MethodCandidate] = {}
    searched: list[str] = []

    for directory, origin in method_search_dirs(root, search_dirs):
        searched.append(str(directory))
        for candidate in scan_dir_for_method_files(directory, origin):
            by_path.setdefault(Path(candidate.path).resolve(), candidate)

    runs_dir = root / RUNS_SUBDIR_NAME
    if runs_dir.is_dir():
        searched.append(str(runs_dir))
    for path in past_run_method_files(root):
        keys = read_method_keys(path)
        if not keys:
            continue
        # 出所の情報量が多い past_run を優先して上書きする（同じパスが
        # same_dir としても拾われうる）。
        by_path[path.resolve()] = MethodCandidate(
            path=str(path),
            ion_mode=(keys.get("ion mode") or "").strip().lower() or None,
            omics=(keys.get("target omics") or "").strip().lower() or None,
            has_lbm=bool((keys.get(LBM_KEY.lower()) or "").strip()),
            mtime=path.stat().st_mtime,
            origin="past_run",
        )

    selected = [c for c in by_path.values()
                if omics is None or c.omics == omics]
    annotated: list[MethodCandidate] = []
    attach_params = len(selected) <= KEY_PARAMS_MAX_CANDIDATES
    for candidate in selected:
        usable = ("direct" if polarity is None or candidate.ion_mode == polarity
                  else "needs_polarity_conversion")
        key_params = (extract_key_params(read_method_keys(Path(candidate.path)))
                      if attach_params else None)
        annotated.append(replace(candidate, usable=usable, key_params=key_params))

    annotated.sort(key=lambda c: (c.usable != "direct", -c.mtime))
    return annotated, searched
```

`from dataclasses import dataclass, field, replace` になるよう冒頭の import を直す（現状 `replace` が無ければ足す）。

- [ ] **Step 4: 通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS。

- [ ] **Step 5: コミット**

```bash
git add lipidmix/console/method_file.py tests/test_console_method_file.py
git commit -m "feat(console): メソッド候補を統合し usable と key_params を付ける"
```

---

### Task 5: `console_method_candidates` ツールの新設

ツールが 1 件増えるので、腐敗防止テストが縛る 4 か所を**同じコミットで**揃える。

**Files:**
- Modify: `lipidmix/tools/console_tools.py`（`__all__` と新ツール）
- Modify: `tests/test_server_registration.py`（`EXPECTED_TOOLS`）
- Modify: `tests/test_workflow_docs.py`（`OUT_OF_SCOPE`）
- Modify: `USAGE.md`（見出しの件数・Console 節の行）
- Modify: `CLAUDE.md`（冒頭の規模表記）
- Modify: `docs/workflow/index.md`（Console 実行層の件数と列挙、合計）
- Test: `tests/test_console_method_candidates.py`（新規）

**Interfaces:**
- Consumes: `discover_method_candidates`（Task 4）
- Produces: MCP ツール `console_method_candidates(dataset_root, polarity=None, omics="lipidomics", search_dirs=None) -> str`（JSON）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_console_method_candidates.py` を新規作成:

```python
"""候補提示ツール。失敗する前に候補を列挙できることが要点。

手動実行 WebUI の「メソッドを選ぶ」画面はこれを叩く。console_plan の封筒を
待たないと候補が分からない設計では、画面を先に描けない。
"""
from __future__ import annotations

import json as _json
from pathlib import Path

from lipidmix.tools.console_tools import console_method_candidates


def _write_param(directory: Path, name: str, ion: str = "Positive") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(f"Ion mode: {ion}\nTarget omics: Lipidomics\n"
                 "Minimum peak height: 1000\n", encoding="ascii")
    return p


def test_lists_a_sibling_candidate_of_the_other_polarity(tmp_path):
    root = tmp_path / "POS"
    root.mkdir()
    _write_param(tmp_path / "NEG", "d_param_1.txt", ion="Negative")
    parsed = _json.loads(console_method_candidates(str(root), polarity="positive"))
    assert parsed["n_candidates"] == 1
    entry = parsed["candidates"][0]
    assert entry["origin"] == "sibling"
    assert entry["usable"] == "needs_polarity_conversion"
    assert entry["key_params"]["Ion mode"] == "Negative"


def test_reports_where_it_looked_when_nothing_is_found(tmp_path):
    root = tmp_path / "POS"
    root.mkdir()
    parsed = _json.loads(console_method_candidates(str(root), polarity="positive"))
    assert parsed["n_candidates"] == 0
    assert str(root) in parsed["searched"]


def test_polarity_may_be_omitted(tmp_path):
    root = tmp_path / "POS"
    _write_param(root, "own_param_1.txt")
    parsed = _json.loads(console_method_candidates(str(root)))
    assert parsed["candidates"][0]["usable"] == "direct"


def test_missing_dataset_root_is_an_envelope_not_an_exception(tmp_path):
    parsed = _json.loads(console_method_candidates(str(tmp_path / "nope")))
    assert parsed["error"]["code"] == "DATASET_ROOT_NOT_FOUND"
```

- [ ] **Step 2: 落ちることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_method_candidates.py -q`
Expected: `ImportError: cannot import name 'console_method_candidates'`。

- [ ] **Step 3: 最小の実装**

`lipidmix/tools/console_tools.py` の `__all__` を差し替え:

```python
__all__ = ["console_plan", "console_prepare_input", "console_method_template",
           "console_method_candidates", "console_run", "console_status",
           "console_cleanup", "job_list"]
```

`console_method_template` の定義の直後に足す:

```python
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
```

ファイル末尾のヘルパ群（`_artifacts_tsv` の近く）に足す:

```python
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
```

`json_payload` / `console_error` / `method_file_mod` / `Path` が既に import 済みであることを確認する（`console_plan` が全て使っている）。

- [ ] **Step 4: 通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_method_candidates.py -q`
Expected: PASS。

- [ ] **Step 5: 腐敗防止テストが落ちることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_server_registration.py tests/test_workflow_docs.py tests/test_readme_links.py -q`
Expected: FAIL — 登録ツール数が `EXPECTED_TOOLS` と合わない／分類と登録が一致しない／USAGE.md の件数が実登録と合わない。

- [ ] **Step 6: 4 か所を揃える**

`tests/test_server_registration.py` の `EXPECTED_TOOLS` に `"console_method_candidates",` を（アルファベット順で `console_method_template` の前に）足す。

`tests/test_workflow_docs.py` の `OUT_OF_SCOPE` に `"console_method_candidates",` を足す。

`USAGE.md` 1 行目を `# USAGE — ms-data-parser MCP ツール一覧(全55ツール)` にする。Console 節の `console_method_template` の行の直後に足す:

```markdown
| `console_method_candidates` | 使えるメソッドファイルの候補を列挙する(`dataset_root`, `polarity`, `omics`, `search_dirs`)。**`console_plan` が失敗する前に呼べる** — 手動実行 UI の「メソッドを選ぶ」画面はこれを叩く。探すのは `dataset_root` 直下 → 兄弟フォルダ直下 → `runs/*/analysis-job.json` の `software.method_file`(過去 run が実際に使ったメソッド)で、**再帰はしない**。各候補に `origin`(`same_dir`/`sibling`/`past_run`/`given`)、`usable`(`direct` / `needs_polarity_conversion`)、比較用の `key_params` が付く(候補 10 件超では `key_params` を付けない)。別極性の候補は自動採用せず `console_method_template` を経由させる。 |
```

`CLAUDE.md` 8 行目の `ツール 54・リソース 4・リソーステンプレート 3` を `ツール 55・リソース 4・リソーステンプレート 3` にする。

`docs/workflow/index.md` の該当箇所を書き換える:

```markdown
合計 35 ツール。対象外は 20 ツール——文献探索・レポート記録系 12
（`record_objective` `knowledge_coverage` `paper_search` `ingest_*` `write_report` など）＋
Console 実行層 8（`console_plan` `console_prepare_input` `console_method_template`
`console_method_candidates` `console_run` `console_status` `console_cleanup` `job_list`）。
登録ツール総数は 55（35 + 20）。
```

- [ ] **Step 7: 全テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS。

- [ ] **Step 8: コミット**

```bash
git add lipidmix/tools/console_tools.py tests/test_console_method_candidates.py \
        tests/test_server_registration.py tests/test_workflow_docs.py \
        USAGE.md CLAUDE.md docs/workflow/index.md
git commit -m "feat(console): メソッドファイル候補を列挙する console_method_candidates を足す"
```

---

### Task 6: `console_plan` の分岐を 3 つにする

**Files:**
- Modify: `lipidmix/tools/console_tools.py:console_plan`（`method_file is None` の分岐）
- Test: `tests/test_console_plan_method.py`

**Interfaces:**
- Consumes: `discover_method_candidates`（Task 4）、`_candidate_payload`（Task 5）
- Produces: 封筒 `METHOD_FILE_CHOICE_REQUIRED`（`details.candidates` / `details.searched`、`required_tools: ["console_method_template", "console_plan"]`）

| 状況 | 結果 |
|---|---|
| 極性一致の候補が 1 件以上 | 成功。最新を採用し `method_source.discovered_from` に出所 |
| 候補はあるが極性一致が無い | `METHOD_FILE_CHOICE_REQUIRED` |
| 候補 0 件 | `METHOD_FILE_NOT_GIVEN`（`details.searched` を追加） |

- [ ] **Step 0: 既存テストを兄弟探索から隔離する**

`tests/test_console_plan_method.py` には `dataset_root=str(tmp_path)` を渡して**探索を走らせる**
テストが 3 件ある。このまま Task 6 を入れると pytest の共有親フォルダを兄弟として走査し、
他テストの `*_param_<ts>.txt` を拾って実行順に依存する。1 段ネストして隔離する。

`_plan_ready` の直後にヘルパを足す:

```python
def _nested_root(tmp_path):
    """dataset_root を 1 段ネストする。

    tmp_path をそのまま dataset_root にすると、兄弟フォルダ探索が pytest の共有親
    （pytest-<N>/）を走査して他テストの一時フォルダを拾う。
    """
    root = tmp_path / "dataset"
    root.mkdir(exist_ok=True)
    (root / "a.wiff").touch()
    return root
```

次の 3 テストを書き換える。いずれも「パラメータを置く場所」と「dataset_root」を
`_nested_root(tmp_path)` が返すフォルダに揃える:

```python
def test_console_plan_discovers_auto_saved_method_file(tmp_path, monkeypatch):
    """GUI は実行のたびに *_param_<ts>.txt を自動保存する。渡さなくても見つける。"""
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    root = _nested_root(tmp_path)
    (root / "Dataset_2026_param_202605151055.txt").write_text(
        "Ion mode: Negative\nTarget omics: Lipidomics\nLbm file path: \n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(root),
                                      polarity="negative", measure="peak_height"))
    assert parsed["status"] == "planned"
    assert parsed["method_source"]["discovered_from"].endswith(
        "Dataset_2026_param_202605151055.txt")


def test_console_plan_discovery_ignores_other_polarity(tmp_path, monkeypatch):
    """NEG の自動保存パラメータを POS の計画に流用してはいけない。

    候補として提示はするが、採用はしない（METHOD_FILE_CHOICE_REQUIRED）。
    """
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    root = _nested_root(tmp_path)
    (root / "Dataset_2026_param_202605151055.txt").write_text(
        "Ion mode: Negative\nTarget omics: Lipidomics\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(root),
                                      polarity="positive", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_CHOICE_REQUIRED"
    assert parsed["error"]["details"]["candidates"][0]["usable"] == "needs_polarity_conversion"


def test_console_plan_missing_method_file_reports_no_candidates(tmp_path, monkeypatch):
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    root = _nested_root(tmp_path)
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(root),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_GIVEN"
```

`test_console_plan_discovery_ignores_other_polarity` は**期待する code が変わる**（`METHOD_FILE_NOT_GIVEN`
→ `METHOD_FILE_CHOICE_REQUIRED`）。テストの意図（NEG を POS に流用しない）は変わっておらず、
「黙って落とす」から「候補として見せて止める」に変えたことの反映。

`tests/test_console_method_template.py` は既に `dataset_root` を `tmp_path/data` にネストして
おり（`_ready` は `tmp_path/app` に `.lbm2` を置くだけでパラメータは書かない）、この隔離は不要。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_console_plan_method.py` の末尾に追記:

```python
def test_console_plan_offers_a_sibling_folder_candidate(tmp_path, monkeypatch):
    """実データの形（POS の隣に GUI 処理済みの NEG がある）。黙って採用せず提示する。"""
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    root = _nested_root(tmp_path)
    neg = tmp_path / "NEG"
    neg.mkdir()
    (neg / "Dataset_2026_param_202605151055.txt").write_text(
        "Ion mode: Negative\nTarget omics: Lipidomics\n", encoding="ascii")

    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(root), polarity="positive"))
    error = parsed["error"]
    assert error["code"] == "METHOD_FILE_CHOICE_REQUIRED"
    entry = error["details"]["candidates"][0]
    assert entry["origin"] == "sibling"
    assert entry["usable"] == "needs_polarity_conversion"
    assert "console_method_template" in error["required_tools"]


def test_console_plan_adopts_a_sibling_of_the_same_polarity(tmp_path, monkeypatch):
    """極性が一致していれば兄弟フォルダのものでも採用してよい（変換が要らない）。"""
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    root = _nested_root(tmp_path)
    other = tmp_path / "OTHER"
    other.mkdir()
    (other / "Dataset_2026_param_202605151055.txt").write_text(
        "Ion mode: Negative\nTarget omics: Lipidomics\nLbm file path: \n", encoding="ascii")

    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(root), polarity="negative"))
    assert parsed["status"] == "planned"
    assert parsed["method_source"]["discovered_from"].endswith(
        "Dataset_2026_param_202605151055.txt")


def test_console_plan_not_given_reports_where_it_looked(tmp_path, monkeypatch):
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    root = _nested_root(tmp_path)
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(root), polarity="positive"))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_GIVEN"
    assert str(root) in parsed["error"]["details"]["searched"]
```

- [ ] **Step 2: 落ちることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_plan_method.py -q`
Expected: FAIL 4 件 —
`test_console_plan_discovery_ignores_other_polarity`（Step 0 で期待を変えたが実装がまだ `METHOD_FILE_NOT_GIVEN`）、
`test_console_plan_offers_a_sibling_folder_candidate`（同上）、
`test_console_plan_adopts_a_sibling_of_the_same_polarity`（兄弟を見ていないので `METHOD_FILE_NOT_GIVEN`）、
`test_console_plan_not_given_reports_where_it_looked`（`details` に `searched` が無い → `KeyError`）。

- [ ] **Step 3: 最小の実装**

`console_plan` の該当ブロックを差し替える:

```python
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
```

- [ ] **Step 4: 通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS。既存の「同じフォルダに極性一致のパラメータがあれば自動採用」テストも緑のまま。

- [ ] **Step 5: USAGE.md の `console_plan` の行を更新**

`(見つからなければ `METHOD_FILE_NOT_GIVEN`)` の部分を次に差し替える:

```
(極性一致が無くて別極性の候補があれば `METHOD_FILE_CHOICE_REQUIRED` に候補一覧を載せて止まり、候補が 0 件なら `METHOD_FILE_NOT_GIVEN`。どちらも `details.searched` に探した場所が入る。探索は `dataset_root` 直下 → 兄弟フォルダ → 過去 run で、候補の列挙だけなら `console_method_candidates`)
```

- [ ] **Step 6: コミット**

```bash
git add lipidmix/tools/console_tools.py tests/test_console_plan_method.py USAGE.md
git commit -m "feat(console): console_plan が別極性の候補を提示して止まるようにする"
```

---

### Task 7: `console_method_template` の `based_on` 省略を広げる

**Files:**
- Modify: `lipidmix/tools/console_tools.py:console_method_template`
- Test: `tests/test_console_method_template.py`（`_ready` / `_neg_param` が既にある。`dataset_root` は
  既に `tmp_path/data` にネストされているので兄弟探索の隔離は不要）

**Interfaces:**
- Consumes: `discover_method_candidates`（Task 4）、`_candidate_payload`（Task 5）

`based_on` 省略時は同じ探索を使う。候補が 1 件なら採用、複数なら `METHOD_FILE_CHOICE_REQUIRED`。**mtime 順の最新を黙って採らない** — `based_on` は「何を土台にするか」という解析条件そのものの選択。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_console_method_template.py` の末尾に追記:

```python
def test_template_finds_a_sibling_folder_when_based_on_is_omitted(tmp_path, monkeypatch):
    """実データの形。POS のフォルダには何も無く、隣の NEG に GUI 由来のものがある。"""
    from lipidmix.tools.console_tools import console_method_template
    _ready(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    neg = tmp_path / "NEG"
    neg.mkdir()
    _neg_param(neg / "Dataset_2026_param_202605151055.txt")

    parsed = _json.loads(console_method_template(
        out_path=str(tmp_path / "param_POS.txt"), polarity="positive",
        dataset_root=str(data)))
    assert parsed["status"] == "written"
    assert Path(parsed["based_on"]).name == "Dataset_2026_param_202605151055.txt"


def test_template_asks_which_base_when_several_exist(tmp_path, monkeypatch):
    """土台の選択は解析条件そのもの。mtime 順の最新を黙って採らない。"""
    from lipidmix.tools.console_tools import console_method_template
    _ready(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    neg = tmp_path / "NEG"
    neg.mkdir()
    _neg_param(neg / "a_param_1.txt")
    _neg_param(neg / "b_param_2.txt")

    parsed = _json.loads(console_method_template(
        out_path=str(tmp_path / "param_POS.txt"), polarity="positive",
        dataset_root=str(data)))
    assert parsed["error"]["code"] == "METHOD_FILE_CHOICE_REQUIRED"
    assert len(parsed["error"]["details"]["candidates"]) == 2
```

- [ ] **Step 2: 落ちることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_console_method_template.py -q`
Expected: FAIL 2 件 — どちらも `METHOD_FILE_NOT_GIVEN`（`dataset_root` 直下しか見ていない）。

- [ ] **Step 3: 最小の実装**

`console_method_template` の `elif dataset_root:` ブロックを差し替える:

```python
    elif dataset_root:
        # 極性で絞らない。「別極性から作る」のがこのツールの用途なので、
        # polarity=None のまま候補を集める。
        candidates, searched = method_file_mod.discover_method_candidates(
            Path(dataset_root).expanduser(), polarity=None, omics=omics)
        if not candidates:
            return console_error(
                "METHOD_FILE_NOT_GIVEN",
                "元にできるパラメータファイル（`*_param_<ts>.txt`）が見つかりません: "
                f"{dataset_root}  MS-DIAL GUI で一度も解析していないフォルダには"
                "存在しません（兄弟フォルダと過去 run も探しました）。"
                "他のデータセットのパラメータを based_on で明示してください。",
                {"dataset_root": dataset_root, "searched": searched})
        if len(candidates) > 1:
            return console_error(
                "METHOD_FILE_CHOICE_REQUIRED",
                f"元にできる候補が {len(candidates)} 件あります。based_on で 1 つ"
                "選んでください。検出・アライメント条件は選んだファイルのものが"
                "そのまま引き継がれるため、どれを土台にするかは解析条件の選択です。",
                {"dataset_root": dataset_root, "searched": searched,
                 "candidates": [_candidate_payload(c) for c in candidates]},
                required_tools=["console_method_template"])
        src = Path(candidates[0].path)
```

- [ ] **Step 4: 通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS。

- [ ] **Step 5: USAGE.md の `console_method_template` の行を更新**

`**まず `console_plan` の `method_file` 省略を試すこと**` の直後に足す:

```
`based_on` を省略すると `dataset_root` 直下・兄弟フォルダ・過去 run から候補を探し、1 件なら採用、複数なら `METHOD_FILE_CHOICE_REQUIRED` で選ばせる(土台の選択は解析条件そのものなので最新を黙って採らない)。
```

- [ ] **Step 6: コミット**

```bash
git add lipidmix/tools/console_tools.py tests/test_console_plan_method.py USAGE.md
git commit -m "feat(console): console_method_template の based_on 省略を兄弟フォルダまで広げる"
```

---

### Task 8: 実データでの通し確認と記録

単体テストは fixture を自分で作るので、実データのフォルダ構成（`POS_wiff` の隣に `NEG` がある）で意図どおり動くかは別に確かめる。

**Files:**
- Modify: `docs/HISTRY.md`（追記）
- Modify: `docs/task.md`（追記）

- [ ] **Step 1: 候補提示を実データで確認**

Run:

```bash
C:/Python314/python.exe -c "import sys; sys.path.insert(0,'.'); import server, json; print(server.console_method_candidates('C:/Users/yuu18/datasets/2_lipidome_lcms/POS_wiff', polarity='positive'))"
```

Expected: `n_candidates >= 2`。`POS_wiff/param_POS_generated.txt` が `usable="direct"`（`origin` は `past_run` — 実走のジョブが `software.method_file` にこれを記録している）、`NEG/Dataset_2026_05_15_10_12_46_param_202605151055.txt` が `origin="sibling"` / `usable="needs_polarity_conversion"` で出ること。`searched` に `POS`・`NEG`・`POS_console.prev_20260904_1450` が並ぶこと。

- [ ] **Step 2: 候補ゼロだった状況が解消していることを確認**

Run:

```bash
C:/Python314/python.exe -c "import sys; sys.path.insert(0,'.'); import server; print(server.console_plan(dataset_root='C:/Users/yuu18/datasets/2_lipidome_lcms/POS_wiff', polarity='positive')[:400])"
```

Expected: `status="planned"`。`method_source.discovered_from` が `param_POS_generated.txt` を指すこと（2026-09-04 の実走では `METHOD_FILE_NOT_GIVEN` で止まっていた）。**この確認で作られたジョブは使わないので、`runs/` に増えた新しいジョブフォルダを削除する。**

- [ ] **Step 3: 別極性しか無い状況を確認**

Run:

```bash
C:/Python314/python.exe -c "import sys; sys.path.insert(0,'.'); import server; print(server.console_plan(dataset_root='C:/Users/yuu18/datasets/2_lipidome_lcms/POS_wiff', polarity='negative')[:600])"
```

Expected: `METHOD_FILE_CHOICE_REQUIRED` で、`candidates` に POS 側のファイルが `needs_polarity_conversion` として並ぶこと。

- [ ] **Step 4: 全テスト**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS。

- [ ] **Step 5: 記録して仕上げる**

`docs/HISTRY.md` の末尾を見て、その日の既存の最大番号 + 1 を N とし、`## 2026-09-05(N) メソッドファイル候補の提示とユーザー選択` の見出しで**末尾に追記**する（既存の節は書き換えない。追跡外ファイルなので git が競合を検出せず、書き換えは後勝ちで静かに消える）。書く内容: 探索範囲の 3 か所、`METHOD_FILE_CHOICE_REQUIRED` を新設した理由（別極性の自動採用は黙って別解析になる）、Step 1〜3 の実データ確認結果、`key_params` のキー名が実ファイル由来であること。

`docs/task.md` にも同じ N で `## 2026-09-05(N) メソッドファイル候補の提示とユーザー選択 — DONE` を追記し、spec の未解決 3 点（§9）のうち残っているものを HOLD で残す。

- [ ] **Step 6: コミット**

```bash
git add -A
git commit -m "docs: メソッドファイル候補の提示を実データで確認した結果を記録する"
```

---

## 実装後に残ること（この計画の外）

- **アーティファクト UI**（候補の比較ビューア）はサーバ側の変更ではないので、この計画に含めない。`console_method_candidates` の JSON がそのまま入力になる。
- **手動実行 WebUI**（Use-LLLM 側）も別リポジトリなのでこの計画の外。
- spec §9 の未解決 3 点のうち、兄弟フォルダ探索のコスト（NAS 配置）と `key_params` のキー名の他装置での妥当性は、実測できる環境が出てきたときに確かめる。
