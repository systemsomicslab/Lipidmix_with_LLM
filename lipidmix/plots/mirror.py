"""測定と参照の対向プロット（mirror plot）。

MS-DIAL の peak spot 画面が出す `Deconvolution vs. Reference` と同じ形——
上段が測定（上向き）、下段が参照（下向き。強度に `-1` を掛ける）、横軸 m/z 共通。

`lipidmix.analysis.spectral_match.match_spectrum` が返す `alignment`
（参照グリッドの窓ごとの `{"mz", "measured", "reference", "matched"}`）を
どの m/z が一致したかの注釈として使う。数値スコアだけでは分からない
「どのフラグメントが合っていて、どれが欠けているか」を人が見て判断できる
ようにするのがこの層の役割。

`lipidmix.plots.volcano` / `eic` と同じ役割分担: payload の組み立て（純ロジック、
matplotlib 非依存）と、明示的に図が要るときだけ使う描画（`render_mirror`）を分ける。
matplotlib は関数内 import に留め、payload 組み立てだけを使う経路には載せない。

依存グラフ: このモジュールは `lipidmix.plots.render` にのみ依存する。
`mcp_core` / `session_state` / `lipidmix.tools.*` / `lipidmix.library.*` /
`lipidmix.analysis.*` は import しない（「渡されたものを描く」だけの層）。

payload の `measured` / `reference` は**生のスペクトル**（正規化前）をそのまま
保持する。正規化は描画時に行う。座標をセッションへ保持できる形にしておくのが
狙いなので、ここでは payload を返すだけに留める（セッションへの保持は別レイヤの
責務）。
"""
from __future__ import annotations

from typing import TypedDict

MIRROR_PLOT_SCHEMA = "lipidmix.mirror.v1"

DEFAULT_TOP_LABELS = 8


class MirrorLabel(TypedDict):
    mz: float
    intensity: float
    side: str  # "measured" | "reference"


class MirrorPayload(TypedDict):
    plot_schema: str
    plot_type: str
    title: str
    measured: list[list[float]]
    reference: list[list[float]]
    matched_mz: list[float]
    labels: list[MirrorLabel]


def build_mirror_payload(
    measured: list[list[float]],
    reference: list[list[float]],
    alignment: list[dict],
    *,
    title: str,
    top_labels: int = DEFAULT_TOP_LABELS,
) -> MirrorPayload:
    """測定・参照スペクトル（生の値）と一致した m/z を対向プロット用にまとめる。

    `measured` / `reference` は `[[mz, intensity], ...]` の生のスペクトル
    （正規化前）をそのまま保持する。正規化は描画時（`render_mirror`）に行う。
    `alignment` は `match_spectrum` の戻り値の同名フィールド
    （参照グリッドの窓ごとの `{"mz", "measured", "reference", "matched"}`）で、
    `matched: True` の窓の `mz` だけを `matched_mz` として抜き出す。

    `labels` は測定・参照を強度でまとめて降順に並べ、上位 `top_labels` 件
    （測定・参照の合計で打ち切り）だけを残す。ラベルが多すぎると重なって
    読めなくなるための上限で、`build_volcano_plot_payload` の `max_points` と
    同じ考え方。
    """
    if isinstance(top_labels, bool) or not isinstance(top_labels, int) or top_labels < 0:
        raise ValueError("top_labels must be a non-negative integer")

    measured_points = [[float(p[0]), float(p[1])] for p in (measured or [])]
    reference_points = [[float(p[0]), float(p[1])] for p in (reference or [])]
    matched_mz = [
        float(entry["mz"]) for entry in (alignment or []) if entry.get("matched")
    ]

    candidates: list[MirrorLabel] = [
        {"mz": mz, "intensity": intensity, "side": "measured"}
        for mz, intensity in measured_points
    ] + [
        {"mz": mz, "intensity": intensity, "side": "reference"}
        for mz, intensity in reference_points
    ]
    candidates.sort(key=lambda item: item["intensity"], reverse=True)
    labels = candidates[:top_labels]

    return {
        "plot_schema": MIRROR_PLOT_SCHEMA,
        "plot_type": "mirror",
        "title": title,
        "measured": measured_points,
        "reference": reference_points,
        "matched_mz": matched_mz,
        "labels": labels,
    }


