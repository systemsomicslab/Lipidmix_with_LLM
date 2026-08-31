# mzTab-M Phase 1 実装計画 — Reader・DatasetState・dataset_load/status

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mzTab-M 2.0 ファイルを読み込み、`DatasetState` に変換して `dataset_load` / `dataset_status` MCP ツールとして公開する。InChIKey 導出・エラーエンベロープ・セッションスロット追加を含む。

**Architecture:** 純関数層（`lipidmix/mztab/reader.py`, `validator.py`, `identity.py`, `dataset_state.py`）を積み重ね、最上位の `lipidmix/tools/mztab_tools.py` がセッション・エラー処理を担う。既存 ARF 経路には一切触れない。

**Tech Stack:** Python 3.14, FastMCP, numpy, rdkit（新規追加）

**Spec:** `docs/superpowers/specs/2026-08-29-msdial-mztab-lipidmix-integration-design.md`（§9, §11, §12, §15, §18 Phase 1, §22, §23）

## Global Constraints

- Python は `C:/Python314/python.exe`。テスト実行は **リポジトリルート** から `C:/Python314/python.exe -m pytest tests -q`
- `mcp_errors.py` は依存グラフの leaf。`tools_*` / `server` を import してはならない
- `session_state.py` は leaf 依存のみ。`lipidmix.mztab.tools` を import してはならない
- 新ツール名は `dataset_load`, `dataset_status`（`load_dataset` とは別物）
- 全ツールに `structured_output=False`、戻り値は `json_payload()` または文字列
- `session.dataset` スロットは `session.arf` など既存スロットを絶対に上書きしない
- mzTab-M 2.0 のみ対応。2.1 以降を黙って受け入れてはならない
- `.gitignore` に `*.txt` があるため新規 `.txt` は `git add -f` が必要（本計画では不要）

---

## File Structure（作成・変更するファイル）

| 操作 | パス | 役割 |
|------|------|------|
| 変更 | `lipidmix/core/mcp_errors.py` | `mztab_error()` ファクトリ追加 |
| 変更 | `requirements.txt` | `rdkit` 追加 |
| 作成 | `lipidmix/mztab/__init__.py` | パッケージマーカー |
| 作成 | `lipidmix/mztab/identity.py` | `derive_inchikey()` 純関数 |
| 作成 | `lipidmix/mztab/reader.py` | `parse_mztab()` — mzTab-M 2.0 テキストパーサ |
| 作成 | `lipidmix/mztab/validator.py` | 構造検証・定量種別検証 |
| 作成 | `lipidmix/mztab/dataset_state.py` | `DatasetState` クラス + `build_dataset_state()` |
| 変更 | `lipidmix/core/session_state.py` | `self.dataset = None` スロット追加 |
| 作成 | `lipidmix/tools/mztab_tools.py` | `dataset_load`, `dataset_status` MCP ツール |
| 変更 | `server.py` | 新ツールモジュールを import して登録 |
| 作成 | `docs/workflow/mztab.md` | 呼び出し連鎖ドキュメント（腐敗防止テスト対象） |
| 変更 | `tests/test_server_registration.py` | `EXPECTED_TOOLS` に新ツール 2 件追加 |
| 変更 | `tests/test_workflow_docs.py` | `IN_SCOPE` に `mztab.md` を追加、合計数を 31 に更新 |
| 作成 | `tests/test_mztab_errors.py` | `mztab_error()` の単体テスト |
| 作成 | `tests/test_mztab_identity.py` | `derive_inchikey()` の単体テスト |
| 作成 | `tests/test_mztab_reader.py` | `parse_mztab()` の単体テスト |
| 作成 | `tests/test_mztab_validator.py` | `validate_mztab()` の単体テスト |
| 作成 | `tests/test_dataset_state.py` | `DatasetState` / `build_dataset_state()` の単体テスト |
| 作成 | `tests/test_mztab_tools.py` | `dataset_load` / `dataset_status` の統合テスト |

---

### Task 1: `mztab_error()` ファクトリ + `rdkit` 依存

**Files:**
- Modify: `lipidmix/core/mcp_errors.py`
- Modify: `requirements.txt`
- Test: `tests/test_mztab_errors.py`

**Interfaces:**
- Produces: `mztab_error(code: str, message: str, details: dict | None = None) -> str`

- [ ] **Step 1: テストを書く**

```python
# tests/test_mztab_errors.py
import json
from lipidmix.core.mcp_errors import mztab_error, missing_state, MISSING_STATE

def test_mztab_error_returns_json_string():
    result = mztab_error("MZTAB_NOT_FOUND", "ファイルが見つかりません")
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_NOT_FOUND"
    assert parsed["error"]["message"] == "ファイルが見つかりません"
    assert parsed["error"].get("details") is None

def test_mztab_error_with_details():
    result = mztab_error("QUANTIFICATION_CONFLICT", "定量値が衝突", {"mtd_unit": "height", "filename_prefix": "Area"})
    parsed = json.loads(result)
    assert parsed["error"]["details"]["mtd_unit"] == "height"

def test_mztab_error_code_is_not_missing_state():
    result = mztab_error("MZTAB_STRUCTURE_INVALID", "構造不正")
    parsed = json.loads(result)
    assert parsed["error"]["code"] != MISSING_STATE

def test_missing_state_still_works_after_change():
    result = missing_state("preprocessed_matrix", ["arf_preprocess"], "テスト")
    parsed = json.loads(result)
    assert parsed["error"]["code"] == MISSING_STATE
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_errors.py -q
```

期待: `ImportError` または `AttributeError: module has no attribute 'mztab_error'`

- [ ] **Step 3: `mztab_error()` を実装する**

`lipidmix/core/mcp_errors.py` の末尾に追加:

