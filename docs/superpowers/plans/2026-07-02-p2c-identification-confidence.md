# P2c Identification Confidence / Standardization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add offline lipid-identity standardization (GOSLIN name normalization, bundled RefMet/LIPID MAPS mapping, MSI confidence-level heuristic) and expand the adduct/element tables, then surface them in `verify_peak_annotation` and a new `arf2_annotate_identities` tool.

**Architecture:** A new MCP-independent pure-logic module `lipid_identity.py`, bundled reference tables under `reference/`, an expansion of `peak_verification.py`'s adduct/element tables, and integration into the existing `verify_peak_annotation` dossier plus a batch ARF2 annotation tool.

**Tech Stack:** Python 3; `pygoslin` (offline, pure-Python) for name parsing; existing `peak_verification.py`.

## Global Constraints

- Everything offline: no network calls. `pygoslin` and bundled TSV tables only.
- `lipid_identity.py` must be MCP-independent and unit-testable.
- MSI level output must be labelled a heuristic and must never assert Level 1 (authentic-standard confirmation).
- Bundled mapping coverage is partial; unmatched names return `matched=False` with a caveat, never a guess.
- Backward compatible: `verify_peak_annotation`'s existing keys are unchanged; new data goes under a new `identity_normalization` block.
- Multiply-charged adducts require charge-aware m/z: `m/z = (neutral + shift) / |charge|`. The existing singly-charged entries must keep identical numeric behavior.
- Run tests with: `python -m unittest discover -s tests -t .`
- Commit after each task with the shown message.

---

### Task 1: Expand adduct/element tables in `peak_verification.py`

**Files:**
- Modify: `peak_verification.py:16-26` (ELEMENT_MASSES), `peak_verification.py:54-63` (ADDUCT_SHIFTS), `peak_verification.py:66-71` (adduct_mz)
- Test: `tests/test_peak_verification.py` (extend existing)

**Interfaces:**
- Consumes: nothing new.
- Produces: extended `ADDUCT_SHIFTS` now stores `(sign, shift, charge)`; `adduct_mz(neutral_mass, adduct)` divides by `charge`. New adducts: `[2M-H]-`, `[M+FA-H]-` (alias of `[M+HCOO]-`), `[M-2H]2-`. New elements: `D` (²H), `F`, `Br`, `C13`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_peak_verification.py
import peak_verification as pv


