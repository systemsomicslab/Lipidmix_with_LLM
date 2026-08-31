# 差次的結果の InChIKey 付きエクスポート 実装計画（ms-data-parser 側）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 群間比較の結果を、InChIKey と来歴を伴う 1 つの TSV ファイルとして書き出せるようにする。併せて log2FC の符号を一般的な慣習に合わせる。

**Architecture:** 既存の `arf_differential` が `session.arf.last_differential` に残す全特徴の結果を土台にし、同一アラインメントの兄弟 `.arf2` から InChIKey・Ontology・m/z・RT を `MasterAlignmentID` で結合して書き出す。結合は既存の `_sibling_arf2_path()`（語幹一致による兄弟解決）を再利用する。新ツールは 1 本だけで、既存ツールの戻り値は変えない。

**Tech Stack:** Python 3.14 / FastMCP / numpy / unittest（pytest で実行）

**Spec:** `C:/Users/yuu18/massbank-context/docs/superpowers/specs/2026-08-31-pathway-activity-design.md`（§3.5・§3.6・§6.1・§12 Phase A）

**このリポジトリ:** `C:/Users/yuu18/Lipidmix_with_LLM`（MCP サーバ名 `ms-data-parser`）。**全コマンドはリポジトリルートで実行する。**

## Global Constraints

- Python は `C:/Python314/python.exe`（`python` は PATH に無いことがある）
- テストは `C:/Python314/python.exe -m pytest tests -q` をリポジトリルートから実行する
- コード・コメント・docstring・コミットメッセージはすべて**日本語**
- 戻り値は `lipidmix/core/serialization.py` の `json_payload()` で返す。`json.dumps(..., indent=2)` を書かない
- エラーは `lipidmix/core/mcp_errors.py` の `missing_state(state, required_tools, message)` を使う
- MCP ツールは `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)` で登録し、`__all__` に載せる
- 可変状態は `session_state.session.arf` のようにモジュール修飾で参照する（スナップショット束縛を作らない）
- **契約の符号**: `log2fc = mean_b − mean_a`（log 空間）。**正 = group_b が高い = 上昇**。`group_a` が基準（対照）、`group_b` が比較対象

---

## File Structure

| ファイル | 責務 |
|---|---|
| `lipidmix/analysis/differential.py`（修正） | `_log2fc` と `two_group_test` の符号。統計の計算のみ |
| `lipidmix/arf/tools.py`（修正） | `arf_differential` の caveats に向きを開示。新ツール `arf_export_differential` |
| `lipidmix/arf/identity_join.py`（新規） | `.arf` の spot id と `.arf2` の同定情報を結合する純関数。ファイル I/O は呼び出し側 |
| `docs/output_format/arf.md`（修正） | 符号の記述 3 箇所と、新ツールの契約 |
| `tests/test_differential.py`（修正） | 符号の既知解 |
| `tests/test_identity_join.py`（新規） | 結合関数 |
| `tests/test_export_differential.py`（新規） | 書き出しツール |
| `tests/test_server_registration.py`（修正） | 登録スナップショット |

`identity_join.py` を独立させるのは、`arf/tools.py` が既に 1,000 行を超えており、結合ロジックを純関数として単独でテストしたいためである。ファイルの読み込み（`load_catalog`）は `tools.py` に残し、`identity_join.py` は**辞書を受けて辞書を返すだけ**にする。

---

## Task 1: log2FC の符号を慣習に合わせる

**Files:**
- Modify: `lipidmix/analysis/differential.py:135-139`（`_log2fc`）
- Modify: `lipidmix/analysis/differential.py:158-168`（`two_group_test` の 2 分岐）
- Test: `tests/test_differential.py:36-40`, `tests/test_differential.py:95-107`

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces: `two_group_test(...) -> list[dict]` の各要素の `log2fc` が「正 = group_b が高い」になる。後続タスクと massbank-context 側の契約がこれに依存する

- [ ] **Step 1: 符号を固定する失敗テストを書く**

`tests/test_differential.py` の `TestTwoGroup` クラスに追加する。

