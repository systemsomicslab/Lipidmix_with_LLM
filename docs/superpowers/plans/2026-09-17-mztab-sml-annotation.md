# mzTab-M の SML 注釈を同定ラベルとして扱う 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mzTab-M の SML セクションに載る同定を「ラベル」として回収し、証拠
（`required_evidence` を満たす材料）としては一切使わないようにする。

**Architecture:** SML 由来の同定を新設の `ds.feature_annotations` に隔離する。
`feature_metadata` / `feature_candidates`（SME 由来＝証拠）は一切変えない。結果
`lipidmix/analysis/feature_bindings.py` は 1 行も変更せずに済み、証拠水準が動いて
いないことをファイル差分の不在で示せる。consumer 側（特徴表・`dataset_load` 要約・
差次的エクスポート）が明示的にマージする。

**Tech Stack:** Python 3.14（`C:/Python314/python.exe`）、pytest、numpy、RDKit（任意）。

**Spec:** [docs/superpowers/specs/2026-09-17-mztab-sml-annotation-design.md](../specs/2026-09-17-mztab-sml-annotation-design.md)

## Global Constraints

- **`lipidmix/analysis/feature_bindings.py` を変更してはならない。** 完了時の
  `git diff` にこのファイルが現れたら失敗（spec §3.2・§5）。
- `feature_metadata` / `feature_candidates` の**中身**を変えてはならない。
- `lipidmix/analysis/export_contract.py` の `EXPORT_COLUMNS` と `CONTRACT_VERSION`
  を変更してはならない（別リポ massbank-context との契約。spec §3.2）。
- `msi_level` に値を入れてはならない（spec §8）。
- テストは `C:/Python314/python.exe -m pytest tests -q` をリポジトリルートから実行する。
- テスト fixture は**テスト自身が作る**。`data/` の実ファイルに依存させない
  （追跡外なのでユーザ環境依存になる）。
- mzTab fixture は**実形式**で書く: 小分子ヘッダ行は `SMH`（`SML` 接頭辞のヘッダ行に
  頼らない）。SME セクションは 0 行にできる。
- 全ツールの戻り値は `json_payload()` で返す。`json.dumps(..., indent=2)` を書かない。
- コミットは日本語の要約 + 本文。末尾に
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。
- 作業ブランチは `feature/mztab-sml-annotation`。main へは最後に `--no-ff` で
  マージする（マージと push は人間の指示を待つ）。

---

## File Structure

| ファイル | 役割 | 変更 |
|---|---|---|
| `lipidmix/mztab/dataset_state.py` | `feature_annotations` の構築、coverage 集計 | 変更 |
| `lipidmix/analysis/feature_export.py` | `.annotations.tsv` に `ms1_annotation` を追加 | 変更 |
| `lipidmix/tools/mztab_tools.py` | `dataset_load` 要約に出所内訳を出す | 変更 |
| `lipidmix/analysis/dataset_export.py` | 差次的エクスポートで SML をマージ | 変更 |
| `lipidmix/analysis/feature_bindings.py` | — | **変更禁止** |
| `tests/test_dataset_state.py` | Task 1 の試験 | 変更 |
| `tests/test_feature_bindings.py` | Task 2 の不変条件 | 変更 |
| `tests/test_metabolomics_report.py` | Task 3 の試験 | 変更 |
| `tests/test_mztab_tools.py` | Task 4 の試験 | 変更 |
| `tests/test_result_output.py` | Task 5 の試験 | 変更 |
| `docs/output_format/identity.md` ほか | Task 6 | 変更 |

---

### Task 1: `feature_annotations` の構築

**Files:**
- Modify: `lipidmix/mztab/dataset_state.py`
- Test: `tests/test_dataset_state.py`

**Interfaces:**
- Consumes: `parse_mztab()` の `sections["SML"]["rows"]`、`ds.feature_ids`。
- Produces:
  - `DatasetState.feature_annotations: dict[str, dict]` — `feature_id`（= `SMF_ID`）→
    注釈。曖昧でない値は
    `{"sml_id": str, "ambiguous": False, "name": str|None,
      "database_identifier": str|None, "chemical_formula": str|None,
      "smiles": str|None, "adduct": str|None, "reliability": str|None,
      "confidence_measure": str|None, "confidence_value": float|None,
      "inchikey": str|None, "inchikey_source": str}`。
    曖昧な値は `{"ambiguous": True, "sml_ids": list[str], "name": None}`。
  - `_build_feature_annotations(sml_rows, known_feature_ids) -> tuple[dict, list[str]]`
  - `ds.inchikey_coverage["identified_by"] = {"sme": int, "sml_only": int, "none": int}`

- [ ] **Step 1: 失敗テストを書く**

`tests/test_dataset_state.py` の末尾に追記する。

```python
# ---------- SML 由来の注釈（spec 2026-09-17-mztab-sml-annotation-design）----------

# Text DB 運用の実形状: SME セクションが 0 行で、同定は SML にしか出ない。
# MS-DIAL の `ShouldWriteSmeLine` が `IsTextDbBasedRepresentative` を除外するため
# （MztabFormatExport.cs）。ヘッダ行は実形式の `SMH` で書く。
_SML_ONLY_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tchemical_formula\tsmiles\tinchi\tchemical_name\tadduct_ions\treliability\tbest_id_confidence_measure\tbest_id_confidence_value
    SML\t1\t1\tTextDB:GABA\tnull\tnull\tnull\tGABA\t[M+H]1+\tannotated by user-defined text library\t[,, MS-DIAL algorithm matching score, ]\t0.999982
    SML\t2\t2\tnull\tnull\tnull\tnull\tnull\t[M+H]1+\tnull\tnull\tnull
    SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]
    SMF\t1\tnull\t104.07066\t72.0\t5000.0
    SMF\t2\tnull\t880.8\t360.0\t100.0
""")


def _build(tmp_path, content, name="Height_sml.mzTab"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return build_dataset_state(parse_mztab(p), p.name, p)


def test_an_sml_annotation_is_recorded_for_its_feature(tmp_path):
    ds = _build(tmp_path, _SML_ONLY_MZTAB)

    annotation = ds.feature_annotations["1"]
    assert annotation["name"] == "GABA"
    assert annotation["database_identifier"] == "TextDB:GABA"
    assert annotation["adduct"] == "[M+H]1+"
    assert annotation["confidence_value"] == pytest.approx(0.999982)
    assert annotation["ambiguous"] is False


def test_an_sml_row_without_identity_is_not_recorded(tmp_path):
    ds = _build(tmp_path, _SML_ONLY_MZTAB)

    # SML 2 は chemical_name も database_identifier も null。
    assert "2" not in ds.feature_annotations


def test_the_annotation_never_holds_an_inchi_key(tmp_path):
    """MS-DIAL は SML の `inchi` を常に null で書く（MztabFormatExport.cs:393）。

    キーを置くと「取得していない」と「無い」の区別を偽るので、持たない。
    """
    ds = _build(tmp_path, _SML_ONLY_MZTAB)

    assert "inchi" not in ds.feature_annotations["1"]


def test_the_evidence_slots_stay_untouched_by_an_sml_annotation(tmp_path):
    """SML 注釈は証拠スロットへ一切入らない（spec §5 の不変条件）。"""
    ds = _build(tmp_path, _SML_ONLY_MZTAB)

    assert ds.feature_metadata["1"]["name"] is None
    assert ds.feature_metadata["1"]["inchikey"] is None
    assert ds.feature_candidates["1"] == []
```