class TestExpandedTables(unittest.TestCase):
    def test_dimer_adduct_mz(self):
        # [2M-H]- : (2*neutral - proton) / 1
        neutral = 100.0
        mz = pv.adduct_mz(neutral, "[2M-H]-")
        self.assertAlmostEqual(mz, 2 * 100.0 - pv.PROTON_MASS, places=4)

    def test_doubly_charged_divides(self):
        neutral = 800.0
        mz = pv.adduct_mz(neutral, "[M-2H]2-")
        self.assertAlmostEqual(mz, (800.0 - 2 * pv.PROTON_MASS) / 2, places=4)

    def test_new_elements_present(self):
        for el in ("D", "F", "Br", "C13"):
            self.assertIn(el, pv.ELEMENT_MASSES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_peak_verification.TestExpandedTables -v`
Expected: FAIL — `[2M-H]-` returns None (unknown adduct) / new elements missing.

- [ ] **Step 3: Write minimal implementation**

Extend `ELEMENT_MASSES` (add entries):

```python
    "D": 2.0141017779,
    "F": 18.9984031627,
    "Br": 78.9183376,
    "C13": 13.0033548378,
```

Replace `ADDUCT_SHIFTS` values with 3-tuples `(sign, shift, charge)` and add multimers/doubly-charged. The `n_mol` multiplier for dimers is handled in `adduct_mz`, so encode dimers with a 4th field `n_mol`:

```python
# ADDUCT_SHIFTS: adduct -> (sign, shift, charge, n_mol)
ADDUCT_SHIFTS: dict[str, tuple[str, float, int, int]] = {
    "[M+H]+": ("+", PROTON_MASS, 1, 1),
    "[M+NH4]+": ("+", 18.03382555, 1, 1),
    "[M+Na]+": ("+", 22.9892207, 1, 1),
    "[M-H2O+H]+": ("+", -17.00328823, 1, 1),
    "[M-H]-": ("-", -1.00727646, 1, 1),
    "[M+HCOO]-": ("-", 44.99820286, 1, 1),
    "[M+FA-H]-": ("-", 44.99820286, 1, 1),
    "[M+CH3COO]-": ("-", 59.01385292, 1, 1),
    "[M+Cl]-": ("-", 34.96940129, 1, 1),
    "[2M-H]-": ("-", -1.00727646, 1, 2),
    "[M-2H]2-": ("-", -2 * PROTON_MASS, 2, 1),
}
```

Update `adduct_mz` to use the tuple:

```python
def adduct_mz(neutral_mass: float, adduct: str) -> float | None:
    """中性質量とアダクトから観測 m/z を返す。未知アダクトは None。

    m/z = (n_mol * neutral + shift) / charge。多量体・多価に対応。
    """
    entry = ADDUCT_SHIFTS.get(adduct)
    if entry is None:
        return None
    sign, shift, charge, n_mol = entry
    return (n_mol * neutral_mass + shift) / charge
```

Update any unpacking of `ADDUCT_SHIFTS[...]` elsewhere: `adduct_consistency` reads `entry[0]` (sign) — that index is unchanged, so it still works. Verify by search: `grep -n "ADDUCT_SHIFTS" peak_verification.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_peak_verification -v`
Expected: PASS (existing + 3 new). If an existing test unpacked the 2-tuple, fix it to the new tuple shape.

- [ ] **Step 5: Commit**

```bash
git add peak_verification.py tests/test_peak_verification.py
git commit -m "feat(p2c): expand adduct (dimer/doubly-charged) and element tables"
```

---

### Task 2: GOSLIN name normalization

**Files:**
- Create: `lipid_identity.py`
- Modify: `requirements.txt` (add `pygoslin`)
- Test: `tests/test_lipid_identity.py`

**Interfaces:**
- Produces: `normalize_lipid_name(name: str) -> dict` with keys `parse_ok` (bool), `normalized` (str|None), `level` (str|None), `lipid_maps_category` (str|None), `error` (str|None). Uses `pygoslin`; if `pygoslin` is not importable, returns `parse_ok=False, error="pygoslin unavailable"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lipid_identity.py
import unittest
import lipid_identity as li


class TestNormalizeLipidName(unittest.TestCase):
    def test_basic_pc_parses(self):
        out = li.normalize_lipid_name("PC 34:1")
        # pygoslin should parse; if unavailable, parse_ok False but no crash
        self.assertIn("parse_ok", out)
        if out["parse_ok"]:
            self.assertTrue(out["normalized"].upper().startswith("PC"))

    def test_ether_o_vs_p_distinguished(self):
        o = li.normalize_lipid_name("PC O-34:1")
        p = li.normalize_lipid_name("PC P-34:0")
        if o["parse_ok"] and p["parse_ok"]:
            self.assertNotEqual(o["normalized"], p["normalized"])

    def test_garbage_returns_parse_error_not_crash(self):
        out = li.normalize_lipid_name("not a lipid ###")
        self.assertFalse(out["parse_ok"])
        self.assertIsNotNone(out.get("error"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_lipid_identity -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lipid_identity'`.

- [ ] **Step 3: Write minimal implementation**

Install dependency first: `python -m pip install pygoslin`.

```python
# lipid_identity.py
"""脂質同定の標準化・信頼度（純ロジック層、MCP 非依存、オフライン）。

GOSLIN による脂質名正規化、同梱表による RefMet/LIPID MAPS ID 付与、MSI レベル推定。
"""

from __future__ import annotations


def normalize_lipid_name(name: str) -> dict:
    """脂質ショートハンド名を GOSLIN で正規化する（オフライン）。

    pygoslin が無い/解析不能でも例外を投げず parse_ok=False を返す。
    """
    result = {"parse_ok": False, "normalized": None, "level": None,
              "lipid_maps_category": None, "error": None}
    if not name or not str(name).strip():
        result["error"] = "empty name"
        return result
    try:
        from pygoslin.parser.Parser import LipidParser
    except Exception as exc:  # pygoslin 未導入
        result["error"] = f"pygoslin unavailable: {exc}"
        return result
    try:
        lipid = LipidParser().parse(str(name).strip())
        result["parse_ok"] = True
        result["normalized"] = lipid.get_lipid_string()
        result["level"] = str(getattr(lipid, "level", "") or "") or None
        try:
            result["lipid_maps_category"] = lipid.lipid.headgroup.lipid_category.name
        except Exception:
            result["lipid_maps_category"] = None
    except Exception as exc:
        result["error"] = f"parse error: {exc}"
    return result
```

Note: `pygoslin`'s API surface may vary by version; the `get_lipid_string()` call is the stable public method. If the installed version differs, adapt inside this function only (interface stays the same).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_lipid_identity -v`
Expected: PASS (3 tests). Tests are written to pass whether or not pygoslin is installed.

- [ ] **Step 5: Commit**

```bash
git add lipid_identity.py requirements.txt tests/test_lipid_identity.py
git commit -m "feat(p2c): add GOSLIN offline lipid-name normalization"
```

---

### Task 3: Bundled RefMet / LIPID MAPS mapping tables

**Files:**
- Create: `reference/lipidmaps_classes.tsv`
- Create: `reference/refmet_map.tsv`
- Modify: `lipid_identity.py`
- Test: `tests/test_lipid_identity.py`

**Interfaces:**
- Produces: `load_reference_tables(reference_dir="reference") -> dict` and `map_to_reference(class_token, tables) -> dict` with keys `matched` (bool), `lipid_maps_category`, `lipid_maps_main_class`, `refmet_name`, `caveat` (str|None).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_lipid_identity.py
import os


class TestReferenceMapping(unittest.TestCase):
    def setUp(self):
        self.tables = li.load_reference_tables("reference")

    def test_known_class_maps(self):
        out = li.map_to_reference("pc", self.tables)
        self.assertTrue(out["matched"])
        self.assertEqual(out["lipid_maps_category"], "GP")

    def test_unknown_class_has_caveat(self):
        out = li.map_to_reference("zzz", self.tables)
        self.assertFalse(out["matched"])
        self.assertIsNotNone(out["caveat"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_lipid_identity.TestReferenceMapping -v`
Expected: FAIL with `AttributeError: module 'lipid_identity' has no attribute 'load_reference_tables'`.

- [ ] **Step 3: Write minimal implementation**

Create `reference/lipidmaps_classes.tsv` (curated subset; columns: `class_token	lipid_maps_category	lipid_maps_main_class`):

```tsv
class_token	lipid_maps_category	lipid_maps_main_class
fa	FA	Fatty Acids and Conjugates
pc	GP	Glycerophosphocholines
lpc	GP	Glycerophosphocholines
pe	GP	Glycerophosphoethanolamines
lpe	GP	Glycerophosphoethanolamines
pg	GP	Glycerophosphoglycerols
pi	GP	Glycerophosphoinositols
ps	GP	Glycerophosphoserines
tg	GL	Triradylglycerols
dg	GL	Diradylglycerols
mg	GL	Monoradylglycerols
cer	SP	Ceramides
sm	SP	Phosphosphingolipids
hexcer	SP	Neutral glycosphingolipids
ce	ST	Sterol esters
che	ST	Sterol esters
```

Create `reference/refmet_map.tsv` (columns: `class_token	refmet_name`):

```tsv
class_token	refmet_name
fa	Fatty acids
pc	Phosphatidylcholines
lpc	Lysophosphatidylcholines
pe	Phosphatidylethanolamines
lpe	Lysophosphatidylethanolamines
pg	Phosphatidylglycerols
pi	Phosphatidylinositols
ps	Phosphatidylserines
tg	Triacylglycerols
dg	Diacylglycerols
mg	Monoacylglycerols
cer	Ceramides
sm	Sphingomyelins
hexcer	Hexosylceramides
ce	Cholesteryl esters
che	Cholesteryl esters
```

Add loaders to `lipid_identity.py`:

```python
# add to lipid_identity.py (top: `from pathlib import Path`)
from pathlib import Path


def _read_tsv(path: Path) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            values = line.rstrip("\n").split("\t")
            if len(values) == len(header):
                rows.append(dict(zip(header, values)))
    return rows


def load_reference_tables(reference_dir="reference") -> dict:
    base = Path(reference_dir)
    lm = {r["class_token"]: r for r in _read_tsv(base / "lipidmaps_classes.tsv")}
    rm = {r["class_token"]: r for r in _read_tsv(base / "refmet_map.tsv")}
    return {"lipidmaps": lm, "refmet": rm}


def map_to_reference(class_token, tables) -> dict:
    token = (class_token or "").strip().lower()
    lm = tables.get("lipidmaps", {}).get(token)
    rm = tables.get("refmet", {}).get(token)
    if not lm and not rm:
        return {"matched": False, "lipid_maps_category": None,
                "lipid_maps_main_class": None, "refmet_name": None,
                "caveat": f"クラス '{class_token}' は同梱マッピング表に無いため ID 未付与。"}
    return {
        "matched": True,
        "lipid_maps_category": lm["lipid_maps_category"] if lm else None,
        "lipid_maps_main_class": lm["lipid_maps_main_class"] if lm else None,
        "refmet_name": rm["refmet_name"] if rm else None,
        "caveat": None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_lipid_identity.TestReferenceMapping -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add reference/ lipid_identity.py tests/test_lipid_identity.py
git commit -m "feat(p2c): add bundled RefMet/LIPID MAPS class mapping tables"
```

---

### Task 4: MSI confidence-level heuristic

**Files:**
- Modify: `lipid_identity.py`
- Test: `tests/test_lipid_identity.py`

**Interfaces:**
- Produces: `msi_level(*, name, ontology, has_msms, mass_error_band, adduct_band) -> dict` with keys `level` (2/3/4), `label` (str), `rationale` (str), `heuristic` (True). Never returns 1.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_lipid_identity.py
class TestMsiLevel(unittest.TestCase):
    def test_msms_plus_mass_ok_is_level2(self):
        out = li.msi_level(name="PC 34:1", ontology="PC", has_msms=True,
                           mass_error_band="PASS", adduct_band="PASS")
        self.assertEqual(out["level"], 2)
        self.assertTrue(out["heuristic"])

    def test_class_only_is_level3(self):
        out = li.msi_level(name="", ontology="PC", has_msms=False,
                           mass_error_band="UNKNOWN", adduct_band="UNKNOWN")
        self.assertEqual(out["level"], 3)

    def test_unknown_is_level4(self):
        out = li.msi_level(name="", ontology="", has_msms=False,
                           mass_error_band="UNKNOWN", adduct_band="UNKNOWN")
        self.assertEqual(out["level"], 4)

    def test_never_level1(self):
        out = li.msi_level(name="PC 34:1", ontology="PC", has_msms=True,
                           mass_error_band="PASS", adduct_band="PASS")
        self.assertNotEqual(out["level"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_lipid_identity.TestMsiLevel -v`
Expected: FAIL with `AttributeError: module 'lipid_identity' has no attribute 'msi_level'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to lipid_identity.py
def msi_level(*, name, ontology, has_msms, mass_error_band, adduct_band) -> dict:
    """決定論的シグナルから MSI 同定信頼度レベルを推定する（ヒューリスティック）。

    Level 1（標準品照合）は主張しない。
    - 2: 名称あり かつ MS/MS取得 かつ 精密質量整合（putative annotated compound）
    - 3: クラス（ontology）は分かるが上記を満たさない（putative class）
    - 4: 名称もクラスも無い（unknown）
    """
    has_name = bool(name and str(name).strip())
    has_class = bool(ontology and str(ontology).strip() and str(ontology).strip() != "Unknown")
    mass_ok = mass_error_band == "PASS"
    adduct_ok = adduct_band in ("PASS", "UNKNOWN")  # FAIL は同定を疑う

    if has_name and has_msms and mass_ok and adduct_ok:
        return {"level": 2, "label": "putative annotated compound",
                "rationale": "名称あり＋MS/MS取得＋精密質量整合。標準品照合ではないため Level 2。",
                "heuristic": True}
    if has_class:
        return {"level": 3, "label": "putative class-level",
                "rationale": "クラス（ontology）は判別できるが MS/MS または質量整合が不十分。",
                "heuristic": True}
    return {"level": 4, "label": "unknown",
            "rationale": "名称・クラスとも無し。", "heuristic": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_lipid_identity.TestMsiLevel -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add lipid_identity.py tests/test_lipid_identity.py
git commit -m "feat(p2c): add MSI confidence-level heuristic (never asserts level 1)"
```

---

### Task 5: `build_identity_block` aggregator

**Files:**
- Modify: `lipid_identity.py`
- Test: `tests/test_lipid_identity.py`

**Interfaces:**
- Consumes: `normalize_lipid_name`, `load_reference_tables`, `map_to_reference`, `msi_level`, and `peak_verification.extract_class_token`.
- Produces: `build_identity_block(feature: dict, tables: dict, *, mass_error_band, adduct_band) -> dict` combining GOSLIN normalization, reference mapping, and MSI level. `feature` uses the pai2/arf2 keys `name`, `ontology`, `has_msms`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_lipid_identity.py
class TestIdentityBlock(unittest.TestCase):
    def test_block_has_all_sections(self):
        tables = li.load_reference_tables("reference")
        feat = {"name": "PC 34:1", "ontology": "PC", "has_msms": True}
        block = li.build_identity_block(feat, tables,
                                        mass_error_band="PASS", adduct_band="PASS")
        self.assertIn("goslin", block)
        self.assertIn("reference", block)
        self.assertIn("msi", block)
        self.assertEqual(block["msi"]["level"], 2)
        self.assertTrue(block["reference"]["matched"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_lipid_identity.TestIdentityBlock -v`
Expected: FAIL with `AttributeError: module 'lipid_identity' has no attribute 'build_identity_block'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to lipid_identity.py (top: `import peak_verification as pv`)
import peak_verification as pv


def build_identity_block(feature, tables, *, mass_error_band, adduct_band):
    """1 feature の同定標準化ブロック（GOSLIN + reference + MSI）を組み立てる。"""
    name = feature.get("name") or ""
    ontology = feature.get("ontology") or ""
    has_msms = bool(feature.get("has_msms"))
    class_token = pv.extract_class_token(name, ontology)
    goslin = normalize_lipid_name(name) if name.strip() else {
        "parse_ok": False, "normalized": None, "level": None,
        "lipid_maps_category": None, "error": "no name"}
    reference = map_to_reference(class_token, tables)
    msi = msi_level(name=name, ontology=ontology, has_msms=has_msms,
                    mass_error_band=mass_error_band, adduct_band=adduct_band)
    return {"class_token": class_token, "goslin": goslin,
            "reference": reference, "msi": msi}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_lipid_identity.TestIdentityBlock -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add lipid_identity.py tests/test_lipid_identity.py
git commit -m "feat(p2c): add build_identity_block aggregator"
```

---

### Task 6: Integrate into `verify_peak_annotation` + `arf2_annotate_identities` + docs

**Files:**
- Modify: `server.py:1145-1210` (`_build_verification_dossier`) and add `@mcp.tool() arf2_annotate_identities`
- Modify: `requirements.txt` (ensure `pygoslin`), `README.md`, `docs/output_format.md`, `docs/HISTRY.md`, `docs/task.md`
- Test: `tests/test_lipid_identity_tools.py`

**Interfaces:**
- Consumes: `lipid_identity.build_identity_block`, `lipid_identity.load_reference_tables`; existing dossier's `mass_error`/`adduct_check` bands.
- Produces: `verify_peak_annotation` dossier gains `identity_normalization` block; new tool `arf2_annotate_identities(file_path=None, max_rows=50) -> str` returning per-spot normalized identity + MSI level for the loaded ARF2 catalog.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lipid_identity_tools.py
import json
import unittest
import server


class TestVerifyHasIdentityBlock(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_dossier_includes_identity_normalization(self):
        feat = {"id": 1, "name": "PC 34:1", "ontology": "PC",
                "formula": "C42H82NO8P", "adduct": "[M+HCOO]-",
                "m/z": 850.0, "ion_mode": "Negative", "has_msms": True,
                "time": {"rt": 5.0}}
        vocab = {}
        dossier = server._build_verification_dossier(feat, vocab)
        self.assertIn("identity_normalization", dossier)
        self.assertIn("msi", dossier["identity_normalization"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_lipid_identity_tools -v`
Expected: FAIL — `identity_normalization` not in dossier.

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add `import lipid_identity` at top and a module-level cached table load:

```python
_IDENTITY_TABLES = None


def _identity_tables():
    global _IDENTITY_TABLES
    if _IDENTITY_TABLES is None:
        _IDENTITY_TABLES = lipid_identity.load_reference_tables()
    return _IDENTITY_TABLES
```

In `_build_verification_dossier`, after `adduct_check` and `mass_error` are computed and before the return, add:

```python
    identity_block = lipid_identity.build_identity_block(
        feat, _identity_tables(),
        mass_error_band=mass_error["band"],
        adduct_band=adduct_check["band"],
    )
```

Add `"identity_normalization": identity_block,` to the returned dossier dict (top level, alongside `analytical_checks` and `biological_plausibility`).

Add the batch ARF2 tool (after `arf2_parser`):

```python
@mcp.tool()
def arf2_annotate_identities(file_path: str | None = None, max_rows: int = 50) -> str:
    """ロード中/指定の ARF2 スポット注釈を GOSLIN 正規化・RefMet/LIPID MAPS ID・
    MSI レベルで一括標準化して返す（オフライン、上位 max_rows 件）。"""
    path = resolve_arf2_file_path(file_path)
    if not path:
        return json.dumps({"status": "error", "message": ".arf2 が見つかりません。"},
                          ensure_ascii=False, indent=2)
    from test_arf2 import deserialize as arf2_deserialize, extract_arf2_data
    with open(path, "rb") as fh:
        spots = extract_arf2_data(arf2_deserialize(io.BytesIO(fh.read())))
    tables = _identity_tables()
    rows = []
    for spot in spots[:max_rows]:
        feat = {"name": spot.get("Name") or "", "ontology": spot.get("Ontology") or "",
                "has_msms": False}
        # ARF2 に MS/MS 取得フラグは無いため has_msms=False（MSI は保守的にクラス上限）
        block = lipid_identity.build_identity_block(
            feat, tables, mass_error_band="UNKNOWN", adduct_band="UNKNOWN")
        rows.append({"MasterAlignmentID": spot.get("MasterAlignmentID"),
                     "name": feat["name"], "normalized": block["goslin"]["normalized"],
                     "refmet": block["reference"]["refmet_name"],
                     "lipid_maps_category": block["reference"]["lipid_maps_category"],
                     "msi_level": block["msi"]["level"]})
    return json.dumps({"status": "success", "count": len(rows), "rows": rows},
                      ensure_ascii=False, indent=2)
```

Then update docs:
- `requirements.txt`: ensure `pygoslin` is listed.
- `README.md`: add `arf2_annotate_identities(...)` to the tool list; note `verify_peak_annotation` now returns an `identity_normalization` block.
- `docs/output_format.md`: add `## 13. 同定信頼度・標準化（P2c）` describing GOSLIN normalization, bundled RefMet/LIPID MAPS mapping (partial coverage caveat), and the MSI-level heuristic (never level 1).
- `docs/HISTRY.md`: prepend a dated P2c entry.
- `docs/task.md`: add `T15. 同定信頼度・標準化（P2c）` as DONE.

- [ ] **Step 4: Run the full test suite**

Run: `python -m unittest discover -s tests -t .`
Expected: PASS (all tests: P2a + P2b + P2c).

- [ ] **Step 5: Commit**

```bash
git add server.py requirements.txt README.md docs/output_format.md docs/HISTRY.md docs/task.md tests/test_lipid_identity_tools.py
git commit -m "feat(p2c): surface identity normalization in verify + arf2_annotate_identities; docs"
```

---

## Self-Review Notes

- **Spec coverage:** adduct/element expansion (§5.4, Task 1), GOSLIN normalization (§5.1, Task 2), bundled RefMet/LIPID MAPS mapping (§5.2, Task 3), MSI heuristic (§5.3, Task 4), aggregator (§5.5, Task 5), `verify_peak_annotation` integration + `arf2_annotate_identities` + docs (§5.5/§6, Task 6).
- **Offline guarantee:** `pygoslin` is pure-Python offline; mapping tables are bundled TSV; no network calls anywhere. Tests pass with or without pygoslin installed (Task 2 tests guard on `parse_ok`).
- **Out of scope (per spec §7):** CCS/RT reference matching, isotope-pattern matching, online ID resolution.
- **Cross-module reuse:** MSI heuristic consumes bands from the existing `peak_verification` checks (already in the dossier), avoiding duplicate mass/adduct logic.
- **Independence:** P2c is orthogonal to P2a/P2b and can be implemented in any order relative to them; it only depends on the existing `peak_verification.py` and `verify_peak_annotation`.
```