_MEASURED_COLOR = "#2471a3"  # 上段・測定（volcano の down と同系統の青）
_REFERENCE_COLOR = "#c0392b"  # 下段・参照（volcano の up と同系統の赤）
_MATCHED_COLOR = "#27ae60"  # 一致した m/z の印（緑）


def _normalized_by_max(points: list[list[float]]) -> list[list[float]]:
    """自分自身の最大強度で正規化する（生の強度だと片方が潰れるため）。"""
    if not points:
        return []
    max_intensity = max(intensity for _, intensity in points)
    if max_intensity <= 0:
        return [[mz, 0.0] for mz, _ in points]
    return [[mz, intensity / max_intensity] for mz, intensity in points]


def render_mirror(payload: MirrorPayload):
    """対向プロットを PNG バイト列にする（`lipidmix.plots.render.figure_to_png` 経由）。

    上段が測定（上向き）、下段が参照（下向き。正規化強度に `-1` を掛ける）。
    一致した m/z（`payload["matched_mz"]`）は縦の目印線で示し、対応する測定・参照
    のステムを一致色に変える。ラベルは `payload["labels"]` のみ（上位 `top_labels` 本）。
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from lipidmix.plots.render import figure_to_png

    measured = _normalized_by_max(payload["measured"])
    reference = _normalized_by_max(payload["reference"])
    matched_mz = set(payload.get("matched_mz") or [])

    fig, ax = plt.subplots(figsize=(9, 5))

    for mz in sorted(matched_mz):
        ax.axvline(mz, color=_MATCHED_COLOR, alpha=0.25, linewidth=1.0, zorder=0)

    if measured:
        colors = [_MATCHED_COLOR if mz in matched_mz else _MEASURED_COLOR for mz, _ in measured]
        ax.vlines(
            [mz for mz, _ in measured], 0, [intensity for _, intensity in measured],
            colors=colors, linewidth=1.4,
        )
        ax.scatter(
            [mz for mz, _ in measured], [intensity for _, intensity in measured],
            color=colors, s=10, zorder=3,
        )

    if reference:
        colors = [_MATCHED_COLOR if mz in matched_mz else _REFERENCE_COLOR for mz, _ in reference]
        ax.vlines(
            [mz for mz, _ in reference], 0, [-intensity for _, intensity in reference],
            colors=colors, linewidth=1.4,
        )
        ax.scatter(
            [mz for mz, _ in reference], [-intensity for _, intensity in reference],
            color=colors, s=10, zorder=3,
        )

    raw_max_measured = max(
        (intensity for _, intensity in payload["measured"]), default=0.0
    )
    raw_max_reference = max(
        (intensity for _, intensity in payload["reference"]), default=0.0
    )
    for label in payload.get("labels") or []:
        raw_max = raw_max_measured if label["side"] == "measured" else raw_max_reference
        normalized = label["intensity"] / raw_max if raw_max > 0 else 0.0
        y = normalized if label["side"] == "measured" else -normalized
        ax.annotate(
            f"{label['mz']:.4f}",
            xy=(label["mz"], y),
            xytext=(0, 4 if label["side"] == "measured" else -4),
            textcoords="offset points",
            fontsize=6,
            ha="center",
            va="bottom" if label["side"] == "measured" else "top",
        )

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xlabel("m/z")
    ax.set_ylabel("Relative intensity (measured / reference)")
    ax.set_ylim(-1.15, 1.15)
    ax.set_title(payload.get("title") or "")

    from matplotlib.lines import Line2D

    legend_handles = [
        Line2D([0], [0], color=_MEASURED_COLOR, lw=1.4, label="Measured"),
        Line2D([0], [0], color=_REFERENCE_COLOR, lw=1.4, label="Reference"),
        Line2D([0], [0], color=_MATCHED_COLOR, lw=1.4, label="Matched"),
    ]
    ax.legend(handles=legend_handles, fontsize=8, loc="best")

    return figure_to_png(fig)