```python
MZTAB_ERROR_CODES = frozenset({
    "MZTAB_NOT_FOUND",
    "MZTAB_STRUCTURE_INVALID",
    "QUANTIFICATION_CONFLICT",
    "AMBIGUOUS_PRIMARY_MZTAB",
    "UNSUPPORTED_AREA_CONSOLE",
    "SAMPLE_DESIGN_MISSING",
    "COMPANION_ARTIFACT_MISSING",
    "ARTIFACT_HASH_MISMATCH",
    "POLARITY_MISMATCH",
})


def mztab_error(code: str, message: str, details: dict | None = None) -> str:
    """mzTab-M 処理固有のエラーを機械可読エンベロープで返す。

    code は MZTAB_ERROR_CODES の値を使う。missing_state とは用途が異なる:
    こちらはバリデーション失敗・契約違反のような確定エラーで、
    「前提状態が無い」という復旧可能な状態不足とは意味が異なる。
    """
    if not code or not message:
        raise ValueError("code と message は必須です。")
    payload: dict = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return json_payload({"error": payload})
```

- [ ] **Step 4: `rdkit` を `requirements.txt` に追加する**

`requirements.txt` の末尾に `rdkit` を追記する。

- [ ] **Step 5: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_errors.py -q
```

期待: 4 tests passed

- [ ] **Step 6: コミット**

```bash
git add lipidmix/core/mcp_errors.py requirements.txt tests/test_mztab_errors.py
git commit -m "feat: mztab_error ファクトリを追加し rdkit を依存に加える"
```

---

### Task 2: `derive_inchikey()` 純関数

**Files:**
- Create: `lipidmix/mztab/__init__.py`
- Create: `lipidmix/mztab/identity.py`
- Test: `tests/test_mztab_identity.py`

**Interfaces:**
- Produces: `derive_inchikey(database_identifier, inchi, smiles) -> tuple[str | None, str]`
  - 戻り値の 2 番目は source: `"database_identifier"` / `"inchi_derived"` / `"smiles_derived"` / `"none"`

- [ ] **Step 1: テストを書く**

```python
# tests/test_mztab_identity.py
import re
import sys
from unittest.mock import patch, MagicMock

from lipidmix.mztab.identity import derive_inchikey, _INCHIKEY_RE

# PC 36:2 の本物の InChIKey
_VALID_IK = "IPCSVZSSVZVIGE-UHFFFAOYSA-N"
# PC 36:2 の SMILES（テスト用に簡略化してもよいが、実際の値を使う）
_PC362_SMILES = "CCCCCCCCCCCCCCCC(=O)OC[C@H](COP(=O)([O-])OCC[N+](C)(C)C)OC(=O)CCCCCCC/C=C\\CCCCCCCC"

def test_inchikey_regex_accepts_valid():
    assert _INCHIKEY_RE.match(_VALID_IK)

def test_inchikey_regex_rejects_short():
    assert not _INCHIKEY_RE.match("IPCSVZSSVZVIGE")

def test_derive_from_database_identifier():
    ik, src = derive_inchikey(_VALID_IK, None, None)
    assert ik == _VALID_IK
    assert src == "database_identifier"

def test_derive_ignores_non_inchikey_database_identifier():
    ik, src = derive_inchikey("HMDB:HMDB0000001", None, None)
    assert ik is None
    assert src == "none"

def test_derive_returns_none_when_all_absent():
    ik, src = derive_inchikey(None, None, None)
    assert ik is None
    assert src == "none"

def test_derive_from_smiles_with_rdkit(monkeypatch):
    # RDKit をモックして SMILES 経路を検証する
    mock_rdkit = MagicMock()
    mock_mol = MagicMock()
    mock_rdkit.Chem.MolFromSmiles.return_value = mock_mol
    mock_rdkit.Chem.inchi.MolToInchi.return_value = "InChI=1S/test"
    mock_rdkit.Chem.inchi.InchiToInchiKey.return_value = _VALID_IK
    monkeypatch.setitem(sys.modules, "rdkit", mock_rdkit)
    monkeypatch.setitem(sys.modules, "rdkit.Chem", mock_rdkit.Chem)
    monkeypatch.setitem(sys.modules, "rdkit.Chem.inchi", mock_rdkit.Chem.inchi)

    ik, src = derive_inchikey(None, None, _PC362_SMILES)
    assert ik == _VALID_IK
    assert src == "smiles_derived"

def test_derive_returns_none_when_rdkit_absent():
    with patch.dict(sys.modules, {"rdkit": None, "rdkit.Chem": None, "rdkit.Chem.inchi": None}):
        ik, src = derive_inchikey(None, None, _PC362_SMILES)
    assert ik is None
    assert src == "none"

def test_derive_returns_none_on_invalid_smiles(monkeypatch):
    mock_rdkit = MagicMock()
    mock_rdkit.Chem.MolFromSmiles.return_value = None  # 無効 SMILES → None mol
    monkeypatch.setitem(sys.modules, "rdkit", mock_rdkit)
    monkeypatch.setitem(sys.modules, "rdkit.Chem", mock_rdkit.Chem)
    monkeypatch.setitem(sys.modules, "rdkit.Chem.inchi", mock_rdkit.Chem.inchi)

    ik, src = derive_inchikey(None, None, "INVALID_SMILES_XYZ")
    assert ik is None
    assert src == "none"

def test_database_identifier_takes_priority_over_smiles():
    ik, src = derive_inchikey(_VALID_IK, None, _PC362_SMILES)
    assert src == "database_identifier"
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_identity.py -q
```

期待: `ModuleNotFoundError: No module named 'lipidmix.mztab'`

- [ ] **Step 3: パッケージと `identity.py` を作成する**

`lipidmix/mztab/__init__.py`（空ファイル）を作成する。

`lipidmix/mztab/identity.py`:

```python
"""SME同定情報からのInChIKey導出（純関数）。

優先順位: database_identifier 正規表現一致 → inchi → smiles (RDKit) → None。
RDKit 非インストール時は smiles/inchi 経路が silent fallback する。
spec §23 参照。
"""
from __future__ import annotations
import re

_INCHIKEY_RE = re.compile(r'^[A-Z]{14}-[A-Z]{10}-[A-Z]$')