- [ ] **Step 2: 失敗を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q -k "sml or annotation"`
Expected: FAIL — `AttributeError: 'DatasetState' object has no attribute 'feature_annotations'`

- [ ] **Step 3: `DatasetState.__init__` にスロットを足す**

`lipidmix/mztab/dataset_state.py` の `self.feature_candidates: dict = {}`（61 行目付近）の
直後に挿入する。

```python
        # feature_annotations: smf_id -> SML 由来のラベル。
        # **証拠ではない。** MS-DIAL は Text DB 由来の同定を SME に書かない
        # （MztabFormatExport.cs `ShouldWriteSmeLine` が
        # `IsTextDbBasedRepresentative` を除外する）ので、Text DB 運用では
        # SML が唯一の同定情報源になる。
        # feature_metadata / feature_candidates とは**別スロット**に隔離する——
        # feature_bindings._check_identity は候補ゼロのとき
        # feature_metadata["inchikey"] を見て matched を返すため、ここへ混ぜると
        # MS1 注釈だけで authentic_standard_match が通ってしまう。
        self.feature_annotations: dict = {}
```

- [ ] **Step 4: 構築関数を書く**

`lipidmix/mztab/dataset_state.py` の `_all_candidates` の直後に追加する。

```python
#: SML 行からそのまま写す列（mzTab-M 2.0.0-M の列名）。
#: `inchi` は含めない——MS-DIAL は常に "null" を書く（MztabFormatExport.cs:393）。
_SML_TEXT_FIELDS = {
    "name": "chemical_name",
    "database_identifier": "database_identifier",
    "chemical_formula": "chemical_formula",
    "smiles": "smiles",
    "adduct": "adduct_ions",
    "reliability": "reliability",
    "confidence_measure": "best_id_confidence_measure",
}


def _build_feature_annotations(sml_rows, known_feature_ids: set) -> tuple[dict, list[str]]:
    """SML 行を feature_id（SMF_ID）ごとのラベルへ畳む。

    戻り値は `(annotations, warnings)`。警告は**種類**で集約する（行ごとに積むと
    実データで数百件になり、表示制限で重要な警告が埋もれる）。
    """
    by_feature: dict[str, list[dict]] = {}
    unknown_refs = 0
    for row in sml_rows or []:
        if not (row.get("chemical_name") or row.get("database_identifier")):
            continue
        for ref in str(row.get("SMF_ID_REFS") or "").split("|"):
            fid = ref.strip()
            if not fid:
                continue
            if fid not in known_feature_ids:
                unknown_refs += 1
                continue
            entry = {"sml_id": str(row.get("SML_ID")), "ambiguous": False}
            for key, column in _SML_TEXT_FIELDS.items():
                entry[key] = row.get(column)
            entry["confidence_value"] = _to_float(row.get("best_id_confidence_value"))
            # inchi は保持しないが、導出の材料としては渡す（他実装が書く余地を残す）。
            ik, src = derive_inchikey(row.get("database_identifier"),
                                      row.get("inchi"), row.get("smiles"))
            entry["inchikey"] = ik
            entry["inchikey_source"] = src
            by_feature.setdefault(fid, []).append(entry)

    annotations: dict[str, dict] = {}
    ambiguous = 0
    for fid, entries in by_feature.items():
        if len(entries) == 1:
            annotations[fid] = entries[0]
            continue
        ambiguous += 1
        annotations[fid] = {"ambiguous": True,
                            "sml_ids": [e["sml_id"] for e in entries],
                            "name": None}

    warnings: list[str] = []
    if ambiguous:
        warnings.append(
            f"1 つの特徴に複数の SML 注釈が当たっています（{ambiguous} 件）。"
            "どれが正しいか決められないため名前を付けていません。")
    if unknown_refs:
        warnings.append(
            f"SML の SMF_ID_REFS が存在しない特徴を指しています（{unknown_refs} 件）。")
    return annotations, warnings
```

`derive_inchikey` は同ファイル先頭で既に import 済み（`from lipidmix.mztab.identity
import derive_inchikey` 相当）。未 import なら既存の import 行に合わせて足す。

- [ ] **Step 5: `build_dataset_state` から呼ぶ**

`ds.sme_rows = parse_result["sections"].get("SME", {}).get("rows")`（226 行目付近）の
直後に挿入する。

```python
    # SML 由来のラベル（証拠ではない）。証拠スロットの構築が終わってから作る。
    ds.feature_annotations, annotation_warnings = _build_feature_annotations(
        ds.sml_rows, set(ds.feature_ids))
    if annotation_warnings:
        existing = ds.validation_result.setdefault("warnings", [])
        ds.validation_result["warnings"] = [*existing, *annotation_warnings]
```

- [ ] **Step 6: テストを走らせて通す**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q`
Expected: PASS（既存テストも全て緑）

