"""解析・解釈レポートと図の保存ツール群。

write/read/list_reports, save_pca_figure, save_volcano_figure, save_eic_figure。
レポート先の解決は mcp_core（DATA_DIR を動的参照）に委ねる。deps: mcp_core /
session_state / tool_helpers / knowledge_store / matplotlib。
tools_* / server は import しない。
"""
import math

import matplotlib.pyplot as plt

import knowledge_store
import session_state
from mcp_core import mcp, _resolve_report_dir, _report_dir_candidates, _build_report_meta
from tool_helpers import _pca_scatter_arrays
from eic_plot import render_eic_plot

__all__ = [
    "write_report",
    "read_report",
    "list_reports",
    "save_pca_figure",
    "save_volcano_figure",
    "save_eic_figure",
]


# --- 解析・解釈レポート（reports/<analysis_id>.md） ---
@mcp.tool()
def write_report(
    analysis_id: str,
    dataset: str,
    body: str,
    status: str = "draft",
    knowledge_refs: list[str] | None = None,
) -> str:
    """解析・解釈レポートを reports/<analysis_id>.md に上書き保存する（成果物＋記録）。

    body は frontmatter を含まない Markdown 本文。推奨セクション見出し:
    `## 目的` / `## 実施した解析` / `## 主要な所見` / `## 解釈` /
    `## 注意点・コンフリクト` / `## 結論`。所見が増えたら本文を作り直して再度呼ぶ
    （ファイルは毎回上書き）。引用した knowledge/playbook の slug を knowledge_refs に渡す。
    書き込み先は解析フォルダ配下 reports/、不可なら LIPIDMIX_REPORTS_DIR（既定 <project>/reports）。

    analysis_id はファイル名 slug の元になるため ASCII で一意に。別IDが同一slugに潰れて既存
    レポートを上書きしそうな場合は保存を中止して通知する（objectiveレコードと同じIDを推奨）。
    """
    slug = knowledge_store.make_slug(analysis_id)
    # slug衝突ガード: 別の analysis_id が同一ファイル名に潰れる場合は、既存レポートを
    # 黙って上書きせず中止する（同一IDの上書きは意図どおり許可）。非ASCII/記号違いのIDで起きうる。
    for directory in _report_dir_candidates():
        existing = directory / f"{slug}.md"
        if existing.is_file():
            ex_meta, _ = knowledge_store.parse_frontmatter(existing.read_text(encoding="utf-8"))
            ex_id = str(ex_meta.get("analysis_id", ""))
            if ex_id and ex_id != analysis_id:
                return (
                    f"slug衝突のため中止: analysis_id '{analysis_id}' はファイル名 '{slug}.md' に潰れますが、"
                    f"そこには別の '{ex_id}' のレポートが既にあります（{existing}）。"
                    "上書きを避けました。ASCIIで一意な analysis_id を指定してください。"
                )
            break  # 同一ID → 上書きしてよい
    reports_dir = _resolve_report_dir()
    meta = _build_report_meta(analysis_id, dataset, status, knowledge_refs)
    path = knowledge_store.write_note(reports_dir, slug, meta, body)
    return f"レポートを保存: {path}（status={status}）。read_report('{analysis_id}') で読み戻せます。"


@mcp.tool()
def read_report(analysis_id: str) -> str:
    """過去レポートを読み戻す（候補ディレクトリ横断で最新更新のものを返す）。セッション継続用。

    解析フォルダ配下と退避先の両方に同名レポートが残る場合（書き込み可否が途中で変化した等）、
    古い方を返さないよう mtime が最新のファイルを採用する。
    """
    slug = knowledge_store.make_slug(analysis_id)
    matches = [d / f"{slug}.md" for d in _report_dir_candidates()]
    matches = [p for p in matches if p.is_file()]
    if not matches:
        return f"レポートが見つかりません: {analysis_id}（write_report で作成してください）"
    newest = max(matches, key=lambda p: p.stat().st_mtime)
    return newest.read_text(encoding="utf-8")


@mcp.tool()
def list_reports() -> str:
    """既存レポートの1行索引（analysis_id / date / status）を返す。

    候補ディレクトリ横断で同一 analysis_id が重複する場合は mtime が最新の1件を採用する。
    """
    # analysis_id -> (mtime, 表示行)。最新更新の行を残す。
    best: dict[str, tuple[float, str]] = {}
    for directory in _report_dir_candidates():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            meta, _body = knowledge_store.parse_frontmatter(path.read_text(encoding="utf-8"))
            if meta.get("type") != "report":
                continue
            aid = str(meta.get("analysis_id", path.stem))
            line = f"- {aid} | date={meta.get('date', '?')} | status={meta.get('status', '?')}"
            mtime = path.stat().st_mtime
            if aid not in best or mtime > best[aid][0]:
                best[aid] = (mtime, line)
    lines = ["# レポート一覧"]
    for aid, (_mtime, line) in sorted(best.items()):
        lines.append(line)
    if len(lines) == 1:
        lines.append("（レポートはまだありません）")
    return "\n".join(lines)


