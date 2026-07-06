"""MCP リソース（@mcp.resource ×6）: output-format 参照、knowledge/playbook の
index・expand、knowledge inbox。

このモジュールを import すると副作用でリソースが mcp に登録される。server は
`import tools_resources` するだけでよい。tools_* / server は import しない。
"""
import knowledge_store
import mcp_core
from mcp_core import mcp, OUTPUT_FORMAT_DOC, PLAYBOOK_DIR


@mcp.resource(
    "lipidmix://docs/output-format",
    name="output_format",
    title="MS-DIAL parser output format and ontology",
    description=(
        "Authoritative field-by-field reference for ARF, ARF2, PAI2, DCL, "
        "and EIC/AEF parser outputs. Read before interpreting parser results."
    ),
    mime_type="text/markdown",
)
def output_format_reference() -> str:
    """Return the parser output format and ontology reference for LLM clients."""
    try:
        return OUTPUT_FORMAT_DOC.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Output format reference was not found: {OUTPUT_FORMAT_DOC}"
        ) from exc


# --- 知識・ワークフロー蓄積層（knowledge / playbook） ---
# 索引は frontmatter から動的生成（実INDEXファイルは持たない）。展開は [[link]]
# グラフを構造予算内（max 1 hop / 5本体 / 約15kトークン）で束ねて返す。
# 関連性の判断（どのノートを採用するか）は LLM 側に委ねる。
@mcp.resource(
    "lipidmix://knowledge/index",
    name="knowledge_index",
    title="Knowledge note index (literature-derived)",
    description=(
        "One line per knowledge note (description + claim_strength). Consult this "
        "before interpreting; expand only relevant slugs."
    ),
    mime_type="text/markdown",
)
def knowledge_index() -> str:
    """論文由来の宣言的知識ノートの1行索引を返す。"""
    return knowledge_store.build_index(mcp_core.KNOWLEDGE_DIR, "knowledge")


@mcp.resource(
    "lipidmix://playbook/index",
    name="playbook_index",
    title="Playbook index (reusable analysis workflows)",
    description=(
        "One line per playbook note (when_to_use). Consult this before proposing a "
        "workflow; expand only relevant slugs."
    ),
    mime_type="text/markdown",
)
def playbook_index() -> str:
    """再利用可能な解析手順ノートの1行索引を返す。"""
    return knowledge_store.build_index(PLAYBOOK_DIR, "playbook")


@mcp.resource(
    "lipidmix://knowledge/expand/{slug}",
    name="knowledge_expand",
    title="Expand a knowledge note with its 1-hop neighbors",
    description=(
        "Returns the note body plus directly-linked neighbors within a structural "
        "budget (1 hop, 5 bodies, ~15k tokens). Overflow is demoted to index lines."
    ),
    mime_type="text/markdown",
)
def knowledge_expand(slug: str) -> str:
    """knowledge ノートを1ホップ展開して予算内で返す。"""
    return knowledge_store.expand(slug, [mcp_core.KNOWLEDGE_DIR, PLAYBOOK_DIR])


@mcp.resource(
    "lipidmix://playbook/expand/{slug}",
    name="playbook_expand",
    title="Expand a playbook note with its 1-hop neighbors",
    description=(
        "Returns the playbook body plus directly-linked neighbors within a "
        "structural budget (1 hop, 5 bodies, ~15k tokens). Overflow is demoted."
    ),
    mime_type="text/markdown",
)
def playbook_expand(slug: str) -> str:
    """playbook ノートを1ホップ展開して予算内で返す。"""
    return knowledge_store.expand(slug, [PLAYBOOK_DIR, mcp_core.KNOWLEDGE_DIR])


@mcp.resource(
    "lipidmix://knowledge/inbox",
    name="knowledge_inbox",
    title="Pending literature notes awaiting review",
    description=(
        "Quarantined (speculative) notes from gap-driven discovery, grouped by "
        "analysis_id/Qi. Promote with ingest_promote or discard with ingest_reject."
    ),
    mime_type="text/markdown",
)
def knowledge_inbox() -> str:
    """_inbox の保留中ノートを found_for/query/score 付きで一覧する。"""
    return knowledge_store.build_inbox_index(mcp_core.KNOWLEDGE_DIR)