- [ ] **Step 7: 対応付けの規則を固めるテストを足す**

`tests/test_dataset_state.py` の末尾に追記する。

```python
_MULTI_REF_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tsmiles\tchemical_name\tadduct_ions
    SML\t1\t1|2\tTextDB:GABA\tnull\tGABA\t[M+H]1+
    SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]
    SMF\t1\tnull\t104.07\t72.0\t1.0
    SMF\t2\tnull\t126.05\t72.0\t2.0
""")

_AMBIGUOUS_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tsmiles\tchemical_name\tadduct_ions
    SML\t1\t1\tTextDB:GABA\tnull\tGABA\t[M+H]1+
    SML\t2\t1\tTextDB:Alanine\tnull\tAlanine\t[M+H]1+
    SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]
    SMF\t1\tnull\t104.07\t72.0\t1.0
""")

_UNKNOWN_REF_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tsmiles\tchemical_name\tadduct_ions
    SML\t1\t99\tTextDB:GABA\tnull\tGABA\t[M+H]1+
    SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]
    SMF\t1\tnull\t104.07\t72.0\t1.0
""")

_SMILES_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tsmiles\tchemical_name\tadduct_ions
    SML\t1\t1\tTextDB:GABA\tNCCCC(=O)O\tGABA\t[M+H]1+
    SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]
    SMF\t1\tnull\t104.07\t72.0\t1.0
""")


def test_one_sml_pointing_at_two_features_annotates_both(tmp_path):
    """同一分子が複数 adduct で検出された形。両方に同じ注釈が付くのが正しい。"""
    ds = _build(tmp_path, _MULTI_REF_MZTAB, "Height_multi.mzTab")

    assert ds.feature_annotations["1"]["name"] == "GABA"
    assert ds.feature_annotations["2"]["name"] == "GABA"


def test_two_sml_rows_on_one_feature_do_not_pick_a_name(tmp_path):
    """どれが正しいか決められないときは選ばない（リポジトリ共通の方針）。"""
    ds = _build(tmp_path, _AMBIGUOUS_MZTAB, "Height_ambiguous.mzTab")

    annotation = ds.feature_annotations["1"]
    assert annotation["ambiguous"] is True
    assert annotation["name"] is None
    assert sorted(annotation["sml_ids"]) == ["1", "2"]


def test_an_ambiguous_annotation_is_reported_once(tmp_path):
    """警告は件数ではなく種類で 1 件に集約する。"""
    ds = _build(tmp_path, _AMBIGUOUS_MZTAB, "Height_ambiguous2.mzTab")

    hits = [w for w in ds.validation_result.get("warnings", []) if "複数の SML" in w]
    assert len(hits) == 1


def test_an_sml_pointing_at_a_missing_feature_is_reported(tmp_path):
    ds = _build(tmp_path, _UNKNOWN_REF_MZTAB, "Height_unknown.mzTab")

    assert ds.feature_annotations == {}
    hits = [w for w in ds.validation_result.get("warnings", []) if "SMF_ID_REFS" in w]
    assert len(hits) == 1


def test_an_inchikey_is_derived_from_the_sml_smiles(tmp_path):
    """MS-DIAL の SML で InChIKey に到達しうるのは smiles 経由だけ。

    `database_identifier` は必ず `<db>:<name>` 形式（MztabFormatExport.cs:407）、
    `inchi` は常に null（同 :393）。
    """
    pytest.importorskip("rdkit")
    ds = _build(tmp_path, _SMILES_MZTAB, "Height_smiles.mzTab")

    annotation = ds.feature_annotations["1"]
    assert annotation["inchikey"] == "BTCSSZJGUNDROE-UHFFFAOYSA-N"
    assert annotation["inchikey_source"] == "smiles_derived"
```

- [ ] **Step 8: 失敗を確認してから通す**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q`
Expected: まず失敗（曖昧・未知参照の分岐が未実装なら）。Step 4 の実装で全て
賄えているはずなので、通らない場合は実装側を直す（テストを緩めない）。

**注意**: `test_an_inchikey_is_derived_from_the_sml_smiles` の期待 InChIKey は
GABA の正準値。実際に RDKit が返す値が違う場合は**テストの期待値を実測値へ
書き換えてよい**（RDKit の版差）。ただし `inchikey_source == "smiles_derived"`
の方は必ず成り立つこと。

- [ ] **Step 9: coverage に出所内訳を足す — 失敗テストを書く**

```python
def test_the_coverage_separates_evidence_from_ms1_annotation(tmp_path):
    ds = _build(tmp_path, _SML_ONLY_MZTAB, "Height_cov.mzTab")

    by = ds.inchikey_coverage["identified_by"]
    assert by["sme"] == 0
    assert by["sml_only"] == 1
    assert by["none"] == 1
```

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q -k coverage`
Expected: FAIL — `KeyError: 'identified_by'`

- [ ] **Step 10: coverage の集計を足す**

Step 5 で挿入したブロックの直後に続けて書く。

```python
    # 同定の出所内訳。MS1 注釈を MS/MS 裏付けと取り違えないために分けて数える。
    sme_named = sum(1 for m in ds.feature_metadata.values() if m.get("name"))
    sml_only = sum(
        1 for fid, a in ds.feature_annotations.items()
        if a.get("name") and not (ds.feature_metadata.get(fid) or {}).get("name"))
    ds.inchikey_coverage["identified_by"] = {
        "sme": sme_named,
        "sml_only": sml_only,
        "none": len(ds.feature_ids) - sme_named - sml_only,
    }
    # SME が InChIKey を出せなかった特徴を SML が補ったぶんを足す。
    # by_source は全特徴で 1 回ずつ数える不変条件を保つため、'none' から移す。
    for fid, a in ds.feature_annotations.items():
        if not a.get("inchikey"):
            continue
        if (ds.feature_metadata.get(fid) or {}).get("inchikey"):
            continue
        src = a.get("inchikey_source") or "none"
        ds.inchikey_coverage["by_source"]["none"] -= 1
        ds.inchikey_coverage["by_source"][src] = (
            ds.inchikey_coverage["by_source"].get(src, 0) + 1)
        ds.inchikey_coverage["with_inchikey"] += 1
```