```python
    def test_log2fc_positive_when_group_b_higher(self):
        # 慣習: log2FC = log2(比較対象 / 基準)。group_a が基準、group_b が比較対象。
        # B(40) が A(10) より高いので正になる。log2((40+1)/(10+1)) ≒ 1.898
        matrix = np.array([[10.0], [10.0], [10.0], [40.0], [40.0], [40.0]])
        labels = ["A", "A", "A", "B", "B", "B"]
        res = diff.two_group_test(matrix, ["f0"], labels, "A", "B",
                                  log_transform=False)
        self.assertGreater(res[0]["log2fc"], 1.85)
        self.assertLess(res[0]["log2fc"], 1.95)

    def test_log2fc_sign_is_same_in_log_space(self):
        # log_transform=True の分岐でも向きが一致すること（2 分岐あるので両方を固定する）
        matrix = np.array([[10.0], [10.0], [10.0], [40.0], [40.0], [40.0]])
        labels = ["A", "A", "A", "B", "B", "B"]
        res = diff.two_group_test(matrix, ["f0"], labels, "A", "B",
                                  log_transform=True)
        self.assertGreater(res[0]["log2fc"], 1.85)
        self.assertLess(res[0]["log2fc"], 1.95)
```

- [ ] **Step 2: 失敗を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_differential.py -q -k log2fc_positive_or_sign`

より確実には次で実行する。

Run: `C:/Python314/python.exe -m pytest tests/test_differential.py::TestTwoGroup -q`

Expected: 新しい 2 件が FAIL（現行実装は `-1.898` を返すため `assertGreater(..., 1.85)` で落ちる）。

- [ ] **Step 3: `_log2fc` の符号を反転する**

`lipidmix/analysis/differential.py` の `_log2fc` を差し替える。

```python
def _log2fc(mean_a, mean_b, pseudo_count):
    """log2 fold change。**正なら group_b が高い（＝上昇）**。

    慣習（log2FC = log2(比較対象 / 基準)）に合わせる。group_a が基準（対照）、
    group_b が比較対象である。2026-08-31 に向きを反転した（旧実装は
    正 = group_a が高い、で慣習と逆だった）。過去の解析結果とは符号が逆になる。
    """
    num = max(mean_b, 0.0) + pseudo_count
    den = max(mean_a, 0.0) + pseudo_count
    return math.log2(num / den)
```

- [ ] **Step 4: log 空間の分岐も反転する**

同ファイル `two_group_test` の中の 1 行を変える。

```python
            fc = (lm_b - lm_a) if (log2 and math.isfinite(lm_a) and math.isfinite(lm_b)) else math.nan
```

併せて docstring の該当行を次に差し替える。

```python
    """群 a/b について特徴量ごとに Welch t 検定と log2 fold change を計算する。

    **log2fc は正なら group_b が高い（上昇）。** group_a が基準（対照）、
    group_b が比較対象である。log_transform=True のときは log2(x+pseudo_count)
    空間で検定し、log2FC も log2 空間の群平均差（＝幾何平均比の log2）とする。
    MS 強度は対数正規に近く、生強度での t 検定は正規性仮定を外れやすいため、
    こちらが推奨経路。mean_a/mean_b は解釈用に常に生強度平均を返す。
    """
```

- [ ] **Step 5: 既存テストの期待値を反転する**

`tests/test_differential.py:36-40` のコメントと assertion を差し替える。

```python
        # log2fc = log2(mean_b / mean_a)。f0 は A(~10) より B(50) が高いので正。
        self.assertGreater(by_feat["f0"]["log2fc"], 1.0)
```

`tests/test_differential.py:104` を差し替える。

```python
        # log2 空間の群平均差 ~ log2(50/10) ~ +2.3。生の平均は従来どおり返る。
        self.assertGreater(res[0]["log2fc"], 2.0)
```

同 103 行のコメントも `# B is ~5x A on the linear scale for f0.` のままでよい（内容は正しい）。

