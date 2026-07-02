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
    mode_known = is_positive or is_negative

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

    if not mode_known:
        advisory = advisory + f" （ion_mode={ion_mode!r} が正/負極性として認識できないため極性整合を判定できませんでした。）"
        return {
            "polarity_ok": None,
            "band": "UNKNOWN",
            "class_typical": class_typical,
            "advisory": advisory,
        }

    polarity_ok = (sign == "+" and is_positive) or (sign == "-" and is_negative)
    band = "PASS" if polarity_ok else "FAIL"

    return {
        "polarity_ok": polarity_ok,
        "band": band,
        "class_typical": class_typical,
        "advisory": advisory,
    }


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
