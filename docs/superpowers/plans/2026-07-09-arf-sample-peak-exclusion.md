# ARF サンプル/ピーク手動除外ツール Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PCA 外れサンプルや特定ピークを名前/ID で手動除外・再包含できる可逆・非破壊の MCP ツール `arf_exclude` を追加する。

**Architecture:** 除外集合（`excluded_samples` / `excluded_spots`）をセッションに保持し、行列を組む直前に純関数 `exclusions.prune_spots` で `filtered_features` から非破壊プルーニングした派生リストを `build_pca_matrix` に渡す。`filtered_features` 本体は無傷で解除・再解析が自在。除外がスポット/サンプル除去の後・行列構築の前に走るため、平均補完・分散フィルタは残サンプルで再計算される。

**Tech Stack:** Python 3.13, numpy, pandas, fastmcp（MCP）, pytest（unittest 記法）。

## Global Constraints

- テスト実行: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest <path> -q`（Windows / Git Bash）。
- 純ロジック層（`exclusions.py`）は MCP 非依存の leaf モジュール（`preprocessing.py` / `differential.py` と同格）。
- セッション参照は必ず `session_state.session`（module 修飾・動的）で行う。テストは `session_state.session = server.AnalysisSession()` で丸ごと差し替える。
- ツールは `tools_arf.py` に `@mcp.tool()` で定義し、`server.arf_*` で再エクスポートされる（テストは `server.arf_exclude` 等で参照）。
- サンプル照合は `file_name` 完全一致、スポット照合は `MasterAlignmentID`（int）。
- 各エントリの `file_name` は `arf_reader._convert_to_alignment_feature(entry).get("file_name")` で導出（位置インデックス非依存）。
- 除外集合が両方空のとき、`prune_spots` は入力をそのまま返し既存挙動と完全一致（回帰なし）。

---

### Task 1: 純ロジック層 `exclusions.py`（prune_spots / roster）

**Files:**
- Create: `exclusions.py`
- Test: `tests/test_exclusions.py`

**Interfaces:**
- Consumes: `arf_reader._convert_to_alignment_feature(entry) -> dict`（既存。`file_name` キーを持つ）。
- Produces:
  - `prune_spots(spots: list[dict], excluded_samples: set[str], excluded_spots: set[int]) -> list[dict]`
  - `roster(spots: list[dict]) -> tuple[set[str], set[int]]`（存在する file_name 集合, MasterAlignmentID 集合）

- [ ] **Step 1: Write the failing tests**

`tests/test_exclusions.py`:
```python
import unittest
import exclusions


def _row(file_id, name, height):
    """現実的な AlignmentChromPeakFeature 生 row を作る。

    arf_reader._convert_to_alignment_feature は len(data) > 25 かつ
    data[18] が数値のときだけ本来の変換パスに入り、data[:10] 中の文字列を
    file_name として拾う。テストがこの実パスを通るよう 26 要素・data[18] を
    数値・data[1] に名前を置く（3 要素の短い row では実パスに入らない）。"""
    row = [0] * 26
    row[0] = file_id
    row[1] = name           # data[:10] 中の文字列 → file_name として解釈
    row[2] = file_id        # master_peak_id (>=0 → 非ギャップフィル)
    row[18] = float(height)  # height（数値 → 実変換パスを起動）
    return row


def _spot(master_id, rows):
    return {"MasterAlignmentID": master_id,
            "AlignedPeakProperties": [list(r) for r in rows]}


def _fixture():
    # 3 サンプル (sA, sB, sC) x 2 スポット (id=1, id=2)
    return [
        _spot(1, [_row(0, "sA", 10), _row(1, "sB", 20), _row(2, "sC", 30)]),
        _spot(2, [_row(0, "sA", 11), _row(1, "sB", 21), _row(2, "sC", 31)]),
    ]