- [ ] **Step 6: テストが通ることを確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_differential.py -q`
Expected: PASS（失敗 0 件）。

- [ ] **Step 7: 全体テストで巻き添えが無いことを確認する**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: 全件 PASS。落ちた場合は、そのテストが符号に依存していたということなので、期待値を「正 = group_b が高い」に合わせて直す。

- [ ] **Step 8: commit**

```bash
git add lipidmix/analysis/differential.py tests/test_differential.py
git commit -m "fix: log2FC の向きが慣習と逆で、上昇と低下が入れ替わっていた"
```

---

## Task 2: 向きと契約版を開示する

**Files:**
- Modify: `lipidmix/arf/tools.py`（`arf_differential` の caveats と payload、`last_differential`）
- Modify: `docs/output_format/arf.md:225`, `:243`, `:228` 周辺
- Test: `tests/test_differential_tools.py`

**Interfaces:**
- Consumes: Task 1 の `two_group_test`
- Produces: `session_state.session.arf.last_differential` に `"contract_version": 1` と `"log2fc_sign"` が入る。Task 4 の export がこれを読む

- [ ] **Step 1: 失敗テストを書く**

`tests/test_differential_tools.py` の末尾に追加する。既存ファイルの import と前準備（`arf_preprocess` を呼んでから `arf_differential` を呼ぶ流れ）は同ファイル内の既存テストに倣うこと。

```python
class TestDifferentialSignDisclosure(unittest.TestCase):
    def test_payload_and_state_disclose_sign(self):
        """符号の向きが payload と session 状態の両方に出ること。

        向きは 2026-08-31 に反転したため、古い出力と区別できないと
        解釈が静かに逆転する。
        """
        _run_preprocess_and_differential()   # 同ファイルの既存ヘルパに合わせる
        payload = json.loads(server.arf_differential(group_a="A", group_b="B"))
        self.assertEqual(payload["differential_contract_version"], 1)
        self.assertIn("group_b", payload["log2fc_sign"])
        state = server.session_state.session.arf.last_differential
        self.assertEqual(state["contract_version"], 1)
```

**注**: `_run_preprocess_and_differential()` は同ファイルに既存の前準備手順があればそれを使い、無ければ既存テストの前準備を関数に切り出して共有する。切り出す場合はこのタスクの中で行う。

- [ ] **Step 2: 失敗を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_differential_tools.py -q -k SignDisclosure`
Expected: FAIL（`KeyError: 'differential_contract_version'`）。

- [ ] **Step 3: payload と状態に向きを載せる**

`lipidmix/arf/tools.py` の `arf_differential` 内、`session_state.session.arf.last_differential = {...}` に 2 キーを足す。

```python
        session_state.session.arf.last_differential = {
            "kind": "two_group", "a": group_a, "b": group_b,
            "n_a": n_a, "n_b": n_b,
            "q_threshold": q_threshold,
            "log2fc_threshold": log2fc_threshold,
            "contract_version": 1,
            "log2fc_sign": "positive means group_b is higher",
            "log_transform": log_transform,
            "results": results, "volcano": volcano}
```

同じ関数の `payload = {...}` に 2 キーを足す。

```python
                   "differential_contract_version": 1,
                   "log2fc_sign": ("log2fc は正なら group_b が高い（上昇）。"
                                   "2026-08-31 以前の出力とは符号が逆である。"),
```

- [ ] **Step 4: caveats にも 1 行足す**

同関数の `caveats.append(...)` 群の末尾（`results = differential.two_group_test(...)` の直前）に足す。

```python
        caveats.append(
            f"log2FC の向き: 正なら {group_b} が高い（上昇）、負なら {group_a} が高い（低下）。"
            "2026-08-31 に慣習へ合わせて反転したため、それ以前の出力とは符号が逆である。")
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_differential_tools.py -q`
Expected: PASS。

- [ ] **Step 6: ドキュメントの符号記述を直す**

`docs/output_format/arf.md` の 3 箇所を差し替える。

225 行付近:

```
- **2群比較**（`group_a` と `group_b` を指定）: 特徴量ごとに Welch t 検定（等分散を仮定しない）と log2 fold change を計算する。`log2fc = log2((mean_b + 擬似カウント) / (mean_a + 擬似カウント))`（**正=群Bで高い（上昇）**、擬似カウント既定1.0でゼロ割回避）。`group_a` が基準（対照）、`group_b` が比較対象。小n・分散0・全欠損は `p=NaN`。**2026-08-31 に向きを反転した**（それ以前の出力とは符号が逆）。
```

243 行付近の表の行:

```
| `points[].log2fc` | log2 fold change（x軸）。**正=群Bで高い（上昇）** |
```

228 行付近の volcano 説明にある `up` / `down` の定義文は、次のように意味を明示する。

