"""解釈品質評価の具体10ケース（全5フェーズを被覆）。

各 Case の pipeline は順に execute_tool され、最後のツール出力が解釈対象。

実データ制約（2026-07-07 discovery）に合わせた構成:
  NEG = 4群（G / ILG / LPS / control 各15, n=60）で群依存フェーズを支える。
  POS = 単一群 "1"（control_0h, n=3）で群構造なし → 差次的解析は不能、
        PCA/QC は n=3 の退化結果（群差を捏造しないかのハルシネーション・プローブ）。
  よって差次的解析は POS を諦め、NEG の2コントラスト（LPS→control / ILG→control）に
  再配分する。厳密な 5×2 直積ではなく「全5フェーズ被覆」を不変条件とする。
"""
from interp_eval import Case, ToolStep

DIR_NEG = r"C:\Users\yuu18\datasets\2_lipidome_lcms\NEG"
DIR_POS = r"C:\Users\yuu18\datasets\2_lipidome_lcms\POS"


def _pca(mode, directory):
    return Case(
        id=f"pca_{mode.lower()}", phase_label="PCA", mode=mode,
        query="このPCA結果を解釈し、群分離の有無と生物学的な意味を述べて。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_re_pca", {"top_features": 10}),
        ],
    )


def _differential(case_id, mode, directory, group_a, group_b):
    return Case(
        id=case_id, phase_label="DIFFERENTIAL", mode=mode,
        query=f"{group_a} 群と {group_b} 群の差次的解析結果を解釈し、"
              "有意な脂質と注意点を述べて。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_preprocess", {"normalize": "median", "impute": "half_min"}),
            ToolStep("arf_differential", {"group_a": group_a, "group_b": group_b}),
        ],
    )


def _qc(mode, directory):
    return Case(
        id=f"qc_{mode.lower()}", phase_label="QC", mode=mode,
        query="この前処理/QCレポートを解釈し、データ品質の問題と対処を述べて。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_preprocess",
                     {"normalize": "median", "max_qc_rsd": 30, "impute": "half_min"}),
        ],
    )


def _identity(mode, directory):
    return Case(
        id=f"identity_{mode.lower()}", phase_label="IDENTITY", mode=mode,
        query="この脂質同定結果を解釈し、確信度と過剰主張のリスクを述べて。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf2_annotate_identities", {"max_rows": 30}),
        ],
    )


def _literature(mode, query_text):
    return Case(
        id=f"literature_{mode.lower()}", phase_label="LITERATURE", mode=mode,
        query="この文献検索結果を解釈し、どの知見が解析仮説を支持するか述べて。",
        pipeline=[ToolStep("paper_search", {"query": query_text, "max_results": 10})],
    )


# 群名は実データ discovery 済み（NEG: G/ILG/LPS/control）。差次は POS 不能のため NEG 2件。
CASES = [
    _pca("NEG", DIR_NEG),
    _pca("POS", DIR_POS),
    _differential("differential_lps", "NEG", DIR_NEG, group_a="LPS", group_b="control"),
    _differential("differential_ilg", "NEG", DIR_NEG, group_a="ILG", group_b="control"),
    _qc("NEG", DIR_NEG),
    _qc("POS", DIR_POS),
    _identity("NEG", DIR_NEG),
    _identity("POS", DIR_POS),
    _literature("NEG", "RAW264 macrophage LPS lipidomics phospholipid remodeling"),
    _literature("POS", "macrophage inflammation ceramide sphingolipid signaling"),
]