def derive_inchikey(
    database_identifier: str | None,
    inchi: str | None,
    smiles: str | None,
) -> tuple[str | None, str]:
    """SME フィールドから InChIKey を導出する。

    戻り値: (inchikey, source)。source は
    "database_identifier" / "inchi_derived" / "smiles_derived" / "none"。
    """
    if database_identifier:
        candidate = database_identifier.strip()
        if _INCHIKEY_RE.match(candidate):
            return candidate, "database_identifier"

    try:
        from rdkit import Chem
        from rdkit.Chem.inchi import MolToInchi, InchiToInchiKey
        if inchi:
            ik = InchiToInchiKey(inchi.strip())
            if ik:
                return ik, "inchi_derived"
        if smiles:
            mol = Chem.MolFromSmiles(smiles.strip())
            if mol:
                inchi_str = MolToInchi(mol)
                if inchi_str:
                    ik = InchiToInchiKey(inchi_str)
                    if ik:
                        return ik, "smiles_derived"
    except (ImportError, Exception):
        pass

    return None, "none"
```

- [ ] **Step 4: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_identity.py -q
```

期待: 9 tests passed

- [ ] **Step 5: コミット**

```bash
git add lipidmix/mztab/__init__.py lipidmix/mztab/identity.py tests/test_mztab_identity.py
git commit -m "feat: SMILES/InChI から InChIKey を導出する純関数を追加する"
```

---

### Task 3: mzTab-M 2.0 パーサ

**Files:**
- Create: `lipidmix/mztab/reader.py`
- Test: `tests/test_mztab_reader.py`

**Interfaces:**
- Produces: `parse_mztab(path: str | Path) -> dict`
  - 戻り値: `{"metadata": dict, "sections": {"SML": {...}, "SMF": {...}, "SME": {...}}, "warnings": list}`
  - 各 section: `{"header": list[str], "rows": list[dict], "warnings": list[str]}`
- Produces: `extract_abundance_matrix(parse_result: dict) -> tuple[np.ndarray, list[str], list[str]]`
  - 戻り値: `(matrix, sample_names, feature_ids)`、`matrix` shape `(n_features, n_samples)`、欠損は `np.nan`
- Produces: `get_assay_count(metadata: dict) -> int`

- [ ] **Step 1: テスト用 fixture 文字列を定義してテストを書く**

```python
# tests/test_mztab_reader.py
import math
import textwrap
from pathlib import Path
import pytest
from lipidmix.mztab.reader import parse_mztab, extract_abundance_matrix, get_assay_count

# 最小 mzTab-M 2.0 fixture（2 assay × 3 feature）
_MINIMAL_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\ttitle\tTest
    MTD\tdescription\tTest dataset
    MTD\tms_run[1]-location\tfile:///data/s1.raw
    MTD\tms_run[2]-location\tfile:///data/s2.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    MTD\tassay[2]-ms_run_ref\tms_run[2]
    MTD\tstudy_variable[1]-assay_refs\tassay[1]
    MTD\tstudy_variable[1]-description\tcontrol
    MTD\tstudy_variable[2]-assay_refs\tassay[2]
    MTD\tstudy_variable[2]-description\ttreated
    MTD\tquantification_method\t[MS, MS:1001829, label-free raw feature quantitation, ]
    MTD\tsmall_molecule-quantification_unit\t[PRIDE, PRIDE:0000429, Abundance, ]
    SML\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tbest_id_confidence_value
    SML\t1\tSMF:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tPC 36:2\tCCC\tInChI=1S/test\t0.95
    SML\t2\tSMF:2\tnull\tTG 54:3\tnull\tnull\t0.70
    SML\t3\tSMF:3\tBADFORMAT\tPE 34:1\tnull\tnull\t0.60
    SMF\tSMF_ID\tSML_ID_REFS\tchemical_name\tsmiles\tinchi\tdatabase_identifier\tcharge\tmz_exp\trt_mean\tabundance_assay[1]\tabundance_assay[2]
    SMF\t1\tSML:1\tPC 36:2\tCCC\tInChI=1S/test\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\t1\t758.5684\t5.23\t12345.6\t23456.7
    SMF\t2\tSML:2\tTG 54:3\tnull\tnull\tnull\t1\t896.7923\t8.45\t45678.9\tnull
    SMF\t3\tSML:3\tPE 34:1\tnull\tnull\tBADFORMAT\t-1\t716.5245\t3.10\tnull\t56789.0
    SEH\tSME_ID\tSMF_ID_REFS\tdatabase_identifier\tchemical_formula\tsmiles\tinchi
    SME\t1\tSMF:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tC42H80NO8P\tCCC\tInChI=1S/test
    SME\t2\tSMF:2\tnull\tC57H104O6\tnull\tnull\t
""")

_SME_TRAILING_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///data/s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tabundance_assay[1]
    SMF\t1\tSML:1\tnull\t9999.0
    SEH\tSME_ID\tSMF_ID_REFS\tdatabase_identifier
    SME\t1\tSMF:1\tnull\t
""")


@pytest.fixture
def minimal_file(tmp_path):
    p = tmp_path / "test.mzTab"
    p.write_text(_MINIMAL_MZTAB, encoding="utf-8")
    return p


@pytest.fixture
def trailing_file(tmp_path):
    p = tmp_path / "trailing.mzTab"
    p.write_text(_SME_TRAILING_MZTAB, encoding="utf-8")
    return p


def test_parse_metadata(minimal_file):
    result = parse_mztab(minimal_file)
    assert result["metadata"]["mzTab-version"] == "2.0.0-M"
    assert result["metadata"]["assay[1]-ms_run_ref"] == "ms_run[1]"

def test_parse_smf_header(minimal_file):
    result = parse_mztab(minimal_file)
    header = result["sections"]["SMF"]["header"]
    assert "SMF_ID" in header
    assert "abundance_assay[1]" in header

def test_parse_smf_rows(minimal_file):
    result = parse_mztab(minimal_file)
    rows = result["sections"]["SMF"]["rows"]
    assert len(rows) == 3
    assert rows[0]["SMF_ID"] == "1"
    assert rows[0]["abundance_assay[1]"] == "12345.6"
    assert rows[1]["abundance_assay[2]"] is None  # "null" → None

def test_parse_sme_rows(minimal_file):
    result = parse_mztab(minimal_file)
    rows = result["sections"]["SME"]["rows"]
    assert len(rows) == 2

def test_sme_trailing_column_normalized(trailing_file):
    result = parse_mztab(trailing_file)
    sme_warnings = result["sections"]["SME"]["warnings"]
    assert any("trailing" in w.lower() for w in sme_warnings)

def test_null_string_becomes_none(minimal_file):
    result = parse_mztab(minimal_file)
    rows = result["sections"]["SML"]["rows"]
    assert rows[1]["database_identifier"] is None

def test_get_assay_count(minimal_file):
    result = parse_mztab(minimal_file)
    assert get_assay_count(result["metadata"]) == 2

def test_extract_abundance_matrix(minimal_file):
    result = parse_mztab(minimal_file)
    matrix, samples, features = extract_abundance_matrix(result)
    assert matrix.shape == (3, 2)
    assert matrix[0, 0] == pytest.approx(12345.6)
    assert math.isnan(matrix[1, 1])
    assert math.isnan(matrix[2, 0])
    assert features == ["1", "2", "3"]
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_reader.py -q
```