```
`up`=q≤閾値かつlog2fc≥+閾値（群Bで高い＝上昇） / `down`=q≤閾値かつlog2fc≤−閾値（群Aで高い＝低下）
```

- [ ] **Step 7: commit**

```bash
git add lipidmix/arf/tools.py docs/output_format/arf.md tests/test_differential_tools.py
git commit -m "feat: log2FC の向きと契約版を payload と状態に開示する"
```

---

## Task 3: `.arf2` の同定情報を結合する純関数

**Files:**
- Create: `lipidmix/arf/identity_join.py`
- Test: `tests/test_identity_join.py`

**Interfaces:**
- Consumes: なし（純関数）
- Produces:
  - `join_identity(results: list[dict], catalog: dict[int, dict]) -> tuple[list[dict], dict]`
    `results` は `two_group_test` + `add_fdr` の出力（各要素に `feature` / `mean_a` / `mean_b` / `log2fc` / `p` / `q`）。`catalog` は `MasterAlignmentID -> .arf2 スポット辞書`。
    戻り値は `(rows, report)`。`rows` の各要素は
    `{spot_id, name, ontology, inchikey, smiles, mz, rt, log2fc, p_value, q_value, mean_a, mean_b}`。
    `report` は `{"n_features_total": int, "n_with_inchikey": int, "n_unannotated": int}`。
  - `spot_id_of(feature_name: str) -> int | None`

- [ ] **Step 1: 失敗テストを書く**

`tests/test_identity_join.py` を新規作成する。

```python
"""spot id と .arf2 同定情報の結合（純関数）。"""
import unittest

from lipidmix.arf import identity_join


def _catalog():
    return {
        1: {"MasterAlignmentID": 1, "Name": "PC 34:1", "Ontology": "PC",
            "InChIKey": "AAAAAAAAAAAAAA-BBBBBBBBBB-C", "SMILES": "CCO",
            "MassCenter": 760.5851, "RT": 12.34},
        2: {"MasterAlignmentID": 2, "Name": "Unknown", "Ontology": "",
            "InChIKey": "", "SMILES": "", "MassCenter": 100.0, "RT": 1.0},
    }


class TestSpotIdOf(unittest.TestCase):
    def test_extracts_id(self):
        self.assertEqual(identity_join.spot_id_of("Spot_474_height"), 474)

    def test_returns_none_for_other_shapes(self):
        self.assertIsNone(identity_join.spot_id_of("f0"))


class TestJoinIdentity(unittest.TestCase):
    def test_keeps_only_rows_with_inchikey(self):
        results = [
            {"feature": "Spot_1_height", "mean_a": 10.0, "mean_b": 40.0,
             "log2fc": 1.9, "p": 0.001, "q": 0.01},
            {"feature": "Spot_2_height", "mean_a": 5.0, "mean_b": 5.0,
             "log2fc": 0.0, "p": 0.9, "q": 0.95},
        ]
        rows, report = identity_join.join_identity(results, _catalog())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["spot_id"], 1)
        self.assertEqual(rows[0]["inchikey"], "AAAAAAAAAAAAAA-BBBBBBBBBB-C")
        self.assertEqual(rows[0]["ontology"], "PC")
        self.assertAlmostEqual(rows[0]["mz"], 760.5851)
        self.assertEqual(report["n_features_total"], 2)
        self.assertEqual(report["n_with_inchikey"], 1)
        self.assertEqual(report["n_unannotated"], 1)

    def test_unknown_spot_id_counts_as_unannotated(self):
        results = [{"feature": "Spot_999_height", "mean_a": 1.0, "mean_b": 1.0,
                    "log2fc": 0.0, "p": 0.5, "q": 0.6}]
        rows, report = identity_join.join_identity(results, _catalog())
        self.assertEqual(rows, [])
        self.assertEqual(report["n_unannotated"], 1)

    def test_non_spot_feature_name_counts_as_unannotated(self):
        results = [{"feature": "f0", "mean_a": 1.0, "mean_b": 1.0,
                    "log2fc": 0.0, "p": 0.5, "q": 0.6}]
        rows, report = identity_join.join_identity(results, _catalog())
        self.assertEqual(rows, [])
        self.assertEqual(report["n_unannotated"], 1)
```