- [ ] **Step 11: テストを通す**

Run: `C:/Python314/python.exe -m pytest tests/test_dataset_state.py tests/test_mztab_tools.py -q`
Expected: PASS

- [ ] **Step 12: コミット**

```bash
git add lipidmix/mztab/dataset_state.py tests/test_dataset_state.py
git commit -m "$(cat <<'EOF'
feat: mzTab-M の SML 注釈を feature_annotations へ回収する

MS-DIAL は Text DB 由来の同定を SME へ書かない（MztabFormatExport.cs の
ShouldWriteSmeLine が IsTextDbBasedRepresentative を除外する）ため、Text DB
運用では SML が唯一の同定情報源になる。これを新スロットへ隔離して回収する。

証拠スロット（feature_metadata / feature_candidates）には入れない。
feature_bindings._check_identity は候補ゼロのとき feature_metadata["inchikey"]
を見て matched を返すため、混ぜると MS1 注釈だけで authentic_standard_match が
通ってしまう。

InChIKey は smiles 経由でのみ導出しうる（database_identifier は必ず
"<db>:<name>"、inchi は常に null）。inchi のキーは持たない。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: binding の不変条件を固定する

**Files:**
- Test: `tests/test_feature_bindings.py`
- **実装の変更なし。** `lipidmix/analysis/feature_bindings.py` は触らない。

**Interfaces:**
- Consumes: Task 1 の `DatasetState.feature_annotations`、既存の
  `bind_features(ds, profile, evidence, standard_assays, overrides)`。
- Produces: なし（回帰テストのみ）。

- [ ] **Step 1: 不変条件のテストを書く**

`tests/test_feature_bindings.py` の末尾に追記する。既存ヘルパ `_target` /
`_profile` / `_dataset` / `_evidence` / `_row` をそのまま使う。

```python
# ---------- SML 由来の注釈は証拠にならない（spec 2026-09-17 §5）----------

def _ms1_annotated_dataset():
    """m/z と RT は合うが、同定は SML 由来のラベルしか無い dataset。"""
    ds = _dataset({"1": {"mz": 104.0706, "rt": 1.2, "candidates": []}})
    ds.feature_annotations = {
        "1": {"sml_id": "1", "ambiguous": False, "name": "GABA",
              "database_identifier": "TextDB:GABA", "chemical_formula": None,
              "smiles": None, "adduct": "[M+H]1+", "reliability": None,
              "confidence_measure": "[,, MS-DIAL algorithm matching score, ]",
              "confidence_value": 0.999982,
              "inchikey": "BTCSSZJGUNDROE-UHFFFAOYSA-N",
              "inchikey_source": "smiles_derived"},
    }
    return ds


def test_an_ms1_annotation_does_not_satisfy_a_library_match():
    """SML の score があっても library_match（スペクトル照合）は満たさない。"""
    ds = _ms1_annotated_dataset()
    target = _target(required_evidence=[{
        "kind": "library_match", "library_id": "lib1",
        "library_sha256": "0" * 64,
        "score_field": "[,, MS-DIAL algorithm matching score, ]",
        "score_threshold": 0.8}])

    out = bind_features(ds, _profile({"gaba": target}), _evidence(ds), {}, None)

    entry = out["bindings"]["gaba"]
    assert entry["status"] == "needs_input"
    # 同定が「評価できない」ままであること（matched へ昇格していない）。
    reasons = entry["candidates"][0]["reasons"]
    assert "identity_not_evaluable" in reasons
    assert "identification_required" in reasons


def test_an_ms1_annotation_does_not_unlock_an_authentic_standard_match():
    """ここが緩むと MS1 注釈だけで標準品照合が通る。spec §5 の核心。"""
    ds = _ms1_annotated_dataset()
    target = _target(required_evidence=[{
        "kind": "authentic_standard_match",
        "mz_tolerance_ppm": 10.0, "rt_tolerance_min": 0.1}])
    evidence = _evidence(ds, rows=[_row("1", "assay[1]")])

    out = bind_features(ds, _profile({"gaba": target}),
                        evidence, {"gaba": ["assay[1]"]}, None)

    entry = out["bindings"]["gaba"]
    assert entry["status"] == "needs_input"
    assert "identification_required" in entry["candidates"][0]["reasons"]


def test_an_ms1_annotation_still_allows_a_mass_rt_binding():
    """証拠水準が mass_rt だけなら、同定の有無に関係なく成立する（従来どおり）。"""
    ds = _ms1_annotated_dataset()

    out = bind_features(ds, _profile(), _evidence(ds), {}, None)

    assert out["bindings"]["gaba"]["status"] == "resolved"
```

- [ ] **Step 2: テストを走らせる**

Run: `C:/Python314/python.exe -m pytest tests/test_feature_bindings.py -q -k ms1`
Expected: **3 件とも PASS**。

これは「失敗してから通す」テストではなく、**既にある性質を固定する**テスト。
Task 1 の実装が証拠スロットを汚していれば、ここが赤くなって検出できる。

- [ ] **Step 3: もし赤かったら Task 1 を直す**

赤い場合、Task 1 が `feature_metadata` か `feature_candidates` を汚している。
**このテストを緩めてはならない。** Task 1 の実装を直す。

- [ ] **Step 4: binding が無変更であることを確認する**

Run: `git diff --name-only main -- lipidmix/analysis/feature_bindings.py`
Expected: 出力が**空**

- [ ] **Step 5: コミット**

```bash
git add tests/test_feature_bindings.py
git commit -m "$(cat <<'EOF'
test: SML 由来の注釈が証拠水準を満たさないことを固定する

MS1 注釈（Text DB の m/z 照合）が library_match と
authentic_standard_match のどちらも満たさないこと、mass_rt だけの
証拠水準は従来どおり成立することを縛る。

authentic_standard_match が要。feature_metadata へ SML 由来の識別子が
漏れると _check_identity が not_evaluable から matched へ変わり、
この証拠水準だけが実質的に緩む。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `.annotations.tsv` に `ms1_annotation` を足す

