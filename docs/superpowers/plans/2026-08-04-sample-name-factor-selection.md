# サンプル名の因子トークンによる選択・群分け Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MS-DIAL の Class ID に入っていない因子（時点・複製・測定日）をサンプル名のトークンから解決し、フィルタ・PCA群分け・2群比較・サンプル検索のすべてで使えるようにする。

**Architecture:** 各サンプルのトークン集合を `tokens(サンプル名) ∪ tokens(Class ID)` に統合する純関数モジュール `sample_factors.py` を新設し、既存の Class ID 専用ロジック（`msdial_classes.filter_arf_by_class_ids` / `assign_sample_groups`、`tools_arf._pool_group_labels`）をその上に載せ替える。既存の引数名（`class_ids` / `group_levels` / `group_a` / `group_b`）はそのままサンプル名トークンにも効くようになるため、API 追加は `group_factors` / `include_roles` と新ツール `sample_search` に限られる。

**Tech Stack:** Python 3.13、標準ライブラリ + numpy、MCP（FastMCP）、テストは `unittest` を pytest で実行。

## Global Constraints

- 仕様書: [docs/superpowers/specs/2026-08-04-sample-name-factor-selection-design.md](../specs/2026-08-04-sample-name-factor-selection-design.md)
- ブランチ: `feat/sample-name-factor-selection`（`codex/eic-plot-payload` 起点）。
- テスト実行: `./.venv-1/Scripts/python.exe -m pytest tests/ -q`（PowerShell では `.\.venv-1\Scripts\python.exe -m pytest tests\ -q`）。仮想環境は `.venv` ではなく **`.venv-1`**（`.venv` は空）。
- **ベースライン: 388 passed, 3 skipped, 1 failed。** 唯一の失敗 `tests/test_ingest_tools.py::KnowledgeCoverageSmokeTests::test_coverage_on_sample_objective` は `analyses/` のフィクスチャ欠如による**既存の失敗**で本作業とは無関係。この1件以外が失敗したら退行。
- **既存テストは1行も書き換えない。** 既存の `class_ids` / `group_levels` / `group_a` / `group_b` 系テストが無改修で通ることが、統合トークン空間の後方互換性の主証拠。
  - **唯一の例外**: `tests/test_server_registration.py` の `EXPECTED_TOOLS` は MCP 登録面の正準スナップショットで、ツールを意図的に追加したときに更新する設計。Task 10 で `"sample_search"` を1行追加する（それ以外の行は触らない）。
- 依存の向き: `sample_factors.py` は leaf（`msdial_tags` と `preprocessing` のみ import）。`msdial_classes` → `sample_factors` の向きに依存させ、逆向き（`sample_factors` → `msdial_classes`）は**作らない**。`tools_*` / `session_state` / `server` は `sample_factors` から import しない。
- docstring・コメント・ユーザー向けメッセージは既存コードと同じく**日本語**で書く。ただし `assign_factor_groups` の排他違反メッセージだけは既存テスト `assertRaisesRegex(ValueError, "multiple")` を満たすため英単語 `multiple` を必ず含める。
- コミットは各タスク末尾で1回。`git add` は変更したファイルのみを明示列挙する。

---

### Task 1: `sample_factors.py` — トークン化とファセット構築

**Files:**
- Create: `sample_factors.py`
- Test: `tests/test_sample_factors.py`

**Interfaces:**
- Consumes: `msdial_tags.normalize_sample_name`、`preprocessing.detect_sample_roles`
- Produces:
  - `SampleFacet`（frozen dataclass: `name: str`, `file_id: int | None`, `class_id: str | None`, `role: str`, `tokens: frozenset[str]`）
  - `split_tokens(value) -> frozenset[str]`
  - `sample_tokens(sample_name, class_id=None, extra=None) -> frozenset[str]`
  - `build_sample_facets(sample_names, class_index=None, sample_meta=None) -> dict[str, SampleFacet]`
  - `arf_sample_names(features) -> list[str]`

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` を新規作成:

```python
import unittest

from msdial_tags import normalize_sample_name
from sample_factors import (
    SampleFacet,
    arf_sample_names,
    build_sample_facets,
    sample_tokens,
    split_tokens,
)


def name_class_index(name_to_class: dict[str, str]) -> dict:
    """discover_arf_class_index の戻りのうち、本モジュールが使う部分だけを作る。"""
    records = []
    by_file_name = {}
    for file_id, (name, class_id) in enumerate(name_to_class.items()):
        record = {"file_id": file_id, "file_name": name, "class_id": class_id}
        records.append(record)
        by_file_name[normalize_sample_name(name, strip_processing_timestamp=False)] = record
    return {"records": records, "by_file_id": {}, "by_file_name": by_file_name}


class SplitTokensTests(unittest.TestCase):
    def test_splits_on_underscore_and_casefolds(self):
        self.assertEqual(split_tokens("Cerebellum_gf_AIN"), frozenset({"cerebellum", "gf", "ain"}))

    def test_none_and_empty_yield_empty_set(self):
        self.assertEqual(split_tokens(None), frozenset())
        self.assertEqual(split_tokens("__"), frozenset())


class SampleTokensTests(unittest.TestCase):
    def test_strips_trailing_processing_timestamp(self):
        # 末尾12桁は MS-DIAL の処理タイムスタンプ（再処理ごとに変わる）で実験因子ではない。
        self.assertEqual(
            sample_tokens("20220902_RAW_ILG_6h_2_NEG_202605151012"),
            frozenset({"20220902", "raw", "ilg", "6h", "2", "neg"}),
        )

    def test_strips_known_measurement_suffix(self):
        self.assertEqual(sample_tokens("sample_A.wiff"), frozenset({"sample", "a"}))

    def test_merges_class_id_tokens(self):
        self.assertEqual(
            sample_tokens("s1", class_id="Cerebellum_gf_AIN"),
            frozenset({"s1", "cerebellum", "gf", "ain"}),
        )

    def test_merges_extra_label_tokens(self):
        self.assertEqual(sample_tokens("s1", extra="24M_GF"), frozenset({"s1", "24m", "gf"}))


class BuildSampleFacetsTests(unittest.TestCase):
    def test_multi_token_value_splits_into_two_tokens(self):
        # G_uralensis は2トークンに割れる。だから位置インデックスでの因子指定は使わない。
        name = "20220902_RAW_G_uralensis_6h_2_NEG"
        facets = build_sample_facets([name], name_class_index({name: "G"}))
        self.assertEqual(
            facets[name].tokens,
            frozenset({"20220902", "raw", "g", "uralensis", "6h", "2", "neg"}),
        )
        self.assertEqual(facets[name].class_id, "G")
        self.assertEqual(facets[name].file_id, 0)
        self.assertEqual(facets[name].role, "sample")

    def test_works_without_class_index(self):
        # .mddata が無いフォルダでもサンプル名トークンだけで成立する。
        facets = build_sample_facets(["20220901_RAW_LPS_6h_1_NEG"], None)
        facet = facets["20220901_RAW_LPS_6h_1_NEG"]
        self.assertIsNone(facet.class_id)
        self.assertIsNone(facet.file_id)
        self.assertIn("lps", facet.tokens)
        self.assertIn("6h", facet.tokens)

    def test_detects_qc_and_blank_roles(self):
        facets = build_sample_facets(["20240311_QC_Cerebellum_NEG_1", "Blank_01", "s1"], None)
        self.assertEqual(facets["20240311_QC_Cerebellum_NEG_1"].role, "qc")
        self.assertEqual(facets["Blank_01"].role, "blank")
        self.assertEqual(facets["s1"].role, "sample")

    def test_sample_meta_supplies_role_and_group_tokens(self):
        # arf_differential は class_index を持たず sample_meta["group"] だけを持つ経路。
        facets = build_sample_facets(
            ["s0", "qc1"],
            None,
            sample_meta={"s0": {"group": "24M_GF", "role": "sample"},
                         "qc1": {"group": "24M_GF", "role": "qc"}},
        )
        self.assertEqual(facets["s0"].tokens, frozenset({"s0", "24m", "gf"}))
        self.assertEqual(facets["qc1"].role, "qc")

    def test_preserves_input_order(self):
        facets = build_sample_facets(["b", "a", "c"], None)
        self.assertEqual(list(facets), ["b", "a", "c"])

    def test_is_frozen_dataclass(self):
        facet = build_sample_facets(["s1"], None)["s1"]
        self.assertIsInstance(facet, SampleFacet)
        with self.assertRaises(Exception):
            facet.name = "other"