class TestPruneSpots(unittest.TestCase):
    def test_empty_exclusions_returns_input_unchanged(self):
        spots = _fixture()
        out = exclusions.prune_spots(spots, set(), set())
        self.assertEqual(out, spots)

    def test_exclude_sample_drops_entry_from_all_spots(self):
        spots = _fixture()
        out = exclusions.prune_spots(spots, {"sB"}, set())
        for spot in out:
            names = {row[1] for row in spot["AlignedPeakProperties"]}
            self.assertNotIn("sB", names)
            self.assertEqual(names, {"sA", "sC"})

    def test_exclude_spot_drops_whole_spot(self):
        spots = _fixture()
        out = exclusions.prune_spots(spots, set(), {1})
        ids = {s["MasterAlignmentID"] for s in out}
        self.assertEqual(ids, {2})

    def test_exclude_both(self):
        spots = _fixture()
        out = exclusions.prune_spots(spots, {"sA"}, {2})
        self.assertEqual([s["MasterAlignmentID"] for s in out], [1])
        names = {row[1] for row in out[0]["AlignedPeakProperties"]}
        self.assertEqual(names, {"sB", "sC"})

    def test_non_destructive(self):
        spots = _fixture()
        exclusions.prune_spots(spots, {"sB"}, {1})
        # 元データは不変
        self.assertEqual(len(spots), 2)
        self.assertEqual(len(spots[0]["AlignedPeakProperties"]), 3)

    def test_unknown_names_are_noop(self):
        spots = _fixture()
        out = exclusions.prune_spots(spots, {"zzz"}, {999})
        self.assertEqual(len(out), 2)
        self.assertEqual(len(out[0]["AlignedPeakProperties"]), 3)


class TestRoster(unittest.TestCase):
    def test_roster_returns_names_and_ids(self):
        names, ids = exclusions.roster(_fixture())
        self.assertEqual(names, {"sA", "sB", "sC"})
        self.assertEqual(ids, {1, 2})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_exclusions.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'exclusions'`）

- [ ] **Step 3: Write `exclusions.py`**

```python
"""ARF スポットリストに対するサンプル/スポット手動除外（純ロジック層・MCP 非依存）。

preprocessing.py / differential.py と同格の leaf モジュール。行列を組む直前に
filtered_features から除外集合を適用した派生リストを作るために使う。
サンプルキーは各 AlignedPeakProperties エントリの file_name、スポットキーは
MasterAlignmentID。build_pca_matrix と同じ file_name 導出を用いる。
"""
from __future__ import annotations


def _entry_file_name(entry) -> str | None:
    """AlignedPeakProperties の1エントリ（生 list）から file_name を導出する。"""
    from arf_reader import _convert_to_alignment_feature
    try:
        feature = _convert_to_alignment_feature(entry)
    except Exception:
        return None
    return feature.get("file_name")


def prune_spots(spots, excluded_samples, excluded_spots):
    """除外集合を適用したスポットリストの非破壊コピーを返す。

    - MasterAlignmentID in excluded_spots のスポットを丸ごと除外。
    - 残スポットの AlignedPeakProperties から file_name in excluded_samples の
      エントリを除去する（スポット dict は浅いコピーし、AlignedPeakProperties を
      フィルタ済みリストへ差し替え。元の spots / エントリは変更しない）。
    - 除外集合が両方空なら入力をそのまま返す（コピー不要・恒等）。
    """
    if not excluded_samples and not excluded_spots:
        return spots
    excluded_samples = set(excluded_samples or ())
    excluded_spots = set(excluded_spots or ())
    out = []
    for spot in spots:
        if spot.get("MasterAlignmentID") in excluded_spots:
            continue
        if not excluded_samples:
            out.append(spot)
            continue
        aligned = spot.get("AlignedPeakProperties")
        if not isinstance(aligned, list):
            out.append(spot)
            continue
        kept = [e for e in aligned if _entry_file_name(e) not in excluded_samples]
        new_spot = dict(spot)
        new_spot["AlignedPeakProperties"] = kept
        out.append(new_spot)
    return out


