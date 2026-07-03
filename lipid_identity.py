"""脂質同定の標準化・信頼度（純ロジック層、MCP 非依存、オフライン）。

GOSLIN による脂質名正規化、同梱表による RefMet/LIPID MAPS ID 付与、MSI レベル推定。
ネットワークは一切使わない（pygoslin と同梱 TSV のみ）。
"""

from __future__ import annotations

from pathlib import Path


def normalize_lipid_name(name: str) -> dict:
    """脂質ショートハンド名を GOSLIN で正規化する（オフライン）。

    pygoslin が無い/解析不能でも例外を投げず parse_ok=False を返す。
    返り値: parse_ok / normalized / level / lipid_maps_category / error。
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
        # 構造レベル（species/molecular species/sn-position 等）。版差に強い経路を優先。
        level = None
        try:
            level = lipid.lipid.info.level.name
        except Exception:
            lvl = getattr(lipid, "level", None)
            level = lvl.name if hasattr(lvl, "name") else (str(lvl) if lvl else None)
        result["level"] = level
        try:
            result["lipid_maps_category"] = lipid.lipid.headgroup.lipid_category.name
        except Exception:
            result["lipid_maps_category"] = None
    except Exception as exc:
        result["error"] = f"parse error: {exc}"
    return result


def _read_tsv(path: Path) -> list[dict]:
    """タブ区切り表を dict のリストで読む。存在しなければ空リスト。"""
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
    """同梱の RefMet / LIPID MAPS クラス対応表を読み込む（オフライン）。"""
    base = Path(reference_dir)
    lm = {r["class_token"]: r for r in _read_tsv(base / "lipidmaps_classes.tsv")}
    rm = {r["class_token"]: r for r in _read_tsv(base / "refmet_map.tsv")}
    return {"lipidmaps": lm, "refmet": rm}


def map_to_reference(class_token, tables) -> dict:
    """クラストークンを同梱表で RefMet 名・LIPID MAPS カテゴリ/メインクラスに写像する。

    表に無ければ matched=False＋caveat（推測はしない）。
    """
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
