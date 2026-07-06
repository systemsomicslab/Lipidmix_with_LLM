# Peak Annotation Biochemical Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `verify_peak_annotation` MCP tool that judges whether a single peak's annotation is biochemically defensible, by combining deterministic analytical checks (mass error, adduct/ion-mode consistency) with knowledge-note material for the LLM's biological-plausibility verdict.

**Architecture:** A new pure-logic module `peak_verification.py` (no MCP dependency, fully unit-testable, mirroring `knowledge_store.py`) holds formula/mass/adduct math and class-token helpers. A new `@mcp.tool()` in `server.py` resolves the target peak from `session.filtered_features`, runs the deterministic checks, gathers related knowledge slugs via the existing `knowledge_store`, and returns a structured JSON dossier. The tool deliberately omits an overall verdict — the LLM renders it.

**Tech Stack:** Python 3.11+, `unittest` (existing test style), FastMCP (`server.py`), existing `test_pai2.py` / `knowledge_store.py` modules.

## Global Constraints

- Target dataset is the loaded **pai2** feature set: `server.session.filtered_features` (list of dicts). Feature dict keys used: `"id"`, `"name"`, `"ontology"`, `"formula"`, `"adduct"`, `"m/z"`, `"ion_mode"` (a `test_pai2.IonMode` enum, has `.name`), `"time"` (dict with `"rt"`), `"S/N"`.
- The tool returns a JSON **string** via `json.dumps(..., ensure_ascii=False, indent=2)`.
- Deterministic sub-scores use `band` values exactly: `"PASS"`, `"BORDERLINE"`, `"FAIL"`, `"UNKNOWN"`.
- ppm bands: `abs(ppm) <= 5 → PASS`, `<= 10 → BORDERLINE`, `> 10 → FAIL`. Missing/unknown formula or adduct → `UNKNOWN` (never raise to caller).
- Proton mass constant: `1.00727646`. Electron mass: `0.00054858`.
- No new third-party dependencies. Tests use `unittest` (run with `pytest`), matching `tests/test_objective_tools.py`.
- Do not add an overall verdict field to the dossier; the LLM decides.

---

### Task 1: Formula parsing and monoisotopic mass

**Files:**
- Create: `peak_verification.py`
- Test: `tests/test_peak_verification.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ELEMENT_MASSES: dict[str, float]` — monoisotopic masses.
  - `PROTON_MASS: float = 1.00727646`, `ELECTRON_MASS: float = 0.00054858`.
  - `parse_formula(formula: str) -> dict[str, int]` — element→count; raises `ValueError` on empty/None.
  - `monoisotopic_mass(counts: dict[str, int]) -> float` — raises `KeyError` on unknown element.

- [ ] **Step 1: Write the failing test**

Create `tests/test_peak_verification.py`:

```python
"""peak_verification 純ロジックの検証。"""

import unittest

import peak_verification as pv


class FormulaMassTests(unittest.TestCase):
    def test_parse_formula_multi_element_and_multi_digit(self):
        self.assertEqual(
            pv.parse_formula("C42H82NO8P"),
            {"C": 42, "H": 82, "N": 1, "O": 8, "P": 1},
        )

    def test_parse_formula_single_atom_defaults_to_one(self):
        self.assertEqual(pv.parse_formula("CH4"), {"C": 1, "H": 4})

    def test_parse_formula_empty_raises(self):
        with self.assertRaises(ValueError):
            pv.parse_formula("")

    def test_monoisotopic_mass_pc_34_1(self):
        mass = pv.monoisotopic_mass(pv.parse_formula("C42H82NO8P"))
        self.assertAlmostEqual(mass, 759.5778, places=2)

    def test_monoisotopic_mass_unknown_element_raises(self):
        with self.assertRaises(KeyError):
            pv.monoisotopic_mass({"Xx": 1})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_peak_verification.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'peak_verification'`.

- [ ] **Step 3: Write minimal implementation**