@mcp.tool()
def save_pca_figure(analysis_id: str, title: str | None = None) -> str:
    """明示的なユーザー要求時だけ、直近のセッションPCA結果をPNGとして保存する。

    先に arf_parser / arf_pca_preprocessed / load_dataset 等でPCAを実行する。通常の
    対話描画ではこのツールを呼ばず、各MCPクライアントのUIへ描画を任せる（PCA座標は
    解析ツールの返り値に同梱されている）。返り値の相対パスは write_report の本文に
    `![PCA](figures/<analysis_id>_pca.png)` として埋め込める。
    """
    plot = getattr(session_state.session, "last_pca_plot", None)
    if not plot or not plot.get("points"):
        return "先に arf_parser / arf_re_pca / pai2_parser 等でPCAを実行してください（PCA結果がありません）。"

    slug = knowledge_store.make_slug(analysis_id)
    reports_dir = _resolve_report_dir()
    figures_dir = reports_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    xs, ys, labels, x_label, y_label, plot_title = _pca_scatter_arrays(plot)
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(xs, ys, alpha=0.6)
    for x, y, label in zip(xs, ys, labels):
        if label:
            ax.annotate(str(label), (x, y), fontsize=8)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title or plot_title)
    out_path = figures_dir / f"{slug}_pca.png"
    try:
        fig.savefig(out_path, format="png", bbox_inches="tight")
    finally:
        plt.close(fig)

    rel = f"figures/{out_path.name}"
    return f"PCA図を保存: {out_path}\n本文に ![PCA]({rel}) で埋め込めます。"


@mcp.tool()
def save_volcano_figure(analysis_id: str, title: str | None = None) -> str:
    """明示的なユーザー要求時だけ、直近の差次的解析を volcano プロットのPNGとして
    reports/figures/<analysis_id>_volcano.png に保存し、相対パスを返す。

    先に arf_differential（2群比較）を実行する。通常の対話描画ではこのツールを呼ばず、
    arf_plot_volcano で構造化した点列を返して各MCPクライアントのUIへ描画を任せる。
    なお本PNGは間引き前の全特徴を描く（arf_plot_volcano は ns 点を間引くことがある）。
    返り値の相対パスは write_report の本文に
    `![volcano](figures/<analysis_id>_volcano.png)` として埋め込める。
    """
    last = getattr(session_state.session, "last_differential", None)
    if not last or not last.get("volcano"):
        return "[error] 直近の差次的解析（volcano データ）がありません。先に arf_differential を実行してください。"

    slug = knowledge_store.make_slug(analysis_id)
    reports_dir = _resolve_report_dir()
    figures_dir = reports_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    colors = {"up": "#c0392b", "down": "#2471a3", "ns": "#95a5a6"}
    fig, ax = plt.subplots(figsize=(6, 5))
    for sig in ("ns", "up", "down"):
        pts = [p for p in last["volcano"] if p["sig"] == sig
               and p["log2fc"] is not None and math.isfinite(p["log2fc"])
               and math.isfinite(p["neg_log10_p"])]
        if pts:
            ax.scatter([p["log2fc"] for p in pts], [p["neg_log10_p"] for p in pts],
                       s=12, c=colors[sig], label=sig, alpha=0.7)
    ax.set_xlabel("log2 fold change")
    ax.set_ylabel("-log10 p")
    ax.set_title(title or f"Volcano ({last.get('a')} vs {last.get('b')})")
    ax.legend()
    out_path = figures_dir / f"{slug}_volcano.png"
    try:
        fig.savefig(out_path, dpi=120, bbox_inches="tight")
    finally:
        plt.close(fig)

    rel = f"figures/{out_path.name}"
    return f"volcano図を保存: {out_path}\n本文に ![volcano]({rel}) で埋め込めます。"


@mcp.tool()
def save_eic_figure(analysis_id: str, title: str | None = None) -> str:
    """明示的なユーザー要求時だけ、直近EICプロット情報をPNGとして保存する。

    先に ``eic_plot_chromatograms``（1物質×複数サンプル）または ``eic_plot_compounds``
    （複数物質×1サンプル）でクライアント描画用の構造化情報を作る。通常の対話描画では
    このツールを呼ばず、各MCPクライアントのUIへ描画を任せる。
    """
    plot = getattr(session_state.session, "last_eic_plot", None)
    if not plot or not plot.get("series"):
        return (
            "先に eic_plot_chromatograms または eic_plot_compounds を実行してください"
            "（EICプロット情報がありません）。"
        )

    slug = knowledge_store.make_slug(analysis_id)
    reports_dir = _resolve_report_dir()
    figures_dir = reports_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    out_path = figures_dir / f"{slug}_eic.png"
    fig = render_eic_plot(plot, title=title)
    try:
        fig.savefig(out_path, dpi=120, format="png", bbox_inches="tight")
    finally:
        plt.close(fig)

    rel = f"figures/{out_path.name}"
    return f"EIC図を保存: {out_path}\n本文に ![EIC]({rel}) で埋め込めます。"
