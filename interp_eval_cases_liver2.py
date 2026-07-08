"""解釈精度評価の10ケース（20230824_liver, GF/SPF×AIN/HFD/NC の 2×3）。

各 Case の pipeline は gold 凍結（frozen 正準証拠）の生成に使う。フルツール駆動の
自動ランナー（interp_eval_liver2.do_run_auto）とサブエージェントは pipeline を実行せず
case.query のみを起点にモデル自身がツールを駆動する。

データ制約（2026-07-08 discovery）:
  - NEG arf（48MB, 単ブロック）は正常。**分離の主因は食餌**（silhouette PC1-2=0.61,
    PC1: NC<AIN<HFD, ANOVA F=92）で、**腸内細菌叢 GF/SPF は弱い**（silhouette≈-0.03）。
  - POS arf（95MB, 別ブロック構造）は現行パーサで展開不可（98/19388スポットに退化）＝
    POS の PCA/差次/QC は実行不能。よって定量フェーズは NEG に寄せ、POS は同定(arf2)・
    文献で活用する（両者は正常動作）。全5フェーズ・両極性を被覆する。
"""
from interp_eval import Case, ToolStep

DIR_NEG = r"C:\Users\yuu18\datasets\20230824_liver\20230824_liver\NEG"
DIR_POS = r"C:\Users\yuu18\datasets\20230824_liver\20230824_liver\POS"


def _pca_overall():
    return Case(
        id="pca_neg", phase_label="PCA", mode="NEG",
        query=("このデータセット(NEG)のPCAを実行し、群分離の有無と主因（腸内細菌叢 GF/SPF か "
               "食餌 AIN/HFD/NC か）、および生物学的な意味を解釈して。QC/Blank は除き、"
               "分離が弱ければ群指定を変えて再PCAして。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": DIR_NEG}),
            ToolStep("arf_re_pca",
                     {"class_ids": ["GF", "SPF"], "group_levels": ["GF", "SPF"],
                      "top_features": 10}),
        ],
    )


def _pca_within_hfd():
    return Case(
        id="pca_neg_hfd_micro", phase_label="PCA", mode="NEG",
        query=("HFD食の群に限定して(NEG)、腸内細菌叢 GF vs SPF で分離するか再PCAして"
               "解釈して。食餌を統制したとき菌叢効果が見えるかに注意。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": DIR_NEG}),
            ToolStep("arf_re_pca",
                     {"class_ids": ["HFD"], "group_levels": ["GF", "SPF"],
                      "top_features": 10}),
        ],
    )


def _differential(case_id, group_a, group_b, note):
    return Case(
        id=case_id, phase_label="DIFFERENTIAL", mode="NEG",
        query=(f"Class ID {group_a} 群と {group_b} 群の差次的解析を実行し(NEG, 各n=4, "
               f"{note})、有意な脂質と注意点（多重比較・バッチ交絡・n）を解釈して。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": DIR_NEG}),
            ToolStep("arf_preprocess", {"normalize": "median", "impute": "half_min"}),
            ToolStep("arf_differential", {"group_a": group_a, "group_b": group_b}),
        ],
    )


def _qc():
    return Case(
        id="qc_neg", phase_label="QC", mode="NEG",
        query=("前処理/QCを実行し(NEG, QC n=5)、QC-RSD・drift・欠測補完・blank 由来など"
               "データ品質の問題と対処を解釈して。"),
        pipeline=[
            ToolStep("load_dataset", {"directory": DIR_NEG}),
            ToolStep("arf_preprocess",
                     {"normalize": "median", "max_qc_rsd": 30, "impute": "half_min"}),
        ],
    )


def _identity(mode, directory):
    return Case(
        id=f"identity_{mode.lower()}", phase_label="IDENTITY", mode=mode,
        query=f"脂質同定結果を確認し({mode})、確信度（MSIレベル）と過剰主張のリスクを解釈して。",
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


# NEG中心。差次は食餌の強コントラスト2件（HFD vs NC / HFD vs AIN）＋菌叢の弱コントラスト
# （SPF vs GF＝自制のテスト）。POS は同定・文献で被覆。
LIVER2_CASES = [
    _pca_overall(),
    _pca_within_hfd(),
    _differential("differential_hfd_nc", "GF_HFD", "GF_NC", "食餌効果・強シグナル"),
    _differential("differential_hfd_ain", "GF_HFD", "GF_AIN", "食餌効果・中シグナル"),
    _differential("differential_microbiome", "GF_HFD", "SPF_HFD", "菌叢効果・弱シグナル"),
    _qc(),
    _identity("NEG", DIR_NEG),
    _identity("POS", DIR_POS),
    _literature("NEG", "germ-free SPF mouse liver lipidome gut microbiota phospholipid"),
    _literature("POS", "high-fat diet mouse liver lipidomics triacylglycerol ceramide"),
]