**Files:**
- Modify: `lipidmix/analysis/feature_export.py:134-167`
- Test: `tests/test_metabolomics_report.py`

**Interfaces:**
- Consumes: Task 1 の `ds.feature_annotations`。
- Produces: `.annotations.tsv` の `identification_status` が
  `candidate` / `ms1_annotation` / `unidentified` の 3 値になる。列（`_ANNOTATION_COLUMNS`）は不変。

- [ ] **Step 1: 失敗テストを書く**

`tests/test_metabolomics_report.py` の末尾に追記する。同ファイルの既存ヘルパ
`_dataset()` / `_matrix()` / `_read()` と `export_features` をそのまま使う。
`_dataset()` の feature `"2"` は候補 0 件・名前も InChIKey も無いので、ここへ
注釈を付けるのがちょうどよい。

```python
# ---------- SML 由来の注釈（spec 2026-09-17-mztab-sml-annotation-design §7.1）----------

def _ms1_annotation(**over) -> dict:
    annotation = {"sml_id": "1", "ambiguous": False, "name": "GABA",
                  "database_identifier": "TextDB:GABA", "chemical_formula": None,
                  "smiles": None, "adduct": "[M+H]1+", "reliability": None,
                  "confidence_measure": "MS-DIAL algorithm matching score",
                  "confidence_value": 0.99,
                  "inchikey": None, "inchikey_source": "none"}
    annotation.update(over)
    return annotation


def _annotation_rows(tmp_path, ds) -> list[dict]:
    """`.annotations.tsv` を dict の一覧で返す。"""
    out = export_features(ds, [_matrix()], tmp_path / "features.tsv")
    table = _read(tmp_path / out["annotation_file"])
    header = table[0]
    return [dict(zip(header, row)) for row in table[1:]]


def test_an_ms1_annotation_gets_its_own_identification_status(tmp_path):
    """SME 候補が無く SML 注釈だけがある特徴は `ms1_annotation`。

    `unidentified` に寄せると「同定できなかった」と読まれ、`candidate` に
    寄せると MS/MS 裏付けがあるように読まれる。
    """
    ds = _dataset()
    ds.feature_annotations = {"2": _ms1_annotation()}

    rows = _annotation_rows(tmp_path, ds)

    row = next(r for r in rows if r["feature_id"] == "2")
    assert row["identification_status"] == "ms1_annotation"
    assert row["name"] == "GABA"
    assert row["database_identifier"] == "TextDB:GABA"
    assert row["adduct"] == "[M+H]1+"
    assert row["candidate_rank"] == ""


def test_an_sme_backed_feature_is_unaffected_by_an_annotation(tmp_path):
    """SME 候補がある特徴は従来どおり candidate。注釈があっても変わらない。"""
    ds = _dataset()
    ds.feature_annotations = {"1": _ms1_annotation(name="別の名前"),
                              "2": _ms1_annotation()}

    rows = _annotation_rows(tmp_path, ds)

    for row in (r for r in rows if r["feature_id"] == "1"):
        assert row["identification_status"] == "candidate"
        assert row["name"] != "別の名前"


def test_a_feature_without_any_annotation_stays_unidentified(tmp_path):
    ds = _dataset()
    ds.feature_annotations = {}

    rows = _annotation_rows(tmp_path, ds)

    row = next(r for r in rows if r["feature_id"] == "2")
    assert row["identification_status"] == "unidentified"


def test_an_ambiguous_annotation_stays_unidentified(tmp_path):
    """どの名前かを決められない注釈で名前を出すと嘘になる。"""
    ds = _dataset()
    ds.feature_annotations = {
        "2": {"ambiguous": True, "sml_ids": ["1", "2"], "name": None}}

    rows = _annotation_rows(tmp_path, ds)

    row = next(r for r in rows if r["feature_id"] == "2")
    assert row["identification_status"] == "unidentified"
```

- [ ] **Step 2: 失敗を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_report.py -q -k "ms1_annotation or unidentified or ambiguous"`
Expected: FAIL — `ms1_annotation` ではなく `unidentified` が返る

- [ ] **Step 3: 実装する**

`lipidmix/analysis/feature_export.py` の 135 行目付近を書き換える。

変更前:

```python
    metadata = getattr(ds, "feature_metadata", {}) or {}
    candidates_by_feature = getattr(ds, "feature_candidates", {}) or {}
    feature_ids = list(getattr(ds, "feature_ids", []) or [])
```

変更後:

```python
    metadata = getattr(ds, "feature_metadata", {}) or {}
    candidates_by_feature = getattr(ds, "feature_candidates", {}) or {}
    # SML 由来のラベル。**証拠ではない**ので、読むのは ms1_annotation 行だけ。
    annotations_by_feature = getattr(ds, "feature_annotations", {}) or {}
    feature_ids = list(getattr(ds, "feature_ids", []) or [])
```

続けて、`if not candidates:` ブロックの**手前**に新しい分岐を挿入する。

```python
        annotation = annotations_by_feature.get(feature_id) or {}
        if not candidates and annotation and not annotation.get("ambiguous"):
            # MS1 の照合だけで付いた名前。candidate と混ぜると MS/MS 裏付けが
            # あるように読まれ、unidentified と混ぜると同定できなかったと読まれる。
            annotation_rows.append({
                "feature_id": feature_id, "candidate_rank": None,
                "identification_status": "ms1_annotation",
                "name": annotation.get("name"),
                "database_identifier": annotation.get("database_identifier"),
                "inchikey": annotation.get("inchikey"),
                "adduct": annotation.get("adduct"), "charge": None,
                "confidence_measure": annotation.get("confidence_measure"),
                "confidence_value": annotation.get("confidence_value"),
                "mz": meta.get("mz"), "rt_min": meta.get("rt")})
            continue
```

既存の `if not candidates:` ブロックと `for candidate in candidates:` ループは
**一切変更しない**。

- [ ] **Step 4: テストを通す**

Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_report.py -q`
Expected: PASS（既存テストも全て緑）

- [ ] **Step 5: コミット**

