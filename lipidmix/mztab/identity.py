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
