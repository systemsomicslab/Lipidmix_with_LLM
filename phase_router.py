"""phase-router: クエリの解析フェーズを判定し、そのフェーズのツール＋常時コアツール
だけを LLM へ露出する。MCP サーバは非改変で、これは将来の Python Agent 側の部品。

「35ツールを一度に露出しない」が鉄則。judgement はハイブリッド（状態ゲート →
キーワード短絡 → LLM分類 → last_phase フォールバック）。LLM 依存は classify_fn の
1点のみ（DI）で、テストはフェイクを注入して Ollama 非依存で検証する。
"""

import os
import re
from dataclasses import dataclass
from typing import Callable

import httpx

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

# 高精度・低誤爆のトークンだけを短絡に使う（曖昧語は入れない）。
KEYWORD_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\.pai2\b|pai2", re.IGNORECASE), "PAI2"),
    (re.compile(r"m/?z\b|\bEIC\b|\.aef\b|\bAEF\b", re.IGNORECASE), "EIC"),
    (re.compile(r"Europe ?PMC|PMC|文献|論文|paper", re.IGNORECASE), "LITERATURE"),
]


@dataclass
class RouterState:
    """Agent が保持する軽量ビュー。route はこれを読むだけで書き換えない。"""
    dataset_loaded: bool
    last_phase: str | None = None


@dataclass
class RouteResult:
    phase: str
    tool_names: list[str]
    reason: str  # "gate" | "keyword" | "llm" | "fallback"


def _match_keyword(query: str) -> str | None:
    for pattern, phase in KEYWORD_RULES:
        if pattern.search(query):
            return phase
    return None


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def route(
    query: str,
    state: RouterState,
    classify_fn: Callable[[str, list[str]], str],
) -> RouteResult:
    """クエリを1フェーズへ振り分け、露出ツール名（フェーズ＋コア、dedup）を返す。

    1. 状態ゲート: dataset 未ロードなら必ず ENTRY（LLM を呼ばない）。
    2. キーワード短絡: 明示的トークンにヒットしたらそのフェーズ（LLM を呼ばない）。
    3. LLM分類: 曖昧な時だけ classify_fn に解析7フェーズから1つ選ばせる。
    4. フォールバック: 分類が既知フェーズ名でなければ last_phase（無ければ ENTRY）。
    """
    if not state.dataset_loaded:
        phase, reason = "ENTRY", "gate"
    else:
        kw = _match_keyword(query)
        if kw is not None:
            phase, reason = kw, "keyword"
        else:
            candidate = classify_fn(query, list(ANALYSIS_PHASES))
            if candidate in PHASES:
                phase, reason = candidate, "llm"
            else:
                phase = state.last_phase or "ENTRY"
                reason = "fallback"

    tool_names = _dedup(PHASES[phase] + CORE_TOOLS)
    return RouteResult(phase=phase, tool_names=tool_names, reason=reason)


OLLAMA_URL = os.environ.get("LIPIDMIX_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
CLASSIFIER_MODEL = os.environ.get("LIPIDMIX_ROUTER_MODEL", "qwen3:14b")

# 分類器プロンプト用の各フェーズ一行説明（キーは ANALYSIS_PHASES と一致させる）。
PHASE_DESCRIPTIONS: dict[str, str] = {
    "OBJECTIVE": "解析目的の記録・更新、知識カバレッジ(GAP)の確認",
    "ARF": "ARFアライメント行列のPCA・前処理・差次的解析・クラス/タグ/ロール一覧",
    "ARF2": "ARF2オーバービューのパースと identity 注釈",
    "PAI2": "PAI2ピークレベルのパース・上位代謝物・詳細確認・フィルタ更新",
    "EIC": "EIC/AEFクロマトのパース・m/z/RT範囲検索・ピークトップ",
    "LITERATURE": "文献検索(Europe PMC)と知識ノートのステージ/レビュー/昇格/却下",
    "FIGURES": "PCA/ボルケーノ図の保存・レポート執筆・ピークアノテーション検証",
}


def _parse_phase(text: str, candidates: list[str]) -> str | None:
    """LLM 応答から既知フェーズ名を抽出する。完全一致 → 埋め込み一致の順。"""
    stripped = text.strip().upper()
    for c in candidates:
        if c.upper() == stripped:
            return c
    for c in candidates:
        if re.search(rf"\b{re.escape(c)}\b", text, re.IGNORECASE):
            return c
    return None


def ollama_classify_fn(query: str, candidate_phases: list[str]) -> str:
    """既定 classify_fn。qwen3:14b(think OFF, temp0) にフェーズを1つ選ばせる。

    パースできなければ空文字を返し、route 側のフォールバックに委ねる。
    """
    listing = "\n".join(
        f"- {p}: {PHASE_DESCRIPTIONS[p]}" for p in candidate_phases
    )
    system = (
        "あなたはMS-DIALリピドミクス解析のルータです。ユーザーのクエリが属する"
        "解析フェーズを、次の候補からちょうど1つ選び、フェーズ名のみを大文字で"
        "答えてください（説明は不要）。\n" + listing
    )
    payload = {
        "model": CLASSIFIER_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    content = resp.json()["message"].get("content") or ""
    return _parse_phase(content, candidate_phases) or ""
