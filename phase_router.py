"""phase-router: クエリの解析フェーズを判定し、そのフェーズのツール＋常時コアツール
だけを LLM へ露出する。MCP サーバは非改変で、これは将来の Python Agent 側の部品。

「35ツールを一度に露出しない」が鉄則。judgement はハイブリッド（状態ゲート →
キーワード短絡 → LLM分類 → last_phase フォールバック）。LLM 依存は classify_fn の
1点のみ（DI）で、テストはフェイクを注入して Ollama 非依存で検証する。
"""

# フェーズ → そのフェーズで露出するツール名。全35ツールの厳密な分割
# （tests/test_phase_router.py の test_partition_is_exact が保証）。
PHASES: dict[str, list[str]] = {
    "ENTRY": ["load_dataset", "list_data_files", "list_reports", "read_report"],
    "OBJECTIVE": ["record_objective", "update_objective", "knowledge_coverage"],
    "ARF": [
        "arf_parser", "arf_re_pca", "arf_list_classes", "arf_list_tags",
        "arf_list_sample_roles", "arf_preprocess", "arf_pca_preprocessed",
        "arf_differential",
    ],
    "ARF2": ["arf2_parser", "arf2_annotate_identities"],
    "PAI2": [
        "pai2_parser", "pai2_get_top_metabolites",
        "pai2_inspect_metabolite_details", "pai2_update_analysis_filter",
    ],
    "EIC": [
        "eicaef_parser", "eicaef_search_by_mz_range",
        "eicaef_search_by_rt_range", "eicaef_top_peak_tops",
    ],
    "LITERATURE": [
        "paper_search", "ingest_stage", "ingest_review_queue",
        "ingest_promote", "ingest_reject", "log_search",
    ],
    "FIGURES": [
        "save_pca_figure", "save_volcano_figure", "write_report",
        "verify_peak_annotation",
    ],
}

# どのフェーズでも「入口へ戻る」操作は意味が通るため常時露出する（少数ゆえ氾濫に無害）。
CORE_TOOLS: list[str] = ["load_dataset", "list_data_files", "list_reports"]

# LLM 分類の候補集合（ENTRY は状態ゲートで扱うので除外）。
ANALYSIS_PHASES: list[str] = [p for p in PHASES if p != "ENTRY"]
