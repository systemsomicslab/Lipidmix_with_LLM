"""解釈精度評価の10ケース（20230824_liver, GF/SPF×AIN/HFD/NC の 2×3）。

各 Case の pipeline は gold 凍結（frozen 正準証拠）の生成に使う。フルツール駆動の
自動ランナー（interp_eval_liver2.do_run_auto）は pipeline を実行せず case.query のみを
モデルへ渡し、モデル自身にツールを駆動させる。差次コントラスト(#3/#4)は暫定で、
Task 5 のライブ再PCAで主分離因子を見極めてから最終確定する。
"""
from interp_eval import Case, ToolStep

DIR_NEG = r"C:\Users\yuu18\datasets\20230824_liver\20230824_liver\NEG"
DIR_POS = r"C:\Users\yuu18\datasets\20230824_liver\20230824_liver\POS"


def _pca(mode, directory):
    return Case(
        id=f"pca_{mode.lower()}", phase_label="PCA", mode=mode,
        query=("このデータセットのPCAを実行し、群分離の有無と主因（腸内細菌叢 GF/SPF か "
               "食餌 AIN/HFD/NC か）、および生物学的な意味を解釈して。分離が弱ければ"
               "フィルタや群指定を変えて再PCAして。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_re_pca", {"top_features": 10}),
        ],
    )


def _differential(case_id, mode, directory, group_a, group_b):
    return Case(
        id=case_id, phase_label="DIFFERENTIAL", mode=mode,
        query=(f"{group_a} 群と {group_b} 群の差次的解析を実行し、有意な脂質と"
               "注意点（多重比較・バッチ交絡・n）を解釈して。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_preprocess", {"normalize": "median", "impute": "half_min"}),
            ToolStep("arf_differential", {"group_a": group_a, "group_b": group_b}),
        ],
    )


def _qc(mode, directory):
    return Case(
        id=f"qc_{mode.lower()}", phase_label="QC", mode=mode,
        query=("前処理/QCを実行し、QC-RSD・drift・欠測補完・blank 由来などデータ品質の"
               "問題と対処を解釈して。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf_preprocess",
                     {"normalize": "median", "max_qc_rsd": 30, "impute": "half_min"}),
        ],
    )


def _identity(mode, directory):
    return Case(
        id=f"identity_{mode.lower()}", phase_label="IDENTITY", mode=mode,
        query="脂質同定結果を確認し、確信度（MSIレベル）と過剰主張のリスクを解釈して。",
        pipeline=[
            ToolStep("load_dataset", {"directory": directory}),
            ToolStep("arf2_annotate_identities", {"max_rows": 30}),
        ],
    )


def _literature(mode, query_text):
    return Case(
        id=f"literature_{mode.lower()}", phase_label="LITERATURE", mode=mode,
        query=("この解析に関連する文献を検索し、どの知見が本データの仮説（腸内細菌叢・"
               "食餌による肝リピドーム再構成）を支持するか解釈して。"),
        pipeline=[ToolStep("paper_search", {"query": query_text, "max_results": 10})],
    )


# 差次コントラストは暫定（Task 5 で確定）: 菌叢効果 SPF vs GF / 食餌効果 HFD vs NC。
LIVER2_CASES = [
    _pca("NEG", DIR_NEG),
    _pca("POS", DIR_POS),
    _differential("differential_microbiome", "NEG", DIR_NEG, group_a="SPF", group_b="GF"),
    _differential("differential_diet", "POS", DIR_POS, group_a="HFD", group_b="NC"),
    _qc("NEG", DIR_NEG),
    _qc("POS", DIR_POS),
    _identity("NEG", DIR_NEG),
    _identity("POS", DIR_POS),
    _literature("NEG", "germ-free SPF mouse liver lipidome gut microbiota phospholipid"),
    _literature("POS", "high-fat diet mouse liver lipidomics triacylglycerol ceramide"),
]