- [ ] **Step 2: 失敗を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_identity_join.py -q`
Expected: FAIL（`ModuleNotFoundError: lipidmix.arf.identity_join`）。

- [ ] **Step 3: 実装する**

`lipidmix/arf/identity_join.py` を新規作成する。

```python
"""差次的結果の spot id に .arf2 の同定情報を結合する（純関数）。

ファイルの読み込みは行わない。呼び出し側が `.arf2` を読んで
`MasterAlignmentID -> スポット辞書` を渡す。純関数にしておくのは、
結合の規則そのものを単独でテストしたいためである。

InChIKey が無い行は落とす。パスウェイ照会の鍵が無い行を残すと、下流で
「照会したがパスウェイが無かった化合物」と区別が付かなくなる。落とした件数は
report で必ず返す（黙って減らさない）。
"""
from __future__ import annotations

import re

_SPOT_ID_RE = re.compile(r"^Spot_(\d+)_")


def spot_id_of(feature_name) -> int | None:
    """feature 名 `Spot_<id>_<prop>` から MasterAlignmentID を取る。"""
    match = _SPOT_ID_RE.match(str(feature_name))
    return int(match.group(1)) if match else None


def join_identity(results: list[dict],
                  catalog: dict[int, dict]) -> tuple[list[dict], dict]:
    """差次的結果に .arf2 の同定情報を結合する。

    引数:
        results: two_group_test + add_fdr の出力。
        catalog: MasterAlignmentID -> .arf2 スポット辞書。

    戻り値: (rows, report)。rows は InChIKey を持つ行だけ。
    """
    rows: list[dict] = []
    n_unannotated = 0
    for result in results:
        spot_id = spot_id_of(result.get("feature"))
        spot = catalog.get(spot_id) if spot_id is not None else None
        inchikey = str((spot or {}).get("InChIKey") or "").strip()
        if not inchikey:
            n_unannotated += 1
            continue
        rows.append({
            "spot_id": spot_id,
            "name": str(spot.get("Name") or "").strip(),
            "ontology": str(spot.get("Ontology") or "").strip(),
            "inchikey": inchikey,
            "smiles": str(spot.get("SMILES") or "").strip(),
            "mz": spot.get("MassCenter"),
            "rt": spot.get("RT"),
            "log2fc": result.get("log2fc"),
            "p_value": result.get("p"),
            "q_value": result.get("q"),
            "mean_a": result.get("mean_a"),
            "mean_b": result.get("mean_b"),
        })
    report = {
        "n_features_total": len(results),
        "n_with_inchikey": len(rows),
        "n_unannotated": n_unannotated,
    }
    return rows, report
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_identity_join.py -q`
Expected: PASS（4 件）。

- [ ] **Step 5: commit**

```bash
git add lipidmix/arf/identity_join.py tests/test_identity_join.py
git commit -m "feat: 差次的結果に .arf2 の InChIKey を結合する純関数を足す"
```

---

## Task 4: `arf_export_differential` ツール

**Files:**
- Modify: `lipidmix/arf/tools.py`（新ツールと `__all__`）
- Test: `tests/test_export_differential.py`

**Interfaces:**
- Consumes: Task 2 の `last_differential`（`contract_version` / `log2fc_sign` / `log_transform` を含む）、Task 3 の `join_identity` と `spot_id_of`、既存の `_sibling_arf2_path()` と `lipidmix.arf2.reader.load_catalog`
- Produces: `arf_export_differential(output_path: str) -> str`。書き出したファイルは massbank-context の `load_differential` が読む契約（spec §6.1）

- [ ] **Step 1: 失敗テストを書く**

`tests/test_export_differential.py` を新規作成する。

```python
"""差次的結果のエクスポート契約（spec §6.1）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class TestExportDifferential(unittest.TestCase):
    def setUp(self):
        server.session_state.session.arf.last_differential = {
            "kind": "two_group", "a": "gf_AIN", "b": "gf_HFD",
            "n_a": 5, "n_b": 5,
            "q_threshold": 0.05, "log2fc_threshold": 1.0,
            "contract_version": 1,
            "log2fc_sign": "positive means group_b is higher",
            "log_transform": True,
            "results": [
                {"feature": "Spot_1_height", "mean_a": 10.0, "mean_b": 40.0,
                 "log2fc": 1.9, "p": 0.001, "q": 0.01},
                {"feature": "Spot_2_height", "mean_a": 5.0, "mean_b": 5.0,
                 "log2fc": 0.0, "p": 0.9, "q": 0.95},
            ],
            "volcano": [],
        }
        self.catalog = [
            {"MasterAlignmentID": 1, "Name": "PC 34:1", "Ontology": "PC",
             "InChIKey": "AAAAAAAAAAAAAA-BBBBBBBBBB-C", "SMILES": "CCO",
             "MassCenter": 760.5851, "RT": 12.34},
            {"MasterAlignmentID": 2, "Name": "Unknown", "Ontology": "",
             "InChIKey": "", "SMILES": "", "MassCenter": 100.0, "RT": 1.0},
        ]

    def test_missing_state_without_differential(self):
        server.session_state.session.arf.last_differential = None
        payload = json.loads(server.arf_export_differential("out.tsv"))
        self.assertEqual(payload["error"]["code"], "missing_state")
        self.assertIn("arf_differential", payload["error"]["required_tools"])

    def test_fails_when_sibling_arf2_missing(self):
        with patch("lipidmix.arf.tools._sibling_arf2_path", return_value=None):
            payload = json.loads(server.arf_export_differential("out.tsv"))
        self.assertEqual(payload["error"]["code"], "missing_state")
        self.assertEqual(payload["error"]["state"], "sibling_arf2")

    def test_writes_meta_and_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "differential.tsv"
            arf2 = Path(tmp) / "AlignmentResult_2026_01_01_00_00_00.arf2"
            arf2.write_bytes(b"")
            with patch("lipidmix.arf.tools._sibling_arf2_path", return_value=arf2), \
                 patch("lipidmix.arf2.reader.load_catalog", return_value=self.catalog):
                payload = json.loads(server.arf_export_differential(str(out)))
            text = out.read_text(encoding="utf-8")

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["n_with_inchikey"], 1)
        self.assertEqual(payload["n_unannotated"], 1)
        self.assertIn("# contract_version = 1", text)
        self.assertIn("# group_a = gf_AIN", text)
        self.assertIn("# log2fc_sign = positive means group_b is higher", text)
        self.assertIn("# n_unannotated = 1", text)
        header = [l for l in text.splitlines() if not l.startswith("#")][0]
        self.assertEqual(header.split("\t")[0], "spot_id")
        self.assertIn("inchikey", header.split("\t"))
        body = [l for l in text.splitlines()
                if not l.startswith("#")][1:]
        self.assertEqual(len(body), 1)
        self.assertIn("AAAAAAAAAAAAAA-BBBBBBBBBB-C", body[0])

    def test_significant_flag_uses_stored_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "differential.tsv"
            arf2 = Path(tmp) / "AlignmentResult_2026_01_01_00_00_00.arf2"
            arf2.write_bytes(b"")
            with patch("lipidmix.arf.tools._sibling_arf2_path", return_value=arf2), \
                 patch("lipidmix.arf2.reader.load_catalog", return_value=self.catalog):
                server.arf_export_differential(str(out))
            row = [l for l in out.read_text(encoding="utf-8").splitlines()
                   if not l.startswith("#")][1]
        # q=0.01 <= 0.05 かつ |log2fc|=1.9 >= 1.0 なので significant
        self.assertEqual(row.split("\t")[-1], "true")
```

- [ ] **Step 2: 失敗を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_export_differential.py -q`
Expected: FAIL（`AttributeError: module 'server' has no attribute 'arf_export_differential'`）。

- [ ] **Step 3: ツールを実装する**

`lipidmix/arf/tools.py` の末尾に追加する。ファイル冒頭の import に `from datetime import datetime, timezone` と `from lipidmix.arf import identity_join` を足す（既存の import 群に合わせる）。

```python
_EXPORT_COLUMNS = ["spot_id", "name", "name_source", "ontology", "inchikey",
                   "inchikey_source", "msi_level", "mz", "rt", "log2fc",
                   "p_value", "q_value", "mean_a", "mean_b", "significant"]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def arf_export_differential(output_path: str) -> str:
    """差次的結果を InChIKey 付きの 1 ファイルへ書き出す（spec §6.1 の契約）。

    先に arf_preprocess → arf_differential を実行しておくこと。
    同一アラインメントの兄弟 .arf2 から InChIKey・Ontology・m/z・RT を
    MasterAlignmentID で結合する。

    **有意な行だけでなく、InChIKey が付いた全行を書き出す。** 下流の濃縮解析は
    「検出された化合物」を背景に取る必要があり、有意な行だけでは背景が作れない。
    有意かどうかは significant 列のフラグで持ち、判定に使った閾値はメタ行に残す。

    **兄弟 .arf2 が無ければ失敗させる。** InChIKey 空欄の行を黙って出すと、
    下流で「パスウェイが無い化合物」と区別が付かなくなる。

    msi_level は .arf2 由来の注釈確度であり、**MS/MS の有無ではない**
    （.arf2 は MS/MS 取得フラグを持たない）。
    """
    last = getattr(session_state.session.arf, "last_differential", None)
    if not last or last.get("kind") != "two_group":
        return mcp_errors.missing_state(
            "two_group_differential", ["arf_differential"],
            "先に arf_preprocess → arf_differential（2群）を実行してください。")

    arf2_path = _sibling_arf2_path()
    if not arf2_path:
        return mcp_errors.missing_state(
            "sibling_arf2", ["arf_parser"],
            "同一アラインメントの .arf2 が隣接していません。InChIKey を補えないため"
            "書き出しません（空欄のまま出すと、下流でパスウェイ無しと区別できません）。")

    from lipidmix.arf2.reader import load_catalog
    catalog = {s.get("MasterAlignmentID"): s for s in load_catalog(arf2_path)}
    rows, report = identity_join.join_identity(last.get("results") or [], catalog)

    q_threshold = last.get("q_threshold")
    log2fc_threshold = last.get("log2fc_threshold")

    def _is_significant(row) -> bool:
        q = row.get("q_value")
        fc = row.get("log2fc")
        if q is None or fc is None:
            return False
        if not (math.isfinite(q) and math.isfinite(fc)):
            return False
        return q <= q_threshold and abs(fc) >= log2fc_threshold

    meta = [
        "# contract_version = 1",
        f"# exported_at = {datetime.now(timezone.utc).isoformat()}",
        f"# source_arf = {getattr(session_state.session.arf, 'current_file_path', '')}",
        f"# source_arf2 = {arf2_path}",
        f"# group_a = {last['a']}\tn_a = {last['n_a']}",
        f"# group_b = {last['b']}\tn_b = {last['n_b']}",
        "# log2fc_sign = positive means group_b is higher",
        f"# q_threshold = {q_threshold}\tlog2fc_threshold = {log2fc_threshold}"
        f"\tlog_transform = {str(bool(last.get('log_transform'))).lower()}",
        f"# preprocess = {getattr(session_state.session, 'preprocessing_recipe', None)}",
        f"# n_features_total = {report['n_features_total']}"
        f"\tn_with_inchikey = {report['n_with_inchikey']}"
        f"\tn_unannotated = {report['n_unannotated']}",
        "# msi_level は .arf2 由来の注釈確度。MS/MS の有無ではない",
    ]

    lines = list(meta)
    lines.append("\t".join(_EXPORT_COLUMNS))
    for row in rows:
        lines.append("\t".join([
            str(row["spot_id"]), row["name"], "arf2", row["ontology"],
            row["inchikey"], "arf2", "",
            "" if row["mz"] is None else f"{float(row['mz']):.4f}",
            "" if row["rt"] is None else f"{float(row['rt']):.4f}",
            "" if row["log2fc"] is None else f"{float(row['log2fc']):.6f}",
            "" if row["p_value"] is None else f"{float(row['p_value']):.6g}",
            "" if row["q_value"] is None else f"{float(row['q_value']):.6g}",
            "" if row["mean_a"] is None else f"{float(row['mean_a']):.6g}",
            "" if row["mean_b"] is None else f"{float(row['mean_b']):.6g}",
            "true" if _is_significant(row) else "false",
        ]))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return json_payload({
        "status": "success",
        "output_path": str(out),
        "contract_version": 1,
        "group_a": last["a"], "group_b": last["b"],
        "n_features_total": report["n_features_total"],
        "n_with_inchikey": report["n_with_inchikey"],
        "n_unannotated": report["n_unannotated"],
        "log2fc_sign": "log2fc は正なら group_b が高い（上昇）。",
        "note": ("n_unannotated は注釈が付かず書き出さなかった行数です。"
                 "「変化が無かった」ではなく「調べていない」行です。"),
    })
```

- [ ] **Step 4: `__all__` に載せる**

`lipidmix/arf/tools.py` の `__all__` に `"arf_export_differential"` を足す。`__all__` が無ければ、そのファイルの既存の公開方法（`from lipidmix.arf.tools import *` で拾われる形）に合わせる。

- [ ] **Step 5: テストが通ることを確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_export_differential.py -q`
Expected: PASS（4 件）。

`math` が未 import なら `import math` を足す。`Path` と `json_payload` と `mcp_errors` は既存の import を使う。

- [ ] **Step 6: commit**

```bash
git add lipidmix/arf/tools.py tests/test_export_differential.py
git commit -m "feat: 差次的結果を InChIKey 付き TSV へ書き出すツールを足す"
```

---

## Task 5: 登録スナップショットと最終確認

**Files:**
- Modify: `tests/test_server_registration.py`（`EXPECTED_TOOLS`）
- Modify: `docs/output_format/arf.md`（新ツールの契約）

**Interfaces:**
- Consumes: Task 4 の `arf_export_differential`
- Produces: なし（最終タスク）

- [ ] **Step 1: 登録スナップショットを更新して失敗を確認する**

`tests/test_server_registration.py` の `EXPECTED_TOOLS` に `"arf_export_differential"` を**アルファベット順の正しい位置**（`"arf_exclude"` の前）に足す。

```python
    "arf_differential",
    "arf_exclude",
    "arf_export_differential",
    "arf_list_classes",
```

**注**: `"arf_exclude"` < `"arf_export_differential"` < `"arf_list_classes"`（`exc` < `exp` < `lis`）なので、この順が正しい。

- [ ] **Step 2: 登録テストを実行する**

Run: `C:/Python314/python.exe -m pytest tests/test_server_registration.py -q`
Expected: PASS。落ちる場合は `__all__` への追加漏れ（Task 4 Step 4）。

- [ ] **Step 3: ドキュメントに契約を書く**

`docs/output_format/arf.md` の差次的解析の節の末尾に追加する。

```markdown
### arf_export_differential — 差次的結果のエクスポート契約

`arf_preprocess` → `arf_differential`（2群）の後に呼ぶ。同一アラインメントの
兄弟 `.arf2` から InChIKey・Ontology・m/z・RT を `MasterAlignmentID` で結合し、
1 ファイルに書き出す。**兄弟 `.arf2` が無ければ書き出さない**（InChIKey 空欄の行を
出すと、下流で「パスウェイが無い化合物」と区別が付かなくなるため）。

`#` 始まりのメタ行に来歴（`contract_version` / `group_a` / `group_b` /
`log2fc_sign` / 閾値 / `n_features_total` / `n_with_inchikey` / `n_unannotated`）を置き、
続けて TSV 本体を置く。列は
`spot_id / name / name_source / ontology / inchikey / inchikey_source / msi_level /
mz / rt / log2fc / p_value / q_value / mean_a / mean_b / significant`。

**有意な行だけでなく、InChIKey が付いた全行を書き出す。** 下流の濃縮解析は
「検出された化合物」を背景に取る必要があり、有意な行だけでは背景が作れない。

`msi_level` は `.arf2` 由来の注釈確度であり、**MS/MS の有無ではない**
（`.arf2` は MS/MS 取得フラグを持たない）。
```

- [ ] **Step 4: 全体テストを実行する**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: 全件 PASS。件数は Task 開始時の 563 件 + 新規テスト分（本計画で 10 件追加）。

- [ ] **Step 5: commit**

```bash
git add tests/test_server_registration.py docs/output_format/arf.md
git commit -m "docs: エクスポート契約を output_format に記録し、登録面を固定する"
```

---

## 完了条件

- [ ] `C:/Python314/python.exe -m pytest tests -q` が全件 PASS
- [ ] `arf_export_differential` が MCP に登録されている（`test_server_registration.py` が通る）
- [ ] 書き出したファイルが spec §6.1 のメタ行 11 行 + ヘッダ + 本文の形になっている
- [ ] `log2fc` が「正 = group_b が高い」になっている（`tests/test_differential.py` の既知解で固定）