```bash
git add lipidmix/analysis/feature_export.py tests/test_metabolomics_report.py
git commit -m "$(cat <<'EOF'
feat: 特徴表に ms1_annotation の同定状態を足す

SME 候補が無く SML 注釈だけがある特徴を candidate とも unidentified とも
区別する。candidate に寄せると MS/MS 裏付けがあるように読まれ、
unidentified に寄せると同定できなかったと読まれる。

曖昧な注釈（複数 SML が 1 特徴に当たる）は unidentified のまま。
列（_ANNOTATION_COLUMNS）は不変。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `dataset_load` の要約に出所内訳を出す

**Files:**
- Modify: `lipidmix/tools/mztab_tools.py:210-222`
- Test: `tests/test_mztab_tools.py`

**Interfaces:**
- Consumes: Task 1 の `ds.inchikey_coverage["identified_by"]`。
- Produces: `_identification_line(ds) -> str`。`dataset_load` のテキスト要約に 1 行増える。

- [ ] **Step 1: 失敗テストを書く**

`tests/test_mztab_tools.py` の末尾に追記する。`dataset_load` の呼び方は同ファイルの
既存テストに合わせる。

`tests/test_mztab_tools.py` の末尾に追記する。`dataset_load(str(path))` は要約の
テキストを返す（同ファイル 41-44 行目の既存テストと同じ）。fixture は Task 1 と
同じ内容をこのファイルにも置く（テスト間で fixture を共有しない既存の流儀）。

```python
# ---------- 同定の出所内訳（spec 2026-09-17-mztab-sml-annotation-design §7.2）----------

# Text DB 運用の実形状: SME 0 行、同定は SML にしか無い。ヘッダは実形式の `SMH`。
_SML_ONLY_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tsmiles\tchemical_name\tadduct_ions
    SML\t1\t1\tTextDB:GABA\tnull\tGABA\t[M+H]1+
    SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]
    SMF\t1\tnull\t104.07066\t72.0\t5000.0
    SMF\t2\tnull\t880.8\t360.0\t100.0