期待: `ModuleNotFoundError: No module named 'lipidmix.mztab.reader'`

- [ ] **Step 3: `reader.py` を実装する**

`lipidmix/mztab/reader.py`:

```python
"""mzTab-M 2.0 テキスト形式パーサ（純関数）。

セクション識別規則:
  - MTD 行: メタデータ。tab 区切り [prefix, key, value]。
  - SML/SMF/SME の最初の行: ヘッダー（列名）。以降: データ行。
  - SEH / SFH が行頭のファイルは非標準。SEH→SME / SFH→SMF と読み替える。
  - COM 行: コメント。無視する。
  - "null" / 空文字列 → None に正規化する。
  - SME 行の末尾空欄（既知の MS-DIAL 問題）を除去しwarningを記録する。

spec §9 参照。
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# 非標準ヘッダー接頭辞の正規化マップ
_HEADER_PREFIX_MAP = {"SEH": "SME", "SFH": "SMF", "SLH": "SML"}
# データ行として認識する接頭辞
_DATA_PREFIXES = frozenset({"SML", "SMF", "SME"})
# abundance 列名パターン（命名揺れに対応）
_ABUNDANCE_RE = re.compile(r"abundance_assay\[(\d+)\]", re.IGNORECASE)
# assay MTD キーパターン
_ASSAY_RE = re.compile(r"^assay\[(\d+)\]-ms_run_ref$")


def _normalize_value(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    return None if s in ("null", "") else s


def parse_mztab(path: str | Path) -> dict:
    """mzTab-M 2.0 ファイルを解析し構造化辞書を返す。

    戻り値:
        {
          "metadata": {key: value},
          "sections": {
            "SML": {"header": [...], "rows": [...], "warnings": [...]},
            "SMF": {"header": [...], "rows": [...], "warnings": [...]},
            "SME": {"header": [...], "rows": [...], "warnings": [...]},
          },
          "warnings": [...],  # ファイル全体のwarning
        }
    """
    metadata: dict[str, str] = {}
    sections: dict[str, dict] = {}
    global_warnings: list[str] = []

    with open(path, encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            prefix = parts[0].strip()

            if prefix == "MTD":
                if len(parts) >= 3:
                    metadata[parts[1].strip()] = parts[2].strip()
                elif len(parts) == 2:
                    metadata[parts[1].strip()] = ""
                continue

            if prefix == "COM":
                continue

            # ヘッダー行（SFH / SEH / SLH は非標準だが存在する）
            canonical = _HEADER_PREFIX_MAP.get(prefix)
            if canonical:
                sections.setdefault(canonical, {"header": None, "rows": [], "warnings": []})
                sections[canonical]["header"] = [c.strip() for c in parts[1:]]
                continue

            # データ行
            if prefix in _DATA_PREFIXES:
                sec = sections.setdefault(prefix, {"header": None, "rows": [], "warnings": []})
                # ヘッダー未定義の場合、この行がヘッダー
                if sec["header"] is None:
                    sec["header"] = [c.strip() for c in parts[1:]]
                    continue
                header = sec["header"]
                values = [c.strip() for c in parts[1:]]
                # 末尾空欄の正規化（既知の MS-DIAL SME 問題）
                while values and values[-1] == "":
                    values.pop()
                    sec["warnings"].append(f"L{lineno}: trailing empty column removed")
                row = {}
                for i, col in enumerate(header):
                    raw = values[i] if i < len(values) else None
                    row[col] = _normalize_value(raw)
                sec["rows"].append(row)

    return {"metadata": metadata, "sections": sections, "warnings": global_warnings}


def get_assay_count(metadata: dict) -> int:
    """MTD から assay 数を返す。"""
    return sum(1 for k in metadata if _ASSAY_RE.match(k))


def extract_abundance_matrix(
    parse_result: dict,
) -> tuple[np.ndarray, list[str], list[str]]:
    """SMF 行列から abundance 値を numpy 配列に変換する。

    戻り値: (matrix, sample_names, feature_ids)
      matrix shape: (n_features, n_samples)、欠損は np.nan。
      sample_names: abundance 列名をアサイ番号昇順に並べた list。
      feature_ids: SMF_ID を行順に並べた list。
    """
    smf = parse_result["sections"].get("SMF", {})
    header = smf.get("header") or []
    rows = smf.get("rows") or []

    # abundance 列を番号昇順でソート
    abundance_cols = sorted(
        (col for col in header if _ABUNDANCE_RE.search(col)),
        key=lambda c: int(_ABUNDANCE_RE.search(c).group(1)),
    )
    feature_ids = [r.get("SMF_ID") or str(i) for i, r in enumerate(rows)]
    n_feat, n_samp = len(rows), len(abundance_cols)
    matrix = np.full((n_feat, n_samp), np.nan, dtype=float)
    for i, row in enumerate(rows):
        for j, col in enumerate(abundance_cols):
            val = row.get(col)
            if val is not None:
                try:
                    matrix[i, j] = float(val)
                except (ValueError, TypeError):
                    pass

    return matrix, abundance_cols, feature_ids
```