Create `peak_verification.py`:

```python
"""ピークアノテーションの生化学的検証（純ロジック層、MCP 非依存）。

分子式→質量、アダクト→m/z、精密質量誤差、アダクト-極性整合、脂質クラストークン
抽出を提供する。``server.py`` の ``verify_peak_annotation`` ツールがこれを使う。
``knowledge_store.py`` と同じく MCP に依存しない純関数群で、単体テスト可能。
"""

from __future__ import annotations

import re

# --- 定数（モノアイソトピック質量） ---
PROTON_MASS = 1.00727646
ELECTRON_MASS = 0.00054858

ELEMENT_MASSES: dict[str, float] = {
    "H": 1.0078250319,
    "C": 12.0,
    "N": 14.0030740052,
    "O": 15.9949146221,
    "P": 30.97376151,
    "S": 31.97207069,
    "Na": 22.98976928,
    "Cl": 34.96885271,
    "K": 38.9637069,
}

_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def parse_formula(formula: str) -> dict[str, int]:
    """``"C42H82NO8P"`` のような分子式を 元素→個数 に分解する。

    数の省略は 1 とみなす。空/None は ``ValueError``。
    """
    if not formula or not formula.strip():
        raise ValueError("empty formula")
    counts: dict[str, int] = {}
    for element, digits in _FORMULA_TOKEN_RE.findall(formula.strip()):
        if not element:
            continue
        counts[element] = counts.get(element, 0) + (int(digits) if digits else 1)
    if not counts:
        raise ValueError(f"unparseable formula: {formula!r}")
    return counts


def monoisotopic_mass(counts: dict[str, int]) -> float:
    """元素カウントから中性モノアイソトピック質量を返す。未知元素は ``KeyError``。"""
    return sum(ELEMENT_MASSES[element] * n for element, n in counts.items())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_peak_verification.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add peak_verification.py tests/test_peak_verification.py
git commit -m "feat: add formula parsing and monoisotopic mass"
```

---

### Task 2: Adduct m/z and precise mass error

**Files:**
- Modify: `peak_verification.py`
- Test: `tests/test_peak_verification.py`