""")


def test_the_load_summary_separates_evidence_from_ms1_annotation(tmp_path):
    """LLM が MS1 注釈を MS/MS 裏付けと取り違えないよう、入口で分けて言う。"""
    from lipidmix.tools.mztab_tools import dataset_load
    p = tmp_path / "Height_sml.mzTab"
    p.write_text(_SML_ONLY_MZTAB, encoding="utf-8")

    text = dataset_load(str(p))

    assert "MS/MS 証拠あり 0 件" in text
    assert "MS1 注釈のみ 1 件" in text
```

`textwrap` がこのファイルで未 import なら import 行に足す。

- [ ] **Step 2: 失敗を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_mztab_tools.py -q -k ms1`
Expected: FAIL — 文言が無い

- [ ] **Step 3: 実装する**

`lipidmix/tools/mztab_tools.py` に関数を足す（`_detection_line` の近くに置く）。

```python
def _identification_line(ds) -> str:
    """同定の出所内訳。MS1 注釈と MS/MS 裏付けを入口で区別する。

    MCP サーバ指示の「`analytical_checks.msms.band` の PASS と FLAG_ONLY を
    同一視するな」と同じ趣旨。SML 由来の注釈は m/z 照合だけで付く。
    """
    by = (ds.inchikey_coverage or {}).get("identified_by") or {}
    return (f"- 同定: MS/MS 証拠あり {by.get('sme', 0)} 件 / "
            f"MS1 注釈のみ {by.get('sml_only', 0)} 件 / "
            f"同定なし {by.get('none', 0)} 件"
            "（MS1 注釈は m/z 照合のみ。MS/MS 裏付けと同一視しないこと）")
```

`lines` のリスト（216 行目付近の `- InChIKey 付き: ...` の次）に足す。

```python
        _identification_line(ds),
```

- [ ] **Step 4: テストを通す**

Run: `C:/Python314/python.exe -m pytest tests/test_mztab_tools.py -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add lipidmix/tools/mztab_tools.py tests/test_mztab_tools.py
git commit -m "$(cat <<'EOF'
feat: dataset_load 要約に同定の出所内訳を出す

MS/MS 証拠あり / MS1 注釈のみ / 同定なし を分けて数える。MS1 注釈は
m/z 照合だけで付くので、入口で区別しないと MS/MS 裏付けと取り違えられる。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 差次的エクスポートで SML をマージする

**Files:**
- Modify: `lipidmix/analysis/dataset_export.py:65-92`
- Test: `tests/test_result_output.py`（`export_dataset_result` を実際に叩いている
  のはここ。`tests/test_export_contract.py` は契約定数とメタ行だけを見ている）

**Interfaces:**
- Consumes: Task 1 の `ds.feature_annotations`。
- Produces: エクスポート行の `name_source` が `"mztab_sme"` / `"mztab_sml"` になる。
  `EXPORT_COLUMNS` と `CONTRACT_VERSION` は不変。

- [ ] **Step 1: 失敗テストを書く**

`tests/test_result_output.py` の末尾に追記する。同ファイルの既存ヘルパ
`_prepared_dataset()`（`tests.pipeline_fixtures.make_dataset` で 20 特徴・8 サンプルを
作り、前処理と比較まで済ませる）をそのまま使う。

```python
# ---------- SML 由来の同定（spec 2026-09-17-mztab-sml-annotation-design §7.3）----------

def _export_rows(tmp_path, ds, result, name="sml.tsv") -> list[dict]:
    """エクスポート TSV の本体を dict の一覧で返す（`#` メタ行は落とす）。"""
    from lipidmix.analysis.dataset_export import export_dataset_result
    path = tmp_path / name
    export_dataset_result(ds, result, path)
    body = [l for l in path.read_text(encoding="utf-8").splitlines()
            if l and not l.startswith("#")]
    header = body[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in body[1:]]


def test_an_evidence_backed_row_says_sme(tmp_path):
    """既存経路の name_source を "mztab_smf" から "mztab_sme" へ訂正した。

    名前の実体は SMF 行ではなく SME 行なので、従来の値は事実と違っていた。
    """
    ds, result = _prepared_dataset()

    rows = _export_rows(tmp_path, ds, result, "sme.tsv")

    assert {r["name_source"] for r in rows} == {"mztab_sme"}


def test_a_row_sourced_from_an_sml_annotation_says_so(tmp_path):
    """MS1 注釈由来の行は name_source で見分けられる。

    下流（massbank-context）は inchikey を結合キーにするので、証拠水準を
    伝える列が無いと MS/MS 裏付けと区別できなくなる。
    """
    ds, result = _prepared_dataset()
    # InChIKey を持つ特徴を1つ選び、証拠を剥がして注釈だけにする。
    fid = next(f for f in ds.feature_ids
               if (ds.feature_metadata.get(f) or {}).get("inchikey"))
    ds.feature_metadata[fid]["inchikey"] = None
    ds.feature_metadata[fid]["name"] = None
    ds.feature_annotations = {
        fid: {"sml_id": "1", "ambiguous": False, "name": "GABA",
              "database_identifier": "TextDB:GABA", "chemical_formula": None,
              "smiles": "NCCCC(=O)O", "adduct": "[M+H]1+", "reliability": None,
              "confidence_measure": "MS-DIAL algorithm matching score",
              "confidence_value": 0.99,
              "inchikey": "BTCSSZJGUNDROE-UHFFFAOYSA-N",
              "inchikey_source": "smiles_derived"},
    }

    rows = _export_rows(tmp_path, ds, result)

    row = next(r for r in rows if r["spot_id"] == fid)
    assert row["name"] == "GABA"
    assert row["name_source"] == "mztab_sml"
    assert row["inchikey"] == "BTCSSZJGUNDROE-UHFFFAOYSA-N"
    assert row["inchikey_source"] == "smiles_derived"
    assert row["msi_level"] == ""


def test_a_name_without_an_inchikey_is_still_dropped(tmp_path):
    """InChIKey ゲートは変えない。下流が InChIKey で結合するため。"""
    from lipidmix.analysis.dataset_export import export_dataset_result
    from lipidmix.core.atomic_io import DomainError
    ds, result = _prepared_dataset()
    for fid in ds.feature_ids:
        ds.feature_metadata.setdefault(fid, {})["inchikey"] = None
    # 名前だけの注釈（InChIKey なし）。これでは救われない。
    ds.feature_annotations = {
        fid: {"sml_id": "1", "ambiguous": False, "name": "GABA",
              "database_identifier": "TextDB:GABA", "chemical_formula": None,
              "smiles": None, "adduct": "[M+H]1+", "reliability": None,
              "confidence_measure": "score", "confidence_value": 0.99,
              "inchikey": None, "inchikey_source": "none"}
        for fid in ds.feature_ids}

    with pytest.raises(DomainError, match="NO_ANNOTATED_FEATURES"):
        export_dataset_result(ds, result, tmp_path / "dropped.tsv")


def test_an_ambiguous_annotation_does_not_reach_the_export(tmp_path):
    ds, result = _prepared_dataset()
    fid = next(f for f in ds.feature_ids
               if (ds.feature_metadata.get(f) or {}).get("inchikey"))
    ds.feature_metadata[fid]["inchikey"] = None
    ds.feature_annotations = {
        fid: {"ambiguous": True, "sml_ids": ["1", "2"], "name": None}}

    rows = _export_rows(tmp_path, ds, result, "ambiguous.tsv")

    assert not any(r["spot_id"] == fid for r in rows)
```

**注**: `_prepared_dataset()` が作る `ds` の `feature_metadata` に `inchikey` が
何件あるかは `tests/pipeline_fixtures.py` の `make_dataset` 次第（既存テスト
`test_export_writes_the_contract_columns` は 6 件を期待している）。上の
`next(...)` はその中から 1 件選ぶだけなので件数に依存しない。

- [ ] **Step 2: 失敗を確認する**

Run: `C:/Python314/python.exe -m pytest tests/test_result_output.py -q -k "sml or sme or dropped or ambiguous"`
Expected: FAIL — `name_source` が `"mztab_smf"`、SML 行が出ない

- [ ] **Step 3: 実装する**

`lipidmix/analysis/dataset_export.py` の 67-78 行目を書き換える。

変更前:

```python
    for row in result["results"]:
        fid = row["feature"]
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
```

変更後:

```python
    annotations = getattr(ds, "feature_annotations", {}) or {}
    for row in result["results"]:
        fid = row["feature"]
        meta = ds.feature_metadata.get(fid, {})
        # 行単位で出所を1つに決める。決め手は InChIKey を供給した側——列ごとに
        # 選ぶと、name が SML 由来で inchikey が SME 由来、という食い違った行ができる。
        inchikey = str(meta.get("inchikey") or "").strip()
        source = meta
        name_source = "mztab_sme"               # 名前の実体は SME 行（旧 "mztab_smf" は誤り）
        if not inchikey:
            annotation = annotations.get(fid) or {}
            if annotation.get("ambiguous"):
                annotation = {}
            candidate_key = str(annotation.get("inchikey") or "").strip()
            if candidate_key:
                inchikey = candidate_key
                source = annotation
                name_source = "mztab_sml"       # MS1 照合のみ。MS/MS 裏付けではない
        if not inchikey:
            n_unannotated += 1
            continue
        rows.append({
            "spot_id": fid,                     # mzTab-M の SMF_ID（メタ行で id_space を宣言）
            "name": (source.get("name") or "").strip(),
            "name_source": name_source,
            "ontology": "",                     # mzTab-M に対応物なし
            "inchikey": inchikey,
            "inchikey_source": source.get("inchikey_source") or "",
```

`msi_level` 以降の行は**一切変更しない**（`mz` / `rt` は `meta` から採り続ける——
座標は常に SMF 由来で、注釈の出所とは無関係）。

- [ ] **Step 4: テストを通す**

Run: `C:/Python314/python.exe -m pytest tests/test_result_output.py tests/test_export_contract.py tests/test_dataset_analysis_tools.py tests/test_pipeline_report.py -q`
Expected: PASS

- [ ] **Step 5: 契約が動いていないことを確認する**

Run: `git diff --name-only main -- lipidmix/analysis/export_contract.py`
Expected: 出力が**空**

- [ ] **Step 6: コミット**

```bash
git add lipidmix/analysis/dataset_export.py tests/test_result_output.py
git commit -m "$(cat <<'EOF'
feat: 差次的エクスポートで SML 由来の同定をマージする

行単位で出所を1つに決める。決め手は InChIKey を供給した側で、列ごとに
選ぶと name が SML 由来・inchikey が SME 由来という食い違った行ができる。

あわせて name_source の "mztab_smf" を "mztab_sme" へ訂正した。名前の
実体は SMF 行ではなく SME 行で、従来の値は事実と違っていた。下流
（massbank-context の experiment/contract.py）は name_source を読んで
いないので影響は無い。

InChIKey ゲート（InChIKey が無い行を捨てる）は変えない。下流が
InChIKey で結合するため。EXPORT_COLUMNS と CONTRACT_VERSION も不変。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 文書を更新する

**Files:**
- Modify: `docs/output_format/identity.md`
- Modify: `docs/output_format/` の mzTab トピック（`docs/workflow/index.md` の表から実ファイル名を確認する）
- Modify: `docs/workflow/mztab.md`
- Modify: `USAGE.md`

**Interfaces:**
- Consumes: Task 1〜5 の成果。
- Produces: なし（文書のみ）。

- [ ] **Step 1: `docs/output_format/identity.md` に節を足す**

次の内容を、既存の見出し構成に合わせて追記する。

```markdown
## 同定の出所: SME と SML

mzTab-M では同定が 2 か所に出る。**意味が違うので混ぜてはいけない。**

| 出所 | 何か | このサーバでの置き場所 |
|---|---|---|
| SME 行（Small Molecule Evidence） | **スペクトル照合の証拠**。MS/MS が割り当てられた同定だけが載る | `feature_metadata` / `feature_candidates` |
| SML 行（Small Molecule） | 代表同定。MS1 だけの照合（Text DB）もここに載る | `feature_annotations` |

MS-DIAL は Text DB 由来の同定を **SME へ書かない**（`MztabFormatExport.cs` の
`ShouldWriteSmeLine` が `IsTextDbBasedRepresentative` を除外する）。したがって
Text DB 運用のメタボロミクスでは **SME が 0 行**になり、同定は SML にしか無い。
これは異常ではない。

特徴表（`.annotations.tsv`）の `identification_status` がこの区別を伝える。

| 値 | 意味 |
|---|---|
| `candidate` | SME 由来。スペクトル照合の候補（rank 1 でも確定同定は名乗らない） |
| `ms1_annotation` | **SML 由来。m/z 照合のみ。MS/MS の裏付けは無い** |
| `unidentified` | 同定情報が無い。複数の SML が 1 特徴に当たって決められない場合もここ |

**`ms1_annotation` を `candidate` と同一視しないこと。** MSI レベルで言えば
MS/MS 照合とは別水準で、v2 の `required_evidence` では `library_match` も
`authentic_standard_match` も満たさない。

`msi_level` は mzTab-M 経路では常に空欄。`.arf2` 由来の MSI ヒューリスティックは
Level 2 に MS/MS の取得を要件としており、別ルールの値を同じ列へ入れると比較不能な
2 つの意味が同居するため。出所は `name_source`（`mztab_sme` / `mztab_sml`）で伝える。
```

- [ ] **Step 2: mzTab トピックに `feature_annotations` の項目定義を足す**

`docs/workflow/index.md` の表から、出力形式のトピック文書の実ファイル名を確認して
開く。`feature_annotations` の各キーを列挙する。**`inchi` キーは存在しない**こと
（MS-DIAL が常に null を書くため）と、InChIKey は `smiles` 経由でのみ導出されること
（`database_identifier` は必ず `<db>:<name>` 形式）を明記する。

- [ ] **Step 3: `docs/workflow/mztab.md` の呼び出し連鎖を更新する**

`build_dataset_state` の連鎖に次を足す。行番号は書かない（規約）。

```
lipidmix/mztab/dataset_state.py  build_dataset_state()
├─ lipidmix/mztab/dataset_state.py  _build_feature_annotations()
│  └─ lipidmix/mztab/identity.py  derive_inchikey()
```

- [ ] **Step 4: `USAGE.md` の `dataset_load` を更新する**

戻り値の `inchikey_coverage` に `identified_by`（`sme` / `sml_only` / `none`）が
増えたことと、要約に同定の出所内訳の行が出ることを書く。

- [ ] **Step 5: 腐敗防止テストを走らせる**

Run: `C:/Python314/python.exe -m pytest tests/test_workflow_docs.py tests/test_readme_links.py -q`
Expected: PASS

`test_workflow_docs.py` は `docs/workflow/` が挙げるパス・関数名を AST で実在検証する。
`_build_feature_annotations` の綴りが実装と違えばここで落ちる。

- [ ] **Step 6: 全テストを走らせる**

Run: `C:/Python314/python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add docs/ USAGE.md
git commit -m "$(cat <<'EOF'
docs: 同定の出所（SME / SML）と ms1_annotation を文書化する

SME はスペクトル照合の証拠、SML は代表同定で MS1 だけの照合も載る、と
いう区別を output_format に書いた。MS-DIAL は Text DB 同定を SME へ
書かないので、Text DB 運用では SME 0 行が正常であることも明記した。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 完了時の確認

- [ ] `C:/Python314/python.exe -m pytest tests -q` が全て緑
- [ ] `git diff --name-only main -- lipidmix/analysis/feature_bindings.py` が空
- [ ] `git diff --name-only main -- lipidmix/analysis/export_contract.py` が空
- [ ] spec の受け入れ条件 A1〜A12 をすべて指させる
- [ ] `docs/HISTRY.md` へ追記、`docs/task.md` を更新（**main ツリー側**）
- [ ] main への `--no-ff` マージと push は**人間の指示を待つ**

## 持ち越し（この計画の範囲外）

1. テキスト DB へ SMILES 列を足して実 Console を再実行し、`metadata["SMILES"]` が
   実際に埋まるかを確認する（spec §9・§12）。Stage B の確認項目。
2. `massbank-context` 側が `name_source` / `inchikey_source` を読んで証拠水準で
   絞り込めるようにする（先方リポの作業）。