- [ ] **Step 4: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_reader.py -q
```

期待: 12 tests passed

- [ ] **Step 5: コミット**

```bash
git add lipidmix/mztab/reader.py tests/test_mztab_reader.py
git commit -m "feat: mzTab-M 2.0 テキストパーサを追加する"
```

---

### Task 4: 構造バリデータ

**Files:**
- Create: `lipidmix/mztab/validator.py`
- Test: `tests/test_mztab_validator.py`

**Interfaces:**
- Consumes: `parse_mztab()` の戻り値
- Produces: `validate_mztab(parse_result: dict) -> dict`
  - 戻り値: `{"ok": bool, "errors": list[str], "warnings": list[str]}`
- Produces: `detect_quantification_measure(parse_result: dict, filename: str) -> tuple[str | None, str]`
  - 戻り値: `(measure, confidence)` — measure は `"peak_height"` / `"peak_area_above_zero"` / None、confidence は `"verified"` / `"inferred"` / `"conflict"` / `"unknown"`

- [ ] **Step 1: テストを書く**

```python
# tests/test_mztab_validator.py
from lipidmix.mztab.validator import validate_mztab, detect_quantification_measure

_BASE = {
    "metadata": {
        "mzTab-version": "2.0.0-M",
        "mzTab-mode": "Complete",
        "mzTab-type": "Quantification",
        "assay[1]-ms_run_ref": "ms_run[1]",
    },
    "sections": {
        "SMF": {"header": ["SMF_ID", "abundance_assay[1]"], "rows": [{"SMF_ID": "1", "abundance_assay[1]": "100.0"}], "warnings": []},
    },
    "warnings": [],
}


def _make(overrides=None, sections_overrides=None):
    import copy
    r = copy.deepcopy(_BASE)
    if overrides:
        r["metadata"].update(overrides)
    if sections_overrides:
        r["sections"].update(sections_overrides)
    return r


def test_valid_passes():
    result = validate_mztab(_BASE)
    assert result["ok"] is True
    assert result["errors"] == []


def test_wrong_version_fails():
    result = validate_mztab(_make({"mzTab-version": "2.1.0-M"}))
    assert result["ok"] is False
    assert any("2.0.0-M" in e for e in result["errors"])


def test_missing_smf_section_fails():
    import copy
    r = copy.deepcopy(_BASE)
    r["sections"].pop("SMF")
    result = validate_mztab(r)
    assert result["ok"] is False
    assert any("SMF" in e for e in result["errors"])


def test_missing_assay_fails():
    result = validate_mztab(_make({"assay[1]-ms_run_ref": None}))
    assert result["ok"] is False


def test_sme_trailing_warning_propagated():
    r = _make(sections_overrides={
        "SME": {"header": ["SME_ID"], "rows": [], "warnings": ["L10: trailing empty column removed"]}
    })
    result = validate_mztab(r)
    assert any("trailing" in w.lower() for w in result["warnings"])


def test_detect_height_from_filename():
    measure, conf = detect_quantification_measure(
        _BASE, "Height_AlignmentResult_2026.mzTab"
    )
    assert measure == "peak_height"
    assert conf in ("verified", "inferred")


def test_detect_area_from_filename():
    measure, conf = detect_quantification_measure(
        _BASE, "Area_AlignmentResult_2026.mzTab"
    )
    assert measure == "peak_area_above_zero"
    assert conf in ("verified", "inferred")


def test_detect_unknown_from_generic_filename():
    measure, conf = detect_quantification_measure(_BASE, "result.mzTab")
    assert measure is None or conf == "unknown"
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_validator.py -q
```

- [ ] **Step 3: `validator.py` を実装する**

`lipidmix/mztab/validator.py`:

```python
"""mzTab-M 2.0 構造・定量種別バリデータ（純関数）。spec §8, §9 参照。"""
from __future__ import annotations
import re

_HEIGHT_RE = re.compile(r"\bheight\b", re.IGNORECASE)
_AREA_RE = re.compile(r"\barea\b", re.IGNORECASE)
_HEIGHT_PREFIX_RE = re.compile(r"^Height_", re.IGNORECASE)
_AREA_PREFIX_RE = re.compile(r"^Area_", re.IGNORECASE)
_SUPPORTED_VERSION = "2.0.0-M"


def validate_mztab(parse_result: dict) -> dict:
    """mzTab-M 2.0 の構造を検証する。

    戻り値: {"ok": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    meta = parse_result.get("metadata", {})
    sections = parse_result.get("sections", {})

    # バージョン検査
    version = meta.get("mzTab-version", "")
    if version != _SUPPORTED_VERSION:
        errors.append(
            f"mzTab-version が '{version}' です。'{_SUPPORTED_VERSION}' のみ対応しています。"
        )

    # 必須セクション
    if "SMF" not in sections or not sections["SMF"].get("rows"):
        errors.append("SMF セクションが存在しないか行が 0 件です（特徴量行列が空）。")

    # assay 定義
    has_assay = any(re.match(r"^assay\[\d+\]-ms_run_ref$", k) for k in meta)
    if not has_assay:
        errors.append("assay[] の定義が MTD にありません。")

    # SME 末尾空欄 warning を伝播
    for section_name, sec in sections.items():
        for w in sec.get("warnings", []):
            warnings.append(f"[{section_name}] {w}")

    # ファイル全体 warning
    warnings.extend(parse_result.get("warnings", []))

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


def detect_quantification_measure(
    parse_result: dict, filename: str
) -> tuple[str | None, str]:
    """ファイル名と MTD から定量種別を推定する。

    戻り値: (measure, confidence)
      measure: "peak_height" / "peak_area_above_zero" / None
      confidence: "verified" / "inferred" / "unknown" / "conflict"
    """
    meta = parse_result.get("metadata", {})
    mtd_unit = " ".join(
        v for k, v in meta.items()
        if "quantification_unit" in k or "quantification_method" in k
    )
    fname = filename

    height_in_fname = bool(_HEIGHT_PREFIX_RE.match(fname))
    area_in_fname = bool(_AREA_PREFIX_RE.match(fname))
    height_in_mtd = bool(_HEIGHT_RE.search(mtd_unit))
    area_in_mtd = bool(_AREA_RE.search(mtd_unit))

    if height_in_fname and area_in_fname:
        return None, "conflict"
    if height_in_fname:
        confidence = "verified" if height_in_mtd and not area_in_mtd else "inferred"
        return "peak_height", confidence
    if area_in_fname:
        confidence = "verified" if area_in_mtd and not height_in_mtd else "inferred"
        return "peak_area_above_zero", confidence
    if height_in_mtd and not area_in_mtd:
        return "peak_height", "inferred"
    if area_in_mtd and not height_in_mtd:
        return "peak_area_above_zero", "inferred"
    return None, "unknown"
```

- [ ] **Step 4: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_validator.py -q
```

期待: 9 tests passed

- [ ] **Step 5: コミット**

```bash
git add lipidmix/mztab/validator.py tests/test_mztab_validator.py
git commit -m "feat: mzTab-M 構造バリデータと定量種別推定を追加する"
```

---

### Task 5: `DatasetState` + `session.dataset` スロット

**Files:**
- Create: `lipidmix/mztab/dataset_state.py`
- Modify: `lipidmix/core/session_state.py`
- Test: `tests/test_dataset_state.py`

**Interfaces:**
- Consumes: `parse_mztab()`, `extract_abundance_matrix()`, `validate_mztab()`, `detect_quantification_measure()`, `derive_inchikey()`
- Produces: `DatasetState` クラス（フィールドは下記）
- Produces: `build_dataset_state(parse_result, filename, source_path) -> DatasetState`

- [ ] **Step 1: テストを書く**

```python
# tests/test_dataset_state.py
import math
import textwrap
import pytest
from lipidmix.mztab.reader import parse_mztab
from lipidmix.mztab.dataset_state import DatasetState, build_dataset_state
from lipidmix.core import session_state

_MZTAB_CONTENT = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tabundance_assay[1]
    SMF\t1\tSML:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tPC 36:2\tCCC\tInChI=1S/test\t12345.6
    SMF\t2\tSML:2\tnull\tTG 54:3\tnull\tnull\t0.0
""")


