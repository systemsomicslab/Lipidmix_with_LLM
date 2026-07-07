"""解釈品質評価の具体10ケース（5フェーズ × NEG/POS）。

各 Case の pipeline は順に execute_tool され、最後のツール出力が解釈対象。
差次的解析の group_a/group_b は discovery 結果で実際の群名に置換すること。
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


def _differential(mode, directory, group_a, group_b):
    return Case(
        id=f"differential_{mode.lower()}", phase_label="DIFFERENTIAL", mode=mode,
        query=f"{group_a} と {group_b} の差次的解析結果を解釈し、"
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


# discovery 結果で group_a/group_b を実際の群名に置換すること（下は雛形）。
CASES = [
    _pca("NEG", DIR_NEG),
    _pca("POS", DIR_POS),
    _differential("NEG", DIR_NEG, group_a="GROUP_A_NEG", group_b="GROUP_B_NEG"),
    _differential("POS", DIR_POS, group_a="GROUP_A_POS", group_b="GROUP_B_POS"),
    _qc("NEG", DIR_NEG),
    _qc("POS", DIR_POS),
    _identity("NEG", DIR_NEG),
    _identity("POS", DIR_POS),
    _literature("NEG", "liver lipidomics ceramide disease association"),
    _literature("POS", "hepatic phosphatidylcholine remodeling metabolism"),
]