**Interfaces:**
- Consumes: `parse_formula`, `monoisotopic_mass`, `PROTON_MASS` (Task 1).
- Produces:
  - `ADDUCT_SHIFTS: dict[str, tuple[str, float]]` — adduct → (`"+"`/`"-"`, singly-charged m/z delta from neutral mass).
  - `adduct_mz(neutral_mass: float, adduct: str) -> float | None` — `None` if adduct unknown.
  - `mass_error_ppm(observed_mz, formula, adduct, *, pass_ppm=5.0, borderline_ppm=10.0) -> dict` with keys `theoretical_mz` (float|None), `ppm` (float|None), `band` (str).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_peak_verification.py` (before the `if __name__` block):

```python
class AdductMassErrorTests(unittest.TestCase):
    def test_adduct_mz_protonated(self):
        neutral = pv.monoisotopic_mass(pv.parse_formula("C42H82NO8P"))
        self.assertAlmostEqual(pv.adduct_mz(neutral, "[M+H]+"), 760.5851, places=2)

    def test_adduct_mz_ammonium(self):
        self.assertAlmostEqual(pv.adduct_mz(100.0, "[M+NH4]+"), 118.03383, places=4)

    def test_adduct_mz_deprotonated(self):
        self.assertAlmostEqual(pv.adduct_mz(100.0, "[M-H]-"), 98.99272, places=4)

    def test_adduct_mz_unknown_returns_none(self):
        self.assertIsNone(pv.adduct_mz(100.0, "[M+ZZ]+"))

    def test_mass_error_pass(self):
        result = pv.mass_error_ppm(760.5851, "C42H82NO8P", "[M+H]+")
        self.assertEqual(result["band"], "PASS")
        self.assertAlmostEqual(result["ppm"], 0.0, delta=5.0)

    def test_mass_error_borderline(self):
        neutral = pv.monoisotopic_mass(pv.parse_formula("C42H82NO8P"))
        theo = pv.adduct_mz(neutral, "[M+H]+")
        observed = theo * (1 + 8e-6)  # +8 ppm
        result = pv.mass_error_ppm(observed, "C42H82NO8P", "[M+H]+")
        self.assertEqual(result["band"], "BORDERLINE")

    def test_mass_error_fail(self):
        neutral = pv.monoisotopic_mass(pv.parse_formula("C42H82NO8P"))
        theo = pv.adduct_mz(neutral, "[M+H]+")
        observed = theo * (1 + 20e-6)  # +20 ppm
        result = pv.mass_error_ppm(observed, "C42H82NO8P", "[M+H]+")
        self.assertEqual(result["band"], "FAIL")

    def test_mass_error_unknown_formula(self):
        result = pv.mass_error_ppm(760.0, "Unknown", "[M+H]+")
        self.assertEqual(result["band"], "UNKNOWN")
        self.assertIsNone(result["ppm"])

    def test_mass_error_unknown_adduct(self):
        result = pv.mass_error_ppm(760.0, "C42H82NO8P", "Unknown")
        self.assertEqual(result["band"], "UNKNOWN")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_peak_verification.py::AdductMassErrorTests -v`
Expected: FAIL with `AttributeError: module 'peak_verification' has no attribute 'adduct_mz'`.

- [ ] **Step 3: Write minimal implementation**

Append to `peak_verification.py`:

```python
# --- アダクト表（singly charged; m/z = neutral + delta） ---
ADDUCT_SHIFTS: dict[str, tuple[str, float]] = {
    "[M+H]+": ("+", PROTON_MASS),
    "[M+NH4]+": ("+", 18.03382555),
    "[M+Na]+": ("+", 22.9892207),
    "[M-H2O+H]+": ("+", -17.00328823),
    "[M-H]-": ("-", -1.00727646),
    "[M+HCOO]-": ("-", 44.99820286),
    "[M+CH3COO]-": ("-", 59.01385292),
    "[M+Cl]-": ("-", 34.96940129),
}


def adduct_mz(neutral_mass: float, adduct: str) -> float | None:
    """中性質量とアダクトから 1価イオンの m/z を返す。未知アダクトは ``None``。"""
    entry = ADDUCT_SHIFTS.get(adduct)
    if entry is None:
        return None
    return neutral_mass + entry[1]


def _band_for_ppm(ppm: float, pass_ppm: float, borderline_ppm: float) -> str:
    magnitude = abs(ppm)
    if magnitude <= pass_ppm:
        return "PASS"
    if magnitude <= borderline_ppm:
        return "BORDERLINE"
    return "FAIL"