def roster(spots):
    """現データに存在する (サンプル file_name 集合, MasterAlignmentID 集合) を返す。

    除外指定の未一致検出と list 表示に使う。
    """
    names: set[str] = set()
    ids: set[int] = set()
    for spot in spots or []:
        mid = spot.get("MasterAlignmentID")
        if mid is not None:
            ids.add(mid)
        aligned = spot.get("AlignedPeakProperties")
        if isinstance(aligned, list):
            for e in aligned:
                fn = _entry_file_name(e)
                if fn:
                    names.add(fn)
    return names, ids
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_exclusions.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add exclusions.py tests/test_exclusions.py
git commit -m "feat(exclusions): prune_spots/roster 純ロジック層を追加

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: セッション状態に除外集合を追加＋新ファイルでリセット

**Files:**
- Modify: `session_state.py`（`AnalysisSession.__init__`、`load_data` のリセット箇所）
- Test: `tests/test_exclusions_session.py`

**Interfaces:**
- Produces: `session.excluded_samples: set[str]`, `session.excluded_spots: set[int]`（`__init__` で空集合）。新ファイル `load_data`（キャッシュミス経路）で両者を `clear()`。

- [ ] **Step 1: Write the failing test**

`tests/test_exclusions_session.py`:
```python
import io
import unittest

import arf_reader
import session_state


class TestExclusionSessionState(unittest.TestCase):
    def setUp(self):
        session_state.session = session_state.AnalysisSession()

    def test_defaults_are_empty_sets(self):
        self.assertEqual(session_state.session.excluded_samples, set())
        self.assertEqual(session_state.session.excluded_spots, set())

    def test_new_file_load_resets_exclusions(self):
        s = session_state.session
        s.excluded_samples.add("sA")
        s.excluded_spots.add(1)
        # 新ファイル読込（deserialize を差し替えてディスク非依存に）
        orig = arf_reader.deserialize
        arf_reader.deserialize = lambda buf: []
        self.addCleanup(setattr, arf_reader, "deserialize", orig)
        # discover_* は .arf 経路で呼ばれるので空データで無害に通す
        try:
            s.load_data("dummy_path.arf")
        except Exception:
            pass  # discover 系がファイル不在で落ちても、リセットは load_data 冒頭〜features 設定後
        self.assertEqual(s.excluded_samples, set())
        self.assertEqual(s.excluded_spots, set())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_exclusions_session.py -q`
Expected: FAIL（`AttributeError: 'AnalysisSession' object has no attribute 'excluded_samples'`）

- [ ] **Step 3: Edit `session_state.py`**

`AnalysisSession.__init__` の前処理用ブロック末尾（`self.preprocessing_recipe = {}` の直後）に追記:
```python
        # --- 手動除外集合（PCA 外れサンプル / 特定ピークの可逆・非破壊除外） ---
        self.excluded_samples = set()   # 除外する file_name（サンプル）
        self.excluded_spots = set()     # 除外する MasterAlignmentID（スポット）
```

`load_data` のキャッシュミス経路。除外リセットは `with open(...)` の**前**に置く
（存在しないパスでは `open` が先に例外を投げるため、`with` 内に置くと新ファイル読込で
リセットされない。キャッシュヒットの早期 return より後なので同一ファイル再読込では維持される）:
```python
        # 別データに古い手動除外を持ち越さない（open 前に初期化）
        self.excluded_samples = set()
        self.excluded_spots = set()

        print(f"DEBUG: Loading/Deserializing {file_path}", file=sys.stderr)
        with open(file_path, 'rb') as f:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_exclusions_session.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add session_state.py tests/test_exclusions_session.py
git commit -m "feat(session): 手動除外集合 excluded_samples/excluded_spots を保持・新ファイルでリセット

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: MCP ツール `arf_exclude`（add/remove/clear/list）

**Files:**
- Modify: `tools_arf.py`（import に `exclusions` 追加、`__all__` に `arf_exclude` 追加、ツール定義追加）
- Test: `tests/test_arf_exclude.py`

**Interfaces:**
- Consumes: `exclusions.roster(spots)`, `exclusions.prune_spots(spots, es, esp)`, `session.filtered_features`, `session.excluded_samples`, `session.excluded_spots`。
- Produces: `arf_exclude(exclude_samples=None, exclude_spots=None, mode="add") -> str`（JSON 文字列）。戻り JSON キー: `status`, `mode`, `excluded_samples`(list), `excluded_spots`(list), `samples_before`, `samples_after`, `spots_before`, `spots_after`, `unmatched_samples`(list), `unmatched_spots`(list), `caveats`(list)。

- [ ] **Step 1: Write the failing tests**

`tests/test_arf_exclude.py`:
```python
import json
import unittest