@pytest.fixture
def mztab_file(tmp_path):
    p = tmp_path / "Height_test.mzTab"
    p.write_text(_MZTAB_CONTENT, encoding="utf-8")
    return p


def test_build_dataset_state_creates_instance(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert isinstance(ds, DatasetState)


def test_dataset_state_source_format(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert ds.source_format == "mztab"


def test_dataset_state_feature_matrix_shape(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert ds.feature_matrix.shape == (2, 1)
    assert ds.feature_matrix[0, 0] == pytest.approx(12345.6)


def test_dataset_state_inchikey_from_database_identifier(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    f1 = ds.feature_metadata["1"]
    assert f1["inchikey"] == "IPCSVZSSVZVIGE-UHFFFAOYSA-N"
    assert f1["inchikey_source"] == "database_identifier"


def test_dataset_state_inchikey_none_when_absent(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    f2 = ds.feature_metadata["2"]
    assert f2["inchikey"] is None
    assert f2["inchikey_source"] == "none"


def test_dataset_state_inchikey_coverage(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    cov = ds.inchikey_coverage
    assert cov["total_features"] == 2
    assert cov["with_inchikey"] == 1
    assert cov["by_source"]["database_identifier"] == 1
    assert cov["by_source"]["none"] == 1


def test_session_has_dataset_slot():
    sess = session_state.AnalysisSession()
    assert hasattr(sess, "dataset")
    assert sess.dataset is None


def test_session_dataset_does_not_affect_arf_slot():
    sess = session_state.AnalysisSession()
    sess.dataset = "dummy"
    # ARF スロットはそのまま
    assert sess.arf.features is None
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q
```

- [ ] **Step 3: `dataset_state.py` を実装する**

`lipidmix/mztab/dataset_state.py`:

```python
"""DatasetState: mzTab-M を読んだ後の正準モデル。spec §11 参照。

session.arf の ARF 専用構造とは完全に独立している。session.dataset スロットへ格納する。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from lipidmix.mztab.identity import derive_inchikey
from lipidmix.mztab.reader import extract_abundance_matrix
from lipidmix.mztab.validator import detect_quantification_measure, validate_mztab


class DatasetState:
    """mzTab-M 読み込み後の正準モデル。多変量解析の共通入口。"""

    def __init__(self):
        self.source_format: str = "mztab"
        self.source_files: dict[str, str] = {}          # path -> sha256
        self.quantification_measure: str | None = None  # peak_height | peak_area_above_zero
        self.quantification_confidence: str | None = None
        self.feature_matrix: np.ndarray | None = None   # shape (n_features, n_samples)
        self.sample_names: list[str] = []               # abundance 列名（= assay 識別子）
        self.feature_ids: list[str] = []                # SMF_ID 列
        self.assay_metadata: dict = {}                  # assay_id -> MTD 情報
        self.feature_metadata: dict = {}                # smf_id -> {name, mz, rt, inchikey, ...}
        self.sml_rows: list[dict] | None = None
        self.sme_rows: list[dict] | None = None
        self.validation_result: dict = {}
        self.inchikey_coverage: dict = {}


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_dataset_state(
    parse_result: dict,
    filename: str,
    source_path: str | Path,
) -> DatasetState:
    """parse_mztab() の戻り値から DatasetState を構築する。"""
    ds = DatasetState()

    # ファイルハッシュ
    p = Path(source_path)
    if p.is_file():
        ds.source_files[str(p)] = _sha256(p)

    # バリデーション
    ds.validation_result = validate_mztab(parse_result)

    # 定量種別
    measure, confidence = detect_quantification_measure(parse_result, filename)
    ds.quantification_measure = measure
    ds.quantification_confidence = confidence

    # abundance 行列
    matrix, sample_names, feature_ids = extract_abundance_matrix(parse_result)
    ds.feature_matrix = matrix
    ds.sample_names = sample_names
    ds.feature_ids = feature_ids

    # SMF メタデータ + InChIKey 導出
    smf_rows = parse_result["sections"].get("SMF", {}).get("rows", [])
    by_source: dict[str, int] = {"database_identifier": 0, "inchi_derived": 0, "smiles_derived": 0, "none": 0}
    for row in smf_rows:
        fid = row.get("SMF_ID", "")
        ik, src = derive_inchikey(
            row.get("database_identifier"),
            row.get("inchi"),
            row.get("smiles"),
        )
        ds.feature_metadata[fid] = {
            "name": row.get("chemical_name"),
            "mz": _to_float(row.get("mz_exp")),
            "rt": _to_float(row.get("rt_mean")),
            "inchikey": ik,
            "inchikey_source": src,
            "smiles": row.get("smiles"),
            "inchi": row.get("inchi"),
        }
        by_source[src] = by_source.get(src, 0) + 1

    with_ik = sum(v for k, v in by_source.items() if k != "none")
    ds.inchikey_coverage = {
        "total_features": len(smf_rows),
        "with_inchikey": with_ik,
        "by_source": by_source,
    }

    # SML / SME 行
    ds.sml_rows = parse_result["sections"].get("SML", {}).get("rows")
    ds.sme_rows = parse_result["sections"].get("SME", {}).get("rows")

    # assay メタデータ
    meta = parse_result.get("metadata", {})
    import re
    for k, v in meta.items():
        m = re.match(r"^assay\[(\d+)\]-(.+)$", k)
        if m:
            aid = f"assay[{m.group(1)}]"
            ds.assay_metadata.setdefault(aid, {})[m.group(2)] = v

    return ds


def _to_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 4: `session_state.py` に `dataset` スロットを追加する**

`lipidmix/core/session_state.py` の `AnalysisSession.__init__` 末尾（`self.sections_seen` の後）に追記:

```python
        # --- mzTab-M / DatasetState スロット（session.arf とは独立） ---
        self.dataset = None  # DatasetState | None
```

- [ ] **Step 5: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_dataset_state.py -q
```

期待: 9 tests passed

- [ ] **Step 6: 全テストが通ることを確認（既存テストの回帰チェック）**

```
C:/Python314/python.exe -m pytest tests -q --tb=short
```

期待: 563+ tests passed、0 failures

- [ ] **Step 7: コミット**

```bash
git add lipidmix/mztab/dataset_state.py lipidmix/core/session_state.py tests/test_dataset_state.py
git commit -m "feat: DatasetState モデルと session.dataset スロットを追加する"
```

---

### Task 6: `dataset_load` / `dataset_status` MCP ツール

**Files:**
- Create: `lipidmix/tools/mztab_tools.py`
- Test: `tests/test_mztab_tools.py`

**Interfaces:**
- Consumes: `parse_mztab()`, `build_dataset_state()`, `mztab_error()`, `session_state.session.dataset`
- Produces: `dataset_load(mztab_path: str) -> str` — MCP ツール
- Produces: `dataset_status() -> str` — MCP ツール

- [ ] **Step 1: テストを書く**

```python
# tests/test_mztab_tools.py
import json
import textwrap
import pytest
from lipidmix.core import session_state

_CONTENT = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tabundance_assay[1]
    SMF\t1\tSML:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tPC 36:2\tnull\tnull\t12345.6
""")


@pytest.fixture(autouse=True)
def reset_session():
    session_state.session = session_state.AnalysisSession()
    yield
    session_state.session = session_state.AnalysisSession()


@pytest.fixture
def mztab_file(tmp_path):
    p = tmp_path / "Height_test.mzTab"
    p.write_text(_CONTENT, encoding="utf-8")
    return p


def test_dataset_load_success(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load
    result = dataset_load(str(mztab_file))
    assert "dataset_load" in result or "mzTab" in result
    assert session_state.session.dataset is not None


def test_dataset_load_sets_source_format(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load
    dataset_load(str(mztab_file))
    assert session_state.session.dataset.source_format == "mztab"


def test_dataset_load_missing_file():
    from lipidmix.tools.mztab_tools import dataset_load
    result = dataset_load("/nonexistent/path.mzTab")
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_NOT_FOUND"


def test_dataset_load_invalid_structure(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    bad = tmp_path / "bad.mzTab"
    bad.write_text("MTD\tmzTab-version\t3.0.0-M\n", encoding="utf-8")
    result = dataset_load(str(bad))
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_STRUCTURE_INVALID"


def test_dataset_status_no_state():
    from lipidmix.tools.mztab_tools import dataset_status
    result = dataset_status()
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_NOT_FOUND"


def test_dataset_status_after_load(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load, dataset_status
    dataset_load(str(mztab_file))
    result = dataset_status()
    # status は JSON 文字列または markdown 文字列
    assert "mztab" in result.lower() or "source_format" in result


def test_dataset_load_does_not_touch_arf_slot(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load
    dataset_load(str(mztab_file))
    assert session_state.session.arf.features is None
```

- [ ] **Step 2: テストが失敗することを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_tools.py -q
```

- [ ] **Step 3: `mztab_tools.py` を実装する**

`lipidmix/tools/mztab_tools.py`:

```python
"""mzTab-M 入口 MCP ツール: dataset_load, dataset_status。spec §12 参照。"""
from pathlib import Path

from mcp.types import ToolAnnotations

from lipidmix.core import session_state
from lipidmix.core.mcp_core import mcp
from lipidmix.core.mcp_errors import mztab_error
from lipidmix.core.serialization import json_payload
from lipidmix.mztab.reader import parse_mztab
from lipidmix.mztab.dataset_state import build_dataset_state

__all__ = ["dataset_load", "dataset_status"]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
          structured_output=False)
def dataset_load(mztab_path: str) -> str:
    """mzTab-M 2.0 ファイルを読み込んで正準状態（DatasetState）を作る。

    mztab_path: .mzTab ファイルへの絶対パス（list_data_files で取得できる）。
    成功すると session.dataset に DatasetState が格納され、dataset_status で内容を確認できる。
    ファイルが存在しない場合は MZTAB_NOT_FOUND、構造不正は MZTAB_STRUCTURE_INVALID を返す。
    既存の session.arf / .arf2 / .pai2 / .eic は変更しない。
    """
    p = Path(mztab_path)
    if not p.is_file():
        return mztab_error(
            "MZTAB_NOT_FOUND",
            f"mzTab-M ファイルが見つかりません: {mztab_path}",
        )

    parse_result = parse_mztab(p)
    validation = _validate_or_error(parse_result, p.name)
    if isinstance(validation, str):
        return validation  # error envelope

    ds = build_dataset_state(parse_result, p.name, p)
    session_state.session.dataset = ds

    lines = [
        f"## dataset_load 完了: {p.name}",
        f"- source_format: {ds.source_format}",
        f"- 特徴量数: {len(ds.feature_ids)}",
        f"- サンプル数: {len(ds.sample_names)}",
        f"- 定量種別: {ds.quantification_measure or '不明'} ({ds.quantification_confidence})",
        f"- 検証: {'OK' if ds.validation_result['ok'] else 'NG — ' + '; '.join(ds.validation_result['errors'])}",
        f"- InChIKey 付き: {ds.inchikey_coverage.get('with_inchikey', 0)}/{ds.inchikey_coverage.get('total_features', 0)} 件",
    ]
    if ds.validation_result.get("warnings"):
        lines.append(f"- 警告 {len(ds.validation_result['warnings'])} 件: " +
                     "; ".join(ds.validation_result["warnings"][:3]))
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def dataset_status() -> str:
    """現在の DatasetState の概要を返す。

    dataset_load を先に実行しておくこと。
    未実行の場合は MZTAB_NOT_FOUND を返す。
    """
    ds = session_state.session.dataset
    if ds is None:
        return mztab_error(
            "MZTAB_NOT_FOUND",
            "DatasetState がありません。先に dataset_load を実行してください。",
        )
    return json_payload({
        "source_format": ds.source_format,
        "source_files": list(ds.source_files.keys()),
        "quantification_measure": ds.quantification_measure,
        "quantification_confidence": ds.quantification_confidence,
        "n_features": len(ds.feature_ids),
        "n_samples": len(ds.sample_names),
        "validation": {
            "ok": ds.validation_result.get("ok"),
            "n_errors": len(ds.validation_result.get("errors", [])),
            "n_warnings": len(ds.validation_result.get("warnings", [])),
        },
        "inchikey_coverage": ds.inchikey_coverage,
    })


def _validate_or_error(parse_result: dict, filename: str) -> dict | str:
    """バリデーションを実行し、失敗ならエラーエンベロープ文字列を返す。成功なら validation dict。"""
    from lipidmix.mztab.validator import validate_mztab
    result = validate_mztab(parse_result)
    if not result["ok"]:
        return mztab_error(
            "MZTAB_STRUCTURE_INVALID",
            f"mzTab-M 構造不正 ({filename}): " + "; ".join(result["errors"]),
            {"errors": result["errors"], "warnings": result["warnings"]},
        )
    return result
```

- [ ] **Step 4: テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests/test_mztab_tools.py -q
```

期待: 7 tests passed

- [ ] **Step 5: コミット**

```bash
git add lipidmix/tools/mztab_tools.py tests/test_mztab_tools.py
git commit -m "feat: dataset_load/status MCP ツールを追加する"
```

---

### Task 7: サーバー登録・ワークフロードキュメント・腐敗防止テスト更新

**Files:**
- Modify: `server.py`
- Create: `docs/workflow/mztab.md`
- Modify: `tests/test_server_registration.py`
- Modify: `tests/test_workflow_docs.py`

**Interfaces:**
- Consumes: `lipidmix/tools/mztab_tools.py` の `__all__`（`dataset_load`, `dataset_status`）
- Produces: `server.mcp` が 42 ツールを公開する

- [ ] **Step 1: `server.py` に import を追加する**

`server.py` の最後の `from` import 行（`from lipidmix.tools.dataset import *` の直後）に追加:

```python
from lipidmix.tools.mztab_tools import *  # dataset_load, dataset_status
```

- [ ] **Step 2: ワークフロードキュメントを作成する**

`docs/workflow/mztab.md`:

```markdown
# mzTab-M ツール呼び出し連鎖

## dataset_load

1. `lipidmix/tools/mztab_tools.py` `dataset_load()`
2. └─ `lipidmix/mztab/reader.py` `parse_mztab()`
3. └─ `lipidmix/mztab/validator.py` `validate_mztab()`
4. └─ `lipidmix/mztab/validator.py` `detect_quantification_measure()`
5. └─ `lipidmix/mztab/dataset_state.py` `build_dataset_state()`
6.    └─ `lipidmix/mztab/reader.py` `extract_abundance_matrix()`
7.    └─ `lipidmix/mztab/identity.py` `derive_inchikey()`

## dataset_status

1. `lipidmix/tools/mztab_tools.py` `dataset_status()`
```

- [ ] **Step 3: `test_server_registration.py` を更新する**

`EXPECTED_TOOLS` リストに以下の 2 行を追加（sorted 内に挿入）:

```python
    "dataset_load",
    "dataset_status",
```

- [ ] **Step 4: `test_workflow_docs.py` を更新する**

`IN_SCOPE` 辞書に追加:

```python
    "mztab.md": ("dataset_load", "dataset_status"),
```

`test_scope_totals_match_registered_tool_count` の末尾アサーションを更新:

```python
        self.assertEqual(sum(len(v) for v in IN_SCOPE.values()), 31)
```

- [ ] **Step 5: 全テストが通ることを確認**

```
C:/Python314/python.exe -m pytest tests -q --tb=short
```

期待: 全テスト passed（565+ 件）、新規テストを含めて 0 failures

- [ ] **Step 6: コミット**

```bash
git add server.py docs/workflow/mztab.md tests/test_server_registration.py tests/test_workflow_docs.py
git commit -m "feat: mzTab-M ツールをサーバーに登録し腐敗防止テストを更新する"
```

---

## 完了条件チェックリスト

- [ ] `C:/Python314/python.exe -m pytest tests -q` が全件 passed
- [ ] `dataset_load("/path/to/Height_xxx.mzTab")` が成功し `session.dataset` に値が入る
- [ ] `dataset_status()` が `inchikey_coverage` を含む JSON を返す
- [ ] 存在しないパスで `MZTAB_NOT_FOUND` を返す
- [ ] 2.1 版ファイルで `MZTAB_STRUCTURE_INVALID` を返す
- [ ] `session.arf.features` が dataset_load 後も `None` のまま
- [ ] `test_scope_totals_match_registered_tool_count` が 31 で通る