class ArfSampleNamesTests(unittest.TestCase):
    def test_collects_names_in_first_appearance_order_without_duplicates(self):
        features = [
            {"AlignedPeakProperties": [[0, "sB", 1.0], [1, "sA", 2.0]]},
            {"AlignedPeakProperties": [[0, "sB", 3.0], [1, "sA", 4.0]]},
        ]
        self.assertEqual(arf_sample_names(features), ["sB", "sA"])

    def test_decodes_bytes_names_and_skips_malformed_rows(self):
        features = [{"AlignedPeakProperties": [[0, b"sA", 1.0], "not-a-row", [1]]}]
        self.assertEqual(arf_sample_names(features), ["sA"])

    def test_empty_input(self):
        self.assertEqual(arf_sample_names([]), [])
        self.assertEqual(arf_sample_names(None), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sample_factors'`

- [ ] **Step 3: Write minimal implementation**

`sample_factors.py` を新規作成:

```python
"""サンプル単位の因子トークン（サンプル名 ∪ Class ID）による選択・群分け（純ロジック層）。

MS-DIAL の Class ID は「ユーザーが MS-DIAL 上で入力した1文字列」でしかなく、実験
デザインの全因子を含むとは限らない（時点・複製・測定日はサンプル名にしか無いことが
ある）。そのため本モジュールは Class ID とサンプル名のトークンを統合した空間で
spec を解決し、msdial_classes.py の Class ID 専用ロジックを一般化する。

依存は msdial_tags（normalize_sample_name）と preprocessing（detect_sample_roles）
のみの leaf。msdial_classes / tools_* / session_state / server は import しない
（msdial_classes → sample_factors の向きに依存させるため。逆向きは循環になる）。
"""
from __future__ import annotations

from dataclasses import dataclass

import preprocessing
from msdial_tags import normalize_sample_name


@dataclass(frozen=True)
class SampleFacet:
    """1サンプルの選択・群分けに必要な情報一式。

    tokens は casefold 済みの統合トークン集合で、spec 照合の唯一の入力。
    file_id は EIC の file_ids にそのまま渡せる MS-DIAL AnalysisFileId。
    """

    name: str
    file_id: int | None
    class_id: str | None
    role: str
    tokens: frozenset[str]


def split_tokens(value) -> frozenset[str]:
    """`_` 区切りの因子トークン集合（casefold）。空要素は落とす。"""
    if value is None:
        return frozenset()
    return frozenset(token for token in str(value).casefold().split("_") if token)


def sample_tokens(sample_name, class_id=None, extra=None) -> frozenset[str]:
    """サンプル名 ∪ Class ID（∪ 追加ラベル）の統合トークン集合を返す。

    サンプル名は normalize_sample_name で既知の測定ファイル拡張子と末尾12桁の処理
    タイムスタンプを落としてから分割する。処理タイムスタンプは MS-DIAL の再処理
    ごとに変わる識別子で実験因子ではないため、トークン語彙に混ぜない。
    extra は Class ID 以外の群ラベル（session_state の sample_meta["group"] 等）用。
    """
    tokens = set(split_tokens(normalize_sample_name(sample_name, strip_processing_timestamp=True)))
    tokens |= split_tokens(class_id)
    tokens |= split_tokens(extra)
    return frozenset(tokens)


def build_sample_facets(sample_names, class_index=None, sample_meta=None) -> dict[str, SampleFacet]:
    """サンプル名リストから {サンプル名: SampleFacet} を作る（入力順を保持）。

    - class_index: msdial_classes.discover_arf_class_index の戻り。あれば file_id /
      class_id を名前一致で解決する。**None でも成立**し、その場合はサンプル名の
      トークンだけで選択できる（.mddata が無いフォルダでも因子指定が効く）。
    - sample_meta: session_state.session.sample_meta 相当。role と group ラベルの
      供給源。arf_differential は class_index を持たず sample_meta["group"] だけを
      持つ経路があるため、group もトークン源として合流させる。
    - role: sample_meta に明示があればそれを優先し、無ければ
      preprocessing.detect_sample_roles（名前と Class ID のトークン照合）で決める。
    """
    lookup = _class_lookup(class_index)
    meta = sample_meta or {}
    ordered = list(sample_names)

    records: dict[str, dict] = {}
    class_ids: dict[str, str] = {}
    for name in ordered:
        record = lookup.get(normalize_sample_name(name, strip_processing_timestamp=False)) or {}
        records[name] = record
        if record.get("class_id"):
            class_ids[name] = record["class_id"]

    detected = preprocessing.detect_sample_roles(ordered, class_ids)

    facets: dict[str, SampleFacet] = {}
    for name in ordered:
        record = records[name]
        entry = meta.get(name) or {}
        facets[name] = SampleFacet(
            name=name,
            file_id=record.get("file_id"),
            class_id=record.get("class_id"),
            role=entry.get("role") or detected.get(name, "sample"),
            tokens=sample_tokens(name, record.get("class_id"), entry.get("group")),
        )
    return facets


def arf_sample_names(features) -> list[str]:
    """ARF スポット列の AlignedPeakProperties 行に現れるサンプル名を出現順で返す。

    行は MS-DIAL の生 list で index 1 が FileName（AlignmentChromPeakFeature スキーマ）。
    arf_reader を経由せず素の index 参照で済ませ、leaf の依存を増やさない。
    """
    names: list[str] = []
    seen: set[str] = set()
    for spot in features or []:
        for row in (spot or {}).get("AlignedPeakProperties") or []:
            if not isinstance(row, list) or len(row) < 2:
                continue
            value = row[1]
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                names.append(value)
    return names


def _class_lookup(class_index) -> dict[str, dict]:
    """class_index の records を正規化サンプル名で引ける辞書にする。

    msdial_classes.resolve_sample_class を使わないのは、msdial_classes が本モジュール
    を import する側であり、逆向きの import が循環になるため（session_state.
    _build_sample_meta も同じ理由で同じ引き方をしている）。
    """
    if not class_index:
        return {}
    lookup: dict[str, dict] = {}
    for record in class_index.get("records", []):
        key = normalize_sample_name(record.get("file_name"), strip_processing_timestamp=False)
        if key:
            lookup[key] = record
    return lookup
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: PASS（17 tests）

- [ ] **Step 5: Commit**

```bash
git add sample_factors.py tests/test_sample_factors.py
git commit -m "feat(factors): サンプル名とClass IDの統合トークン空間を導入

Class ID は MS-DIAL 上の1文字列にすぎず実験デザインの全因子を含まない。
tokens(サンプル名) ∪ tokens(Class ID) のファセットを組む leaf モジュールを
追加する。.mddata が無くてもサンプル名だけで成立させる。"
```

---

### Task 2: `expand_sample_specs` — spec からサンプル集合への展開

**Files:**
- Modify: `sample_factors.py`
- Test: `tests/test_sample_factors.py`

**Interfaces:**
- Consumes: Task 1 の `SampleFacet` / `split_tokens` / `build_sample_facets`
- Produces: `expand_sample_specs(specs, facets, *, include_roles=("sample",)) -> tuple[dict[str, list[str]], dict[str, list[str]]]`
  戻り値は `(matches, excluded)`。`matches = {spec: [sample_name, ...]}`（spec は元のオブジェクトをキーに、サンプル名は facets の順）、`excluded = {spec: [sample_name, ...]}`（role で落ちたもの）。role 適用後に1件も残らない spec があれば `ValueError`。

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` の import 行に `expand_sample_specs` を足し（`from sample_factors import (...)` の一覧に追加）、末尾の `if __name__ == "__main__":` の直前に追記:

```python
class ExpandSampleSpecsTests(unittest.TestCase):
    def setUp(self):
        self.facets = build_sample_facets([
            "20220901_RAW_control_6h_1_NEG",
            "20220901_RAW_LPS_6h_1_NEG",
            "20220902_RAW_ILG_0h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
        ], None)

    def test_single_token_matches_all_samples_with_token(self):
        matches, _ = expand_sample_specs(["ILG"], self.facets)
        self.assertEqual(matches["ILG"], [
            "20220902_RAW_ILG_0h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
        ])

    def test_multi_token_spec_is_and(self):
        matches, _ = expand_sample_specs(["ILG_6h"], self.facets)
        self.assertEqual(matches["ILG_6h"], [
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
        ])

    def test_token_absent_from_class_id_still_matches(self):
        # 6h は Class ID に無くサンプル名にしかない因子。これが本機能の眼目。
        matches, _ = expand_sample_specs(["6h"], self.facets)
        self.assertEqual(len(matches["6h"]), 4)

    def test_multiple_specs_are_independent(self):
        matches, _ = expand_sample_specs(["ILG_6h", "control_6h"], self.facets)
        self.assertEqual(len(matches["ILG_6h"]), 2)
        self.assertEqual(matches["control_6h"], ["20220901_RAW_control_6h_1_NEG"])

    def test_case_insensitive(self):
        matches, _ = expand_sample_specs(["ilg_6H"], self.facets)
        self.assertEqual(len(matches["ilg_6H"]), 2)

    def test_full_sample_name_matches_only_itself(self):
        matches, _ = expand_sample_specs(["20220902_RAW_ILG_6h_2_NEG"], self.facets)
        self.assertEqual(matches["20220902_RAW_ILG_6h_2_NEG"], ["20220902_RAW_ILG_6h_2_NEG"])

    def test_zero_match_raises_with_available_tokens(self):
        with self.assertRaises(ValueError) as ctx:
            expand_sample_specs(["24h"], self.facets)
        message = str(ctx.exception)
        self.assertIn("matched", message)
        self.assertIn("24h", message)
        self.assertIn("ilg", message)

    def test_blank_specs_are_skipped(self):
        matches, _ = expand_sample_specs(["ILG", "", "  "], self.facets)
        self.assertEqual(list(matches), ["ILG"])


class ExpandSampleSpecsRoleTests(unittest.TestCase):
    def setUp(self):
        self.facets = build_sample_facets([
            "20240311_Cerebellum_ICR_NEG_1",
            "20240311_Cerebellum_ICR_NEG_2",
            "20240311_QC_Cerebellum_ICR_NEG_1",
        ], None)

    def test_qc_is_excluded_by_default_and_reported(self):
        matches, excluded = expand_sample_specs(["cerebellum"], self.facets)
        self.assertEqual(len(matches["cerebellum"]), 2)
        self.assertEqual(excluded["cerebellum"], ["20240311_QC_Cerebellum_ICR_NEG_1"])

    def test_include_roles_can_bring_qc_back(self):
        matches, excluded = expand_sample_specs(
            ["cerebellum"], self.facets, include_roles=("sample", "qc"))
        self.assertEqual(len(matches["cerebellum"]), 3)
        self.assertEqual(excluded["cerebellum"], [])

    def test_include_roles_none_disables_role_filtering(self):
        matches, _ = expand_sample_specs(["cerebellum"], self.facets, include_roles=None)
        self.assertEqual(len(matches["cerebellum"]), 3)

    def test_all_hits_dropped_by_role_raises_and_names_include_roles(self):
        with self.assertRaises(ValueError) as ctx:
            expand_sample_specs(["qc"], self.facets)
        self.assertIn("include_roles", str(ctx.exception))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: FAIL — `ImportError: cannot import name 'expand_sample_specs' from 'sample_factors'`

- [ ] **Step 3: Write minimal implementation**

`sample_factors.py` の `arf_sample_names` の直前に追加:

```python
def expand_sample_specs(specs, facets, *, include_roles=("sample",)):
    """各 spec を該当サンプル名のリストへ展開する。

    spec は `_` 区切りのトークン列で、**その全トークンを含む**サンプルに一致する
    （要素内 AND・順不同）。リスト内の複数 spec は互いに独立（呼び出し側で OR 合算
    する）。完全なサンプル名や完全な Class ID を渡しても同じ規則で解決される。

    include_roles: 既定 ("sample",) で QC/blank を落とす。統合トークン空間により
    `class_ids=["cerebellum"]` が `20240311_QC_Cerebellum_...` を名前経由で掴むように
    なったため、群平均・PCA の汚染を既定で防ぐ。落とした分は excluded に残して
    呼び出し側が開示できるようにする。None を渡すと role による絞り込みをしない。

    戻り値 (matches, excluded)。matches={spec: [サンプル名, ...]}（facets の順）。
    role 適用後に1件も残らない spec があれば ValueError（利用可能トークンを添える）。
    """
    allowed = None if include_roles is None else {str(role).casefold() for role in include_roles}
    matches: dict[str, list[str]] = {}
    excluded: dict[str, list[str]] = {}
    for spec in specs:
        wanted = split_tokens(str(spec).strip())
        if not wanted:
            continue
        hits = [facet.name for facet in facets.values() if wanted <= facet.tokens]
        if allowed is None:
            kept, dropped = hits, []
        else:
            kept = [n for n in hits if facets[n].role.casefold() in allowed]
            dropped = [n for n in hits if facets[n].role.casefold() not in allowed]
        if not kept:
            raise ValueError(_no_match_message(spec, facets, dropped))
        matches[spec] = kept
        excluded[spec] = dropped
    return matches, excluded


def _no_match_message(spec, facets, dropped) -> str:
    """一致ゼロの spec に対する説明文。role で全滅した場合はその旨を明示する。

    'matched' の語を含めるのは、既存テスト（expand_class_specs 由来）が
    assertRaisesRegex(ValueError, "matched") で拾っているため。
    """
    if dropped:
        roles = sorted({facets[n].role for n in dropped})
        return (
            f"No sample matched spec '{spec}' after role filtering: "
            f"{len(dropped)} 件が role={'/'.join(roles)} のため除外されました。"
            f"含めるには include_roles=[\"sample\", \"{roles[0]}\"] を指定してください。"
        )
    available = sorted({token for facet in facets.values() for token in facet.tokens})
    return (
        f"No sample matched spec '{spec}'. "
        f"Available tokens: {', '.join(available) if available else 'なし'}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: PASS（29 tests）

- [ ] **Step 5: Commit**

```bash
git add sample_factors.py tests/test_sample_factors.py
git commit -m "feat(factors): 因子トークンspecからサンプル集合への展開を追加

要素内AND・順不同のトークン部分集合一致。既定で role=sample のみ残し、
QC/blank を落とした内訳を excluded として返して呼び出し側が開示できるようにする。"
```

---

### Task 3: `assign_factor_groups` — 因子軸の直積による群ラベル

**Files:**
- Modify: `sample_factors.py`
- Test: `tests/test_sample_factors.py`

**Interfaces:**
- Consumes: Task 1 の `SampleFacet` / `split_tokens` / `build_sample_facets`
- Produces: `assign_factor_groups(facets, group_factors=None, group_levels=None, sep="|") -> dict[str, str | None]`

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` の import 一覧に `assign_factor_groups` を追加し、末尾の `if __name__ == "__main__":` の直前に追記:

```python
class AssignFactorGroupsTests(unittest.TestCase):
    def setUp(self):
        self.names = [
            "20220901_RAW_control_0h_1_NEG",
            "20220901_RAW_control_6h_1_NEG",
            "20220902_RAW_ILG_0h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_G_uralensis_6h_1_NEG",
        ]
        self.facets = build_sample_facets(self.names, name_class_index({
            "20220901_RAW_control_0h_1_NEG": "control",
            "20220901_RAW_control_6h_1_NEG": "control",
            "20220902_RAW_ILG_0h_1_NEG": "ILG",
            "20220902_RAW_ILG_6h_1_NEG": "ILG",
            "20220902_RAW_G_uralensis_6h_1_NEG": "G",
        }))

    def test_no_factors_falls_back_to_class_id(self):
        groups = assign_factor_groups(self.facets)
        self.assertEqual(groups["20220902_RAW_ILG_6h_1_NEG"], "ILG")

    def test_single_axis_label_has_no_separator(self):
        # 1軸なら従来の group_levels と出力文字列が完全一致する（後方互換の要）。
        groups = assign_factor_groups(self.facets, group_levels=["0h", "6h"])
        self.assertEqual(groups["20220902_RAW_ILG_0h_1_NEG"], "0h")
        self.assertEqual(groups["20220902_RAW_ILG_6h_1_NEG"], "6h")

    def test_two_axes_produce_cross_product_labels(self):
        groups = assign_factor_groups(self.facets, group_factors=[
            ["control", "ILG", "G_uralensis"],
            ["0h", "6h"],
        ])
        self.assertEqual(groups["20220901_RAW_control_0h_1_NEG"], "control|0h")
        self.assertEqual(groups["20220902_RAW_ILG_6h_1_NEG"], "ILG|6h")
        self.assertEqual(groups["20220902_RAW_G_uralensis_6h_1_NEG"], "G_uralensis|6h")

    def test_custom_separator(self):
        groups = assign_factor_groups(
            self.facets, group_factors=[["ILG"], ["6h"]], sep=" / ")
        self.assertEqual(groups["20220902_RAW_ILG_6h_1_NEG"], "ILG / 6h")

    def test_axis_without_hit_becomes_other(self):
        groups = assign_factor_groups(self.facets, group_factors=[["ILG"], ["24h"]])
        self.assertEqual(groups["20220902_RAW_ILG_6h_1_NEG"], "ILG|other")

    def test_two_hits_within_one_axis_raises(self):
        with self.assertRaises(ValueError) as ctx:
            assign_factor_groups(self.facets, group_factors=[["ILG", "6h"]])
        self.assertIn("multiple", str(ctx.exception))

    def test_group_factors_wins_over_group_levels(self):
        groups = assign_factor_groups(
            self.facets, group_factors=[["0h"], ["ILG"]], group_levels=["control"])
        self.assertEqual(groups["20220902_RAW_ILG_0h_1_NEG"], "0h|ILG")

    def test_blank_axis_values_are_ignored(self):
        groups = assign_factor_groups(self.facets, group_factors=[["ILG", "", "  "]])
        self.assertEqual(groups["20220902_RAW_ILG_6h_1_NEG"], "ILG")


class AssignFactorGroupsRoleTests(unittest.TestCase):
    def test_non_sample_roles_are_labeled_by_role_not_excluded(self):
        # QC は除外せず可視化する。QC の凝集は前処理品質の判断材料になるため。
        facets = build_sample_facets(
            ["20240311_ILG_6h_1_NEG", "20240311_QC_ILG_NEG_1"], None)
        groups = assign_factor_groups(facets, group_levels=["6h"])
        self.assertEqual(groups["20240311_ILG_6h_1_NEG"], "6h")
        self.assertEqual(groups["20240311_QC_ILG_NEG_1"], "qc")

    def test_role_labeling_does_not_apply_without_factors(self):
        facets = build_sample_facets(["20240311_QC_ILG_NEG_1"], None)
        self.assertIsNone(assign_factor_groups(facets)["20240311_QC_ILG_NEG_1"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: FAIL — `ImportError: cannot import name 'assign_factor_groups' from 'sample_factors'`

- [ ] **Step 3: Write minimal implementation**

`sample_factors.py` の `_no_match_message` の直後に追加:

```python
def assign_factor_groups(facets, group_factors=None, group_levels=None, sep="|"):
    """因子軸ごとに値 spec を解決し、直積ラベルを {サンプル名: ラベル} で返す。

    - group_factors: 因子軸のリスト。各軸は値 spec のリスト（例
      [["control", "ILG"], ["0h", "6h"]] → "control|0h" 等）。値 spec は多トークン可
      （"G_uralensis"）。位置インデックスを使わないのは、G_uralensis のように値が2
      トークンに割れると以降の位置がずれて壊れるため。
    - group_levels: 1軸のときの糖衣で group_factors=[group_levels] と等価。1軸では
      連結が起きないため、従来の出力文字列と完全に一致する（後方互換）。
      group_factors が指定されていればそちらが優先。
    - 両方未指定なら各サンプルの Class ID（無ければ None）をそのままラベルにする。
    - 軸内で2つ以上の値に一致したら ValueError（値が相互排他でない＝指定ミス）。
    - 軸内でどの値にも一致しなければその軸は "other"（除外はしない）。
    - role が sample でないサンプルは直積ではなく role 名（"qc"/"blank"）をラベルに
      する。除外しないのは QC の凝集が前処理品質の判断材料になるため。
    """
    if group_factors:
        axes = [[str(v).strip() for v in axis if str(v).strip()] for axis in group_factors]
        axes = [axis for axis in axes if axis]
    elif group_levels:
        axes = [[str(v).strip() for v in group_levels if str(v).strip()]]
        axes = [axis for axis in axes if axis]
    else:
        axes = []

    groups: dict[str, str | None] = {}
    for name, facet in facets.items():
        if not axes:
            groups[name] = facet.class_id
            continue
        if facet.role != "sample":
            groups[name] = facet.role
            continue
        parts = []
        for axis in axes:
            hits = [value for value in axis if split_tokens(value) <= facet.tokens]
            if len(hits) > 1:
                raise ValueError(
                    f"Sample '{name}' matches multiple values in one factor axis "
                    f"({', '.join(hits)}); values within an axis must be mutually exclusive."
                )
            parts.append(hits[0] if hits else "other")
        groups[name] = sep.join(parts)
    return groups
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: PASS（39 tests）

- [ ] **Step 5: Commit**

```bash
git add sample_factors.py tests/test_sample_factors.py
git commit -m "feat(factors): 因子軸の直積による群ラベル付けを追加

処置x時点のような多因子色分けを、群を手書き列挙せずに出せるようにする。
1軸なら従来の group_levels と出力文字列が完全一致する。QCは除外せず
role名でラベルし、前処理品質の判断材料として可視化する。"
```

---

### Task 4: `token_vocabulary` — 何で絞れるかの語彙一覧

**Files:**
- Modify: `sample_factors.py`
- Test: `tests/test_sample_factors.py`

**Interfaces:**
- Consumes: Task 1 の `SampleFacet` / `build_sample_facets`、`msdial_tags.normalize_sample_name`
- Produces: `token_vocabulary(facets) -> dict`
  形は `{"tokens": {token: {"samples": int, "roles": {role: int}, "positions": [int, ...]}}, "by_position": {"0": [token, ...], ...}}`

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` の import 一覧に `token_vocabulary` を追加し、末尾の `if __name__ == "__main__":` の直前に追記:

```python
class TokenVocabularyTests(unittest.TestCase):
    def setUp(self):
        self.facets = build_sample_facets([
            "20220901_RAW_control_6h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_QC_RAW_NEG_1",
        ], None)
        self.vocab = token_vocabulary(self.facets)

    def test_counts_samples_per_token(self):
        self.assertEqual(self.vocab["tokens"]["raw"]["samples"], 3)
        self.assertEqual(self.vocab["tokens"]["6h"]["samples"], 2)
        self.assertEqual(self.vocab["tokens"]["ilg"]["samples"], 1)

    def test_breaks_down_by_role(self):
        self.assertEqual(self.vocab["tokens"]["raw"]["roles"], {"qc": 1, "sample": 2})
        self.assertEqual(self.vocab["tokens"]["ilg"]["roles"], {"sample": 1})

    def test_reports_positions_within_sample_name(self):
        self.assertEqual(self.vocab["tokens"]["raw"]["positions"], [1, 2])
        self.assertEqual(self.vocab["tokens"]["6h"]["positions"], [3])

    def test_by_position_lists_token_vocabulary(self):
        self.assertEqual(self.vocab["by_position"]["0"], ["20220901", "20220902"])
        self.assertEqual(set(self.vocab["by_position"]["2"]), {"control", "ilg", "raw"})

    def test_class_only_token_has_no_position(self):
        facets = build_sample_facets(["s1"], name_class_index({"s1": "Cerebellum_gf"}))
        vocab = token_vocabulary(facets)
        self.assertEqual(vocab["tokens"]["cerebellum"]["positions"], [])
        self.assertEqual(vocab["tokens"]["cerebellum"]["samples"], 1)

    def test_empty_facets(self):
        self.assertEqual(token_vocabulary({}), {"tokens": {}, "by_position": {}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: FAIL — `ImportError: cannot import name 'token_vocabulary' from 'sample_factors'`

- [ ] **Step 3: Write minimal implementation**

`sample_factors.py` の `assign_factor_groups` の直後に追加:

```python
def token_vocabulary(facets) -> dict:
    """トークン語彙を集計する（何で絞れるかを発見するための一覧）。

    戻り値:
      {"tokens": {token: {"samples": 出現サンプル数,
                          "roles": {role: 件数},
                          "positions": [サンプル名を `_` 分割したときの出現位置, ...]}},
       "by_position": {"0": [token, ...], ...}}

    positions / by_position は因子の並びを推測する手掛かりだが、G_uralensis のように
    値が2トークンに割れると以降がずれるため、**フィルタ指定には使わない**（指定は
    常に値トークンで行う）。Class ID にしか無いトークンは positions が空になる。
    """
    counts: dict[str, dict] = {}
    by_position: dict[int, set[str]] = {}

    for facet in facets.values():
        for token in facet.tokens:
            entry = counts.setdefault(token, {"samples": 0, "roles": {}, "positions": set()})
            entry["samples"] += 1
            entry["roles"][facet.role] = entry["roles"].get(facet.role, 0) + 1
        normalized = normalize_sample_name(facet.name, strip_processing_timestamp=True)
        for position, token in enumerate(normalized.split("_")):
            if not token:
                continue
            by_position.setdefault(position, set()).add(token)
            counts.setdefault(
                token, {"samples": 0, "roles": {}, "positions": set()},
            )["positions"].add(position)

    return {
        "tokens": {
            token: {
                "samples": entry["samples"],
                "roles": dict(sorted(entry["roles"].items())),
                "positions": sorted(entry["positions"]),
            }
            for token, entry in sorted(counts.items())
        },
        "by_position": {str(position): sorted(tokens) for position, tokens in sorted(by_position.items())},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: PASS（45 tests）

- [ ] **Step 5: Commit**

```bash
git add sample_factors.py tests/test_sample_factors.py
git commit -m "feat(factors): トークン語彙の集計を追加

出現サンプル数・role内訳・出現位置を返し、何で絞れるかを発見できるようにする。"
```

---

### Task 5: `msdial_classes` をファセット経由へ載せ替え

**Files:**
- Modify: `msdial_classes.py:177-247`（`assign_sample_groups`）、`msdial_classes.py:250-315`（`filter_arf_by_class_ids`）
- Test: `tests/test_msdial_classes.py`（既存・**書き換えない**）、`tests/test_sample_factors.py`

**Interfaces:**
- Consumes: Task 1-3 の `build_sample_facets` / `expand_sample_specs` / `assign_factor_groups` / `arf_sample_names`
- Produces:
  - `filter_arf_by_class_ids(features, class_index, class_ids, *, missing_sample_policy="error", include_roles=("sample",)) -> tuple[list[dict], dict]`
    stats に `matched_samples: list[str]` と `excluded_by_role: list[str]` を追加（既存キーは維持）
  - `assign_sample_groups(sample_names, class_index, group_levels=None, group_factors=None) -> dict[str, str | None]`
  - `expand_class_specs` は現状のまま据え置き（削除も変更もしない）

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` の import 群に以下を追加:

```python
from msdial_classes import assign_sample_groups, filter_arf_by_class_ids
```

末尾の `if __name__ == "__main__":` の直前に追記:

```python
class FilterArfBySampleTokensTests(unittest.TestCase):
    def _features(self, names):
        return [{"MasterAlignmentID": 1,
                 "AlignedPeakProperties": [[i, n, 100.0 + i] for i, n in enumerate(names)]}]

    def setUp(self):
        self.names = [
            "20220901_RAW_control_0h_1_NEG",
            "20220901_RAW_control_6h_1_NEG",
            "20220902_RAW_ILG_0h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
        ]
        self.features = self._features(self.names)
        self.index = name_class_index({
            "20220901_RAW_control_0h_1_NEG": "control",
            "20220901_RAW_control_6h_1_NEG": "control",
            "20220902_RAW_ILG_0h_1_NEG": "ILG",
            "20220902_RAW_ILG_6h_1_NEG": "ILG",
        })

    def test_filters_by_token_absent_from_class_id(self):
        # 6h は Class ID に無い。これが通ることが本タスクの眼目。
        filtered, stats = filter_arf_by_class_ids(self.features, self.index, ["6h"])
        kept = {row[1] for row in filtered[0]["AlignedPeakProperties"]}
        self.assertEqual(kept, {"20220901_RAW_control_6h_1_NEG", "20220902_RAW_ILG_6h_1_NEG"})
        self.assertEqual(stats["after_sample_peaks"], 2)
        self.assertEqual(stats["before_sample_peaks"], 4)

    def test_multi_token_spec_and_or_across_specs(self):
        filtered, stats = filter_arf_by_class_ids(
            self.features, self.index, ["ILG_6h", "control_6h"])
        kept = {row[1] for row in filtered[0]["AlignedPeakProperties"]}
        self.assertEqual(kept, {"20220901_RAW_control_6h_1_NEG", "20220902_RAW_ILG_6h_1_NEG"})
        self.assertEqual(sorted(stats["matched_class_ids"]), ["ILG", "control"])
        self.assertEqual(len(stats["matched_samples"]), 2)

    def test_works_without_mddata(self):
        # class_index=None は従来 ValueError だった。名前トークンだけで成立させる。
        filtered, stats = filter_arf_by_class_ids(self.features, None, ["ILG"])
        kept = {row[1] for row in filtered[0]["AlignedPeakProperties"]}
        self.assertEqual(kept, {"20220902_RAW_ILG_0h_1_NEG", "20220902_RAW_ILG_6h_1_NEG"})
        self.assertEqual(stats["matched_class_ids"], [])
        self.assertEqual(stats["missing_samples"], 0)

    def test_qc_is_excluded_by_default_and_reported(self):
        names = self.names + ["20220901_QC_RAW_NEG_1"]
        filtered, stats = filter_arf_by_class_ids(self._features(names), None, ["raw"])
        kept = {row[1] for row in filtered[0]["AlignedPeakProperties"]}
        self.assertNotIn("20220901_QC_RAW_NEG_1", kept)
        self.assertEqual(stats["excluded_by_role"], ["20220901_QC_RAW_NEG_1"])

    def test_include_roles_can_bring_qc_back(self):
        names = self.names + ["20220901_QC_RAW_NEG_1"]
        filtered, stats = filter_arf_by_class_ids(
            self._features(names), None, ["raw"], include_roles=("sample", "qc"))
        kept = {row[1] for row in filtered[0]["AlignedPeakProperties"]}
        self.assertIn("20220901_QC_RAW_NEG_1", kept)
        self.assertEqual(stats["excluded_by_role"], [])


class AssignSampleGroupsFactorTests(unittest.TestCase):
    def test_group_factors_produce_cross_product(self):
        index = name_class_index({
            "20220901_RAW_control_0h_1_NEG": "control",
            "20220902_RAW_ILG_6h_1_NEG": "ILG",
        })
        groups = assign_sample_groups(
            list(index["by_file_name"].keys() and [
                "20220901_RAW_control_0h_1_NEG", "20220902_RAW_ILG_6h_1_NEG"]),
            index,
            group_factors=[["control", "ILG"], ["0h", "6h"]],
        )
        self.assertEqual(groups["20220901_RAW_control_0h_1_NEG"], "control|0h")
        self.assertEqual(groups["20220902_RAW_ILG_6h_1_NEG"], "ILG|6h")

    def test_group_levels_work_without_class_index(self):
        groups = assign_sample_groups(
            ["20220902_RAW_ILG_6h_1_NEG"], None, group_levels=["6h"])
        self.assertEqual(groups["20220902_RAW_ILG_6h_1_NEG"], "6h")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -q`
Expected: FAIL — `test_filters_by_token_absent_from_class_id` が `ValueError: No Class ID matched spec '6h'` で落ちる

- [ ] **Step 3: Write minimal implementation**

(a) `msdial_classes.py` の import 群（14行目 `from msdial_tags import normalize_sample_name` の直後）に追加:

```python
from sample_factors import (
    arf_sample_names,
    assign_factor_groups,
    build_sample_facets,
    expand_sample_specs,
)
```

(b) `assign_sample_groups`（211-247行）を丸ごと以下に置換:

```python
def assign_sample_groups(
    sample_names: list[str],
    class_index: dict | None,
    group_levels: list[str] | None = None,
    group_factors: list[list[str]] | None = None,
) -> dict[str, str | None]:
    """各 PCA サンプル名を、色分け用の群ラベルへ対応づける。

    - 因子指定なし: 群はサンプルの完全 Class ID（class_index が無ければ None）。
    - ``group_levels``（1因子の値トークン、例 ["gf", "spf"]）: その因子だけで統合する。
      どの値にも該当しなければ "other"、2つ以上に該当すれば ValueError。
    - ``group_factors``（因子軸のリスト、例 [["control","ILG"], ["0h","6h"]]）: 軸ごとの
      値を解決して直積ラベル（"control|0h"）にする。group_levels より優先。

    値トークンは Class ID だけでなくサンプル名からも解決される。時点や複製のように
    Class ID に入っていない因子で色分けできるようにするため（詳細は sample_factors）。
    """
    facets = build_sample_facets(sample_names, class_index)
    return assign_factor_groups(facets, group_factors=group_factors, group_levels=group_levels)
```

(c) `filter_arf_by_class_ids`（250-315行）を丸ごと以下に置換:

```python
def filter_arf_by_class_ids(
    features: list[dict],
    class_index: dict | None,
    class_ids: list[str] | None,
    *,
    missing_sample_policy: str = "error",
    include_roles=("sample",),
) -> tuple[list[dict], dict]:
    """サンプル名 ∪ Class ID の因子トークンでサンプル別ピーク行を絞り込む。

    ``class_ids`` の各要素は `_` 区切りの部分指定（要素内 AND・順不同、要素間 OR）。
    Class ID だけでなくサンプル名のトークンにも一致するため、Class ID に入っていない
    因子（時点・複製・測定日）でも絞り込める。``class_index`` が None（.mddata 未検出）
    でもサンプル名だけで成立する。

    ``include_roles`` は既定 ("sample",) で QC/blank を落とし、内訳を stats の
    ``excluded_by_role`` に残す。行の照合キーは AlignedPeakProperties の FileName。
    """
    specs = [str(value) for value in class_ids or [] if str(value).strip()]
    before_rows = _count_rows(features)
    if not specs:
        return features, {
            "requested_class_ids": [],
            "matched_class_ids": [],
            "before_spots": len(features),
            "after_spots": len(features),
            "before_sample_peaks": before_rows,
            "after_sample_peaks": before_rows,
        }
    if missing_sample_policy not in {"error", "exclude"}:
        raise ValueError("missing_sample_policy must be 'error' or 'exclude'")

    facets = build_sample_facets(arf_sample_names(features), class_index)
    matches, excluded = expand_sample_specs(specs, facets, include_roles=include_roles)
    selected = {name for names in matches.values() for name in names}
    excluded_by_role = sorted({name for names in excluded.values() for name in names})

    # Class メタデータを持つはずなのに解決できないサンプルは、FileID/FileName の
    # 食い違いを示す。従来どおり既定でエラーにする（class_index が無いときは
    # そもそも名前トークンだけで動く設計なので「未対応」ではない）。
    missing_samples: set[str] = set()
    if class_index is not None:
        missing_samples = {name for name, facet in facets.items() if facet.class_id is None}
        if missing_samples and missing_sample_policy == "error":
            raise ValueError(
                "No Class ID metadata matched these ARF samples: "
                + ", ".join(sorted(missing_samples))
            )

    filtered = []
    for spot in features:
        kept_rows = []
        for row in spot.get("AlignedPeakProperties") or []:
            _, file_name = _arf_sample_identity(row)
            if file_name in selected:
                kept_rows.append(row)
        if kept_rows:
            copied = spot.copy()
            copied["AlignedPeakProperties"] = kept_rows
            filtered.append(copied)

    matched_class_ids = sorted({
        facets[name].class_id for name in selected if facets[name].class_id
    })
    return filtered, {
        "requested_class_ids": specs,
        "matched_class_ids": matched_class_ids,
        "matched_samples": sorted(selected),
        "excluded_by_role": excluded_by_role,
        "before_spots": len(features),
        "after_spots": len(filtered),
        "before_sample_peaks": before_rows,
        "after_sample_peaks": _count_rows(filtered),
        "missing_samples": len(missing_samples),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py tests/test_msdial_classes.py tests/test_server_class_filter.py -q`
Expected: PASS（既存 `tests/test_msdial_classes.py` の 13 tests も**無改修で**通ること）

- [ ] **Step 5: Commit**

```bash
git add msdial_classes.py tests/test_sample_factors.py
git commit -m "feat(classes): Class IDフィルタと群分けを統合トークン空間へ載せ替え

filter_arf_by_class_ids / assign_sample_groups をファセット経由に書き換え、
Class ID に無い因子（時点・複製）でも絞り込み・色分けできるようにする。
.mddata 未検出でも成立させ、role による除外内訳を stats に残す。
既存の class_ids / group_levels テストは無改修で通る。"
```

---

### Task 6: `arf_differential` の2群プールをファセット経由へ

**Files:**
- Modify: `tools_arf.py:516-549`（`_pool_group_labels`）、`tools_arf.py:698-722`（呼び出し側）、`tools_arf.py:753-759`（payload）
- Test: `tests/test_differential_tools.py`（既存・**書き換えない**）、`tests/test_sample_factors.py`

**Interfaces:**
- Consumes: Task 1-2 の `build_sample_facets` / `expand_sample_specs`
- Produces: `tools_arf._pool_group_labels(sample_names, group_labels, group_a, group_b) -> tuple[list, dict, dict]`
  第1引数が増え、戻り値が `(relabeled, resolved, resolved_samples)` の3要素になる。
  `resolved = {"group_a": [群ラベル, ...], "group_b": [...]}`（payload の `resolved_class_ids`）、
  `resolved_samples = {"group_a": [サンプル名, ...], "group_b": [...]}`（payload の `resolved_samples`）

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` の import 群に追加:

```python
import json

import numpy as np

import server
import session_state
```

末尾の `if __name__ == "__main__":` の直前に追記:

```python
class DifferentialByNameTokenTests(unittest.TestCase):
    """Class ID は処置だけ、時点はサンプル名にしかない構成で時点を揃えた2群比較。"""

    def setUp(self):
        session_state.session = server.AnalysisSession()
        names = [
            "20220902_RAW_ILG_6h_1_NEG", "20220902_RAW_ILG_6h_2_NEG",
            "20220902_RAW_ILG_6h_3_NEG",
            "20220901_RAW_control_6h_1_NEG", "20220901_RAW_control_6h_2_NEG",
            "20220901_RAW_control_6h_3_NEG",
            "20220902_RAW_ILG_0h_1_NEG", "20220901_RAW_control_0h_1_NEG",
        ]
        session_state.session.feature_matrix = np.array([
            [50.0, 5.0], [52.0, 5.1], [48.0, 4.9],
            [10.0, 5.0], [11.0, 5.2], [9.5, 4.8],
            [30.0, 5.0], [30.0, 5.0],
        ])
        session_state.session.pp_sample_names = names
        session_state.session.pp_feature_names = ["Spot_0_height", "Spot_1_height"]
        session_state.session.preprocessing_recipe = {"normalize": "median"}
        session_state.session.sample_meta = {
            n: {"group": ("ILG" if "ILG" in n else "control"),
                "role": "sample", "batch": "d1"}
            for n in names
        }

    def test_time_matched_two_group_comparison(self):
        out = json.loads(server.arf_differential(group_a="ILG_6h", group_b="control_6h"))
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["n_a"], 3)
        self.assertEqual(out["n_b"], 3)
        self.assertEqual(out["summary"]["n_significant"], 1)

    def test_payload_lists_resolved_samples(self):
        out = json.loads(server.arf_differential(group_a="ILG_6h", group_b="control_6h"))
        self.assertEqual(out["resolved_samples"]["group_a"], [
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
            "20220902_RAW_ILG_6h_3_NEG",
        ])
        self.assertTrue(any("比較サンプル" in c for c in out["caveats"]))

    def test_0h_samples_are_not_pooled_in(self):
        out = json.loads(server.arf_differential(group_a="ILG_6h", group_b="control_6h"))
        joined = " ".join(out["resolved_samples"]["group_a"] + out["resolved_samples"]["group_b"])
        self.assertNotIn("_0h_", joined)

    def test_treatment_only_spec_still_pools_all_timepoints(self):
        out = json.loads(server.arf_differential(group_a="ILG", group_b="control"))
        self.assertEqual(out["n_a"], 4)
        self.assertEqual(out["n_b"], 4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -k Differential -q`
Expected: FAIL — `status == "error"`（`_pool_group_labels` が群ラベル `ILG` しか見ないため `ILG_6h` に一致せずエラー）

- [ ] **Step 3: Write minimal implementation**

(a) `tools_arf.py` の import 群（22行目 `from mcp_core import mcp` の直前）に追加:

```python
import sample_factors
```

(b) `_pool_group_labels`（516-549行）を丸ごと以下に置換:

```python
def _pool_group_labels(sample_names, group_labels, group_a, group_b):
    """完全 Class ID / 群ラベルだけでなく、サンプル名の因子トークンでもプール群を作る。

    Class ID は MS-DIAL 上で入力された1文字列にすぎず、時点や複製のような因子は
    サンプル名にしか無いことがある。トークン空間を tokens(サンプル名) ∪ tokens(群ラベル)
    に統合し、`group_a="ILG_6h"` のような多因子指定を通す。完全な群ラベルを渡した
    場合は（そのトークン集合を含む他のサンプルが無い限り）従来どおりの2群比較になる。

    group_labels が None のサンプル（QC/blank・群未解決）は候補から外す。
    戻り値 (relabeled, resolved, resolved_samples)。一致ゼロ・両群の重複は ValueError。
    """
    if str(group_a) == str(group_b):
        raise ValueError(f"group_a と group_b が同一です: {group_a!r}")
    available = sorted({str(g) for g in group_labels if g is not None})
    if not available:
        raise ValueError(
            "Class ID メタデータが解決できていないため群を特定できません（.mddata 未検出）。")

    meta = {name: {"group": label, "role": "sample"}
            for name, label in zip(sample_names, group_labels) if label is not None}
    facets = sample_factors.build_sample_facets(list(meta), None, sample_meta=meta)
    try:
        # role は呼び出し側が group_labels=None で既に落としているので、ここでは絞らない。
        matches, _ = sample_factors.expand_sample_specs(
            [group_a, group_b], facets, include_roles=None)
    except ValueError as exc:
        raise ValueError(f"{exc} 利用可能な群ラベル: {', '.join(available)}") from exc

    a_names, b_names = set(matches[group_a]), set(matches[group_b])
    overlap = sorted({str(meta[n]["group"]) for n in (a_names & b_names)})
    if overlap:
        raise ValueError(
            f"group_a='{group_a}' と group_b='{group_b}' が同じサンプルを含みます "
            f"({', '.join(overlap)})。群は排他である必要があります。")

    relabeled = []
    for name, label in zip(sample_names, group_labels):
        if label is None:
            relabeled.append(None)
        elif name in a_names:
            relabeled.append(group_a)
        elif name in b_names:
            relabeled.append(group_b)
        else:
            relabeled.append(None)
    resolved = {
        "group_a": sorted({str(meta[n]["group"]) for n in a_names}),
        "group_b": sorted({str(meta[n]["group"]) for n in b_names}),
    }
    resolved_samples = {"group_a": sorted(a_names), "group_b": sorted(b_names)}
    return relabeled, resolved, resolved_samples
```

(c) `tools_arf.py:698-700` の呼び出しを差し替え:

```python
    if group_a is not None and group_b is not None:
        try:
            group_labels, resolved, resolved_samples = _pool_group_labels(
                sample_names, group_labels, group_a, group_b)
```

(d) `tools_arf.py:716-722` のプール caveat の直後（`results = differential.two_group_test(...)` の直前）に、比較サンプルの全列挙を追加:

```python
        caveats.append(
            "比較サンプル: "
            + "; ".join(
                f"{spec} = {', '.join(names)}"
                for spec, names in ((group_a, resolved_samples["group_a"]),
                                    (group_b, resolved_samples["group_b"]))
            )
            + "。指定トークンは Class ID とサンプル名の両方から解決されます。"
        )
```

(e) `tools_arf.py:753-759` の payload に `resolved_samples` を追加:

```python
        payload = {"status": "success", "kind": "two_group",
                   "group_a": group_a, "group_b": group_b,
                   "resolved_class_ids": resolved,
                   "resolved_samples": resolved_samples,
                   "n_a": n_a, "n_b": n_b,
                   "summary": summary, "caveats": caveats,
                   "volcano_note": "全特徴の volcano 点列は本要約に非同梱。"
                                   "save_volcano_figure で図示できます。"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py tests/test_differential_tools.py -q`
Expected: PASS（既存 `TestPooledGroupSpecs` / `TestDifferentialSampleSelection` も**無改修で**通ること）

- [ ] **Step 5: Commit**

```bash
git add tools_arf.py tests/test_sample_factors.py
git commit -m "feat(differential): 時点を揃えた2群比較を可能にする

_pool_group_labels をファセット経由にし、group_a='ILG_6h' のように Class ID に
無い因子を含む多因子指定を通す。比較に使ったサンプル名を caveat と payload の
resolved_samples に全列挙し、何を比べたのかの取り違えを防ぐ。"
```

---

### Task 7: `arf_parser` / `arf_pca_preprocessed` に `group_factors` / `include_roles`

**Files:**
- Modify: `tools_arf.py:279-325`（`arf_pca_preprocessed`）、`tools_arf.py:328-510`（`arf_parser`）、`tool_helpers.py:280-294`（`_format_arf_class_filter`）
- Test: `tests/test_server_class_filter.py`（既存・**書き換えない**）、`tests/test_sample_factors.py`

**Interfaces:**
- Consumes: Task 5 の `filter_arf_by_class_ids(..., include_roles=...)` / `assign_sample_groups(..., group_factors=...)`
- Produces:
  - `arf_parser(..., group_levels=None, group_factors=None, include_roles=None)`
  - `arf_pca_preprocessed(..., group_levels=None, group_factors=None)`
  - `_format_arf_class_filter` が `excluded_by_role` を1行で開示する

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` の末尾の `if __name__ == "__main__":` の直前に追記:

```python
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd


class _ParserFakeSession:
    """test_server_class_filter.FakeSession と同型の最小セッション。"""

    def __init__(self, names):
        self.current_file_path = None
        self.features = [{
            "MasterAlignmentID": 10,
            "AlignedPeakProperties": [[i, n, 100.0 + i] for i, n in enumerate(names)],
        }]
        self.filtered_features = None
        self.pca_result = None
        self.arf_tag_index = {}
        self.arf_class_index = None
        self.excluded_samples = set()
        self.excluded_spots = set()

    def load_data(self, file_path, tag_directory=None):
        self.current_file_path = file_path
        return self.features

    def maybe_prepend_caveat(self, text, topic=None):
        return text


def _fake_extract_peak_properties(features):
    rows = sum(len(spot["AlignedPeakProperties"]) for spot in features)
    return pd.DataFrame({"row": range(rows)})


def _fake_build_pca_matrix(features, use_properties=None, min_detection_rate=0.0):
    rows = features[0]["AlignedPeakProperties"] if features else []
    names = [row[1] for row in rows]
    return np.ones((len(names), 2)), names, ["10_height", "11_height"]


def _fake_run_pca(matrix, n_components=None, log_transform=False):
    return {"components": np.zeros((matrix.shape[0], 2)).tolist(),
            "explained_variance_ratio": [0.6, 0.4]}


class ArfParserFactorTests(unittest.TestCase):
    def setUp(self):
        self.session = _ParserFakeSession([
            "20220901_RAW_control_0h_1_NEG",
            "20220901_RAW_control_6h_1_NEG",
            "20220902_RAW_ILG_0h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220901_QC_RAW_NEG_1",
        ])
        self.patches = [
            patch.object(session_state, "session", self.session),
            patch.object(server.arf_reader, "extract_peak_properties", _fake_extract_peak_properties),
            patch.object(server.arf_reader, "build_pca_matrix", _fake_build_pca_matrix),
            patch.object(server.arf_reader, "run_pca", _fake_run_pca),
            patch.object(server.arf_reader, "get_pca_loading_features", return_value=[]),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def _run(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            arf_path = Path(tmp) / "test.arf"
            arf_path.touch()
            return server.arf_parser(str(arf_path), **kwargs)

    def test_group_factors_produce_cross_product_counts(self):
        result = self._run(group_factors=[["control", "ILG"], ["0h", "6h"]])
        self.assertIn("control|0h=1", result)
        self.assertIn("ILG|6h=1", result)
        self.assertIn("qc=1", result)

    def test_class_ids_filter_by_name_only_token(self):
        result = self._run(class_ids=["6h"])
        self.assertIn("Class IDフィルタ**: `6h`", result)
        rows = self.session.filtered_features[0]["AlignedPeakProperties"]
        self.assertEqual(
            [row[1] for row in rows],
            ["20220901_RAW_control_6h_1_NEG", "20220902_RAW_ILG_6h_1_NEG"],
        )

    def test_qc_excluded_by_role_is_disclosed(self):
        result = self._run(class_ids=["raw"])
        self.assertIn("role により除外", result)
        self.assertIn("20220901_QC_RAW_NEG_1", result)

    def test_include_roles_brings_qc_back(self):
        self._run(class_ids=["raw"], include_roles=["sample", "qc"])
        rows = self.session.filtered_features[0]["AlignedPeakProperties"]
        self.assertIn("20220901_QC_RAW_NEG_1", [row[1] for row in rows])


class ArfPcaPreprocessedFactorTests(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        names = ["20220901_RAW_control_0h_1_NEG", "20220902_RAW_ILG_6h_1_NEG"]
        session_state.session.feature_matrix = np.array([[1.0, 2.0], [3.0, 4.0]])
        session_state.session.pp_sample_names = names
        session_state.session.pp_feature_names = ["Spot_0_height", "Spot_1_height"]
        session_state.session.preprocessing_recipe = {"normalize": "median"}
        session_state.session.features = []

    def test_group_factors_label_the_plot(self):
        with patch.object(server.arf_reader, "run_pca", _fake_run_pca), \
             patch.object(server.arf_reader, "get_pca_loading_features", return_value=[]):
            result = server.arf_pca_preprocessed(
                group_factors=[["control", "ILG"], ["0h", "6h"]])
        self.assertIn("control|0h=1", result)
        self.assertIn("ILG|6h=1", result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -k "ArfParserFactor or ArfPcaPreprocessedFactor" -q`
Expected: FAIL — `TypeError: arf_parser() got an unexpected keyword argument 'group_factors'`

- [ ] **Step 3: Write minimal implementation**

(a) `tool_helpers.py` の `_format_arf_class_filter`（280-294行）を丸ごと以下に置換:

```python
def _format_arf_class_filter(stats: dict | None) -> str:
    if not stats or not stats.get("requested_class_ids"):
        return ""
    matched = stats.get("matched_class_ids") or []
    # 部分指定が複数クラスに展開された場合は、実際にマッチしたClass IDも明示する。
    matched_line = ""
    if matched and list(matched) != list(stats["requested_class_ids"]):
        matched_line = f"- **Class IDフィルタ展開先**: `{', '.join(matched)}`\n"
    # 統合トークン空間ではサンプル名経由で QC/blank を掴み得る。既定で落としたことを
    # 黙らせず、含める手段（include_roles）とセットで開示する。
    excluded = stats.get("excluded_by_role") or []
    excluded_line = ""
    if excluded:
        shown = ", ".join(excluded[:10])
        more = f" ほか{len(excluded) - 10}件" if len(excluded) > 10 else ""
        excluded_line = (
            f"- **role により除外**: {len(excluded)} 件（{shown}{more}）。"
            '含めるには include_roles=["sample", "qc"] を指定\n'
        )
    return (
        f"- **Class IDフィルタ**: `{', '.join(stats['requested_class_ids'])}`\n"
        f"{matched_line}"
        f"{excluded_line}"
        f"- **Class IDフィルタ後**: スポット {stats['after_spots']}/{stats['before_spots']}, "
        f"サンプル別ピーク {stats['after_sample_peaks']}/{stats['before_sample_peaks']}, "
        f"メタデータ未対応サンプル {stats.get('missing_samples', 0)} 件\n"
    )
```

(b) `arf_pca_preprocessed`（279-284行）のシグネチャに `group_factors` を追加:

```python
def arf_pca_preprocessed(
    components: int | None = None,
    top_features: int = 10,
    log_transform: bool = False,
    group_levels: list[str] | None = None,
    group_factors: list[list[str]] | None = None,
) -> str:
```

docstring 末尾（`"""` の直前）に1行追加:

```
    群分けは group_levels（1因子）または group_factors（因子軸の直積、例
    [["control","ILG"],["0h","6h"]] → "control|0h"）で指定する。値トークンは Class ID
    とサンプル名の両方から解決されるため、Class ID に無い時点などでも色分けできる。
```

302行の `assign_sample_groups` 呼び出しを差し替え:

```python
    sample_groups = assign_sample_groups(
        sample_names, session_state.session.arf_class_index, group_levels,
        group_factors=group_factors,
    )
```

(c) `arf_parser`（329-346行）のシグネチャの `group_levels: list[str] | None = None,` の直後に追加:

```python
    group_factors: list[list[str]] | None = None,
    include_roles: list[str] | None = None,
```

docstring の `- group_levels:` の項の直後に追加:

```
    - group_factors: [任意] 因子軸のリスト。各軸は値トークンのリストで、直積ラベルを
      作る（例 `[["control","ILG"],["0h","6h"]]` → `"control|0h"`）。group_levels より優先。
      処置×時点のような多群を手書き列挙せずに色分けできる。
    - include_roles: [任意] class_ids のフィルタに含める role（既定 `["sample"]`）。
      統合トークン空間ではサンプル名経由で QC/blank を掴み得るため既定で落とす。
      QC も含めたいときだけ `["sample","qc"]` のように明示する。
```

さらに `- class_ids:` の説明文を差し替え:

```
    - class_ids: [任意] 因子トークンによるサンプル絞り込み。各要素は `_` 区切りの部分指定
      で、指定した全トークンを含むサンプルに一致する（要素内AND・順不同）。要素間はOR。
      トークンは **Class ID とサンプル名の両方**から解決されるため、Class ID に入って
      いない因子（時点・複製・測定日）でも絞れる。例: `["6h"]`, `["ILG_6h","control_6h"]`。
```

403-408行の `filter_arf_by_class_ids` 呼び出しを差し替え:

```python
        analysis_data, class_filter_stats = filter_arf_by_class_ids(
            analysis_data,
            session_state.session.arf_class_index,
            class_ids,
            missing_sample_policy=class_missing_sample_policy,
            include_roles=tuple(include_roles) if include_roles else ("sample",),
        )
```

446-448行の `assign_sample_groups` 呼び出しを差し替え:

```python
        sample_groups = assign_sample_groups(
            sample_names, session_state.session.arf_class_index, group_levels,
            group_factors=group_factors,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py tests/test_server_class_filter.py tests/test_preprocess_tools.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools_arf.py tool_helpers.py tests/test_sample_factors.py
git commit -m "feat(arf): PCAに因子軸の直積による色分けとrole開示を追加

arf_parser / arf_pca_preprocessed に group_factors を、arf_parser に
include_roles を追加。class_ids がサンプル名トークンでも効くようになった副作用で
QC を掴み得るため、既定で落としたうえで除外内訳を出力に明示する。"
```

---

### Task 8: `arf_list_classes` にサンプル名トークン語彙を追加

**Files:**
- Modify: `tools_arf.py:64-78`（`arf_list_classes`）
- Test: `tests/test_server_class_filter.py`（既存・**書き換えない**）、`tests/test_sample_factors.py`

**Interfaces:**
- Consumes: Task 1/4 の `arf_sample_names` / `build_sample_facets` / `token_vocabulary`
- Produces: `arf_list_classes()` の JSON に `sample_token_vocabulary` キーを追加（既存キー `mddata_path` / `class_counts` / `factors_by_position` は維持）

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` の末尾の `if __name__ == "__main__":` の直前に追記:

```python
class ArfListClassesVocabularyTests(unittest.TestCase):
    def setUp(self):
        self.session = _ParserFakeSession([
            "20220901_RAW_control_6h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220901_QC_RAW_NEG_1",
        ])
        self.session.current_file_path = "test.arf"
        self.patcher = patch.object(session_state, "session", self.session)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_reports_sample_token_vocabulary(self):
        payload = json.loads(server.arf_list_classes())
        tokens = payload["sample_token_vocabulary"]["tokens"]
        self.assertEqual(tokens["6h"]["samples"], 2)
        self.assertEqual(tokens["ilg"]["samples"], 1)

    def test_vocabulary_breaks_down_by_role(self):
        payload = json.loads(server.arf_list_classes())
        tokens = payload["sample_token_vocabulary"]["tokens"]
        self.assertEqual(tokens["raw"]["roles"], {"qc": 1, "sample": 2})

    def test_works_without_mddata(self):
        payload = json.loads(server.arf_list_classes())
        self.assertIsNone(payload["mddata_path"])
        self.assertEqual(payload["class_counts"], {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -k ArfListClasses -q`
Expected: FAIL — `json.decoder.JSONDecodeError`（`arf_class_index is None` なので日本語のエラー文字列が返る）

- [ ] **Step 3: Write minimal implementation**

`arf_list_classes`（64-78行）を丸ごと以下に置換:

```python
@mcp.tool()
def arf_list_classes() -> str:
    """絞り込み・群分けに使える因子トークンの一覧を返す（Class ID とサンプル名の両方）。

    class_ids / group_levels / group_factors / group_a / group_b に何を書けるかを
    発見するための入口。`.mddata` が無くてもサンプル名の語彙は返る。
    `sample_token_vocabulary.tokens[<token>]` は出現サンプル数・role 内訳・
    サンプル名内の出現位置を持つ。位置は因子の並びを推測する手掛かりだが、値が
    2トークンに割れる場合（G_uralensis）にずれるため指定には使わない。
    """
    if (
        session_state.session.features is None
        or not str(session_state.session.current_file_path or "").lower().endswith(".arf")
    ):
        return "先に arf_parser を実行してARFデータを読み込んでください。"
    class_index = session_state.session.arf_class_index or {}
    class_counts = class_index.get("class_counts", {})
    names = sample_factors.arf_sample_names(
        session_state.session.filtered_features or session_state.session.features
    )
    facets = sample_factors.build_sample_facets(
        names, session_state.session.arf_class_index)
    return json.dumps({
        "mddata_path": class_index.get("mddata_path"),
        "class_counts": class_counts,
        "factors_by_position": _class_factors_by_position(class_counts.keys()),
        "sample_token_vocabulary": sample_factors.token_vocabulary(facets),
    }, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py tests/test_server_class_filter.py -q`
Expected: PASS（既存 `test_arf_list_classes_returns_counts` / `test_arf_list_classes_returns_factor_vocabulary` も**無改修で**通ること）

- [ ] **Step 5: Commit**

```bash
git add tools_arf.py tests/test_sample_factors.py
git commit -m "feat(arf): arf_list_classes にサンプル名トークン語彙を追加

何で絞れるかを見に行く既存ツールに語彙を載せ、新ツールを知らなくても
時点・複製のような Class ID に無い因子を発見できるようにする。
.mddata が無い場合もサンプル名の語彙だけ返す。"
```

---

### Task 9: `arf_exclude` を因子トークン spec 対応に

**Files:**
- Modify: `tools_arf.py:101-180`（`arf_exclude`）
- Test: `tests/test_arf_exclude.py`（既存・**書き換えない**）、`tests/test_sample_factors.py`

**Interfaces:**
- Consumes: Task 1-2 の `build_sample_facets` / `expand_sample_specs`
- Produces: `arf_exclude` の payload に `resolved_samples: dict[str, list[str]]`（spec→実サンプル名）を追加

- [ ] **Step 1: Write the failing test**

`tests/test_sample_factors.py` の末尾の `if __name__ == "__main__":` の直前に追記:

```python
def _exclude_row(file_id, name, height):
    """exclusions.roster が file_name を拾える現実的な生 row（len>25・data[18] 数値）。"""
    row = [0] * 26
    row[0] = file_id
    row[1] = name
    row[2] = file_id
    row[18] = float(height)
    return row


class ArfExcludeSpecTests(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        names = [
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
            "20220902_RAW_ILG_0h_1_NEG",
            "20220901_RAW_control_6h_1_NEG",
        ]
        session_state.session.filtered_features = [{
            "MasterAlignmentID": 1,
            "AlignedPeakProperties": [_exclude_row(i, n, 10 + i) for i, n in enumerate(names)],
        }]

    def test_spec_excludes_all_matching_samples(self):
        out = json.loads(server.arf_exclude(exclude_samples=["ILG_6h"]))
        self.assertEqual(out["status"], "success")
        self.assertEqual(sorted(out["excluded_samples"]), [
            "20220902_RAW_ILG_6h_1_NEG", "20220902_RAW_ILG_6h_2_NEG",
        ])
        self.assertEqual(out["samples_after"], 2)

    def test_payload_discloses_spec_resolution(self):
        out = json.loads(server.arf_exclude(exclude_samples=["ILG_6h"]))
        self.assertEqual(out["resolved_samples"]["ILG_6h"], [
            "20220902_RAW_ILG_6h_1_NEG", "20220902_RAW_ILG_6h_2_NEG",
        ])

    def test_remove_mode_accepts_specs(self):
        server.arf_exclude(exclude_samples=["ILG_6h"])
        out = json.loads(server.arf_exclude(exclude_samples=["ILG_6h"], mode="remove"))
        self.assertEqual(out["excluded_samples"], [])
        self.assertEqual(session_state.session.excluded_samples, set())

    def test_exact_name_still_wins(self):
        out = json.loads(server.arf_exclude(exclude_samples=["20220902_RAW_ILG_6h_1_NEG"]))
        self.assertEqual(out["excluded_samples"], ["20220902_RAW_ILG_6h_1_NEG"])

    def test_unmatched_spec_is_reported_not_raised(self):
        out = json.loads(server.arf_exclude(exclude_samples=["24h"]))
        self.assertEqual(out["status"], "success")
        self.assertIn("24h", out["unmatched_samples"])
        self.assertEqual(session_state.session.excluded_samples, set())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py -k ArfExcludeSpec -q`
Expected: FAIL — `test_spec_excludes_all_matching_samples` で `excluded_samples == []`（"ILG_6h" は完全一致しないので現状は未一致扱い）

- [ ] **Step 3: Write minimal implementation**

`tools_arf.py` の `arf_exclude` docstring の `- exclude_samples:` の行（114行）を差し替え:

```
    - exclude_samples: 除外するサンプル。完全なサンプル名に加え、`_` 区切りの因子
      トークン指定（`"ILG_6h"` = ILG かつ 6h の全サンプル）を受ける。トークンは
      Class ID とサンプル名の両方から解決される。解決結果は payload の
      resolved_samples に必ず出る。一致ゼロの指定は unmatched_samples 行きで、
      除外指定の取りこぼしだけで解析を止めない。
```

138-148行の `elif mode in ("add", "remove"):` ブロックを以下に置換:

```python
    elif mode in ("add", "remove"):
        matched_samples, resolved_samples, unmatched_samples = _resolve_exclude_specs(
            req_samples, avail_samples)
        matched_spots = [i for i in req_spots if i in avail_ids]
        unmatched_spots = [i for i in req_spots if i not in avail_ids]
        if mode == "add":
            es.update(matched_samples)
            esp.update(matched_spots)
        else:  # remove
            es.difference_update(matched_samples)
            esp.difference_update(req_spots)
```

（続く `if unmatched_samples:` 以降のブロックはそのまま。）

`arf_exclude` の直前（101行の `@mcp.tool()` の直前）にヘルパを追加:

```python
def _resolve_exclude_specs(req_samples, avail_samples):
    """除外指定を実サンプル名へ解決する（完全一致優先、次に因子トークン spec）。

    完全一致を先に見るのは、サンプル名が他のサンプル名のトークン部分集合に
    なっている場合でも「その1件だけ」という従来の意図を保つため。
    一致ゼロの指定は例外にせず unmatched へ回す（既存方針: 除外指定の取りこぼし
    だけで解析全体を止めない）。
    """
    if not req_samples:
        return [], {}, []
    facets = sample_factors.build_sample_facets(
        sorted(avail_samples), session_state.session.arf_class_index)
    matched: list[str] = []
    resolved: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for spec in req_samples:
        if spec in avail_samples:
            resolved[spec] = [spec]
            matched.append(spec)
            continue
        try:
            hits, _ = sample_factors.expand_sample_specs(
                [spec], facets, include_roles=None)
        except ValueError:
            unmatched.append(spec)
            continue
        resolved[spec] = hits[spec]
        matched.extend(hits[spec])
    return matched, resolved, unmatched
```

`arf_exclude` の冒頭（127-131行）の初期化を差し替え（`resolved_samples` を追加）:

```python
    req_samples = list(exclude_samples or [])
    req_spots = list(exclude_spots or [])
    resolved_samples: dict[str, list[str]] = {}
    unmatched_samples: list[str] = []
    unmatched_spots: list[int] = []
    caveats: list[str] = []
```

payload（164-176行）に `resolved_samples` を追加:

```python
    payload = {
        "status": "success",
        "mode": mode,
        "excluded_samples": sorted(es),
        "excluded_spots": sorted(esp),
        "resolved_samples": resolved_samples,
        "samples_before": len(avail_samples),
        "samples_after": len(pruned_names),
        "spots_before": len(avail_ids),
        "spots_after": len(pruned_ids),
        "unmatched_samples": unmatched_samples,
        "unmatched_spots": unmatched_spots,
        "caveats": caveats,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_sample_factors.py tests/test_arf_exclude.py tests/test_exclude_wiring.py -q`
Expected: PASS（既存 `tests/test_arf_exclude.py` の 8 tests も**無改修で**通ること）

- [ ] **Step 5: Commit**

```bash
git add tools_arf.py tests/test_sample_factors.py
git commit -m "feat(arf): arf_exclude を因子トークン指定に対応

exclude_samples=['ILG_6h'] で該当サンプルをまとめて除外できるようにする。
完全一致を優先し、解決結果を resolved_samples に必ず出す。一致ゼロは
従来どおり unmatched_samples 行きで解析を止めない。"
```

---

### Task 10: `sample_search` ツールと server 配線

**Files:**
- Create: `tools_samples.py`
- Modify: `server.py:70`（`from tools_arf import *` の直後に1行追加）
- Modify: `tests/test_server_registration.py`（`EXPECTED_TOOLS` に1行追加。Global Constraints の唯一の例外）
- Test: `tests/test_tools_samples.py`

**Interfaces:**
- Consumes: Task 1/2/4 の `arf_sample_names` / `build_sample_facets` / `expand_sample_specs` / `token_vocabulary`、`msdial_classes.discover_arf_class_index`、`path_resolvers._select_latest_batch` / `_pick_latest`、`msdial_tags.normalize_sample_name`、`mcp_core.DATA_DIR`
- Produces: MCP ツール `sample_search(specs=None, directory=None, extensions=None, include_roles=None) -> str`（JSON 文字列）

- [ ] **Step 1: Write the failing test**

`tests/test_tools_samples.py` を新規作成:

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mcp_core
import server
import session_state


SAMPLES = [
    "20220901_RAW_control_6h_1_NEG",
    "20220902_RAW_ILG_6h_1_NEG",
    "20220902_RAW_ILG_6h_2_NEG",
    "20220901_QC_RAW_NEG_1",
]


def make_dir(tmp: str, timestamps=("202605151012",)) -> Path:
    """サンプルごとの .pai2 / .dcl を、指定バッチ分だけ作る。"""
    directory = Path(tmp)
    for stamp in timestamps:
        for name in SAMPLES:
            for suffix in (".pai2", ".dcl"):
                (directory / f"{name}_{stamp}{suffix}").touch()
    return directory


class SampleSearchFromDirectoryTests(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()

    def test_vocabulary_mode_lists_all_samples_and_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(server.sample_search(directory=str(make_dir(tmp))))
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["total_samples"], 4)
        self.assertEqual(payload["token_vocabulary"]["tokens"]["6h"]["samples"], 3)

    def test_spec_mode_returns_matching_samples_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(
                server.sample_search(specs=["ILG_6h"], directory=str(make_dir(tmp))))
        self.assertEqual(payload["matched"], 2)
        self.assertEqual(
            [s["name"] for s in payload["samples"]],
            ["20220902_RAW_ILG_6h_1_NEG", "20220902_RAW_ILG_6h_2_NEG"],
        )
        self.assertNotIn("token_vocabulary", payload)

    def test_returns_real_file_paths_per_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = make_dir(tmp)
            payload = json.loads(
                server.sample_search(specs=["ILG_6h_1"], directory=str(directory)))
        files = payload["samples"][0]["files"]
        self.assertTrue(files[".pai2"].endswith("20220902_RAW_ILG_6h_1_NEG_202605151012.pai2"))
        self.assertTrue(Path(files[".dcl"]).is_file())

    def test_selects_latest_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = make_dir(tmp, timestamps=("202601010000", "202605151012"))
            payload = json.loads(
                server.sample_search(specs=["ILG_6h_1"], directory=str(directory)))
        self.assertIn("202605151012", payload["samples"][0]["files"][".pai2"])
        self.assertNotIn("202601010000", payload["samples"][0]["files"][".pai2"])

    def test_search_returns_all_roles_with_role_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(
                server.sample_search(specs=["raw"], directory=str(make_dir(tmp))))
        roles = {s["name"]: s["role"] for s in payload["samples"]}
        self.assertEqual(roles["20220901_QC_RAW_NEG_1"], "qc")
        self.assertEqual(payload["matched"], 4)

    def test_include_roles_narrows_the_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(server.sample_search(
                specs=["raw"], directory=str(make_dir(tmp)), include_roles=["sample"]))
        self.assertEqual(payload["matched"], 3)
        self.assertEqual(payload["excluded_by_role"], ["20220901_QC_RAW_NEG_1"])

    def test_custom_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(server.sample_search(
                specs=["ILG_6h_1"], directory=str(make_dir(tmp)), extensions=[".dcl"]))
        self.assertEqual(list(payload["samples"][0]["files"]), [".dcl"])

    def test_zero_match_returns_error_with_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(
                server.sample_search(specs=["24h"], directory=str(make_dir(tmp))))
        self.assertEqual(payload["status"], "error")
        self.assertIn("24h", payload["message"])
        self.assertIn("token_vocabulary", payload)

    def test_missing_directory_is_an_error(self):
        payload = json.loads(server.sample_search(directory="C:/no/such/dir"))
        self.assertEqual(payload["status"], "error")


class SampleSearchFromLoadedArfTests(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        session_state.session.current_file_path = "loaded.arf"
        session_state.session.features = [{
            "MasterAlignmentID": 1,
            "AlignedPeakProperties": [[i, n, 1.0] for i, n in enumerate(SAMPLES)],
        }]

    def test_uses_loaded_arf_sample_names(self):
        payload = json.loads(server.sample_search(specs=["ILG_6h"]))
        self.assertEqual(payload["source"], "loaded_arf")
        self.assertEqual(payload["matched"], 2)

    def test_file_id_comes_from_class_index_when_absent(self):
        payload = json.loads(server.sample_search(specs=["ILG_6h"]))
        self.assertIsNone(payload["samples"][0]["file_id"])


if __name__ == "__main__":
    unittest.main()
```

MCP への登録は `tests/test_server_registration.py` の `EXPECTED_TOOLS` スナップショットが担うので、ここでは重複して検証しない。

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_tools_samples.py -q`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'sample_search'`

- [ ] **Step 3: Write minimal implementation**

(a) `tools_samples.py` を新規作成:

```python
"""サンプル検索: 因子トークンからサンプル・FileID・実ファイルパスを引く。

ARF に限らず pai2 / dcl / EIC が使える形で条件検索するための入口。ARF のロード前
（＝どのファイルを開くか決める前）でも呼べるよう、ロード済み ARF が無ければ
ディレクトリの .mddata、それも無ければ実ファイル名からサンプル集合を組み立てる。

deps: mcp_core / session_state / path_resolvers / sample_factors / msdial_classes /
msdial_tags。tools_* / server は import しない（循環回避）。
"""
import json
from pathlib import Path

import mcp_core
import path_resolvers
import sample_factors
import session_state
from mcp_core import mcp
from msdial_classes import discover_arf_class_index
from msdial_tags import normalize_sample_name

__all__ = ["sample_search"]

# 既定は「1測定ファイルにつき1個」の per-sample ファイルだけ。.arf / .arf2 /
# .EIC.aef はアラインメント単位でサンプルに1対1対応しないため既定に含めない。
DEFAULT_EXTENSIONS = (".pai2", ".dcl")


@mcp.tool()
def sample_search(
    specs: list[str] | None = None,
    directory: str | None = None,
    extensions: list[str] | None = None,
    include_roles: list[str] | None = None,
) -> str:
    """因子トークンでサンプルを検索し、FileID と実ファイルパスを返す。

    MS-DIAL の Class ID には実験デザインの全因子が入らない（時点・複製・測定日は
    サンプル名にしか無いことがある）。本ツールは Class ID とサンプル名を統合した
    トークン空間で検索し、pai2 / dcl / EIC が直接使える形で結果を返す。

    - specs: `_` 区切りの因子トークン指定のリスト（要素内 AND・順不同、要素間 OR）。
      例 `["ILG_6h"]`, `["ILG_6h","control_6h"]`。**省略すると全サンプルと
      `token_vocabulary`（何で絞れるかの語彙一覧）を返す**。
    - directory: 探索先。省略時は既定のデータディレクトリ。ロード済み ARF があれば
      そのサンプル名を優先して使う。
    - extensions: 実パスを引く拡張子（既定 `[".pai2", ".dcl"]`）。複数バッチが
      混在していても最新バッチを自動選択する。
    - include_roles: 結果に含める role。**省略時は全 role を返す**（`role` フィールドで
      判断できるため検索側では絞らない）。`["sample"]` で QC/blank を落とせる。

    返り値の各サンプルは `name` / `file_id` / `class_id` / `role` / `tokens` /
    `files{拡張子: 実パス}`。`file_id` は `eic_plot_chromatograms(file_ids=[...])`、
    `files[".pai2"]` は `pai2_parser(file_path=...)` にそのまま渡せる。
    """
    try:
        facets, source, target_dir = _collect_facets(directory)
    except (FileNotFoundError, NotADirectoryError) as exc:
        return _error(str(exc))
    if not facets:
        return _error(
            f"サンプルを特定できませんでした（探索先: {target_dir}）。"
            "MS-DIAL の出力フォルダ（.mddata か .pai2 を含む）を directory に指定するか、"
            "先に arf_parser で ARF を読み込んでください。")

    exts = [str(e) for e in extensions] if extensions else list(DEFAULT_EXTENSIONS)
    roles = tuple(include_roles) if include_roles else None
    vocabulary = sample_factors.token_vocabulary(facets)

    cleaned = [str(s) for s in specs or [] if str(s).strip()]
    if not cleaned:
        return json.dumps({
            "status": "success",
            "source": source,
            "directory": str(target_dir) if target_dir else None,
            "specs": [],
            "total_samples": len(facets),
            "matched": len(facets),
            "excluded_by_role": [],
            "samples": [_describe(f, target_dir, exts) for f in facets.values()],
            "token_vocabulary": vocabulary,
        }, ensure_ascii=False, indent=2)

    try:
        matches, excluded = sample_factors.expand_sample_specs(
            cleaned, facets, include_roles=roles)
    except ValueError as exc:
        return json.dumps({
            "status": "error",
            "message": str(exc),
            "token_vocabulary": vocabulary,
        }, ensure_ascii=False, indent=2)

    selected = {n for names in matches.values() for n in names}
    ordered = [f for f in facets.values() if f.name in selected]
    return json.dumps({
        "status": "success",
        "source": source,
        "directory": str(target_dir) if target_dir else None,
        "specs": cleaned,
        "resolved": matches,
        "total_samples": len(facets),
        "matched": len(ordered),
        "excluded_by_role": sorted({n for names in excluded.values() for n in names}),
        "samples": [_describe(f, target_dir, exts) for f in ordered],
    }, ensure_ascii=False, indent=2)


def _error(message: str) -> str:
    return json.dumps({"status": "error", "message": message}, ensure_ascii=False, indent=2)


def _collect_facets(directory):
    """(facets, source, target_dir) を返す。source は facets の出所の説明。

    ロード済み ARF > ディレクトリの .mddata > ディレクトリの per-sample ファイル名、
    の順に試す。ARF ロード前でも呼べることが本ツールの要件。
    """
    session = session_state.session
    if directory is None and _has_loaded_arf(session):
        names = sample_factors.arf_sample_names(
            session.filtered_features or session.features)
        if names:
            facets = sample_factors.build_sample_facets(names, session.arf_class_index)
            return facets, "loaded_arf", _loaded_arf_dir(session)

    target_dir = Path(directory).expanduser() if directory else mcp_core.DATA_DIR
    if not target_dir.exists():
        raise FileNotFoundError(f"データディレクトリが存在しません: {target_dir}")
    if not target_dir.is_dir():
        raise NotADirectoryError(f"指定されたパスはディレクトリではありません: {target_dir}")

    class_index = discover_arf_class_index(target_dir)
    if class_index and class_index.get("records"):
        names = [str(r["file_name"]) for r in class_index["records"] if r.get("file_name")]
        return (sample_factors.build_sample_facets(names, class_index),
                "mddata", target_dir)

    names = _names_from_files(target_dir)
    return sample_factors.build_sample_facets(names, None), "filenames", target_dir


def _has_loaded_arf(session) -> bool:
    return (
        session.features is not None
        and str(session.current_file_path or "").lower().endswith(".arf")
    )


def _loaded_arf_dir(session):
    path = session.current_file_path
    parent = Path(str(path)).parent
    return parent if parent.is_dir() else None


def _names_from_files(target_dir: Path) -> list[str]:
    """per-sample ファイル名から、処理タイムスタンプを除いたサンプル名を復元する。

    .mddata が無いフォルダ向けのフォールバック。表示名は「最新バッチのファイル名から
    タイムスタンプを取り除いた形」にそろえる（MS-DIAL の AnalysisFileName と同じ形）。
    """
    candidates = [str(p) for p in sorted(target_dir.iterdir())
                  if p.is_file() and p.suffix.casefold() in {".pai2", ".dcl"}]
    names: list[str] = []
    seen: set[str] = set()
    for path in path_resolvers._select_latest_batch(candidates):
        stem = Path(path).stem
        key = normalize_sample_name(stem, strip_processing_timestamp=True)
        if not key or key in seen:
            continue
        seen.add(key)
        # 元の大文字小文字を保つため、正規化キーではなくタイムスタンプ除去後の実表記を使う。
        names.append(stem[:-13] if len(stem) > 13 and stem[-13] == "_" and stem[-12:].isdigit() else stem)
    return names


def _describe(facet, target_dir, extensions) -> dict:
    return {
        "name": facet.name,
        "file_id": facet.file_id,
        "class_id": facet.class_id,
        "role": facet.role,
        "tokens": sorted(facet.tokens),
        "files": _resolve_files(facet.name, target_dir, extensions),
    }


def _resolve_files(sample_name, target_dir, extensions) -> dict:
    """サンプル名に対応する実ファイルを拡張子ごとに1つ解決する（最新バッチ優先）。"""
    if target_dir is None or not Path(target_dir).is_dir():
        return {}
    key = normalize_sample_name(sample_name, strip_processing_timestamp=True)
    resolved: dict[str, str] = {}
    for ext in extensions:
        candidates = [
            str(p) for p in sorted(Path(target_dir).iterdir())
            if p.is_file() and str(p).casefold().endswith(ext.casefold())
            and normalize_sample_name(p.name, strip_processing_timestamp=True) == key
        ]
        if not candidates:
            continue
        latest = path_resolvers._pick_latest(path_resolvers._select_latest_batch(candidates))
        if latest:
            resolved[ext] = latest
    return resolved
```

(b) `server.py:70` の `from tools_arf import *` の直後に1行追加:

```python
from tools_samples import *  # sample_search
```

(c) `tests/test_server_registration.py` の `EXPECTED_TOOLS` リストに1行追加。リストは `sorted()` でくるまれているので位置は結果に影響しない。読みやすさのため `"record_objective",` の直後に置く:

```python
    "sample_search",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/test_tools_samples.py tests/test_server_registration.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools_samples.py server.py tests/test_tools_samples.py tests/test_server_registration.py
git commit -m "feat(samples): 因子トークンによるサンプル検索ツールを追加

sample_search が FileID と .pai2/.dcl の実パスを返し、pai2/dcl/EIC 側でも
条件指定でサンプルを選べるようにする。ARF ロード前でも .mddata / ファイル名
から成立し、複数バッチ混在時は最新バッチを選ぶ。specs 省略時は語彙一覧を返す。"
```

---

### Task 11: 全体回帰とドキュメント更新

**Files:**
- Modify: `README.md`（ツール一覧の節）、`USAGE.md`（ARF 解析の節）
- Test: 全テスト

**Interfaces:**
- Consumes: Task 1-10 のすべて
- Produces: なし（回帰確認とドキュメントのみ）

- [ ] **Step 1: 全テストを実行してベースラインと突き合わせる**

Run: `./.venv-1/Scripts/python.exe -m pytest tests/ -q`
Expected: `tests/test_ingest_tools.py::KnowledgeCoverageSmokeTests::test_coverage_on_sample_objective` の**1件だけ**が失敗（既存の失敗）。それ以外はすべて PASS。他に失敗があれば、そのタスクへ戻って直してからここへ戻る。

- [ ] **Step 2: 実データで手動スモークを1回だけ流す**

Run:

```bash
./.venv-1/Scripts/python.exe -c "
import json, mcp_core, server
from pathlib import Path
mcp_core.DATA_DIR = Path(r'C:\Users\yuu18\datasets\2_lipidome_lcms\NEG')
out = json.loads(server.sample_search(specs=['ILG_6h']))
print(out['matched'], [s['file_id'] for s in out['samples']])
print(Path(out['samples'][0]['files']['.pai2']).name)
"
```

Expected: `3 [57, 58, 59]` と `20220902_RAW_ILG_6h_1_NEG_202605151012.pai2`（FileID は mddata の並び順に依存するので3件・連番であればよい）

- [ ] **Step 3: README.md のツール一覧に `sample_search` を追記**

`README.md:166` の `arf_list_classes()` の箇条書き（`- \`arf_list_classes()\` - List Class ID values, ...` で始まる行）を差し替え、直後に1行追加する。README のこの節は英語なので英語で書く:

```markdown
- `arf_list_classes()` - List Class ID values, sample counts, the per-position factor-value vocabulary (`factors_by_position`), and `sample_token_vocabulary` — the factor tokens discovered from **sample names as well as Class IDs**, with per-token sample counts and role breakdown. Use it to find what you can pass to `class_ids` / `group_levels` / `group_factors` / `group_a` / `group_b`.
- `sample_search(specs=None, directory=None, extensions=None, include_roles=None)` - Search samples by factor token (e.g. `["ILG_6h"]`) and return each match's `file_id` plus the real `.pai2` / `.dcl` paths, so pai2 / dcl / EIC tools can be pointed at a condition rather than a hand-picked filename. Omit `specs` to get the full token vocabulary. Works before any ARF is loaded.
```

- [ ] **Step 4: USAGE.md に `sample_search` の行と、時点を揃えた比較の例を追記**

(a) `USAGE.md:1` の見出しのツール数を `全36ツール` → `全39ツール` に更新する。現在の登録数は 38（`tests/test_server_registration.py` の `EXPECTED_TOOLS`）で、見出しが2件ぶん古いまま残っているため、`sample_search` の追加分と合わせてここで実数に合わせる。

(b) `USAGE.md` の `## 1. データ探索・ロード(入口)` の表（15行目以降）の末尾に1行追加:

````markdown
| `sample_search` | 因子トークン(`ILG_6h` 等)でサンプルを検索し、`file_id` と `.pai2`/`.dcl` の実パスを返す。specs 省略で語彙一覧。ARF ロード前でも動く。 |
````

(c) `USAGE.md` の末尾（`| \`list_reports\` \| ...` の行の後）に新しい節を追加。**外側のフェンスは4連バッククォート**にすること（中に3連の python フェンスを含むため）:

````markdown
## 10. Class ID に無い因子で絞る・比べる

MS-DIAL の Class ID は入力された1文字列にすぎず、時点や複製がサンプル名にしか
無いことがある(例: Class ID = `ILG`/`control` だけで、時点 `6h` はサンプル名のみ)。
`class_ids` / `group_a` / `group_b` / `group_levels` / `group_factors` のトークンは
Class ID とサンプル名の**両方**から解決されるため、そのまま書ける。

```python
sample_search()                                    # 何で絞れるかの語彙一覧
arf_parser(class_ids=["ILG_6h", "control_6h"])     # 6h だけで PCA
arf_parser(group_factors=[["control", "LPS", "ILG", "G_uralensis"],
                          ["0h", "15min", "1h", "6h", "24h"]])  # 処置x時点の20群で色分け
arf_preprocess(normalize="median")
arf_differential(group_a="ILG_6h", group_b="control_6h")        # 時点を揃えた2群比較
```

QC/blank はフィルタでは既定で除外され(`include_roles=["sample","qc"]` で戻せる)、
PCA の色分けでは `"qc"`/`"blank"` ラベルとして残る(前処理品質の判断材料になるため)。
````

- [ ] **Step 5: Commit**

```bash
git add README.md USAGE.md
git commit -m "docs: 因子トークンによる選択・群分けの使い方を追記

sample_search をツール一覧へ、Class ID に無い因子での絞り込みと時点を
揃えた2群比較の例を USAGE へ追加する。"
```

---

## Self-Review

**1. Spec coverage**

| 仕様の節 | 実装タスク |
|---|---|
| §1 `sample_factors.py`（SampleFacet / トークン化） | Task 1 |
| §1 `build_sample_facets`（class_index=None 対応） | Task 1 |
| §1 `expand_sample_specs`（spec 単位の ValueError・role 除外内訳） | Task 2 |
| §1 `assign_factor_groups`（直積・排他違反・other・1軸互換） | Task 3 |
| §1 `token_vocabulary` | Task 4 |
| §1 QC/blank: フィルタは除外＋開示 | Task 2（除外）/ Task 7（開示） |
| §1 QC/blank: 群分けは role ラベルで残す | Task 3 |
| §2 `filter_arf_by_class_ids` の載せ替え | Task 5 |
| §2 `assign_sample_groups` の `group_factors` | Task 5 |
| §2 `expand_class_specs` 据え置き | Task 5（明示的に「変更しない」と記載） |
| §2 `_pool_group_labels` の載せ替え | Task 6 |
| §3 `arf_parser` / `arf_pca_preprocessed` | Task 7 |
| §3 `arf_differential`（解決サンプル全列挙） | Task 6 |
| §3 `arf_list_classes` | Task 8 |
| §3 `arf_exclude` | Task 9 |
| §4 `sample_search` / `server.py` 配線 | Task 10 |
| §5 エラーハンドリング（一致ゼロ・排他違反・role で残0） | Task 2 / Task 3 / Task 2 の `_no_match_message` |
| テスト: 新規2ファイル | Task 1-9（`test_sample_factors.py`）/ Task 10（`test_tools_samples.py`） |
| テスト: 既存の無改修通過 | Task 5 / 6 / 7 / 8 / 9 の Step 4、Task 11 の Step 1 |

**2. Placeholder scan** — 全ステップに実コードを記載済み。TBD / TODO / 「適宜」の類は無し。

**3. Type consistency** — 以下を通しで確認済み:
- `SampleFacet` のフィールド名 `name` / `file_id` / `class_id` / `role` / `tokens` は Task 1・3・4・9・10 で同一。
- `expand_sample_specs` は全呼び出し箇所（Task 5・6・9・10）で `(matches, excluded)` の2値として受けている。
- `_pool_group_labels` の3値化（Task 6）は唯一の呼び出し元 `arf_differential` を同タスク内で合わせている。
- `filter_arf_by_class_ids` の stats 追加キー `matched_samples` / `excluded_by_role` は、Task 7 の `_format_arf_class_filter` が `excluded_by_role` を `.get()` で読むため、キーが無い早期 return パス（specs 空）でも壊れない。
- `assign_sample_groups` の引数順 `(sample_names, class_index, group_levels=None, group_factors=None)` は既存の位置引数呼び出し（`session_state._build_sample_meta` の `assign_sample_groups(sample_names, class_index, None)`）と互換。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-04-sample-name-factor-selection.md`.
