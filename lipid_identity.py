"""脂質同定の標準化・信頼度（純ロジック層、MCP 非依存、オフライン）。

GOSLIN による脂質名正規化、同梱表による RefMet/LIPID MAPS ID 付与、MSI レベル推定。
ネットワークは一切使わない（pygoslin と同梱 TSV のみ）。
"""

from __future__ import annotations


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
