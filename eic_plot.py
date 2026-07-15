"""Client-neutral EIC plot payload and optional matplotlib rendering."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from msdial_classes import parse_analysis_file_classes, resolve_mddata_path


class PlotAxis(TypedDict):
    label: str
    unit: str | None
    scale: str


class PlotAxes(TypedDict):
    x: PlotAxis
    y: PlotAxis


class PlotSource(TypedDict):
    file: str
    file_name: str


class PlotSpot(TypedDict):
    spot_id: int
    rt: float
    ri: float
    mz: float
    drift: float
    main_type: int
    total_samples: int
    selected_samples: int


class PlotSeries(TypedDict):
    id: str
    label: str
    file_id: int
    sample_name: str | None
    class_id: str | None
    x: list[float]
    y: list[float]
    peak_left: float
    peak_top: float
    peak_right: float
    max_intensity: float
    mean_intensity: float
    point_count: int


class RenderHints(TypedDict):
    mode: str
    connect_points: bool
    show_legend: bool
    hover_fields: list[str]


class EICPlotPayload(TypedDict):
    plot_schema: str
    plot_type: str
    title: str
    source: PlotSource
    axes: PlotAxes
    spot: PlotSpot
    normalization: str
    series: list[PlotSeries]
    render_hints: RenderHints
    caveats: list[str]


def _axis_definition(main_type: int) -> tuple[str, str | None]:
    if main_type == 0:
        return "RT", "min"
    return f"Coordinate (main_type={main_type})", None


def _sample_metadata(file_path: str | Path) -> tuple[dict[int, dict], list[str]]:
    caveats = []
    try:
        mddata_path = resolve_mddata_path(file_path)
        if mddata_path is None:
            return {}, ["Adjacent .mddata was not found; series labels use FileID."]
        records = parse_analysis_file_classes(mddata_path)
        return {
            record["file_id"]: record
            for record in records
            if isinstance(record.get("file_id"), int)
        }, caveats
    except Exception as exc:
        return {}, [f"Sample metadata could not be loaded; series labels use FileID: {exc}"]


def build_eic_plot_payload(
    spot: dict,
    file_path: str | Path,
    *,
    normalize: str = "none",
    title: str | None = None,
) -> EICPlotPayload:
    """Build a renderer-neutral line-plot payload from selected EIC traces."""
    if normalize not in {"none", "per_trace_max"}:
        raise ValueError("normalize must be 'none' or 'per_trace_max'")

    path = Path(file_path).resolve()
    metadata, caveats = _sample_metadata(path)
    x_label, x_unit = _axis_definition(int(spot["main_type"]))
    series = []
    for sample in spot["samples"]:
        file_id = int(sample["file_id"])
        record = metadata.get(file_id, {})
        sample_name = record.get("file_name")
        class_id = record.get("class_id")
        label = sample_name or f"FileID {file_id}"
        x_values = [float(point[0]) for point in sample["chromatogram"]]
        raw_y = [float(point[1]) for point in sample["chromatogram"]]
        if normalize == "per_trace_max":
            denominator = max(raw_y, default=0.0)
            y_values = [value / denominator if denominator > 0 else 0.0 for value in raw_y]
        else:
            y_values = raw_y
        series.append({
            "id": f"file-{file_id}",
            "label": str(label),
            "file_id": file_id,
            "sample_name": str(sample_name) if sample_name else None,
            "class_id": str(class_id) if class_id else None,
            "x": x_values,
            "y": y_values,
            "peak_left": float(sample["peak_left"]),
            "peak_top": float(sample["peak_top"]),
            "peak_right": float(sample["peak_right"]),
            "max_intensity": float(sample["max_intensity"]),
            "mean_intensity": float(sample["mean_intensity"]),
            "point_count": int(sample["num_points"]),
        })

    y_label = "Relative intensity" if normalize == "per_trace_max" else "Intensity"
    plot_title = title or (
        f"EIC spot {spot['spot_id']} | m/z {spot['mz']:.4f} | RT {spot['rt']:.2f} min"
    )
    return {
        "plot_schema": "lipidmix.eic.v1",
        "plot_type": "line",
        "title": plot_title,
        "source": {"file": str(path), "file_name": path.name},
        "axes": {
            "x": {"label": x_label, "unit": x_unit, "scale": "linear"},
            "y": {"label": y_label, "unit": None, "scale": "linear"},
        },
        "spot": {
            "spot_id": int(spot["spot_id"]),
            "rt": float(spot["rt"]),
            "ri": float(spot["ri"]),
            "mz": float(spot["mz"]),
            "drift": float(spot["drift"]),
            "main_type": int(spot["main_type"]),
            "total_samples": int(spot["num_samples"]),
            "selected_samples": int(spot["selected_samples"]),
        },
        "normalization": normalize,
        "series": series,
        "render_hints": {
            "mode": "lines",
            "connect_points": True,
            "show_legend": True,
            "hover_fields": [
                "label", "file_id", "class_id", "peak_left", "peak_top",
                "peak_right", "max_intensity",
            ],
        },
        "caveats": caveats,
    }


def render_eic_plot(payload: EICPlotPayload, title: str | None = None):
    """Render an approved EIC payload to a matplotlib figure for explicit saving."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 6))
    for item in payload["series"]:
        line = ax.plot(item["x"], item["y"], label=item["label"], linewidth=1.4)[0]
        if item["x"]:
            nearest = min(
                range(len(item["x"])),
                key=lambda index: abs(item["x"][index] - item["peak_top"]),
            )
            ax.scatter(
                [item["x"][nearest]], [item["y"][nearest]],
                color=line.get_color(), s=20, zorder=3,
            )
    if len(payload["series"]) == 1:
        item = payload["series"][0]
        ax.axvspan(item["peak_left"], item["peak_right"], color="#999999", alpha=0.12)
    x_axis = payload["axes"]["x"]
    y_axis = payload["axes"]["y"]
    x_label = x_axis["label"] + (f" ({x_axis['unit']})" if x_axis["unit"] else "")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_axis["label"])
    ax.set_title(title or payload["title"])
    ax.legend(fontsize=8, loc="best")
    ax.grid(alpha=0.2)
    return fig