def mass_error_ppm(
    observed_mz,
    formula,
    adduct,
    *,
    pass_ppm: float = 5.0,
    borderline_ppm: float = 10.0,
) -> dict:
    """実測 m/z と 分子式+アダクト の理論 m/z から ppm 誤差と帯を返す。

    分子式/アダクトが欠落・不明・解釈不能なら ``band="UNKNOWN"`` を返し例外は送出しない。
    """
    unknown = {"theoretical_mz": None, "ppm": None, "band": "UNKNOWN"}
    if observed_mz is None or not formula or formula == "Unknown":
        return unknown
    if not adduct or adduct == "Unknown":
        return unknown
    try:
        neutral = monoisotopic_mass(parse_formula(formula))
    except (ValueError, KeyError):
        return unknown
    theoretical = adduct_mz(neutral, adduct)
    if theoretical is None or theoretical == 0:
        return unknown
    ppm = (float(observed_mz) - theoretical) / theoretical * 1e6
    return {
        "theoretical_mz": round(theoretical, 4),
        "ppm": round(ppm, 2),
        "band": _band_for_ppm(ppm, pass_ppm, borderline_ppm),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_peak_verification.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add peak_verification.py tests/test_peak_verification.py
git commit -m "feat: add adduct m/z and precise mass error"
```

---

### Task 3: Adduct / ion-mode consistency

**Files:**
- Modify: `peak_verification.py`
- Test: `tests/test_peak_verification.py`

**Interfaces:**
- Consumes: `ADDUCT_SHIFTS` (Task 2).
- Produces:
  - `CLASS_TYPICAL_ADDUCTS: dict[str, list[str]]` — lowercase class token → typical adducts.
  - `adduct_consistency(adduct, ion_mode, ontology=None) -> dict` with keys `polarity_ok` (bool|None), `band` (str), `class_typical` (bool|None), `advisory` (str).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_peak_verification.py` (before the `if __name__` block):

```python
class AdductConsistencyTests(unittest.TestCase):
    def test_polarity_match_positive(self):
        result = pv.adduct_consistency("[M+H]+", "Positive", "PC")
        self.assertTrue(result["polarity_ok"])
        self.assertEqual(result["band"], "PASS")
        self.assertTrue(result["class_typical"])

    def test_polarity_mismatch(self):
        result = pv.adduct_consistency("[M+H]+", "Negative", "PC")
        self.assertFalse(result["polarity_ok"])
        self.assertEqual(result["band"], "FAIL")

    def test_class_atypical_adduct_is_advisory_not_fail(self):
        result = pv.adduct_consistency("[M+Na]+", "Positive", "PC")
        self.assertTrue(result["polarity_ok"])
        self.assertEqual(result["band"], "PASS")
        self.assertFalse(result["class_typical"])

    def test_unknown_class_gives_none_typical(self):
        result = pv.adduct_consistency("[M+H]+", "Positive", "ZZZ")
        self.assertIsNone(result["class_typical"])

    def test_unknown_adduct_is_unknown_band(self):
        result = pv.adduct_consistency("Unknown", "Positive", "PC")
        self.assertEqual(result["band"], "UNKNOWN")
        self.assertIsNone(result["polarity_ok"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_peak_verification.py::AdductConsistencyTests -v`
Expected: FAIL with `AttributeError: module 'peak_verification' has no attribute 'adduct_consistency'`.

- [ ] **Step 3: Write minimal implementation**

Append to `peak_verification.py`:

```python
# --- クラス別の典型アダクト（助言のみ。判定はしない） ---
CLASS_TYPICAL_ADDUCTS: dict[str, list[str]] = {
    "pc": ["[M+H]+", "[M+HCOO]-", "[M+CH3COO]-"],
    "lpc": ["[M+H]+", "[M+HCOO]-"],
    "sm": ["[M+H]+", "[M+HCOO]-"],
    "pe": ["[M+H]+", "[M-H]-"],
    "pg": ["[M-H]-", "[M+NH4]+"],
    "pi": ["[M-H]-", "[M+NH4]+"],
    "ps": ["[M-H]-", "[M+H]+"],
    "tg": ["[M+NH4]+", "[M+Na]+"],
    "dg": ["[M+NH4]+", "[M+Na]+", "[M+H]+"],
    "cer": ["[M+H]+", "[M-H]-", "[M+HCOO]-"],
    "che": ["[M+NH4]+", "[M+Na]+"],
    "ce": ["[M+NH4]+", "[M+Na]+"],
    "fa": ["[M-H]-"],
}


def _class_key(ontology: str | None) -> str | None:
    if not ontology:
        return None
    token = ontology.strip().lower()
    if token in CLASS_TYPICAL_ADDUCTS:
        return token
    head = token.split()[0] if token.split() else token
    return head if head in CLASS_TYPICAL_ADDUCTS else None


def adduct_consistency(adduct, ion_mode, ontology=None) -> dict:
    """アダクトの電荷符号と実測極性の整合を判定し、クラス典型性を助言する。

    極性一致は決定的に PASS/FAIL。クラス典型性は助言のみで band には影響しない。
    """
    entry = ADDUCT_SHIFTS.get(adduct) if adduct else None
    if entry is None:
        return {
            "polarity_ok": None,
            "band": "UNKNOWN",
            "class_typical": None,
            "advisory": "アダクトが不明または未対応のため整合判定不可。",
        }

    sign = entry[0]
    mode = str(ion_mode).strip().lower()
    is_positive = mode.startswith("pos")
    is_negative = mode.startswith("neg")
    polarity_ok = (sign == "+" and is_positive) or (sign == "-" and is_negative)
    band = "PASS" if polarity_ok else "FAIL"

    key = _class_key(ontology)
    if key is None:
        class_typical = None
        advisory = "クラス未知のため典型アダクト助言なし。"
    else:
        typical = CLASS_TYPICAL_ADDUCTS[key]
        class_typical = adduct in typical
        if class_typical:
            advisory = f"{key.upper()} で {adduct} は典型的。"
        else:
            advisory = f"{key.upper()} の典型は {', '.join(typical)}（{adduct} は非典型だが誤りとは限らない）。"

    return {
        "polarity_ok": polarity_ok,
        "band": band,
        "class_typical": class_typical,
        "advisory": advisory,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_peak_verification.py -v`
Expected: PASS (all tests through Task 3).

- [ ] **Step 5: Commit**

```bash
git add peak_verification.py tests/test_peak_verification.py
git commit -m "feat: add adduct/ion-mode consistency check"
```

---

### Task 4: Class token, ether caveats, and vocab hits

**Files:**
- Modify: `peak_verification.py`
- Test: `tests/test_peak_verification.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `extract_class_token(name: str | None, ontology: str | None) -> str | None`
  - `ether_caveats(name: str | None, ontology: str | None) -> list[str]`
  - `vocab_hits(class_token: str | None, vocab: dict[str, list[str]]) -> list[str]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_peak_verification.py` (before the `if __name__` block):

```python
class ClassTokenTests(unittest.TestCase):
    def test_extract_class_token_prefers_ontology(self):
        self.assertEqual(pv.extract_class_token("PC 34:1", "PC"), "pc")

    def test_extract_class_token_falls_back_to_name(self):
        self.assertEqual(pv.extract_class_token("TG 52:2", ""), "tg")

    def test_extract_class_token_empty(self):
        self.assertIsNone(pv.extract_class_token("", ""))

    def test_ether_caveat_detected_for_o_prefix(self):
        caveats = pv.ether_caveats("PE O-38:5", "PE")
        self.assertTrue(caveats)
        self.assertIn("P-/O-", caveats[0])

    def test_ether_caveat_detected_for_p_prefix(self):
        self.assertTrue(pv.ether_caveats("PC P-36:4", "PC"))

    def test_no_ether_caveat_for_diacyl(self):
        self.assertEqual(pv.ether_caveats("PC 34:1", "PC"), [])

    def test_vocab_hits_match(self):
        vocab = {"pc": ["phosphatidylcholine"], "tg": ["triacylglycerol"]}
        self.assertEqual(pv.vocab_hits("pc", vocab), ["phosphatidylcholine"])

    def test_vocab_hits_none_token(self):
        self.assertEqual(pv.vocab_hits(None, {"pc": ["x"]}), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_peak_verification.py::ClassTokenTests -v`
Expected: FAIL with `AttributeError: module 'peak_verification' has no attribute 'extract_class_token'`.

- [ ] **Step 3: Write minimal implementation**

Append to `peak_verification.py`:

```python
_ETHER_RE = re.compile(r"\b[POpo]-")


def extract_class_token(name: str | None, ontology: str | None) -> str | None:
    """脂質クラストークン（小文字）を返す。ontology 優先、無ければ name 先頭語。"""
    for source in (ontology, name):
        if source and source.strip():
            head = source.strip().lower().split()[0]
            if head:
                return head
    return None


def ether_caveats(name: str | None, ontology: str | None) -> list[str]:
    """エーテル脂質（P-/O- 表記）に該当する場合、注意ノートへのリンクを返す。"""
    text = f"{name or ''} {ontology or ''}"
    if _ETHER_RE.search(text):
        return [
            "エーテル脂質の P-/O- 表記混同に注意（[[pe-p-vs-pe-o-annotation]]）。"
            "P- は酸化ストレス仮説 [[plasmalogen-oxidation]] に関与するが O- は別物。"
        ]
    return []


def vocab_hits(class_token: str | None, vocab: dict[str, list[str]]) -> list[str]:
    """クラストークンに対応する語彙同義語を返す（無ければ空）。"""
    if not class_token:
        return []
    return list(vocab.get(class_token, []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_peak_verification.py -v`
Expected: PASS (all tests through Task 4).

- [ ] **Step 5: Commit**

```bash
git add peak_verification.py tests/test_peak_verification.py
git commit -m "feat: add class token, ether caveats, vocab hits"
```

---

### Task 5: `verify_peak_annotation` MCP tool

**Files:**
- Modify: `server.py` (imports near line 19-50; new tool + helper near `inspect_metabolite_details` handling, i.e. after the `pai2_inspect_metabolite_details` tool around line 1141)
- Test: `tests/test_peak_verification.py`

**Interfaces:**
- Consumes: `peak_verification` (Tasks 1-4); `server.session` (`AnalysisSession`); `knowledge_store.load_vocab`, `knowledge_store.coverage`; `test_pai2.get_signal_to_noise`; `server.KNOWLEDGE_DIR`.
- Produces:
  - `server._build_verification_dossier(feat: dict, vocab: dict) -> dict`
  - `server.verify_peak_annotation(metabolite_id: str | None = None, metabolite_name: str | None = None) -> str` (JSON string).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_peak_verification.py` (before the `if __name__` block):

```python
import json
import tempfile
from pathlib import Path

import server
from test_pai2 import IonMode


def _feat(**over):
    base = {
        "id": 1,
        "name": "PC 34:1",
        "ontology": "PC",
        "formula": "C42H82NO8P",
        "adduct": "[M+H]+",
        "m/z": 760.5851,
        "ion_mode": IonMode.Positive,
        "time": {"rt": 12.34},
        "S/N": 42.0,
    }
    base.update(over)
    return base


class VerifyPeakToolTests(unittest.TestCase):
    def setUp(self):
        self._orig_features = server.session.filtered_features
        self._orig_knowledge = server.KNOWLEDGE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        server.KNOWLEDGE_DIR = Path(self._tmp.name) / "knowledge"
        server.KNOWLEDGE_DIR.mkdir()

    def tearDown(self):
        server.session.filtered_features = self._orig_features
        server.KNOWLEDGE_DIR = self._orig_knowledge
        self._tmp.cleanup()

    def test_error_when_not_loaded(self):
        server.session.filtered_features = None
        out = json.loads(server.verify_peak_annotation(metabolite_id="1"))
        self.assertEqual(out["status"], "error")

    def test_error_when_no_selector(self):
        server.session.filtered_features = [_feat()]
        out = json.loads(server.verify_peak_annotation())
        self.assertEqual(out["status"], "error")

    def test_not_found(self):
        server.session.filtered_features = [_feat()]
        out = json.loads(server.verify_peak_annotation(metabolite_id="999"))
        self.assertEqual(out["status"], "not_found")

    def test_success_shape_and_bands(self):
        server.session.filtered_features = [_feat()]
        out = json.loads(server.verify_peak_annotation(metabolite_id="1"))
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["identity"]["name"], "PC 34:1")
        self.assertEqual(out["identity"]["ion_mode"], "Positive")
        self.assertEqual(out["analytical_checks"]["mass_error"]["band"], "PASS")
        self.assertEqual(out["analytical_checks"]["adduct_consistency"]["band"], "PASS")
        self.assertIn("class_token", out["biological_plausibility"])

    def test_unknown_formula_degrades_to_unknown_band(self):
        server.session.filtered_features = [_feat(formula="Unknown")]
        out = json.loads(server.verify_peak_annotation(metabolite_id="1"))
        self.assertEqual(out["analytical_checks"]["mass_error"]["band"], "UNKNOWN")

    def test_ether_caveat_surfaced(self):
        server.session.filtered_features = [_feat(name="PE O-38:5", ontology="PE", id=2)]
        out = json.loads(server.verify_peak_annotation(metabolite_id="2"))
        self.assertTrue(out["biological_plausibility"]["caveats"])

    def test_candidate_slugs_from_knowledge(self):
        note = server.KNOWLEDGE_DIR / "pe-p-vs-pe-o-annotation.md"
        note.write_text(
            "---\ntype: knowledge\n"
            "description: PE の P-/O- 表記の混同に注意\n"
            "claim_strength: established\n"
            "tags: [annotation, plasmalogen]\n---\n\n本文\n",
            encoding="utf-8",
        )
        server.session.filtered_features = [_feat(name="PE 38:5", ontology="PE", id=3)]
        out = json.loads(server.verify_peak_annotation(metabolite_id="3"))
        self.assertIn(
            "pe-p-vs-pe-o-annotation",
            out["biological_plausibility"]["candidate_knowledge_slugs"],
        )

    def test_multiple_matches_wrapped(self):
        server.session.filtered_features = [_feat(id=1), _feat(id=2)]
        out = json.loads(server.verify_peak_annotation(metabolite_name="PC"))
        self.assertEqual(out["status"], "success")
        self.assertIn("matches", out)
        self.assertEqual(len(out["matches"]), 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_peak_verification.py::VerifyPeakToolTests -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute 'verify_peak_annotation'`.

- [ ] **Step 3a: Add imports**

In `server.py`, extend the `from test_pai2 import (...)` block (currently lines 45-50) to include `get_signal_to_noise`, and add a `peak_verification` import. The block becomes:

```python
from test_pai2 import (
    perform_pca_summary,
    filter_features_by_params,
    inspect_metabolite_details,
    get_top_contributors,
    get_signal_to_noise,
)
import peak_verification as pv
```

- [ ] **Step 3b: Add the helper and tool**

In `server.py`, immediately after the `pai2_inspect_metabolite_details` tool (ends around line 1141, before `pai2_update_analysis_filter`), insert:

```python
def _build_verification_dossier(feat: dict, vocab: dict) -> dict:
    """1 feature の検証ドシエを組み立てる（決定的チェック + 生物学的妥当性の材料）。"""
    name = feat.get("name") or ""
    ontology = feat.get("ontology") or ""
    formula = feat.get("formula")
    adduct = feat.get("adduct")
    observed_mz = feat.get("m/z")
    ion_mode = feat.get("ion_mode")
    ion_mode_name = ion_mode.name if hasattr(ion_mode, "name") else str(ion_mode)
    rt = (feat.get("time") or {}).get("rt")
    sn = get_signal_to_noise(feat)

    mass_error = pv.mass_error_ppm(observed_mz, formula, adduct)
    adduct_check = pv.adduct_consistency(adduct, ion_mode_name, ontology)
    class_token = pv.extract_class_token(name, ontology)
    caveats = pv.ether_caveats(name, ontology)

    if name.strip():
        cov = knowledge_store.coverage([f"{name} {ontology}"], KNOWLEDGE_DIR, vocab)
        matches_info = next(iter(cov.values()))["matches"]
        candidate_slugs = [m["slug"] for m in matches_info]
        bio = {
            "class_token": class_token,
            "vocab_hits": pv.vocab_hits(class_token, vocab),
            "candidate_knowledge_slugs": candidate_slugs,
            "caveats": caveats,
        }
        instruction = (
            "candidate_knowledge_slugs を knowledge_expand で裏取りし、この試料系に"
            "この脂質種が生物学的に妥当か・表記の落とし穴に当たらないかを判断して"
            "総合判定せよ。"
        )
    else:
        bio = {
            "class_token": None,
            "vocab_hits": [],
            "candidate_knowledge_slugs": [],
            "caveats": ["アノテーション無しにつき生物学的妥当性は判定不可。"],
        }
        instruction = "アノテーションが無いため分析化学的事実のみで判断せよ。"

    return {
        "status": "success",
        "identity": {
            "id": feat.get("id"),
            "name": name or None,
            "ontology": ontology or None,
            "formula": formula,
            "adduct": adduct,
            "observed_mz": observed_mz,
            "rt": rt,
            "ion_mode": ion_mode_name,
            "signal_to_noise": sn,
        },
        "analytical_checks": {
            "mass_error": mass_error,
            "adduct_consistency": adduct_check,
        },
        "biological_plausibility": bio,
        "llm_decision": {
            "instruction": instruction,
            "deterministic_summary": (
                f"mass_error={mass_error['band']}, adduct={adduct_check['band']}"
            ),
        },
    }


@mcp.tool()
def verify_peak_annotation(
    metabolite_id: str | None = None, metabolite_name: str | None = None
) -> str:
    """指定した1ピークのアノテーションが生化学的に妥当かを検証するドシエを返す。

    分析化学的な同定確度（精密質量誤差ppm・アダクト/イオンモード整合）を決定的に
    判定し、生物学的妥当性は関連 knowledge slug を添えて LLM の判断に委ねる。
    先に pai2_parser でデータを読み込むこと。metabolite_id か metabolite_name の
    いずれかを指定する。
    """
    if session.filtered_features is None:
        return json.dumps(
            {"status": "error", "message": "先に pai2_parser を実行してデータを読み込んでください。"},
            ensure_ascii=False,
            indent=2,
        )
    if metabolite_id is None and metabolite_name is None:
        return json.dumps(
            {"status": "error", "message": "metabolite_id か metabolite_name のいずれかを指定してください。"},
            ensure_ascii=False,
            indent=2,
        )

    matches = []
    for feat in session.filtered_features:
        if metabolite_id is not None and str(feat.get("id")) == str(metabolite_id):
            matches.append(feat)
        elif (
            metabolite_name is not None
            and isinstance(feat.get("name"), str)
            and metabolite_name.lower() in feat.get("name", "").lower()
        ):
            matches.append(feat)

    if not matches:
        return json.dumps(
            {"status": "not_found", "message": "指定された代謝物がフィルタ済みデータ内に見つかりませんでした。"},
            ensure_ascii=False,
            indent=2,
        )

    vocab = knowledge_store.load_vocab(KNOWLEDGE_DIR)
    dossiers = [_build_verification_dossier(feat, vocab) for feat in matches]
    payload = dossiers[0] if len(dossiers) == 1 else {"status": "success", "matches": dossiers}
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_peak_verification.py -v`
Expected: PASS (all tests, Tasks 1-5).

- [ ] **Step 5: Run the full test suite for regressions**

Run: `python -m pytest tests/ -v`
Expected: PASS (existing tests unaffected).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_peak_verification.py
git commit -m "feat: add verify_peak_annotation MCP tool"
```

---

## Notes for the implementer

- Run tests from the repo root (`C:\Users\yuu18\Lipidmix_with_LLM`) so `import server` / `import peak_verification` resolve. Existing tests import top-level modules directly (see `tests/test_objective_tools.py`).
- `server.py` imports `matplotlib`/`pandas`/`mcp` at module load; ensure the project venv is active (`.venv-1`).
- Do not add an overall verdict field — the `llm_decision` block is the seam where the LLM takes over.
- ARF-path support, MS/MS `.dcl` fragment checking, and formula↔shorthand consistency are explicitly out of scope (see the design doc §2).
