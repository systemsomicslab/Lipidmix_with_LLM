"""Client-neutral volcano plot payload built from a two-group differential result.

`lipidmix.plots.eic` と同じ役割で、MCP にも matplotlib にも依存しない。`lipidmix.analysis.differential`
が作った点列を、クライアントがそのまま描ける形へ整形するだけの純ロジック層。

点数は特徴量数ぶん（数千〜数万件）になりうるため `ns` 点だけを決定的に間引く。
`up`/`down` を切らないのは、有意点を落とすと図の意味が壊れるため。
"""
from __future__ import annotations

import math
from typing import TypedDict

VOLCANO_PLOT_SCHEMA = "lipidmix.volcano.v1"

DEFAULT_Q_THRESHOLD = 0.05
DEFAULT_LOG2FC_THRESHOLD = 1.0
_SIGNIFICANT = ("up", "down")


class PlotAxis(TypedDict):
    label: str
    unit: str | None
    scale: str


class PlotAxes(TypedDict):
    x: PlotAxis
    y: PlotAxis


class VolcanoComparison(TypedDict):
    group_a: str
    group_b: str
    n_a: int | None
    n_b: int | None


class VolcanoThresholds(TypedDict):
    q: float
    log2fc: float


class VolcanoPoint(TypedDict):
    feature: str
    log2fc: float
    neg_log10_p: float
    sig: str


class VolcanoSelection(TypedDict):
    total: int
    plotted: int
    significant_total: int
    significant_plotted: int
    ns_total: int
    ns_plotted: int
    max_points: int
    dropped_nonfinite: int


class VolcanoGuides(TypedDict):
    x: list[float]
    y: list[float]


class VolcanoRenderHints(TypedDict):
    mode: str
    color_by: str
    show_legend: bool
    guides: VolcanoGuides
    hover_fields: list[str]


class VolcanoPlotPayload(TypedDict):
    plot_schema: str
    plot_type: str
    title: str
    comparison: VolcanoComparison
    axes: PlotAxes
    thresholds: VolcanoThresholds
    points: list[VolcanoPoint]
    selection: VolcanoSelection
    render_hints: VolcanoRenderHints
    caveats: list[str]


def _is_drawable(point: dict) -> bool:
    """log2fc と -log10 p の両方が有限なら描画できる。"""
    values = (point.get("log2fc"), point.get("neg_log10_p"))
    return all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in values
    )


def _subsample(points: list[dict], quota: int) -> list[dict]:
    """先頭から等間隔に最大 `quota` 件を抽出する（決定的・乱数なし）。

    `(index * total) // quota` は `total >= quota` のとき厳密に単調増加するため、
    重複なくちょうど `quota` 件を返す。x/y の分布形を保ったまま点数だけ落とす。
    """
    if quota <= 0 or not points:
        return []
    total = len(points)
    if total <= quota:
        return list(points)
    return [points[(index * total) // quota] for index in range(quota)]


def build_volcano_plot_payload(
    last_differential: dict,
    *,
    max_points: int = 3000,
    title: str | None = None,
) -> VolcanoPlotPayload:
    """`session.arf.last_differential` から描画中立な volcano ペイロードを組み立てる。"""
    if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points < 1:
        raise ValueError("max_points must be a positive integer")

    raw = list(last_differential.get("volcano") or [])
    q_threshold = float(last_differential.get("q_threshold") or DEFAULT_Q_THRESHOLD)
    log2fc_threshold = float(
        last_differential.get("log2fc_threshold") or DEFAULT_LOG2FC_THRESHOLD
    )
    group_a = str(last_differential.get("a") or "")
    group_b = str(last_differential.get("b") or "")

    caveats: list[str] = []
    drawable = [point for point in raw if _is_drawable(point)]
    dropped_nonfinite = len(raw) - len(drawable)
    if dropped_nonfinite:
        caveats.append(
            f"log2fc または p が有限値でない {dropped_nonfinite} 件は描画対象から外しました"
            "（分散0・欠損・片群のみ検出などで検定不能。有意でないという意味ではありません）。"
        )

    significant = [p for p in drawable if p.get("sig") in _SIGNIFICANT]
    ns = [p for p in drawable if p.get("sig") not in _SIGNIFICANT]
    quota = max_points - len(significant)
    if quota <= 0:
        ns_plotted: list[dict] = []
        if ns:
            caveats.append(
                f"有意点 {len(significant)} 件だけで max_points={max_points} に達したため、"
                f"ns 点 {len(ns)} 件は全て省略しました（有意点は間引いていません）。"
            )
    else:
        ns_plotted = _subsample(ns, quota)
        if len(ns_plotted) < len(ns):
            caveats.append(
                f"ns 点は {len(ns)} 件から {len(ns_plotted)} 件へ等間隔で間引きました"
                "（up/down は全件保持）。件数の判断には selection を参照してください。"
            )

    caveats.append(
        "横破線は -log10(q しきい値) の目安であり、各点の y は -log10(p) です"
        "（q と p は別量なので破線位置と有意判定は厳密には一致しません）。"
    )

    points: list[VolcanoPoint] = [
        {
            "feature": str(point.get("feature") or ""),
            "log2fc": float(point["log2fc"]),
            "neg_log10_p": float(point["neg_log10_p"]),
            "sig": str(point.get("sig") or "ns"),
        }
        for point in significant + ns_plotted
    ]

    guide_y = -math.log10(q_threshold) if q_threshold > 0 else 0.0
    return {
        "plot_schema": VOLCANO_PLOT_SCHEMA,
        "plot_type": "scatter",
        "title": title or f"Volcano ({group_a} vs {group_b})",
        "comparison": {
            "group_a": group_a,
            "group_b": group_b,
            "n_a": last_differential.get("n_a"),
            "n_b": last_differential.get("n_b"),
        },
        "axes": {
            "x": {"label": "log2 fold change", "unit": None, "scale": "linear"},
            "y": {"label": "-log10 p", "unit": None, "scale": "linear"},
        },
        "thresholds": {"q": q_threshold, "log2fc": log2fc_threshold},
        "points": points,
        "selection": {
            "total": len(raw),
            "plotted": len(points),
            "significant_total": len(significant),
            "significant_plotted": len(significant),
            "ns_total": len(ns),
            "ns_plotted": len(ns_plotted),
            "max_points": max_points,
            "dropped_nonfinite": dropped_nonfinite,
        },
        "render_hints": {
            "mode": "markers",
            "color_by": "sig",
            "show_legend": True,
            "guides": {
                "x": [-log2fc_threshold, log2fc_threshold],
                "y": [round(guide_y, 4)],
            },
            "hover_fields": ["feature", "log2fc", "neg_log10_p", "sig"],
        },
        "caveats": caveats,
    }