import server
import session_state


def _row(file_id, name, height):
    """現実的な AlignmentChromPeakFeature 生 row（len>25・data[18] 数値）。
    exclusions.roster/_convert_to_alignment_feature が実パスで file_name を拾える。"""
    row = [0] * 26
    row[0] = file_id
    row[1] = name
    row[2] = file_id
    row[18] = float(height)
    return row


def _spot(master_id, rows):
    return {"MasterAlignmentID": master_id,
            "AlignedPeakProperties": [list(r) for r in rows]}


def _fixture():
    return [
        _spot(1, [_row(0, "sA", 10), _row(1, "sB", 20), _row(2, "sC", 30)]),
        _spot(2, [_row(0, "sA", 11), _row(1, "sB", 21), _row(2, "sC", 31)]),
    ]


class TestArfExclude(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        session_state.session.filtered_features = _fixture()

    def test_requires_data(self):
        session_state.session.filtered_features = None
        out = json.loads(server.arf_exclude(exclude_samples=["sA"]))
        self.assertEqual(out["status"], "error")

    def test_add_sample_updates_set_and_counts(self):
        out = json.loads(server.arf_exclude(exclude_samples=["sB"]))
        self.assertEqual(out["status"], "success")
        self.assertIn("sB", out["excluded_samples"])
        self.assertEqual(out["samples_before"], 3)
        self.assertEqual(out["samples_after"], 2)
        self.assertEqual(session_state.session.excluded_samples, {"sB"})

    def test_add_spot_updates_set_and_counts(self):
        out = json.loads(server.arf_exclude(exclude_spots=[1]))
        self.assertEqual(out["spots_before"], 2)
        self.assertEqual(out["spots_after"], 1)
        self.assertEqual(session_state.session.excluded_spots, {1})

    def test_remove_re_includes(self):
        server.arf_exclude(exclude_samples=["sB"])
        out = json.loads(server.arf_exclude(exclude_samples=["sB"], mode="remove"))
        self.assertNotIn("sB", out["excluded_samples"])
        self.assertEqual(session_state.session.excluded_samples, set())

    def test_clear_empties_all(self):
        server.arf_exclude(exclude_samples=["sB"], exclude_spots=[1])
        out = json.loads(server.arf_exclude(mode="clear"))
        self.assertEqual(out["excluded_samples"], [])
        self.assertEqual(out["excluded_spots"], [])
        self.assertEqual(session_state.session.excluded_samples, set())
        self.assertEqual(session_state.session.excluded_spots, set())

    def test_list_reports_without_change(self):
        server.arf_exclude(exclude_samples=["sB"])
        out = json.loads(server.arf_exclude(mode="list"))
        self.assertEqual(out["mode"], "list")
        self.assertEqual(out["excluded_samples"], ["sB"])
        self.assertEqual(session_state.session.excluded_samples, {"sB"})

    def test_unmatched_are_reported_and_not_added(self):
        out = json.loads(server.arf_exclude(exclude_samples=["zzz"], exclude_spots=[999]))
        self.assertIn("zzz", out["unmatched_samples"])
        self.assertIn(999, out["unmatched_spots"])
        self.assertEqual(session_state.session.excluded_samples, set())
        self.assertEqual(session_state.session.excluded_spots, set())
        self.assertTrue(out["caveats"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_arf_exclude.py -q`
Expected: FAIL（`AttributeError: module 'server' has no attribute 'arf_exclude'`）

- [ ] **Step 3: Edit `tools_arf.py`**

import ブロック（`import preprocessing` の並び）に追加:
```python
import exclusions
```

`__all__` リストに `"arf_exclude"` を追加。

`arf_list_sample_roles` の直後（`arf_preprocess` の前）にツールを追加:
```python
@mcp.tool()
def arf_exclude(
    exclude_samples: list[str] | None = None,
    exclude_spots: list[int] | None = None,
    mode: str = "add",
) -> str:
    """PCA 外れサンプルや特定ピークを名前/ID で手動除外・再包含する（可逆・非破壊）。

    先に arf_parser で ARF を読み込んでおくこと。除外は session に保持され、以降の
    arf_re_pca / arf_preprocess（→ arf_pca_preprocessed / arf_differential）へ反映される。
    filtered_features 自体は変更しないため、mode="remove"/"clear" で元に戻せる。

    引数:
    - exclude_samples: 除外するサンプル名（file_name、完全一致）のリスト。
    - exclude_spots: 除外するスポットの MasterAlignmentID（int）のリスト。
    - mode: add（既定・追加）/ remove（再包含）/ clear（全消去）/ list（現状表示のみ）。
    """
    spots = session_state.session.filtered_features
    if spots is None:
        return json.dumps({"status": "error",
                           "message": "先に arf_parser で ARF を読み込んでください。"},
                          ensure_ascii=False, indent=2)

    avail_samples, avail_ids = exclusions.roster(spots)
    es = session_state.session.excluded_samples
    esp = session_state.session.excluded_spots
    req_samples = list(exclude_samples or [])
    req_spots = list(exclude_spots or [])
    unmatched_samples: list[str] = []
    unmatched_spots: list[int] = []
    caveats: list[str] = []

    if mode == "clear":
        es.clear()
        esp.clear()
    elif mode == "list":
        pass
    elif mode in ("add", "remove"):
        matched_samples = [n for n in req_samples if n in avail_samples]
        unmatched_samples = [n for n in req_samples if n not in avail_samples]
        matched_spots = [i for i in req_spots if i in avail_ids]
        unmatched_spots = [i for i in req_spots if i not in avail_ids]
        if mode == "add":
            es.update(matched_samples)
            esp.update(matched_spots)
        else:  # remove
            es.difference_update(req_samples)
            esp.difference_update(req_spots)
        if unmatched_samples:
            preview = ", ".join(sorted(avail_samples)[:10])
            caveats.append(
                f"未一致サンプル {unmatched_samples} は現データに存在しません（無視）。"
                f"利用可能サンプル例: {preview}")
        if unmatched_spots:
            caveats.append(
                f"未一致スポット {unmatched_spots} は現データに存在しません（無視）。")
    else:
        return json.dumps({"status": "error",
                           "message": f"unknown mode: {mode!r}（add/remove/clear/list）"},
                          ensure_ascii=False, indent=2)

    pruned = exclusions.prune_spots(spots, es, esp)
    pruned_names, pruned_ids = exclusions.roster(pruned)
    payload = {
        "status": "success",
        "mode": mode,
        "excluded_samples": sorted(es),
        "excluded_spots": sorted(esp),
        "samples_before": len(avail_samples),
        "samples_after": len(pruned_names),
        "spots_before": len(avail_ids),
        "spots_after": len(pruned_ids),
        "unmatched_samples": unmatched_samples,
        "unmatched_spots": unmatched_spots,
        "caveats": caveats,
    }
    if pruned_names == set() or pruned_ids == set():
        payload["caveats"].append(
            "除外の結果、残サンプルまたは残スポットが 0 件です。PCA/差次的解析は実行できません。")
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_arf_exclude.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add tools_arf.py tests/test_arf_exclude.py
git commit -m "feat(arf): arf_exclude ツール（サンプル/ピーク手動除外・可逆）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `arf_preprocess` に除外プルーニングを配線

**Files:**
- Modify: `tools_arf.py`（`arf_preprocess` の行列構築前・報告）
- Test: `tests/test_exclude_wiring.py`（このタスクで新規作成、Task 5 で追記）

**Interfaces:**
- Consumes: `exclusions.prune_spots`, `session.excluded_samples`, `session.excluded_spots`。
- Produces: `arf_preprocess` が除外後スポットで行列を組む。除外有効時、報告 `caveats` に「ユーザ手動除外: サンプル N 件 / スポット M 件」を追加。

- [ ] **Step 1: Write the failing test**

`tests/test_exclude_wiring.py`:
```python
import json
import unittest

import server
import session_state


def _spot(master_id, entries):
    return {"MasterAlignmentID": master_id, "AlignedPeakProperties": list(entries)}


def _fixture():
    # 4 サンプル x 3 スポット。build_pca_matrix が実際に消費できる生 list 行。
    samples = ["sA", "sB", "sC", "sD"]
    spots = []
    for mid in (1, 2, 3):
        rows = []
        for i, name in enumerate(samples):
            row = [i] * 40  # data[18]=height を確保する長さ
            row[1] = name           # file_name（先頭付近の文字列）
            row[2] = 100 + mid       # master_peak_id（>=0 → 非ギャップフィル）
            row[18] = float(10 * (mid + 1) + i)  # height（サンプル間で分散を持たせる）
            rows.append(row)
        spots.append(_spot(mid, rows))
    return spots


class TestPreprocessExcludeWiring(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        session_state.session.filtered_features = _fixture()
        session_state.session.arf_class_index = None

    def test_preprocess_without_exclusion_uses_all_samples(self):
        out = json.loads(server.arf_preprocess())
        # matrix_shape = [n_samples, n_features]
        self.assertEqual(out["matrix_shape"][0], 4)

    def test_preprocess_honors_excluded_sample(self):
        session_state.session.excluded_samples.add("sB")
        out = json.loads(server.arf_preprocess())
        self.assertEqual(out["matrix_shape"][0], 3)
        self.assertTrue(any("手動除外" in c for c in out.get("caveats", [])))

    def test_preprocess_honors_excluded_spot(self):
        session_state.session.excluded_spots.add(1)
        out = json.loads(server.arf_preprocess())
        # 3 スポット → 2 スポット（列数が減る。各スポット1プロパティ height）
        self.assertEqual(out["matrix_shape"][1], 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_exclude_wiring.py::TestPreprocessExcludeWiring -q`
Expected: FAIL（`test_preprocess_honors_excluded_sample` で shape[0] が 4 のまま、または caveat 欠落）

- [ ] **Step 3: Edit `arf_preprocess` in `tools_arf.py`**

`props = props or ["height"]` の直後、`_pp_build_matrix` 呼び出しの前に除外を適用:
```python
    props = props or ["height"]
    # 手動除外（PCA 外れサンプル / 特定ピーク）を行列構築前に適用（非破壊）
    active = exclusions.prune_spots(
        session_state.session.filtered_features,
        session_state.session.excluded_samples,
        session_state.session.excluded_spots,
    )
    matrix, sample_names, feature_names = tool_helpers._pp_build_matrix(active, props)
```
（既存の `matrix, sample_names, feature_names = tool_helpers._pp_build_matrix(session_state.session.filtered_features, props)` 行を上の 2 行目〜に置換する。）

`report["status"] = "success"` の直前に除外の透明化 caveat を追加:
```python
    n_excl_s = len(session_state.session.excluded_samples)
    n_excl_p = len(session_state.session.excluded_spots)
    if n_excl_s or n_excl_p:
        report.setdefault("caveats", []).append(
            f"ユーザ手動除外: サンプル {n_excl_s} 件 / スポット {n_excl_p} 件を除外済み。")
    report["status"] = "success"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_exclude_wiring.py::TestPreprocessExcludeWiring -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add tools_arf.py tests/test_exclude_wiring.py
git commit -m "feat(arf): arf_preprocess に手動除外プルーニングを配線

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `arf_re_pca` に除外プルーニングを配線

**Files:**
- Modify: `tools_arf.py`（`arf_re_pca` の行列構築前・報告）
- Test: `tests/test_exclude_wiring.py`（Task 4 のファイルにクラス追記）

**Interfaces:**
- Consumes: `exclusions.prune_spots`, `session.excluded_samples`, `session.excluded_spots`。
- Produces: `arf_re_pca` が除外後スポットで PCA を再計算。除外有効時、出力テキストへ手動除外の行を追加。

- [ ] **Step 1: Write the failing test**

`tests/test_exclude_wiring.py` に追記:
```python
class TestRePcaExcludeWiring(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        spots = _fixture()
        session_state.session.features = spots
        session_state.session.filtered_features = spots
        session_state.session.current_file_path = "dummy.arf"
        session_state.session.arf_class_index = None
        session_state.session.arf_tag_index = None

    def test_re_pca_honors_excluded_sample(self):
        session_state.session.excluded_samples.add("sB")
        out = server.arf_re_pca()
        text = out[0]
        # 行列形状 (サンプル数 x 特徴量数) に 3 サンプルが反映される
        self.assertIn("(3,", text)
        self.assertIn("手動除外", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_exclude_wiring.py::TestRePcaExcludeWiring -q`
Expected: FAIL（形状に 4 サンプルが出る、または「手動除外」欠落）

- [ ] **Step 3: Edit `arf_re_pca` in `tools_arf.py`**

クラス/タグフィルタ適用後・`session_state.session.filtered_features = filtered_spots` の直後、
`build_pca_matrix` 呼び出しの前に除外を適用:
```python
        # フィルタリング後のデータをセッションの状態に反映
        session_state.session.filtered_features = filtered_spots

        # 手動除外（PCA 外れサンプル / 特定ピーク）を PCA 再計算前に適用（非破壊）
        active_spots = exclusions.prune_spots(
            filtered_spots,
            session_state.session.excluded_samples,
            session_state.session.excluded_spots,
        )

        # 統計情報の計算
        peak_df = extract_peak_properties(active_spots)
        avg_samples = len(peak_df) / len(active_spots) if len(active_spots) > 0 else 0

        # 2. 正確に使い回された関数による行列構築とPCAの実行
        matrix, sample_names, feature_names = build_pca_matrix(
            active_spots, use_properties=props, min_detection_rate=min_detection_rate,
        )
```
（既存の `peak_df = extract_peak_properties(filtered_spots)` / `avg_samples = ...` / `build_pca_matrix(filtered_spots, ...)` の 3 箇所を `active_spots` を使う上記へ置換する。）

出力テキスト組み立て（`output_text = (` ブロック）の直前で除外注記を用意:
```python
        n_excl_s = len(session_state.session.excluded_samples)
        n_excl_p = len(session_state.session.excluded_spots)
        exclude_note = (
            f"- **ユーザ手動除外**: サンプル {n_excl_s} 件 / スポット {n_excl_p} 件\n"
            if (n_excl_s or n_excl_p) else ""
        )
```
`output_text` の `f"- **適用フィルタ条件**: ...\n"` 行の直後に `f"{exclude_note}"` を挿入する。

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_exclude_wiring.py::TestRePcaExcludeWiring -q`
Expected: PASS（1 passed）

- [ ] **Step 5: Commit**

```bash
git add tools_arf.py tests/test_exclude_wiring.py
git commit -m "feat(arf): arf_re_pca に手動除外プルーニングを配線

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `arf_list_sample_roles` に除外フラグを付与

**Files:**
- Modify: `tools_arf.py`（`arf_list_sample_roles` の各サンプル meta に `excluded` を追加）
- Test: `tests/test_arf_exclude.py`（クラス追記）

**Interfaces:**
- Consumes: `session.excluded_samples`。
- Produces: `arf_list_sample_roles` の各サンプル dict に `excluded: bool` を追加。

- [ ] **Step 1: Write the failing test**

`tests/test_arf_exclude.py` に追記:
```python
class TestSampleRolesExcludedFlag(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        session_state.session.filtered_features = _fixture()
        session_state.session.arf_class_index = None

    def test_roles_mark_excluded_samples(self):
        session_state.session.excluded_samples.add("sB")
        out = json.loads(server.arf_list_sample_roles())
        self.assertEqual(out["status"], "success")
        self.assertTrue(out["samples"]["sB"]["excluded"])
        self.assertFalse(out["samples"]["sA"]["excluded"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_arf_exclude.py::TestSampleRolesExcludedFlag -q`
Expected: FAIL（`KeyError: 'excluded'`）

- [ ] **Step 3: Edit `arf_list_sample_roles` in `tools_arf.py`**

`counts` を数える for ループの後、`return` の前に除外フラグを付与:
```python
    for name, m in meta.items():
        m["excluded"] = name in session_state.session.excluded_samples
```
（`counts` 集計ループはそのまま。上記を集計ループの直後・`return json.dumps(...)` の直前に挿入する。）

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/test_arf_exclude.py::TestSampleRolesExcludedFlag -q`
Expected: PASS（1 passed）

- [ ] **Step 5: Commit**

```bash
git add tools_arf.py tests/test_arf_exclude.py
git commit -m "feat(arf): arf_list_sample_roles に excluded フラグを付与

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 全スイート回帰確認とドキュメント更新

**Files:**
- Modify: `docs/output_format.md`（`arf_exclude` の追加・運用フローを追記。既存の未コミット変更と整合させる）
- Test: 全テストスイート

**Interfaces:**
- Consumes: なし（統合確認）。
- Produces: 回帰なしの確証、ユーザ向けドキュメント。

- [ ] **Step 1: 全スイートを実行して回帰が無いことを確認**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS（既存 + 新規テストすべて緑。失敗が出たら該当タスクへ戻る）

- [ ] **Step 2: `docs/output_format.md` に `arf_exclude` を追記**

ARF ツール群の説明箇所に、`arf_exclude(exclude_samples, exclude_spots, mode)` の
用途（PCA 外れサンプル/特定ピークの可逆・非破壊除外）と運用フロー
（`arf_parser` → 外れ特定 → `arf_exclude` → `arf_re_pca` / `arf_preprocess`＋`arf_pca_preprocessed` →
`arf_differential`、`mode="remove"/"clear"` で復帰）を 1 段落追記する。既存文体に合わせる。

- [ ] **Step 3: 再度全スイートを実行（ドキュメントのみなので緑を再確認）**

Run: `PYTHONPATH=. .venv-1/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add docs/output_format.md
git commit -m "docs: arf_exclude（サンプル/ピーク手動除外）の使い方を追記

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 自己レビュー結果

**スペックカバレッジ**: §4.1 セッション状態→Task 2 ／ §4.2 純ロジック（prune_spots/roster）→Task 1 ／ §4.3 arf_exclude ツール→Task 3 ／ §4.4 配線（arf_preprocess→Task 4, arf_re_pca→Task 5, arf_list_sample_roles→Task 6, 透明化注記→Task 4/5）／ §7 テスト計画→各タスクの TDD ／ §6 エッジケース（空集合恒等→Task1, 全除外0件→Task3, リセット→Task2, 未一致→Task3）。全項目に対応タスクあり。

**プレースホルダ**: なし（全ステップに実コード・実コマンド・期待出力を記載）。

**型整合**: `prune_spots(spots, excluded_samples, excluded_spots)` / `roster(spots)->(set,set)` は Task1 定義と Task3/4/5 呼び出しで一致。`arf_exclude(...)->str(JSON)` のキー（`matrix_shape` は arf_preprocess 側、`excluded_samples` 等）も Task3 定義と Task3 テストで一致。`session.excluded_samples/excluded_spots`（set）は Task2 定義と Task3-6 で一致。
