import base64
import csv
import gzip
import math
import os
import re
import subprocess
import struct
import sys
import threading
import tkinter as tk
import xml.etree.ElementTree as ET
import zlib
from bisect import bisect_left
from dataclasses import dataclass
from tkinter import colorchooser, filedialog, messagebox, ttk

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

try:
    from pyteomics import mzml as pyteomics_mzml
except ImportError:
    pyteomics_mzml = None


def mzml_reader_requirement_message():
    return (
        "pyteomics and psims are required for recommended mzML reading.\n\n"
        "Install them into the Python that is running this app:\n"
        f'"{sys.executable}" -m pip install pyteomics psims'
    )


def ensure_pyteomics_mzml():
    global pyteomics_mzml
    if pyteomics_mzml is not None:
        return True
    try:
        import psims  # noqa: F401
        from pyteomics import mzml as loaded_mzml
    except ImportError:
        return False
    pyteomics_mzml = loaded_mzml
    return True


DEFAULT_LIBRARY = r"C:\Kiban S\toya\toya_20241213_LCMS\20241213_toya\pos_mrmprobs_reference.txt"
DEFAULT_RAW_ROOT = r"C:\Kiban S\toya\toya_20241213_LCMS\20241213_toya\POS"
CARBON_ISOTOPE_MASS = 1.003354835


def detect_msconvert():
    candidates = [
        r"C:\Program Files\ProteoWizard\ProteoWizard 3.0.25248\msconvert.exe",
        r"C:\Program Files\ProteoWizard\ProteoWizard 3.0.25233\msconvert.exe",
        r"C:\Program Files\ProteoWizard\ProteoWizard 3.0.25164\msconvert.exe",
        r"C:\Program Files\ProteoWizard\msconvert.exe",
        r"C:\Program Files (x86)\ProteoWizard\msconvert.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    for folder in [
        r"C:\Program Files\ProteoWizard",
        r"C:\Program Files (x86)\ProteoWizard",
    ]:
        if not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder):
            if "msconvert.exe" in files:
                return os.path.join(root, "msconvert.exe")
    return ""


def safe_filename(value):
    keep = []
    for char in value:
        if char.isalnum() or char in ("-", "_", "."):
            keep.append(char)
        else:
            keep.append("_")
    return "".join(keep).strip("_") or "output"


def read_msdial_alignment(path):
    header_line = None
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line_idx, line in enumerate(fh):
            cells = line.rstrip("\n").split("\t")
            if cells and cells[0] == "Alignment ID":
                header_line = line_idx
                break
    if header_line is None:
        raise ValueError("Could not find the MS-DIAL alignment header line.")
    df = pd.read_csv(path, sep="\t", skiprows=header_line, encoding="utf-8-sig")
    if "Alignment ID" in df.columns:
        numeric_alignment_id = pd.to_numeric(df["Alignment ID"], errors="coerce")
        df = df[numeric_alignment_id.notna()].copy()
    return df


def carbon_count_from_formula(formula):
    if not isinstance(formula, str) or not formula or formula.lower() == "null":
        return None
    match = re.search(r"C(\d*)", formula)
    if not match:
        return None
    return int(match.group(1) or "1")


def is_blank_name(name):
    if not isinstance(name, str):
        return True
    text = name.strip()
    return not text or text.lower() in {"null", "unknown", "nan", "na"}


def build_isotope_library_rows(alignment_path, rt_tolerance, ms1_tolerance):
    df = read_msdial_alignment(alignment_path)
    required = ["Average Rt(min)", "Average Mz", "Metabolite name", "Formula"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required MS-DIAL columns: {', '.join(missing)}")

    if "Isotope tracking weight number" in df.columns:
        df = df.sort_values("Isotope tracking weight number", kind="stable")

    rows = []
    used_names = set()
    for _, row in df.iterrows():
        metabolite = str(row["Metabolite name"]).strip()
        if is_blank_name(metabolite) or metabolite in used_names:
            continue
        carbon_count = carbon_count_from_formula(row.get("Formula", ""))
        if carbon_count is None or carbon_count < 1:
            continue
        try:
            rt_min = float(row["Average Rt(min)"])
            mz0 = float(row["Average Mz"])
        except (TypeError, ValueError):
            continue
        used_names.add(metabolite)
        for isotope_idx in range(carbon_count + 1):
            mz = mz0 + isotope_idx * CARBON_ISOTOPE_MASS
            rows.append(
                {
                    "Compound name": f"{metabolite}_{isotope_idx}",
                    "Precursor mz": round(mz, 6),
                    "Product mz": round(mz, 6),
                    "RT min": round(rt_min, 4),
                    "TQ Ratio": 100,
                    "RT begin": round(max(0.0, rt_min - rt_tolerance), 4),
                    "RT end": round(rt_min + rt_tolerance, 4),
                    "MS1 tolerance": ms1_tolerance,
                    "MS2 tolerance": 0.01,
                    "MS level": 1,
                    "Class": metabolite,
                }
            )
    if not rows:
        raise ValueError("No library rows were generated. Check metabolite names and formulas.")
    return rows


def write_library_rows(rows, output_path):
    columns = [
        "Compound name",
        "Precursor mz",
        "Product mz",
        "RT min",
        "TQ Ratio",
        "RT begin",
        "RT end",
        "MS1 tolerance",
        "MS2 tolerance",
        "MS level",
        "Class",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def points_in_range(points, rt_start, rt_end):
    return [(rt, intensity) for rt, intensity in points if rt_start <= rt <= rt_end]


def moving_average(values, level):
    normalization = 2 * level + 1
    smoothed = []
    for idx in range(len(values)):
        total = 0.0
        for offset in range(-level, level + 1):
            src_idx = idx + offset
            total += values[src_idx] if 0 <= src_idx < len(values) else values[idx]
        smoothed.append(total / normalization)
    return smoothed


def linear_weighted_moving_average(values, level):
    normalization = level + 1 + sum(i * 2 for i in range(1, level + 1))
    smoothed = []
    for idx in range(len(values)):
        weighted_sum = 0.0
        for offset in range(-level, level + 1):
            src_idx = idx + offset
            weight = level + 1 - abs(offset)
            weighted_sum += (values[src_idx] if 0 <= src_idx < len(values) else values[idx]) * weight
        smoothed.append(weighted_sum / normalization)
    return smoothed


def time_based_linear_weighted_moving_average(points, level):
    if len(points) <= 1:
        return [intensity for _rt, intensity in points]
    rts = [rt for rt, _intensity in points]
    intensities = [intensity for _rt, intensity in points]
    time_step = (max(rts) - min(rts)) / (len(points) - 1)
    if time_step <= 0:
        return linear_weighted_moving_average(intensities, level)

    smoothed = []
    start_idx = 0
    distance = level + 1
    for idx, rt in enumerate(rts):
        lo = rt - time_step * distance
        hi = rt + time_step * distance
        while start_idx < len(points) and rts[start_idx] <= lo:
            start_idx += 1

        weighted_sum = 0.0
        weight_total = 0.0
        for src_idx in range(start_idx, len(points)):
            if rts[src_idx] >= hi:
                break
            weight = distance - abs(rt - rts[src_idx]) / time_step
            weighted_sum += intensities[src_idx] * weight
            weight_total += weight
        smoothed.append(weighted_sum / weight_total if weight_total else intensities[idx])
    return smoothed


def binomial_coefficients(level):
    order = max(1, int(level) * 2)
    coeffs = [1]
    for _ in range(order):
        coeffs = [1] + [coeffs[i] + coeffs[i + 1] for i in range(len(coeffs) - 1)] + [1]
    normalization = sum(coeffs)
    return [coeff / normalization for coeff in coeffs]


def convolution_smooth(values, coeffs):
    radius = len(coeffs) // 2
    smoothed = []
    for idx in range(len(values)):
        weighted_sum = 0.0
        for coeff_idx, coeff in enumerate(coeffs):
            src_idx = idx + coeff_idx - radius
            weighted_sum += (values[src_idx] if 0 <= src_idx < len(values) else values[idx]) * coeff
        smoothed.append(weighted_sum)
    return smoothed


def solve_linear_system(matrix, vector):
    size = len(vector)
    aug = [row[:] + [vector[idx]] for idx, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_value = aug[col][col]
        aug[col] = [value / pivot_value for value in aug[col]]
        for row in range(size):
            if row == col:
                continue
            factor = aug[row][col]
            aug[row] = [aug[row][i] - factor * aug[col][i] for i in range(size + 1)]
    return [aug[row][-1] for row in range(size)]


def matrix_multiply(left, right):
    return [
        [
            sum(left[row][k] * right[k][col] for k in range(len(right)))
            for col in range(len(right[0]))
        ]
        for row in range(len(left))
    ]


def matrix_transpose(matrix):
    return [list(row) for row in zip(*matrix)]


def matrix_inverse(matrix):
    size = len(matrix)
    inverse_columns = []
    for col in range(size):
        unit = [0.0] * size
        unit[col] = 1.0
        solved = solve_linear_system(matrix, unit)
        if solved is None:
            return None
        inverse_columns.append(solved)
    return matrix_transpose(inverse_columns)


def savitzky_golay_coefficients(level):
    radius = max(1, int(level))
    xs = [(-radius + idx) for idx in range(2 * radius + 1)]
    vandermonde = [[float(x) ** power for power in range(4)] for x in xs]
    normal = matrix_multiply(matrix_transpose(vandermonde), vandermonde)
    inverse = matrix_inverse(normal)
    if inverse is None:
        return None
    hat = matrix_multiply(matrix_multiply(vandermonde, inverse), matrix_transpose(vandermonde))
    return hat[radius]


def savitzky_golay_smooth(values, level):
    coeffs = savitzky_golay_coefficients(level)
    if not coeffs:
        return values[:]
    return convolution_smooth(values, coeffs)


def tricube_coefficients(level):
    radius = max(1, int(level))
    coeffs = [0.0] * (2 * radius + 1)
    for idx in range(radius):
        value = (1 - (abs(idx - radius) / radius) ** 3) ** 3
        coeffs[idx] = value
        coeffs[2 * radius - idx] = value
    coeffs[radius] = 1.0
    return coeffs


def lowess_smooth(values, level):
    radius = max(1, int(level))
    coeffs = tricube_coefficients(radius)
    a = sum((idx - radius) ** 2 * coeff for idx, coeff in enumerate(coeffs))
    b = sum((idx - radius) * coeff for idx, coeff in enumerate(coeffs))
    d = sum(coeffs)
    det = a * d - b * b
    if abs(det) < 1e-12:
        return values[:]
    inv00, inv01, inv10, inv11 = d / det, -b / det, -b / det, a / det
    smoothed = []
    for idx in range(len(values)):
        aa = 0.0
        bb = 0.0
        for offset in range(-radius, radius + 1):
            src_idx = idx + offset
            value = values[src_idx] if 0 <= src_idx < len(values) else values[idx]
            coeff = coeffs[radius + offset]
            aa += value * offset * coeff
            bb += value * coeff
        smoothed.append(inv10 * aa + inv11 * bb)
    return smoothed


def loess_smooth(values, level):
    radius = max(1, int(level))
    coeffs = tricube_coefficients(radius)
    normal = []
    for row_power in (4, 3, 2):
        normal.append([
            sum((idx - radius) ** power * coeffs[idx] for idx in range(len(coeffs)))
            for power in range(row_power, row_power - 3, -1)
        ])
    inverse = matrix_inverse(normal)
    if inverse is None:
        return values[:]
    smoothed = []
    for idx in range(len(values)):
        rhs = [0.0, 0.0, 0.0]
        for offset in range(-radius, radius + 1):
            src_idx = idx + offset
            value = values[src_idx] if 0 <= src_idx < len(values) else values[idx]
            coeff = coeffs[radius + offset]
            rhs[0] += value * (offset ** 2) * coeff
            rhs[1] += value * offset * coeff
            rhs[2] += value * coeff
        solved = [
            sum(inverse[row][col] * rhs[col] for col in range(3))
            for row in range(3)
        ]
        smoothed.append(solved[2])
    return smoothed


def smooth_eic_points(points, smoothing_method, smoothing_level):
    ordered = sorted(points)
    level = max(0, int(smoothing_level))
    method = str(smoothing_method or "Linear weighted moving average").lower()
    if level <= 0 or len(ordered) <= 2 or method == "none":
        return ordered
    rts = [rt for rt, _intensity in ordered]
    intensities = [intensity for _rt, intensity in ordered]
    if method in {"moving average", "simple moving average"}:
        smoothed = moving_average(intensities, level)
    elif method == "time-based linear weighted moving average":
        smoothed = time_based_linear_weighted_moving_average(ordered, level)
    elif method == "savitzky-golay filter":
        smoothed = savitzky_golay_smooth(intensities, level)
    elif method == "binomial filter":
        smoothed = convolution_smooth(intensities, binomial_coefficients(level))
    elif method == "lowess filter":
        smoothed = lowess_smooth(intensities, level)
    elif method == "loess filter":
        smoothed = loess_smooth(intensities, level)
    else:
        smoothed = linear_weighted_moving_average(intensities, level)
    return list(zip(rts, smoothed))


def trapezoid_area(points):
    ordered = sorted(points)
    if len(ordered) < 2:
        return 0.0
    area = 0.0
    for (rt0, y0), (rt1, y1) in zip(ordered, ordered[1:]):
        area += max(0.0, rt1 - rt0) * (max(0.0, y0) + max(0.0, y1)) / 2.0
    return area


def summarize_peak(points, rt_start, rt_end, source="auto", enabled=True):
    selected = points_in_range(points, rt_start, rt_end)
    if not selected:
        return PeakSelection(rt_start, rt_end, 0.0, 0.0, (rt_start + rt_end) / 2.0, source, enabled)
    apex_rt, peak_height = max(selected, key=lambda item: item[1])
    return PeakSelection(
        rt_start=rt_start,
        rt_end=rt_end,
        peak_height=peak_height,
        peak_area=trapezoid_area(selected),
        apex_rt=apex_rt,
        source=source,
        enabled=enabled,
    )


def auto_pick_peak(points, target_rt, fallback_window, min_peak_width=5):
    ordered = sorted(points)
    if not ordered:
        rt_start = max(0.0, target_rt - fallback_window)
        return PeakSelection(rt_start, target_rt + fallback_window, 0.0, 0.0, target_rt, "auto", False)

    local = [
        (rt, intensity)
        for rt, intensity in ordered
        if target_rt - fallback_window <= rt <= target_rt + fallback_window
    ] or ordered
    apex_rt, apex_height = max(local, key=lambda item: item[1])
    baseline = min(intensity for _rt, intensity in local)
    threshold = baseline + max(apex_height - baseline, 0.0) * 0.1
    apex_index = min(range(len(ordered)), key=lambda idx: abs(ordered[idx][0] - apex_rt))

    left = apex_index
    while left > 0 and ordered[left][1] > threshold:
        left -= 1
    right = apex_index
    while right < len(ordered) - 1 and ordered[right][1] > threshold:
        right += 1

    min_points = max(1, int(min_peak_width))
    while right - left + 1 < min_points and (left > 0 or right < len(ordered) - 1):
        if left > 0:
            left -= 1
        if right - left + 1 >= min_points:
            break
        if right < len(ordered) - 1:
            right += 1

    rt_start = ordered[left][0]
    rt_end = ordered[right][0]
    if rt_start == rt_end:
        rt_start = max(0.0, apex_rt - fallback_window / 3.0)
        rt_end = apex_rt + fallback_window / 3.0
    return summarize_peak(ordered, rt_start, rt_end, source="auto", enabled=True)


def parse_hex_color(value, fallback):
    text = (value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            return tuple(int(text[i : i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return fallback
    return fallback


def normalize_grid_rows(grid):
    if not grid:
        return []
    if isinstance(grid[0], dict) and "panels" in grid[0]:
        return grid
    return [{"class_name": "", "panels": grid}]


def flatten_grid_panels(grid):
    return [panel for row in normalize_grid_rows(grid) for panel in row["panels"]]


def render_grid_png(title, grid, file_infos, output_path, panel_size=(360, 240), dpi=600):
    rows_data = normalize_grid_rows(grid)
    panel_w, panel_h = panel_size
    class_label_w = 150 if len(rows_data) > 1 or any(row.get("class_name") for row in rows_data) else 0
    max_columns = max([len(row["panels"]) for row in rows_data] or [1])
    legend_h = 52 + 18 * math.ceil(max(len(file_infos), 1) / 4)
    title_h = 44
    width = class_label_w + max_columns * panel_w + 48
    height = title_h + legend_h + max(1, len(rows_data)) * panel_h + 36
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    title_font = ImageFont.load_default()

    draw.text((24, 18), title, fill=(15, 23, 42), font=title_font)
    legend_y = title_h
    for idx, info in enumerate(file_infos):
        col = idx % 4
        row = idx // 4
        x = 24 + col * (width - 48) / 4
        y = legend_y + row * 18
        color = parse_hex_color(info["color"], (37, 99, 235))
        draw.rectangle((x, y + 2, x + 12, y + 14), fill=color, outline=color)
        label = f'{info["group"]}: {info["name"]}' if info.get("group") else info["name"]
        draw.text((x + 17, y), label[:42], fill=(51, 65, 85), font=font)

    origin_y = title_h + legend_h
    for row_idx, row in enumerate(rows_data):
        y0 = origin_y + row_idx * panel_h
        if class_label_w:
            draw.text((24, y0 + 12), str(row.get("class_name", ""))[:24], fill=(15, 23, 42), font=title_font)
        for col_idx, panel in enumerate(row["panels"]):
            x0 = 24 + class_label_w + col_idx * panel_w
            draw_single_panel(draw, panel, x0, y0, panel_w - 12, panel_h - 12, font)

    image.save(output_path, dpi=(dpi, dpi))


def draw_single_panel(draw, panel, x0, y0, width, height, font):
    left, right, top, bottom = 48, 14, 30, 32
    plot_x0 = x0 + left
    plot_y0 = y0 + top
    plot_x1 = x0 + width - right
    plot_y1 = y0 + height - bottom
    draw.rectangle((x0, y0, x0 + width, y0 + height), outline=(219, 228, 238), fill=(255, 255, 255))
    draw.text((x0 + 8, y0 + 8), panel["title"][:48], fill=(15, 23, 42), font=font)
    draw.line((plot_x0, plot_y1, plot_x1, plot_y1), fill=(148, 163, 184))
    draw.line((plot_x0, plot_y0, plot_x0, plot_y1), fill=(148, 163, 184))

    all_x = [x for item in panel["series"] for x, _ in item["points"] if math.isfinite(x)]
    all_y = [y for item in panel["series"] for _, y in item["points"] if math.isfinite(y)]
    if not all_x or not all_y:
        draw.text((x0 + width / 2 - 20, y0 + height / 2), "No data", fill=(148, 163, 184), font=font)
        return

    x_min, x_max = min(all_x), max(all_x)
    y_max = max(all_y)
    if x_min == x_max:
        x_min -= 0.05
        x_max += 0.05
    if y_max <= 0:
        y_max = 1.0

    def px(x):
        return plot_x0 + (x - x_min) / (x_max - x_min) * (plot_x1 - plot_x0)

    def py(y):
        return plot_y1 - y / y_max * (plot_y1 - plot_y0)

    for tick_idx in range(4):
        value = y_max * tick_idx / 3
        y_pos = py(value)
        draw.line((plot_x0 - 4, y_pos, plot_x0, y_pos), fill=(100, 116, 139))
        if tick_idx > 0:
            draw.line((plot_x0, y_pos, plot_x1, y_pos), fill=(226, 232, 240))
        label = f"{value:.2g}"
        label_bbox = draw.textbbox((0, 0), label, font=font)
        label_w = label_bbox[2] - label_bbox[0]
        label_h = label_bbox[3] - label_bbox[1]
        draw.text(
            (plot_x0 - 8 - label_w, y_pos - label_h / 2),
            label,
            fill=(71, 85, 105),
            font=font,
        )

    marker_rt = panel.get("marker_rt")
    if marker_rt is not None and x_min <= marker_rt <= x_max:
        marker_pos = px(marker_rt)
        dash_y = plot_y0
        while dash_y < plot_y1:
            draw.line((marker_pos, dash_y, marker_pos, min(dash_y + 8, plot_y1)), fill=(17, 24, 39), width=2)
            dash_y += 14
        draw.text((marker_pos + 4, plot_y0 + 4), f"RT {marker_rt:.3f}", fill=(17, 24, 39), font=font)

    for item in panel["series"]:
        points = sorted(item["points"])
        if len(points) < 2:
            continue
        color = parse_hex_color(item.get("color"), (37, 99, 235))
        coords = [(px(x), py(y)) for x, y in points]
        draw.line(coords, fill=color, width=2)

    draw.text((plot_x0, plot_y1 + 8), f"{x_min:.3f}", fill=(100, 116, 139), font=font)
    draw.text((plot_x1 - 36, plot_y1 + 8), f"{x_max:.3f}", fill=(100, 116, 139), font=font)


@dataclass
class LibraryEntry:
    compound_name: str
    precursor_mz: float
    product_mz: float
    rt_min: float
    rt_begin: float
    rt_end: float
    ms1_tolerance: float
    ms2_tolerance: float
    ms_level: int
    compound_class: str


@dataclass
class SpectrumSlice:
    file_name: str
    rt_values: list
    mz_values: list
    intensity_values: list
    eic_points: list


@dataclass
class PeakSelection:
    rt_start: float
    rt_end: float
    peak_height: float
    peak_area: float
    apex_rt: float
    source: str
    enabled: bool = True


@dataclass
class InputFile:
    path: str
    group: str
    color: str
    reader: object


@dataclass
class RunRecord:
    name: str
    settings: dict
    data_files: list
    library_path: str
    cached_data_files: object = None
    library_entries: object = None


class MzMLReader:
    CV_MZ_ARRAY = "MS:1000514"
    CV_INTENSITY_ARRAY = "MS:1000515"
    CV_MS_LEVEL = "MS:1000511"
    CV_SCAN_START_TIME = "MS:1000016"
    CV_ZLIB = "MS:1000574"
    CV_32BIT = "MS:1000521"
    CV_64BIT = "MS:1000523"
    CV_NUMPRESS_LINEAR = "MS:1002312"
    CV_NUMPRESS_PIC = "MS:1002313"
    CV_NUMPRESS_SLOF = "MS:1002314"

    def __init__(self, path):
        self.path = path
        self._cache = {}

    def spectra(self, rt_min=None, rt_max=None, ms_level=None):
        if not ensure_pyteomics_mzml():
            raise RuntimeError(mzml_reader_requirement_message())
        yield from self._spectra_pyteomics(rt_min=rt_min, rt_max=rt_max, ms_level=ms_level)

    def _spectra_pyteomics(self, rt_min=None, rt_max=None, ms_level=None):
        with pyteomics_mzml.MzML(self.path) as reader:
            for spectrum in reader:
                spectrum_ms_level = int(spectrum.get("ms level", 1) or 1)
                if ms_level is not None and spectrum_ms_level != ms_level:
                    continue

                rt = self._pyteomics_rt_min(spectrum)
                if rt_min is not None and rt is not None and rt < rt_min:
                    continue
                if rt_max is not None and rt is not None and rt > rt_max:
                    continue

                mzs = spectrum.get("m/z array")
                intensities = spectrum.get("intensity array")
                if mzs is None or intensities is None:
                    continue
                yield rt, list(mzs), list(intensities)

    @staticmethod
    def _pyteomics_rt_min(spectrum):
        scan_list = spectrum.get("scanList", {})
        scans = scan_list.get("scan", []) if isinstance(scan_list, dict) else []
        scan = scans[0] if scans else {}
        rt = scan.get("scan start time")
        if rt is None:
            rt = spectrum.get("scan start time")
        if rt is None:
            return None
        rt = float(rt)
        unit_name = str(scan.get("unitName", scan.get("scan start time unit", ""))).lower()
        unit_accession = str(scan.get("unitAccession", scan.get("unit accession", "")))
        if "second" in unit_name or unit_accession == "UO:0000010":
            rt /= 60.0
        return rt

    def _spectra_custom_xml(self, rt_min=None, rt_max=None, ms_level=None):
        with self._open_mzml() as fh:
            context = ET.iterparse(fh, events=("end",))
            for _, elem in context:
                if self._strip_ns(elem.tag) != "spectrum":
                    continue
                meta = self._spectrum_meta(elem)
                if ms_level is not None and meta["ms_level"] != ms_level:
                    elem.clear()
                    continue
                if rt_min is not None and meta["rt"] is not None and meta["rt"] < rt_min:
                    elem.clear()
                    continue
                if rt_max is not None and meta["rt"] is not None and meta["rt"] > rt_max:
                    elem.clear()
                    continue
                arrays = self._binary_arrays(elem)
                if arrays.get("unsupported_numpress"):
                    raise ValueError(
                        "MS-Numpress compressed mzML is not supported. Convert again without Numpress compression."
                    )
                if "mz" in arrays and "intensity" in arrays:
                    yield meta["rt"], arrays["mz"], arrays["intensity"]
                elem.clear()

    def _open_mzml(self):
        if self.path.lower().endswith(".gz"):
            return gzip.open(self.path, "rb")
        return open(self.path, "rb")

    def extract(self, entry, rt_window, mz_window, ms_level, target_mz_override=None, target_rt_override=None):
        target_mz = target_mz_override if target_mz_override is not None else (
            entry.precursor_mz if ms_level == 1 else entry.product_mz
        )
        target_rt = target_rt_override if target_rt_override is not None else entry.rt_min
        cache_key = (
            entry.compound_name,
            target_mz,
            target_rt,
            rt_window,
            mz_window,
            ms_level,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        rt_min = max(0, target_rt - rt_window)
        rt_max = target_rt + rt_window
        mz_min = target_mz - mz_window
        mz_max = target_mz + mz_window

        mz_values = []
        intensity_values = []
        rt_values = []
        eic_points = []

        for rt, mzs, intensities in self.spectra(rt_min=rt_min, rt_max=rt_max, ms_level=ms_level):
            if rt is None:
                continue
            rt_values.append(rt)
            scan_sum = 0.0
            start = bisect_left(mzs, mz_min)
            for idx in range(start, len(mzs)):
                mz = mzs[idx]
                if mz > mz_max:
                    break
                intensity = intensities[idx]
                mz_values.append(mz)
                intensity_values.append(intensity)
                scan_sum += intensity
            eic_points.append((rt, scan_sum))

        result = SpectrumSlice(
            file_name=os.path.basename(self.path),
            rt_values=rt_values,
            mz_values=mz_values,
            intensity_values=intensity_values,
            eic_points=eic_points,
        )
        self._cache[cache_key] = result
        return result

    def _spectrum_meta(self, elem):
        ms_level = None
        rt = None
        for cv in elem.iter():
            if self._strip_ns(cv.tag) != "cvParam":
                continue
            accession = cv.attrib.get("accession", "")
            if accession == self.CV_MS_LEVEL:
                ms_level = int(float(cv.attrib.get("value", "1")))
            elif accession == self.CV_SCAN_START_TIME:
                rt = float(cv.attrib.get("value", "0"))
                unit = cv.attrib.get("unitName", "").lower()
                unit_accession = cv.attrib.get("unitAccession", "")
                if "second" in unit or unit_accession == "UO:0000010":
                    rt /= 60.0
        return {"ms_level": ms_level or 1, "rt": rt}

    def _binary_arrays(self, spectrum_elem):
        out = {}
        for bda in spectrum_elem.iter():
            if self._strip_ns(bda.tag) != "binaryDataArray":
                continue
            kind = None
            compressed = False
            precision = 64
            unsupported_numpress = False
            binary_text = ""
            for child in bda:
                tag = self._strip_ns(child.tag)
                if tag == "cvParam":
                    accession = child.attrib.get("accession", "")
                    if accession == self.CV_MZ_ARRAY:
                        kind = "mz"
                    elif accession == self.CV_INTENSITY_ARRAY:
                        kind = "intensity"
                    elif accession == self.CV_ZLIB:
                        compressed = True
                    elif accession == self.CV_32BIT:
                        precision = 32
                    elif accession == self.CV_64BIT:
                        precision = 64
                    elif accession in {
                        self.CV_NUMPRESS_LINEAR,
                        self.CV_NUMPRESS_PIC,
                        self.CV_NUMPRESS_SLOF,
                    }:
                        unsupported_numpress = True
                elif tag == "binary":
                    binary_text = child.text or ""
            if unsupported_numpress:
                out["unsupported_numpress"] = True
                continue
            if kind and binary_text.strip():
                out[kind] = self._decode_array(binary_text, compressed, precision)
        return out

    @staticmethod
    def _decode_array(binary_text, compressed, precision):
        raw = base64.b64decode(binary_text)
        if compressed:
            raw = zlib.decompress(raw)
        fmt = "d" if precision == 64 else "f"
        size = 8 if precision == 64 else 4
        count = len(raw) // size
        return list(struct.unpack("<" + fmt * count, raw))

    @staticmethod
    def _strip_ns(tag):
        return tag.rsplit("}", 1)[-1]


class PlotCanvas(tk.Canvas):
    COLORS = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#be123c"]

    def __init__(self, master, **kwargs):
        super().__init__(master, bg="#f8fafc", highlightthickness=1, highlightbackground="#cbd5e1", **kwargs)
        self.bind("<Configure>", lambda _event: self.redraw())
        self.series = []
        self.title = ""
        self.x_label = ""
        self.y_label = ""
        self.marker_x = None

    def set_series(self, series, title, x_label, y_label, marker_x=None):
        self.series = series
        self.title = title
        self.x_label = x_label
        self.y_label = y_label
        self.marker_x = marker_x
        self.redraw()

    def redraw(self):
        self.delete("all")
        width = max(self.winfo_width(), 300)
        height = max(self.winfo_height(), 220)
        margin_left, margin_right, margin_top, margin_bottom = 58, 18, 34, 44
        plot_w = width - margin_left - margin_right
        plot_h = height - margin_top - margin_bottom

        self.create_text(width / 2, 16, text=self.title, fill="#0f172a", font=("Segoe UI", 10, "bold"))
        self.create_rectangle(
            margin_left,
            margin_top,
            width - margin_right,
            height - margin_bottom,
            outline="#94a3b8",
            fill="#ffffff",
        )

        all_x = [x for item in self.series for x, _ in item["points"] if math.isfinite(x)]
        all_y = [y for item in self.series for _, y in item["points"] if math.isfinite(y)]
        if not all_x or not all_y:
            self.create_text(width / 2, height / 2, text="No data", fill="#64748b", font=("Segoe UI", 11))
            return

        x_min, x_max = min(all_x), max(all_x)
        y_min, y_max = 0.0, max(all_y)
        if x_min == x_max:
            x_min -= 0.5
            x_max += 0.5
        if y_max <= 0:
            y_max = 1.0

        def px(x):
            return margin_left + (x - x_min) / (x_max - x_min) * plot_w

        def py(y):
            return margin_top + plot_h - (y - y_min) / (y_max - y_min) * plot_h

        for i in range(5):
            x_val = x_min + (x_max - x_min) * i / 4
            y_val = y_min + (y_max - y_min) * i / 4
            x_pos = px(x_val)
            y_pos = py(y_val)
            self.create_line(x_pos, margin_top, x_pos, height - margin_bottom, fill="#e2e8f0")
            self.create_line(margin_left, y_pos, width - margin_right, y_pos, fill="#e2e8f0")
            self.create_text(x_pos, height - 24, text=f"{x_val:.4g}", fill="#475569", font=("Segoe UI", 8))
            self.create_text(31, y_pos, text=f"{y_val:.3g}", fill="#475569", font=("Segoe UI", 8))

        if self.marker_x is not None and x_min <= self.marker_x <= x_max:
            marker_pos = px(self.marker_x)
            self.create_line(
                marker_pos,
                margin_top,
                marker_pos,
                height - margin_bottom,
                fill="#111827",
                width=2,
                dash=(4, 3),
            )
            self.create_text(
                marker_pos + 4,
                margin_top + 10,
                text=f"RT {self.marker_x:.3f}",
                anchor="w",
                fill="#111827",
                font=("Segoe UI", 8, "bold"),
            )

        for idx, item in enumerate(self.series):
            color = item.get("color") or self.COLORS[idx % len(self.COLORS)]
            points = sorted(item["points"])
            if item.get("style") == "sticks":
                for x, y in points:
                    self.create_line(px(x), py(0), px(x), py(y), fill=color, width=1)
            elif len(points) > 1:
                coords = []
                for x, y in points:
                    coords.extend([px(x), py(y)])
                self.create_line(*coords, fill=color, width=2)
            legend_x = margin_left + 8
            legend_y = margin_top + 14 + idx * 18
            self.create_rectangle(legend_x, legend_y - 5, legend_x + 10, legend_y + 5, fill=color, outline=color)
            self.create_text(legend_x + 15, legend_y, text=item["label"], anchor="w", fill="#334155", font=("Segoe UI", 8))

        self.create_text(width / 2, height - 8, text=self.x_label, fill="#334155", font=("Segoe UI", 9))
        self.create_text(12, height / 2, text=self.y_label, fill="#334155", font=("Segoe UI", 9), angle=90)


class MiniPlotCanvas(tk.Canvas):
    COLORS = PlotCanvas.COLORS

    def __init__(self, master, title, series, marker_x=None, width=220, height=150):
        super().__init__(
            master,
            width=width,
            height=height,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#dbe4ee",
        )
        self.title = title
        self.series = series
        self.marker_x = marker_x
        self.bind("<Configure>", lambda _event: self.redraw())
        self.redraw()

    def redraw(self):
        self.delete("all")
        width = max(self.winfo_width(), 180)
        height = max(self.winfo_height(), 120)
        left, right, top, bottom = 36, 8, 26, 24
        plot_w = width - left - right
        plot_h = height - top - bottom
        self.create_text(width / 2, 12, text=self.title, fill="#0f172a", font=("Segoe UI", 8, "bold"))
        self.create_line(left, top + plot_h, left + plot_w, top + plot_h, fill="#94a3b8")
        self.create_line(left, top, left, top + plot_h, fill="#94a3b8")

        all_x = [x for item in self.series for x, _ in item["points"] if math.isfinite(x)]
        all_y = [y for item in self.series for _, y in item["points"] if math.isfinite(y)]
        if not all_x or not all_y:
            self.create_text(width / 2, height / 2, text="No data", fill="#94a3b8", font=("Segoe UI", 8))
            return

        x_min, x_max = min(all_x), max(all_x)
        y_max = max(all_y)
        if x_min == x_max:
            x_min -= 0.05
            x_max += 0.05
        if y_max <= 0:
            y_max = 1.0

        def px(x):
            return left + (x - x_min) / (x_max - x_min) * plot_w

        def py(y):
            return top + plot_h - y / y_max * plot_h

        for tick_idx in range(4):
            value = y_max * tick_idx / 3
            y_pos = py(value)
            self.create_line(left - 3, y_pos, left, y_pos, fill="#64748b")
            if tick_idx > 0:
                self.create_line(left, y_pos, left + plot_w, y_pos, fill="#e2e8f0")
            self.create_text(
                left - 5,
                y_pos,
                text=f"{value:.2g}",
                anchor="e",
                fill="#475569",
                font=("Segoe UI", 6),
            )

        if self.marker_x is not None and x_min <= self.marker_x <= x_max:
            marker_pos = px(self.marker_x)
            self.create_line(marker_pos, top, marker_pos, top + plot_h, fill="#111827", width=2, dash=(4, 3))

        for item_idx, item in enumerate(self.series):
            points = sorted(item["points"])
            if len(points) < 2:
                continue
            coords = []
            for x, y in points:
                coords.extend([px(x), py(y)])
            color = item.get("color") or self.COLORS[item_idx % len(self.COLORS)]
            self.create_line(*coords, fill=color, width=1)

        self.create_text(left, height - 10, text=f"{x_min:.3f}", anchor="w", fill="#64748b", font=("Segoe UI", 7))
        self.create_text(left + plot_w, height - 10, text=f"{x_max:.3f}", anchor="e", fill="#64748b", font=("Segoe UI", 7))


class PeakPickCanvas(tk.Canvas):
    def __init__(self, master, title, points, selection, color, marker_rt, on_select, on_clear, width=360, height=220):
        super().__init__(
            master,
            width=width,
            height=height,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.title = title
        self.points = sorted(points)
        self.selection = selection
        self.color = color
        self.marker_rt = marker_rt
        self.on_select = on_select
        self.on_clear = on_clear
        self.drag_start_x = None
        self.scale = None
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<ButtonRelease-1>", self.end_drag)
        self.bind("<Double-Button-3>", self.clear_selection)
        self.bind("<Configure>", lambda _event: self.redraw())
        self.redraw()

    def redraw(self):
        self.delete("all")
        width = max(self.winfo_width(), 240)
        height = max(self.winfo_height(), 160)
        left, right, top, bottom = 48, 12, 30, 34
        plot_x0, plot_y0 = left, top
        plot_x1, plot_y1 = width - right, height - bottom
        self.create_text(width / 2, 13, text=self.title, fill="#0f172a", font=("Segoe UI", 8, "bold"))
        self.create_line(plot_x0, plot_y1, plot_x1, plot_y1, fill="#94a3b8")
        self.create_line(plot_x0, plot_y0, plot_x0, plot_y1, fill="#94a3b8")
        if not self.points:
            self.create_text(width / 2, height / 2, text="No data", fill="#94a3b8", font=("Segoe UI", 9))
            self.scale = None
            return

        x_min, x_max = min(rt for rt, _intensity in self.points), max(rt for rt, _intensity in self.points)
        y_max = max(intensity for _rt, intensity in self.points)
        if x_min == x_max:
            x_min -= 0.05
            x_max += 0.05
        if y_max <= 0:
            y_max = 1.0

        def px(rt):
            return plot_x0 + (rt - x_min) / (x_max - x_min) * (plot_x1 - plot_x0)

        def rt_from_px(x):
            clamped = min(max(x, plot_x0), plot_x1)
            return x_min + (clamped - plot_x0) / (plot_x1 - plot_x0) * (x_max - x_min)

        def py(intensity):
            return plot_y1 - intensity / y_max * (plot_y1 - plot_y0)

        self.scale = {"px": px, "rt_from_px": rt_from_px, "plot_y0": plot_y0, "plot_y1": plot_y1}

        for tick_idx in range(6):
            value = y_max * tick_idx / 5
            y_pos = py(value)
            self.create_line(plot_x0 - 4, y_pos, plot_x0, y_pos, fill="#64748b")
            if tick_idx > 0:
                self.create_line(plot_x0, y_pos, plot_x1, y_pos, fill="#e2e8f0")
            self.create_text(
                plot_x0 - 7,
                y_pos,
                text=f"{value:.2g}",
                anchor="e",
                fill="#475569",
                font=("Segoe UI", 7),
            )

        if self.selection and self.selection.enabled:
            x0 = px(self.selection.rt_start)
            x1 = px(self.selection.rt_end)
            self.create_rectangle(x0, plot_y0, x1, plot_y1, fill="#dbeafe", outline="")
            self.create_line(x0, plot_y0, x0, plot_y1, fill="#2563eb", width=2)
            self.create_line(x1, plot_y0, x1, plot_y1, fill="#2563eb", width=2)

        coords = []
        for rt, intensity in self.points:
            coords.extend([px(rt), py(intensity)])
        if len(coords) >= 4:
            self.create_line(*coords, fill=self.color, width=2)

        if self.marker_rt is not None and x_min <= self.marker_rt <= x_max:
            marker_x = px(self.marker_rt)
            self.create_line(marker_x, plot_y0, marker_x, plot_y1, fill="#111827", width=2, dash=(4, 3))
            self.create_text(
                marker_x + 4,
                plot_y0 + 10,
                text=f"RT {self.marker_rt:.3f}",
                anchor="w",
                fill="#111827",
                font=("Segoe UI", 8, "bold"),
            )

        if self.selection:
            if self.selection.enabled:
                label = f"H {self.selection.peak_height:.3g}  A {self.selection.peak_area:.3g}  {self.selection.source}"
            else:
                label = "Peak cleared"
            self.create_text(plot_x0 + 4, plot_y0 + 10, text=label, anchor="w", fill="#334155", font=("Segoe UI", 8))
        self.create_text(plot_x0, height - 12, text=f"{x_min:.3f}", anchor="w", fill="#64748b", font=("Segoe UI", 7))
        self.create_text(plot_x1, height - 12, text=f"{x_max:.3f}", anchor="e", fill="#64748b", font=("Segoe UI", 7))

    def start_drag(self, event):
        self.drag_start_x = event.x

    def drag(self, event):
        if self.drag_start_x is None or self.scale is None:
            return
        self.delete("drag_preview")
        x0, x1 = sorted([self.drag_start_x, event.x])
        self.create_rectangle(
            x0,
            self.scale["plot_y0"],
            x1,
            self.scale["plot_y1"],
            fill="#bfdbfe",
            stipple="gray25",
            outline="#2563eb",
            tags="drag_preview",
        )

    def end_drag(self, event):
        if self.drag_start_x is None or self.scale is None:
            return
        rt0 = self.scale["rt_from_px"](self.drag_start_x)
        rt1 = self.scale["rt_from_px"](event.x)
        self.drag_start_x = None
        self.delete("drag_preview")
        if abs(rt1 - rt0) < 1e-6:
            return
        rt_start, rt_end = sorted([rt0, rt1])
        self.on_select(rt_start, rt_end)

    def clear_selection(self, _event):
        self.on_clear()


class GridPlotWindow(tk.Toplevel):
    def __init__(self, master, title, panels, file_infos):
        super().__init__(master)
        self.title(title)
        self.geometry("1180x760")
        self.minsize(800, 520)

        header = ttk.Frame(self, padding=(10, 8))
        header.pack(fill=tk.X)
        ttk.Label(header, text=title, font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Export CSV", command=lambda: self.export_csv(panels)).pack(side=tk.RIGHT)
        ttk.Button(header, text="Export PNG", command=lambda: self.export_png(title, panels, file_infos)).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        legend = ttk.Frame(self, padding=(10, 0))
        legend.pack(fill=tk.X)
        for idx, info in enumerate(file_infos[:12]):
            item = ttk.Frame(legend)
            item.pack(side=tk.LEFT, padx=(0, 12))
            color = info["color"] or PlotCanvas.COLORS[idx % len(PlotCanvas.COLORS)]
            swatch = tk.Canvas(item, width=12, height=12, highlightthickness=0)
            swatch.create_rectangle(0, 0, 12, 12, fill=color, outline=color)
            swatch.pack(side=tk.LEFT)
            label = f'{info["group"]}: {info["name"]}' if info.get("group") else info["name"]
            ttk.Label(item, text=label, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(3, 0))

        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        canvas = tk.Canvas(outer, bg="#f8fafc", highlightthickness=0)
        y_scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        x_scroll = ttk.Scrollbar(outer, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        body = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))

        max_columns = 4
        for panel_idx, panel in enumerate(panels):
            row = panel_idx // max_columns
            col = panel_idx % max_columns
            frame = ttk.Frame(body, padding=4)
            frame.grid(row=row, column=col, sticky="nsew")
            MiniPlotCanvas(frame, panel["title"], panel["series"], marker_x=panel.get("marker_rt")).pack()

    def export_csv(self, panels):
        path = filedialog.asksaveasfilename(
            title="Export grid EIC data",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["panel", "group", "file", "rt_min", "intensity"])
            for panel in panels:
                for item in panel["series"]:
                    for rt, intensity in item["points"]:
                        writer.writerow(
                            [panel["title"], item.get("group", ""), item.get("file", item["label"]), rt, intensity]
                        )

    def export_png(self, title, panels, file_infos):
        path = filedialog.asksaveasfilename(
            title="Export grid PNG",
            defaultextension=".png",
            filetypes=[("PNG", "*.png")],
        )
        if not path:
            return
        render_grid_png(title, panels, file_infos, path)


class GroupSettingsWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.master = master
        self.title("Group settings")
        self.geometry("820x520")
        self.minsize(680, 360)
        self.rows = []

        header = ttk.Frame(self, padding=(10, 8))
        header.pack(fill=tk.X)
        ttk.Label(header, text="Group settings", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Apply", command=self.apply).pack(side=tk.RIGHT)

        table_outer = ttk.Frame(self, padding=(10, 0, 10, 10))
        table_outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(table_outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
        scroll = ttk.Scrollbar(table_outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        body = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))

        headers = ["Raw file name", "Group", "Color"]
        widths = [58, 22, 18]
        for col, text in enumerate(headers):
            ttk.Label(body, text=text, font=("Segoe UI", 9, "bold")).grid(
                row=0, column=col, sticky="ew", padx=4, pady=(4, 6)
            )
            body.columnconfigure(col, weight=1 if col == 0 else 0)

        for row_idx, data_file in enumerate(master.data_files, start=1):
            group_var = tk.StringVar(value=data_file.group)
            color_var = tk.StringVar(value=data_file.color)
            name = os.path.basename(data_file.path)
            ttk.Label(body, text=name, width=widths[0], anchor="w").grid(
                row=row_idx, column=0, sticky="ew", padx=4, pady=3
            )
            ttk.Entry(body, textvariable=group_var, width=widths[1]).grid(
                row=row_idx, column=1, sticky="ew", padx=4, pady=3
            )
            color_button = tk.Button(
                body,
                text="",
                width=widths[2],
                bg=data_file.color,
                activebackground=data_file.color,
                relief=tk.GROOVE,
                command=lambda var=color_var: self.pick_color(var),
            )
            color_button.grid(row=row_idx, column=2, sticky="ew", padx=4, pady=3)

            def sync_button(*_args, button=color_button, var=color_var):
                color = var.get().strip() or "#2563eb"
                button.configure(bg=color, activebackground=color)

            color_var.trace_add("write", sync_button)
            self.rows.append((data_file, group_var, color_var))

    def pick_color(self, color_var):
        color = colorchooser.askcolor(color=color_var.get() or "#2563eb", parent=self)[1]
        if color:
            color_var.set(color)

    def apply(self):
        for data_file, group_var, color_var in self.rows:
            data_file.group = group_var.get().strip() or data_file.group
            data_file.color = color_var.get().strip() or data_file.color
        self.master.refresh_file_list()
        self.master.update_active_run_group_settings()
        self.master.status_var.set("Updated group settings.")
        self.destroy()
        if self.master.processing_ready and self.master.selected_entries():
            self.master.after(50, self.master.plot_class_grid)


class LibraryBuilderWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.master = master
        self.title("Library builder")
        self.geometry("720x300")
        self.minsize(620, 260)

        self.alignment_path = tk.StringVar(value=master.alignment_path.get())
        self.output_path = tk.StringVar(value=master.generated_library_path.get())
        self.mode_var = tk.StringVar(value=master.library_mode_var.get())
        self.rt_tolerance_var = tk.DoubleVar(value=master.library_rt_tolerance_var.get())
        self.ms1_tolerance_var = tk.DoubleVar(value=master.library_ms1_tolerance_var.get())

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text="MS-DIAL output to library", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self._path_row(body, "Alignment", self.alignment_path, self.browse_alignment)
        self._path_row(body, "Save to", self.output_path, self.browse_output)

        mode = ttk.LabelFrame(body, text="Mode", padding=8)
        mode.pack(fill=tk.X, pady=(10, 0))
        ttk.Radiobutton(mode, text="Isotope labeling", variable=self.mode_var, value="isotope").pack(side=tk.LEFT)
        ttk.Radiobutton(mode, text="MRM mode", variable=self.mode_var, value="mrm").pack(side=tk.LEFT, padx=(12, 0))

        params = ttk.LabelFrame(body, text="Parameters", padding=8)
        params.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(params, text="RT tolerance").pack(side=tk.LEFT)
        ttk.Entry(params, textvariable=self.rt_tolerance_var, width=10).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(params, text="MS1 tolerance").pack(side=tk.LEFT)
        ttk.Entry(params, textvariable=self.ms1_tolerance_var, width=10).pack(side=tk.LEFT, padx=(6, 0))

        actions = ttk.Frame(body)
        actions.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(actions, text="Create library", command=self.create_library).pack(side=tk.RIGHT)

    @staticmethod
    def _path_row(parent, label, var, command):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(row, text=label, width=10).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse", command=command).pack(side=tk.LEFT, padx=(6, 0))

    def browse_alignment(self):
        path = filedialog.askopenfilename(
            title="Select MS-DIAL alignment result",
            filetypes=[("Text/TSV", "*.txt *.tsv"), ("All files", "*.*")],
            parent=self,
        )
        if path:
            self.alignment_path.set(path)
            if not self.output_path.get().strip():
                base = os.path.splitext(os.path.basename(path))[0]
                self.output_path.set(os.path.join(os.path.dirname(path), f"{base}_isotope_library.txt"))

    def browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save generated library",
            defaultextension=".txt",
            filetypes=[("Text/TSV", "*.txt *.tsv"), ("All files", "*.*")],
            parent=self,
        )
        if path:
            self.output_path.set(path)

    def create_library(self):
        if self.mode_var.get() == "mrm":
            messagebox.showinfo("Not supported", "MRM mode library generation is not supported yet.", parent=self)
            return
        alignment_path = self.alignment_path.get().strip()
        output_path = self.output_path.get().strip()
        if not alignment_path or not os.path.exists(alignment_path):
            messagebox.showerror("Alignment missing", "Select an MS-DIAL alignment result file.", parent=self)
            return
        if not output_path:
            base = os.path.splitext(os.path.basename(alignment_path))[0]
            output_path = os.path.join(os.path.dirname(alignment_path), f"{base}_isotope_library.txt")
            self.output_path.set(output_path)
        try:
            rows = build_isotope_library_rows(
                alignment_path,
                float(self.rt_tolerance_var.get()),
                float(self.ms1_tolerance_var.get()),
            )
            write_library_rows(rows, output_path)
            self.master.alignment_path.set(alignment_path)
            self.master.generated_library_path.set(output_path)
            self.master.library_mode_var.set(self.mode_var.get())
            self.master.library_rt_tolerance_var.set(float(self.rt_tolerance_var.get()))
            self.master.library_ms1_tolerance_var.set(float(self.ms1_tolerance_var.get()))
            self.master.library_path.set(output_path)
            self.master.load_library()
            self.master.status_var.set(f"Built isotope library with {len(rows)} rows.")
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Library build error", str(exc), parent=self)


class MzMLConverterWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.master = master
        self.title("mzML converter")
        self.geometry("760x300")
        self.minsize(640, 260)

        self.raw_root_path = tk.StringVar(value=master.raw_root_path.get())
        self.output_dir_path = tk.StringVar(value=master.output_dir_path.get())
        self.msconvert_path = tk.StringVar(value=master.msconvert_path.get())
        self.centroid_var = tk.BooleanVar(value=master.centroid_var.get())

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text="Agilent .d to mzML", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self._path_row(body, "Raw folder", self.raw_root_path, self.browse_raw_root)
        self._path_row(body, "Output dir", self.output_dir_path, self.browse_output_dir)
        self._path_row(body, "MSConvert", self.msconvert_path, self.browse_msconvert)

        opts = ttk.Frame(body)
        opts.pack(fill=tk.X, pady=(12, 0))
        ttk.Checkbutton(opts, text="Centroid / peak picking", variable=self.centroid_var).pack(side=tk.LEFT)

        actions = ttk.Frame(body)
        actions.pack(fill=tk.X, pady=(16, 0))
        ttk.Button(actions, text="Convert to mzML", command=self.convert).pack(side=tk.RIGHT)

    @staticmethod
    def _path_row(parent, label, var, command):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(row, text=label, width=10).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse", command=command).pack(side=tk.LEFT, padx=(6, 0))

    def browse_raw_root(self):
        path = filedialog.askdirectory(
            title="Select Agilent .d folder or a folder containing .d folders",
            parent=self,
        )
        if path:
            self.raw_root_path.set(path)
            if not self.output_dir_path.get().strip():
                self.output_dir_path.set(os.path.join(path, "mzML"))

    def browse_output_dir(self):
        path = filedialog.askdirectory(title="Select mzML output folder", parent=self)
        if path:
            self.output_dir_path.set(path)

    def browse_msconvert(self):
        path = filedialog.askopenfilename(
            title="Select msconvert.exe",
            filetypes=[("MSConvert", "msconvert.exe"), ("Executable", "*.exe"), ("All files", "*.*")],
            parent=self,
        )
        if path:
            self.msconvert_path.set(path)

    def convert(self):
        self.master.raw_root_path.set(self.raw_root_path.get())
        self.master.output_dir_path.set(self.output_dir_path.get())
        self.master.msconvert_path.set(self.msconvert_path.get())
        self.master.centroid_var.set(self.centroid_var.get())
        self.master.convert_raw_to_mzml(parent=self)


class DataProcessingSettingsWindow(tk.Toplevel):
    def __init__(self, master, initial=False):
        super().__init__(master)
        self.master = master
        self.initial = initial
        self.title("Data processing settings")
        self.geometry("780x720")
        self.minsize(680, 560)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._saved_widget_states = {}

        shell = ttk.Frame(self, padding=12)
        shell.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(shell)
        header.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(header, text="Data processing settings", font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Run history", command=self.open_run_history).pack(side=tk.RIGHT)

        content_outer = ttk.Frame(shell)
        content_outer.pack(fill=tk.BOTH, expand=True)
        content_canvas = tk.Canvas(content_outer, highlightthickness=0)
        content_scroll = ttk.Scrollbar(content_outer, orient=tk.VERTICAL, command=content_canvas.yview)
        content_canvas.configure(yscrollcommand=content_scroll.set)
        content_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        content = ttk.Frame(content_canvas)
        content_window = content_canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: content_canvas.configure(scrollregion=content_canvas.bbox("all")))
        content_canvas.bind("<Configure>", lambda event: content_canvas.itemconfigure(content_window, width=event.width))
        content_canvas.bind(
            "<MouseWheel>",
            lambda event: content_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"),
        )

        library = ttk.LabelFrame(content, text="Library", padding=8)
        library.pack(fill=tk.X, pady=(0, 10))
        row = ttk.Frame(library)
        row.pack(fill=tk.X)
        ttk.Entry(row, textvariable=master.library_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse", command=self.browse_library).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(library, text="Library builder", command=self.open_library_builder).pack(fill=tk.X, pady=(8, 0))

        extraction = ttk.LabelFrame(content, text="Extraction", padding=8)
        extraction.pack(fill=tk.X, pady=(0, 10))
        master._number_row(extraction, "RT window min", master.rt_window_var)
        master._number_row(extraction, "m/z window", master.mz_window_var)
        master._text_row(extraction, "Target m/z", master.target_mz_var)
        master._text_row(extraction, "Target RT", master.target_rt_var)
        ms_row = ttk.Frame(extraction)
        ms_row.pack(fill=tk.X, pady=3)
        ttk.Label(ms_row, text="MS level", width=14).pack(side=tk.LEFT)
        ttk.Radiobutton(ms_row, text="MS1", variable=master.ms_level_var, value=1).pack(side=tk.LEFT)
        ttk.Radiobutton(ms_row, text="MS2", variable=master.ms_level_var, value=2).pack(side=tk.LEFT)

        smoothing = ttk.LabelFrame(content, text="Smoothing", padding=8)
        smoothing.pack(fill=tk.X, pady=(0, 10))
        method_row = ttk.Frame(smoothing)
        method_row.pack(fill=tk.X, pady=3)
        ttk.Label(method_row, text="Method", width=14).pack(side=tk.LEFT)
        ttk.Combobox(
            method_row,
            textvariable=master.smoothing_method_var,
            values=[
                "None",
                "Simple moving average",
                "Linear weighted moving average",
                "Savitzky-Golay filter",
                "Binomial filter",
                "Lowess filter",
                "Loess filter",
                "Time-based linear weighted moving average",
            ],
            state="readonly",
            width=34,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        master._number_row(smoothing, "Level", master.smoothing_level_var)
        master._number_row(smoothing, "Min peak width", master.minimum_peak_width_var)

        files = ttk.LabelFrame(content, text="mzML files", padding=8)
        files.pack(fill=tk.X, pady=(0, 10))
        file_buttons = ttk.Frame(files)
        file_buttons.pack(fill=tk.X)
        ttk.Button(file_buttons, text="Add mzML", command=self.add_mzml).pack(side=tk.LEFT)
        ttk.Button(file_buttons, text="Clear", command=self.clear_mzml).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(file_buttons, text="Group settings", command=self.group_settings).pack(side=tk.LEFT, padx=(6, 0))
        self.file_list = tk.Listbox(files, height=6, exportselection=False)
        self.file_list.pack(fill=tk.X, pady=(8, 0))
        self.refresh_file_list()

        convert = ttk.LabelFrame(content, text="Agilent .d to mzML", padding=8)
        convert.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(convert, text="Open mzML converter", command=self.open_mzml_converter).pack(fill=tk.X)

        footer = ttk.Frame(shell)
        footer.pack(fill=tk.X, pady=(10, 0))
        self.progress = ttk.Progressbar(footer, mode="determinate")
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.progress_label = ttk.Label(footer, text="0/0", width=10, anchor="e")
        self.progress_label.pack(side=tk.LEFT, padx=(0, 10))
        self.run_button = ttk.Button(footer, text="Run", command=self.run)
        self.run_button.pack(side=tk.RIGHT)

        self.deiconify()
        self.update_idletasks()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.lift()
        self.focus_force()
        if self.initial:
            self.grab_set()

    def browse_library(self):
        path = filedialog.askopenfilename(
            title="Select MRMProbs reference file",
            filetypes=[("Text/TSV", "*.txt *.tsv"), ("All files", "*.*")],
            parent=self,
        )
        if path:
            self.master.library_path.set(path)
            self.master.load_library()

    def add_mzml(self):
        paths = filedialog.askopenfilenames(
            title="Select mzML files",
            filetypes=[("mzML", "*.mzML *.mzml"), ("gzipped mzML", "*.mzML.gz *.mzml.gz"), ("All files", "*.*")],
            parent=self,
        )
        for path in paths:
            self.master.append_mzml_file(path)
        self.refresh_file_list()

    def clear_mzml(self):
        self.master.clear_files()
        self.refresh_file_list()

    def group_settings(self):
        child = self.open_child_window(self.master.open_group_settings)
        if child:
            child.bind(
                "<Destroy>",
                lambda event, window=child: self.after(100, self.refresh_file_list) if event.widget is window else None,
                add="+",
            )

    def open_library_builder(self):
        self.open_child_window(self.master.open_library_builder)

    def open_mzml_converter(self):
        self.open_child_window(self.master.open_mzml_converter)

    def open_run_history(self):
        self.open_child_window(self.master.open_run_history)

    def open_child_window(self, opener):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        child = opener()
        if child and child.winfo_exists():
            child.lift()
            child.focus_force()
            child.bind(
                "<Destroy>",
                lambda event, window=child: self.restore_grab() if event.widget is window else None,
                add="+",
            )
        else:
            self.after(100, self.restore_grab)
        return child

    def restore_grab(self):
        if self.initial and self.winfo_exists() and not self.master.processing_ready:
            try:
                self.grab_set()
                self.lift()
            except tk.TclError:
                pass

    def refresh_file_list(self):
        self.file_list.delete(0, tk.END)
        for item in self.master.data_files:
            label = f"{os.path.basename(item.path)}  [{item.group}]  {item.color}"
            self.file_list.insert(tk.END, label)

    def set_running(self, running):
        self.progress.configure(mode="determinate")
        if running:
            self.progress.configure(value=0)
            self.progress_label.configure(text="0/0")
            self.set_controls_enabled(False)
        else:
            self.set_controls_enabled(True)
            self.progress.configure(value=0)
            self.progress_label.configure(text="0/0")

    def set_progress(self, done, total):
        total = max(1, total)
        self.progress.configure(maximum=total, value=min(done, total))
        self.progress_label.configure(text=f"{min(done, total)}/{total}")

    def set_controls_enabled(self, enabled):
        if enabled:
            for widget, state in list(self._saved_widget_states.items()):
                try:
                    widget.configure(state=state)
                except tk.TclError:
                    pass
            self._saved_widget_states.clear()
            return
        self._disable_widget_tree(self)

    def _disable_widget_tree(self, widget):
        for child in widget.winfo_children():
            if child in {self.progress, self.progress_label}:
                continue
            try:
                state = child.cget("state")
                if child not in self._saved_widget_states:
                    self._saved_widget_states[child] = state
                child.configure(state=tk.DISABLED)
            except tk.TclError:
                pass
            self._disable_widget_tree(child)

    def run(self):
        self.set_running(True)
        self.master.run_data_processing(settings_window=self, initial=self.initial)

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        if self.initial and not self.master.processing_ready:
            self.master.destroy()
        else:
            self.destroy()


class RunHistoryWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.master = master
        self.title("Run history")
        self.geometry("620x420")
        self.minsize(520, 320)

        shell = ttk.Frame(self, padding=10)
        shell.pack(fill=tk.BOTH, expand=True)
        ttk.Label(shell, text="Run history", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))

        self.listbox = tk.Listbox(shell, exportselection=False)
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.refresh()

        buttons = ttk.Frame(shell)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="Load selected", command=self.load_selected).pack(side=tk.RIGHT)

    def refresh(self):
        self.listbox.delete(0, tk.END)
        for record in self.master.run_history:
            file_count = len(record.data_files)
            self.listbox.insert(tk.END, f"{record.name}  |  {file_count} file(s)")

    def load_selected(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        self.master.restore_run_record(self.master.run_history[selection[0]])
        self.destroy()


class LCMSMSApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LC-MS/MS RT and m/z Viewer")
        self.geometry("1180x760")
        self.minsize(960, 640)

        self.library_path = tk.StringVar(value=DEFAULT_LIBRARY if os.path.exists(DEFAULT_LIBRARY) else "")
        self.raw_root_path = tk.StringVar(value=DEFAULT_RAW_ROOT if os.path.exists(DEFAULT_RAW_ROOT) else "")
        self.output_dir_path = tk.StringVar()
        self.msconvert_path = tk.StringVar(value=detect_msconvert())
        self.centroid_var = tk.BooleanVar(value=True)
        self.alignment_path = tk.StringVar()
        self.generated_library_path = tk.StringVar()
        self.library_mode_var = tk.StringVar(value="isotope")
        self.library_rt_tolerance_var = tk.DoubleVar(value=0.5)
        self.library_ms1_tolerance_var = tk.DoubleVar(value=0.05)
        self.search_var = tk.StringVar()
        self.rt_window_var = tk.DoubleVar(value=0.05)
        self.mz_window_var = tk.DoubleVar(value=0.005)
        self.target_mz_var = tk.StringVar()
        self.target_rt_var = tk.StringVar()
        self.smoothing_method_var = tk.StringVar(value="Linear weighted moving average")
        self.smoothing_level_var = tk.IntVar(value=3)
        self.minimum_peak_width_var = tk.IntVar(value=5)
        self.ms_level_var = tk.IntVar(value=1)
        self.status_var = tk.StringVar(value="Load a library and mzML files.")

        self.library_entries = []
        self.filtered_entries = []
        self.data_files = []
        self.last_results = []
        self.last_result_items = []
        self.peak_selections = {}
        self.quant_current_entry = None
        self.quant_combo_entries = []
        self.quant_points = {}
        self.quant_isotope_var = tk.StringVar()
        self.processing_ready = False
        self.processing_window = None
        self.run_history = []
        self.active_run_record = None

        self._build_ui()
        if self.library_path.get():
            self.load_library()
        self.withdraw()
        self.after(100, self.show_initial_processing_settings)

    def _build_ui(self):
        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Data processing settings", command=self.open_data_processing_settings).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Run history", command=self.open_run_history).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="Group settings", command=self.open_group_settings).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(toolbar, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))

        root = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=tk.BOTH, expand=True)

        left_shell = ttk.Frame(root)
        right = ttk.Frame(root, padding=10)
        root.add(left_shell, weight=0)
        root.add(right, weight=1)

        left_canvas = tk.Canvas(left_shell, highlightthickness=0)
        left_scroll = ttk.Scrollbar(left_shell, orient=tk.VERTICAL, command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        left = ttk.Frame(left_canvas, padding=10)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _event: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>", lambda event: left_canvas.itemconfigure(left_window, width=event.width))
        left_canvas.bind_all("<MouseWheel>", lambda event: self.scroll_left_panel(left_canvas, event))

        search_row = ttk.Frame(left)
        search_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(search_row, text="Search").pack(side=tk.LEFT)
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.search_var.trace_add("write", lambda *_args: self.filter_entries())

        self.compound_list = tk.Listbox(left, width=54, height=18, exportselection=False, selectmode=tk.EXTENDED)
        self.compound_list.pack(fill=tk.BOTH, expand=True)
        self.compound_list.bind("<<ListboxSelect>>", lambda _event: self.show_selected_info())

        actions = ttk.Frame(left)
        actions.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(actions, text="Class grid", command=self.plot_class_grid).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(actions, text="Export class", command=self.export_selected_class).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0)
        )
        ttk.Button(actions, text="Export CSV", command=self.export_csv).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        info = ttk.LabelFrame(right, text="Selected target", padding=8)
        info.pack(fill=tk.X)
        self.info_text = tk.Text(info, height=4, wrap="word", bg="#f8fafc")
        self.info_text.pack(fill=tk.X)

        self.result_tabs = ttk.Notebook(right)
        self.result_tabs.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.eval_tab = ttk.Frame(self.result_tabs, padding=0)
        self.quant_tab = ttk.Frame(self.result_tabs, padding=8)
        self.result_tabs.add(self.eval_tab, text="Evaluation")
        self.result_tabs.add(self.quant_tab, text="Peak quant")

        plots = ttk.PanedWindow(self.eval_tab, orient=tk.VERTICAL)
        plots.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.spectrum_canvas = PlotCanvas(plots, height=330)
        self.lower_panel = ttk.Frame(plots)
        plots.add(self.spectrum_canvas, weight=3)
        plots.add(self.lower_panel, weight=3)
        self.lower_title_var = tk.StringVar(value="Class grid / EIC")
        self.render_empty_lower_panel()
        self.build_quant_tab()

        status = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(10, 4))
        status.pack(fill=tk.X)

    @staticmethod
    def _number_row(parent, label, var):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, width=12).pack(side=tk.LEFT)

    @staticmethod
    def _text_row(parent, label, var):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, width=12).pack(side=tk.LEFT)
        ttk.Label(row, text="blank = library", foreground="#64748b").pack(side=tk.LEFT, padx=(6, 0))

    @staticmethod
    def _path_row(parent, label, var, command):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label, width=10).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, width=28).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse", command=command).pack(side=tk.LEFT, padx=(5, 0))

    @staticmethod
    def scroll_left_panel(canvas, event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def show_initial_processing_settings(self):
        if self.processing_ready:
            return
        self.open_data_processing_settings(initial=True)

    def open_data_processing_settings(self, initial=False):
        if self.processing_window and self.processing_window.winfo_exists():
            self.processing_window.lift()
            return
        self.processing_window = DataProcessingSettingsWindow(self, initial=initial)

    def open_run_history(self):
        return RunHistoryWindow(self)

    def update_active_run_group_settings(self):
        if not self.active_run_record:
            return
        self.active_run_record.data_files = [
            {"path": item.path, "group": item.group, "color": item.color}
            for item in self.data_files
        ]

    def current_processing_settings(self):
        return {
            "rt_window": float(self.rt_window_var.get()),
            "mz_window": float(self.mz_window_var.get()),
            "target_mz": self.target_mz_var.get().strip(),
            "target_rt": self.target_rt_var.get().strip(),
            "ms_level": int(self.ms_level_var.get()),
            "smoothing_method": self.smoothing_method_var.get(),
            "smoothing_level": int(self.smoothing_level_var.get()),
            "minimum_peak_width": int(self.minimum_peak_width_var.get()),
        }

    def apply_processing_settings(self, settings):
        self.rt_window_var.set(settings.get("rt_window", self.rt_window_var.get()))
        self.mz_window_var.set(settings.get("mz_window", self.mz_window_var.get()))
        self.target_mz_var.set(settings.get("target_mz", ""))
        self.target_rt_var.set(settings.get("target_rt", ""))
        self.ms_level_var.set(settings.get("ms_level", self.ms_level_var.get()))
        self.smoothing_method_var.set(settings.get("smoothing_method", self.smoothing_method_var.get()))
        self.smoothing_level_var.set(settings.get("smoothing_level", self.smoothing_level_var.get()))
        self.minimum_peak_width_var.set(settings.get("minimum_peak_width", self.minimum_peak_width_var.get()))

    def run_data_processing(self, settings_window=None, initial=False):
        if not ensure_pyteomics_mzml():
            messagebox.showerror(
                "mzML reader dependency missing",
                mzml_reader_requirement_message(),
                parent=settings_window or self,
            )
            if settings_window:
                settings_window.set_running(False)
            return
        if not self.library_path.get().strip():
            messagebox.showinfo("No library", "Select a library before running.", parent=settings_window or self)
            if settings_window:
                settings_window.set_running(False)
            return
        if not self.library_entries:
            self.load_library()
        if not self.library_entries:
            if settings_window:
                settings_window.set_running(False)
            return
        if not self.data_files:
            messagebox.showinfo("No mzML", "Add one or more mzML files before running.", parent=settings_window or self)
            if settings_window:
                settings_window.set_running(False)
            return

        try:
            settings = self.current_processing_settings()
            target_mz_override = self.optional_float(self.target_mz_var, "Target m/z")
            target_rt_override = self.optional_float(self.target_rt_var, "Target RT")
        except ValueError as exc:
            messagebox.showerror("Parameter error", str(exc), parent=settings_window or self)
            if settings_window:
                settings_window.set_running(False)
            return

        self.quant_points.clear()
        self.peak_selections.clear()
        for data_file in self.data_files:
            if hasattr(data_file.reader, "_cache"):
                data_file.reader._cache.clear()

        self.status_var.set("Running data processing search...")
        entries = list(self.library_entries)
        data_files = list(self.data_files)
        total = max(1, len(entries) * len(data_files))
        if settings_window and settings_window.winfo_exists():
            settings_window.set_progress(0, total)
        thread = threading.Thread(
            target=self._run_data_processing_worker,
            args=(entries, data_files, settings, target_mz_override, target_rt_override, settings_window, initial),
            daemon=True,
        )
        thread.start()

    def _run_data_processing_worker(
        self,
        entries,
        data_files,
        settings,
        target_mz_override,
        target_rt_override,
        settings_window,
        initial,
    ):
        errors = []
        total = max(1, len(entries) * len(data_files))
        done = 0
        for entry in entries:
            for data_file in data_files:
                try:
                    data_file.reader.extract(
                        entry,
                        settings["rt_window"],
                        settings["mz_window"],
                        settings["ms_level"],
                        target_mz_override=target_mz_override,
                        target_rt_override=target_rt_override,
                    )
                except Exception as exc:
                    errors.append(f"{entry.compound_name} / {os.path.basename(data_file.path)}: {exc}")
                done += 1
                if settings_window and settings_window.winfo_exists():
                    self.after(0, settings_window.set_progress, done, total)
                if done % max(len(data_files), 1) == 0:
                    self.after(0, self.status_var.set, f"Processing search {done}/{total}...")
        self.after(0, self._finish_data_processing_run, settings, errors, settings_window, initial)

    def _finish_data_processing_run(self, settings, errors, settings_window, initial):
        record = RunRecord(
            name=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            settings=dict(settings),
            data_files=[
                {"path": item.path, "group": item.group, "color": item.color}
                for item in self.data_files
            ],
            library_path=self.library_path.get(),
            cached_data_files=list(self.data_files),
            library_entries=list(self.library_entries),
        )
        self.run_history.append(record)
        self.active_run_record = record
        self.processing_ready = True
        self.deiconify()
        self.lift()
        if settings_window and settings_window.winfo_exists():
            settings_window.set_running(False)
            try:
                settings_window.grab_release()
            except tk.TclError:
                pass
            settings_window.destroy()
        if self.filtered_entries and not self.compound_list.curselection():
            self.compound_list.selection_set(0)
            self.show_selected_info()
        message = f"Data processing finished. Cached {len(self.library_entries)} target(s) x {len(self.data_files)} file(s)."
        if errors:
            message += " Some targets failed; see popup."
            messagebox.showwarning("Processing warnings", "\n".join(errors[:80]))
        self.status_var.set(message)

    def restore_run_record(self, record):
        self.library_path.set(record.library_path)
        self.apply_processing_settings(record.settings)
        if record.library_entries:
            self.library_entries = list(record.library_entries)
            self.filter_entries()
        else:
            self.load_library()
        if record.cached_data_files:
            self.data_files = list(record.cached_data_files)
        else:
            self.data_files.clear()
            for spec in record.data_files:
                self.append_mzml_file(spec["path"])
                self.data_files[-1].group = spec.get("group", self.data_files[-1].group)
                self.data_files[-1].color = spec.get("color", self.data_files[-1].color)
        self.refresh_file_list()
        self.quant_points.clear()
        self.peak_selections.clear()
        self.active_run_record = record
        self.processing_ready = True
        self.deiconify()
        self.status_var.set(f"Loaded run history: {record.name}")

    def build_quant_tab(self):
        controls = ttk.Frame(self.quant_tab)
        controls.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(controls, text="Isotope").pack(side=tk.LEFT)
        self.quant_isotope_combo = ttk.Combobox(
            controls,
            textvariable=self.quant_isotope_var,
            state="readonly",
            width=36,
        )
        self.quant_isotope_combo.pack(side=tk.LEFT, padx=(6, 8))
        self.quant_isotope_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_quant_selected_isotope())
        ttk.Button(controls, text="Load selected", command=self.load_quant_from_selected_class).pack(side=tk.LEFT)
        ttk.Button(controls, text="Auto pick all", command=self.auto_pick_all_library).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(controls, text="Export CSV", command=self.export_peak_csv).pack(side=tk.RIGHT)

        hint = ttk.Label(
            self.quant_tab,
            text="Left-drag to set peak range. Double right-click to clear the selected peak for that raw file.",
            foreground="#64748b",
        )
        hint.pack(fill=tk.X, pady=(0, 6))

        self.quant_canvas = tk.Canvas(self.quant_tab, bg="#f8fafc", highlightthickness=0)
        self.quant_y_scroll = ttk.Scrollbar(self.quant_tab, orient=tk.VERTICAL, command=self.quant_canvas.yview)
        self.quant_x_scroll = ttk.Scrollbar(self.quant_tab, orient=tk.HORIZONTAL, command=self.quant_canvas.xview)
        self.quant_canvas.configure(yscrollcommand=self.quant_y_scroll.set, xscrollcommand=self.quant_x_scroll.set)
        self.quant_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.quant_y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.quant_x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.quant_body = ttk.Frame(self.quant_canvas)
        self.quant_canvas_window = self.quant_canvas.create_window((0, 0), window=self.quant_body, anchor="nw")
        self.quant_body.bind(
            "<Configure>",
            lambda _event: self.quant_canvas.configure(scrollregion=self.quant_canvas.bbox("all")),
        )

    def clear_lower_panel(self):
        for child in self.lower_panel.winfo_children():
            child.destroy()

    def render_empty_lower_panel(self):
        self.clear_lower_panel()
        ttk.Label(
            self.lower_panel,
            text="Select a metabolite class, then use Class grid to inspect all isotope EIC traces.",
            anchor="center",
            foreground="#64748b",
        ).pack(fill=tk.BOTH, expand=True)

    def render_eic_panel(self, series, title, marker_x=None):
        self.clear_lower_panel()
        canvas = PlotCanvas(self.lower_panel, height=260)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.set_series(series, title, "RT min", "Extracted intensity", marker_x=marker_x)

    def render_class_grid_panel(self, title, grid_rows, file_infos):
        grid_rows = normalize_grid_rows(grid_rows)
        self.clear_lower_panel()
        header = ttk.Frame(self.lower_panel)
        header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header, text=title, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Export PNG", command=lambda: self.export_grid_png(title, grid_rows, file_infos)).pack(
            side=tk.RIGHT
        )
        ttk.Button(header, text="Export CSV", command=lambda: self.export_grid_csv(grid_rows)).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        legend = ttk.Frame(self.lower_panel)
        legend.pack(fill=tk.X, pady=(0, 4))
        for idx, info in enumerate(file_infos[:10]):
            item = ttk.Frame(legend)
            item.pack(side=tk.LEFT, padx=(0, 10))
            color = info["color"] or PlotCanvas.COLORS[idx % len(PlotCanvas.COLORS)]
            swatch = tk.Canvas(item, width=12, height=12, highlightthickness=0)
            swatch.create_rectangle(0, 0, 12, 12, fill=color, outline=color)
            swatch.pack(side=tk.LEFT)
            label = f'{info["group"]}: {info["name"]}' if info.get("group") else info["name"]
            ttk.Label(item, text=label, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(3, 0))

        outer = ttk.Frame(self.lower_panel)
        outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer, bg="#f8fafc", highlightthickness=0)
        y_scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        x_scroll = ttk.Scrollbar(outer, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        body = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        for row_idx, row_data in enumerate(grid_rows):
            class_label = ttk.Label(
                body,
                text=row_data.get("class_name", ""),
                font=("Segoe UI", 9, "bold"),
                anchor="nw",
                width=18,
            )
            class_label.grid(row=row_idx, column=0, sticky="nw", padx=(4, 8), pady=8)
            for col_idx, panel in enumerate(row_data["panels"], start=1):
                frame = ttk.Frame(body, padding=4)
                frame.grid(row=row_idx, column=col_idx, sticky="nsew")
                MiniPlotCanvas(
                    frame,
                    panel["title"],
                    panel["series"],
                    marker_x=panel.get("marker_rt"),
                    width=260,
                    height=170,
                ).pack()

    def export_grid_png(self, title, grid_rows, file_infos):
        default_name = self.grid_default_filename(grid_rows, ".png")
        path = filedialog.asksaveasfilename(
            title="Export grid PNG",
            defaultextension=".png",
            initialfile=default_name,
            filetypes=[("PNG", "*.png")],
        )
        if path:
            render_grid_png(title, grid_rows, file_infos, path)
            self.status_var.set(f"Exported PNG: {path}")

    def export_grid_csv(self, grid_rows):
        default_name = self.grid_default_filename(grid_rows, ".csv")
        path = filedialog.asksaveasfilename(
            title="Export grid CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            self.write_grid_csv(path, grid_rows)
            self.status_var.set(f"Exported CSV: {path}")

    def open_mzml_converter(self):
        return MzMLConverterWindow(self)

    def browse_msconvert(self):
        path = filedialog.askopenfilename(
            title="Select msconvert.exe",
            filetypes=[("MSConvert", "msconvert.exe"), ("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.msconvert_path.set(path)

    def open_library_builder(self):
        return LibraryBuilderWindow(self)

    def find_raw_data_folders(self, root_path):
        if root_path.lower().endswith(".d") and os.path.isdir(root_path):
            return [root_path]
        raw_dirs = []
        for current_root, dirs, _files in os.walk(root_path):
            d_dirs = [name for name in dirs if name.lower().endswith(".d")]
            for name in d_dirs:
                raw_dirs.append(os.path.join(current_root, name))
            dirs[:] = [name for name in dirs if not name.lower().endswith(".d")]
        return sorted(raw_dirs)

    def append_mzml_file(self, path):
        if path in [item.path for item in self.data_files]:
            return
        idx = len(self.data_files)
        color = PlotCanvas.COLORS[idx % len(PlotCanvas.COLORS)]
        item = InputFile(path=path, group=self.guess_group(path), color=color, reader=MzMLReader(path))
        self.data_files.append(item)
        if hasattr(self, "file_list"):
            self.file_list.insert(tk.END, os.path.basename(path))

    def refresh_file_list(self):
        if not hasattr(self, "file_list"):
            return
        self.file_list.delete(0, tk.END)
        for item in self.data_files:
            self.file_list.insert(tk.END, os.path.basename(item.path))

    @staticmethod
    def guess_group(path):
        name = os.path.splitext(os.path.basename(path))[0]
        parts = name.split("_")
        if len(parts) >= 2:
            return parts[1]
        return "Group 1"

    def open_group_settings(self):
        if not self.data_files:
            messagebox.showinfo("No mzML", "Add mzML files before setting groups.")
            return None
        return GroupSettingsWindow(self)

    def convert_raw_to_mzml(self, parent=None):
        parent = parent or self
        msconvert = self.msconvert_path.get().strip()
        raw_root = self.raw_root_path.get().strip()
        output_dir = self.output_dir_path.get().strip()
        if not msconvert or not os.path.exists(msconvert):
            messagebox.showerror(
                "MSConvert not found",
                "Install ProteoWizard or select msconvert.exe with the MSConvert Browse button.",
                parent=parent,
            )
            return
        if not raw_root or not os.path.isdir(raw_root):
            messagebox.showerror(
                "Raw folder missing",
                "Select an Agilent .d folder or a folder containing .d folders.",
                parent=parent,
            )
            return
        if not output_dir:
            output_dir = os.path.join(raw_root, "mzML")
            self.output_dir_path.set(output_dir)
        raw_dirs = self.find_raw_data_folders(raw_root)
        if not raw_dirs:
            messagebox.showinfo("No .d folders", "No Agilent .d folders were found.", parent=parent)
            return
        os.makedirs(output_dir, exist_ok=True)

        self.status_var.set(f"Converting {len(raw_dirs)} raw data folder(s) to mzML...")
        self.update_idletasks()
        thread = threading.Thread(
            target=self._convert_worker,
            args=(msconvert, raw_dirs, output_dir, bool(self.centroid_var.get())),
            daemon=True,
        )
        thread.start()

    def _convert_worker(self, msconvert, raw_dirs, output_dir, centroid):
        converted = []
        errors = []
        for idx, raw_dir in enumerate(raw_dirs, start=1):
            cmd = [
                msconvert,
                raw_dir,
                "--mzML",
                "--64",
                "--zlib",
                "--outdir",
                output_dir,
            ]
            if centroid:
                cmd.extend(["--filter", "peakPicking true 1-"])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if proc.returncode != 0:
                    errors.append(f"{os.path.basename(raw_dir)}: {proc.stderr.strip() or proc.stdout.strip()}")
                    continue
                expected = os.path.join(output_dir, os.path.basename(raw_dir)[:-2] + ".mzML")
                if os.path.exists(expected):
                    converted.append(expected)
                else:
                    matches = [
                        os.path.join(output_dir, name)
                        for name in os.listdir(output_dir)
                        if name.lower().endswith(".mzml")
                        and os.path.splitext(name)[0].lower() == os.path.basename(raw_dir)[:-2].lower()
                    ]
                    converted.extend(matches)
            except Exception as exc:
                errors.append(f"{os.path.basename(raw_dir)}: {exc}")
            self.after(0, self.status_var.set, f"Converted {idx}/{len(raw_dirs)} raw data folder(s)...")

        self.after(0, self._finish_conversion, converted, errors)

    def _finish_conversion(self, converted, errors):
        for path in sorted(set(converted)):
            self.append_mzml_file(path)
        message = f"Conversion finished. Added {len(set(converted))} mzML file(s)."
        if errors:
            message += " Some conversions failed; see popup."
            messagebox.showwarning("MSConvert warnings", "\n\n".join(errors[:20]))
        self.status_var.set(message)

    def browse_library(self):
        path = filedialog.askopenfilename(
            title="Select MRMProbs reference file",
            filetypes=[("Text/TSV", "*.txt *.tsv"), ("All files", "*.*")],
        )
        if path:
            self.library_path.set(path)
            self.load_library()

    def load_library(self):
        try:
            df = pd.read_csv(self.library_path.get(), sep="\t")
            self.library_entries = [
                LibraryEntry(
                    compound_name=str(row["Compound name"]),
                    precursor_mz=float(row["Precursor mz"]),
                    product_mz=float(row["Product mz"]),
                    rt_min=float(row["RT min"]),
                    rt_begin=float(row.get("RT begin", row["RT min"])),
                    rt_end=float(row.get("RT end", row["RT min"])),
                    ms1_tolerance=float(row.get("MS1 tolerance", 0.005)),
                    ms2_tolerance=float(row.get("MS2 tolerance", 0.01)),
                    ms_level=int(row.get("MS level", 1)),
                    compound_class=str(row.get("Class", "")),
                )
                for _, row in df.iterrows()
            ]
            self.filter_entries()
            self.status_var.set(f"Loaded {len(self.library_entries)} library targets.")
        except Exception as exc:
            messagebox.showerror("Library error", str(exc))

    def filter_entries(self):
        query = self.search_var.get().strip().lower()
        class_entries = {}
        for entry in sorted(self.library_entries, key=self.isotope_sort_key):
            class_name = entry.compound_class or entry.compound_name.rsplit("_", 1)[0]
            if query and query not in class_name.lower() and query not in entry.compound_name.lower():
                continue
            class_entries.setdefault(class_name, entry)
        self.filtered_entries = list(class_entries.values())
        self.compound_list.delete(0, tk.END)
        for entry in self.filtered_entries:
            class_name = entry.compound_class or entry.compound_name.rsplit("_", 1)[0]
            isotope_count = len(self.entries_for_class(class_name))
            self.compound_list.insert(
                tk.END,
                f"{class_name} | RT {entry.rt_min:.3f} | {isotope_count} isotopes",
            )
        if self.filtered_entries:
            self.compound_list.selection_set(0)
            self.show_selected_info()

    def add_mzml_files(self):
        paths = filedialog.askopenfilenames(
            title="Select mzML files",
            filetypes=[("mzML", "*.mzML *.mzml"), ("gzipped mzML", "*.mzML.gz *.mzml.gz"), ("All files", "*.*")],
        )
        for path in paths:
            self.append_mzml_file(path)
        if paths:
            self.status_var.set(f"Added {len(paths)} mzML file(s).")

    def clear_files(self):
        self.data_files.clear()
        if hasattr(self, "file_list"):
            self.file_list.delete(0, tk.END)
        self.last_results.clear()
        self.last_result_items.clear()
        self.status_var.set("Cleared mzML files.")

    def selected_entry(self):
        selection = self.compound_list.curselection()
        if not selection:
            return None
        return self.filtered_entries[selection[0]]

    def selected_entries(self):
        return [
            self.filtered_entries[idx]
            for idx in self.compound_list.curselection()
            if 0 <= idx < len(self.filtered_entries)
        ]

    def selected_class_names(self):
        classes = []
        seen = set()
        for entry in self.selected_entries():
            class_name = entry.compound_class or entry.compound_name.rsplit("_", 1)[0]
            if class_name not in seen:
                classes.append(class_name)
                seen.add(class_name)
        return classes

    def optional_float(self, value, label):
        text = value.get().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be numeric or blank.") from exc

    @staticmethod
    def isotope_sort_key(entry):
        tail = entry.compound_name.rsplit("_", 1)[-1]
        if tail.isdigit():
            return int(tail)
        return entry.precursor_mz

    @staticmethod
    def isotope_label(entry):
        tail = entry.compound_name.rsplit("_", 1)[-1]
        if tail.isdigit():
            return f"M+{tail}"
        return entry.compound_name

    def entries_for_class(self, class_name):
        return [
            entry
            for entry in self.library_entries
            if (entry.compound_class or entry.compound_name.rsplit("_", 1)[0]) == class_name
        ]

    def apply_selected_library_tolerances(self, entry):
        rt_window = max(abs(entry.rt_min - entry.rt_begin), abs(entry.rt_end - entry.rt_min))
        if rt_window > 0:
            self.rt_window_var.set(round(rt_window, 6))
        self.ms_level_var.set(entry.ms_level)
        self.apply_selected_mz_tolerance(entry)

    def apply_selected_mz_tolerance(self, entry=None):
        if entry is None:
            entry = self.selected_entry()
        if not entry:
            return
        if int(self.ms_level_var.get()) == 2:
            self.mz_window_var.set(entry.ms2_tolerance)
        else:
            self.mz_window_var.set(entry.ms1_tolerance)

    def show_selected_info(self):
        entry = self.selected_entry()
        self.info_text.delete("1.0", tk.END)
        if not entry:
            return
        self.apply_selected_library_tolerances(entry)
        class_names = self.selected_class_names()
        if len(class_names) > 1:
            preview = ", ".join(class_names[:6])
            if len(class_names) > 6:
                preview += f", ... +{len(class_names) - 6}"
            text = (
                f"{len(class_names)} metabolite classes selected\n"
                f"{preview}\n"
                f"Representative target: {entry.compound_name}\n"
                f"Library tolerances from first selection: MS1 {entry.ms1_tolerance:g}, MS2 {entry.ms2_tolerance:g}"
            )
        else:
            class_name = entry.compound_class or entry.compound_name.rsplit("_", 1)[0]
            isotope_count = len(self.entries_for_class(class_name))
            text = (
                f"{class_name}\n"
                f"Representative target: {entry.compound_name}  Isotope entries: {isotope_count}\n"
                f"RT: {entry.rt_min:.4f} min ({entry.rt_begin:.4f}-{entry.rt_end:.4f})  "
                f"Precursor: {entry.precursor_mz:.6f}  Product: {entry.product_mz:.6f}\n"
                f"Library tolerances: MS1 {entry.ms1_tolerance:g}, MS2 {entry.ms2_tolerance:g}"
            )
        self.info_text.insert("1.0", text)
        self.refresh_quant_isotopes(entry)

    def plot_selected(self):
        entry = self.selected_entry()
        if not entry:
            messagebox.showinfo("No target", "Select a library target first.")
            return
        if not self.data_files:
            messagebox.showinfo("No mzML", "Add one or more mzML files first.")
            return

        rt_window = float(self.rt_window_var.get())
        mz_window = float(self.mz_window_var.get())
        ms_level = int(self.ms_level_var.get())
        try:
            target_mz_override = self.optional_float(self.target_mz_var, "Target m/z")
            target_rt_override = self.optional_float(self.target_rt_var, "Target RT")
        except ValueError as exc:
            messagebox.showerror("Parameter error", str(exc))
            return
        self.status_var.set("Reading mzML data...")
        self.update_idletasks()

        result_items = []
        results = []
        errors = []
        for data_file in self.data_files:
            try:
                result = data_file.reader.extract(
                    entry,
                    rt_window,
                    mz_window,
                    ms_level,
                    target_mz_override=target_mz_override,
                    target_rt_override=target_rt_override,
                )
                results.append(result)
                result_items.append((result, data_file))
            except Exception as exc:
                errors.append(f"{os.path.basename(data_file.path)}: {exc}")

        self.last_results = results
        self.last_result_items = result_items
        spectrum_series = []
        eic_series = []
        for result, data_file in result_items:
            label = f"{data_file.group}: {result.file_name}" if data_file.group else result.file_name
            spectrum_series.append(
                {
                    "label": label,
                    "points": list(zip(result.mz_values, result.intensity_values)),
                    "style": "sticks",
                    "color": data_file.color,
                }
            )
            eic_series.append(
                {
                    "label": label,
                    "points": self.smooth_points_for_display(result.eic_points),
                    "style": "line",
                    "color": data_file.color,
                }
            )

        target_mz = target_mz_override if target_mz_override is not None else (
            entry.precursor_mz if ms_level == 1 else entry.product_mz
        )
        target_rt = target_rt_override if target_rt_override is not None else entry.rt_min
        self.spectrum_canvas.set_series(
            spectrum_series,
            f"{entry.compound_name} spectrum near RT {target_rt:.3f} min",
            "m/z",
            "Intensity",
        )
        self.render_eic_panel(
            eic_series,
            f"EIC m/z {target_mz:.5f} +/- {mz_window:g}",
            marker_x=target_rt,
        )

        total_points = sum(len(result.mz_values) for result in results)
        message = f"Plotted {len(results)} file(s), {total_points} spectrum point(s)."
        if errors:
            message += " Some files failed; see popup."
            messagebox.showwarning("mzML read warnings", "\n".join(errors))
        self.status_var.set(message)

    def plot_class_grid(self):
        entries = self.selected_entries()
        if not entries:
            messagebox.showinfo("No target", "Select a library target first.")
            return
        if not self.data_files:
            messagebox.showinfo("No mzML", "Add one or more mzML files first.")
            return

        try:
            title, grid_rows, file_infos, errors = self.build_class_panels(entries)
        except ValueError as exc:
            messagebox.showerror("Parameter error", str(exc))
            return

        self.render_class_grid_panel(title, grid_rows, file_infos)
        panel_count = len(flatten_grid_panels(grid_rows))
        message = f"Rendered grid with {panel_count} panel(s) and {len(self.data_files)} file(s)."
        if errors:
            message += " Some traces failed; see popup."
            messagebox.showwarning("Grid warnings", "\n".join(errors[:80]))
        self.status_var.set(message)

    def build_class_panels(self, entries):
        if not isinstance(entries, (list, tuple)):
            entries = [entries]
        rt_window = float(self.rt_window_var.get())
        mz_window = float(self.mz_window_var.get())
        ms_level = int(self.ms_level_var.get())
        target_mz_override = self.optional_float(self.target_mz_var, "Target m/z")
        target_rt_override = self.optional_float(self.target_rt_var, "Target RT")

        class_names = []
        seen = set()
        for entry in entries:
            class_name = entry.compound_class or entry.compound_name.rsplit("_", 1)[0]
            if class_name not in seen:
                class_names.append(class_name)
                seen.add(class_name)

        grid_rows = []
        errors = []
        self.status_var.set("Building class grid...")
        self.update_idletasks()
        for class_name in class_names:
            class_entries = sorted(self.entries_for_class(class_name), key=self.isotope_sort_key)
            if target_mz_override is not None and len(class_names) == 1:
                class_entries = [entries[0]]
            row_panels = []
            for target_entry in class_entries:
                series = []
                for data_file in self.data_files:
                    try:
                        result = data_file.reader.extract(
                            target_entry,
                            rt_window,
                            mz_window,
                            ms_level,
                            target_mz_override=target_mz_override,
                            target_rt_override=target_rt_override,
                        )
                        label = f"{data_file.group}: {result.file_name}" if data_file.group else result.file_name
                        eic_points = self.smooth_points_for_display(result.eic_points)
                        series.append(
                            {
                                "label": label,
                                "file": result.file_name,
                                "group": data_file.group,
                                "color": data_file.color,
                                "points": eic_points,
                            }
                        )
                    except Exception as exc:
                        errors.append(f"{target_entry.compound_name} / {os.path.basename(data_file.path)}: {exc}")
                mz_value = target_mz_override if target_mz_override is not None else (
                    target_entry.precursor_mz if ms_level == 1 else target_entry.product_mz
                )
                rt_value = target_rt_override if target_rt_override is not None else target_entry.rt_min
                row_panels.append(
                    {
                        "title": f"{self.isotope_label(target_entry)}  m/z {mz_value:.5f}  RT {rt_value:.3f}",
                        "series": series,
                        "marker_rt": rt_value,
                        "class_name": class_name,
                        "compound_name": target_entry.compound_name,
                    }
                )
            grid_rows.append({"class_name": class_name, "panels": row_panels})

        if len(class_names) == 1:
            title = f"{class_names[0]} EIC overlay"
        else:
            title = f"{len(class_names)} metabolite classes EIC isotope grid"
        file_infos = [
            {
                "name": os.path.basename(data_file.path),
                "group": data_file.group,
                "color": data_file.color,
            }
            for data_file in self.data_files
        ]
        return title, grid_rows, file_infos, errors

    def export_selected_class(self):
        entries = self.selected_entries()
        if not entries:
            messagebox.showinfo("No target", "Select a library target first.")
            return
        if not self.data_files:
            messagebox.showinfo("No mzML", "Add one or more mzML files first.")
            return
        output_dir = filedialog.askdirectory(title="Select output folder for class PNG and CSV")
        if not output_dir:
            return
        try:
            title, grid_rows, file_infos, errors = self.build_class_panels(entries)
        except ValueError as exc:
            messagebox.showerror("Parameter error", str(exc))
            return

        base = os.path.splitext(self.grid_default_filename(grid_rows, ""))[0]
        png_path = os.path.join(output_dir, f"{base}.png")
        csv_path = os.path.join(output_dir, f"{base}.csv")
        render_grid_png(title, grid_rows, file_infos, png_path)
        self.write_grid_csv(csv_path, grid_rows)
        message = f"Exported class PNG and CSV: {base}"
        if errors:
            message += " Some traces failed; see popup."
            messagebox.showwarning("Export warnings", "\n".join(errors[:80]))
        self.status_var.set(message)

    @staticmethod
    def grid_default_filename(grid_rows, extension):
        class_names = [row.get("class_name", "") for row in normalize_grid_rows(grid_rows) if row.get("class_name")]
        if not class_names:
            base = "class_grid"
        elif len(class_names) == 1:
            base = safe_filename(class_names[0])
        elif len(class_names) <= 4:
            base = safe_filename("_".join(class_names))
        else:
            base = safe_filename(f"{class_names[0]}_and_{len(class_names) - 1}_classes")
        return f"{base}{extension}"

    @staticmethod
    def write_grid_csv(path, grid_rows):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["class", "panel", "compound", "group", "file", "rt_min", "intensity"])
            for row in normalize_grid_rows(grid_rows):
                class_name = row.get("class_name", "")
                for panel in row["panels"]:
                    for item in panel["series"]:
                        for rt, intensity in item["points"]:
                            writer.writerow(
                                [
                                    class_name,
                                    panel["title"],
                                    panel.get("compound_name", ""),
                                    item.get("group", ""),
                                    item.get("file", item["label"]),
                                    rt,
                                    intensity,
                                ]
                            )

    def refresh_quant_isotopes(self, entry=None):
        entry = entry or self.selected_entry()
        if not entry or not hasattr(self, "quant_isotope_combo"):
            return
        class_name = entry.compound_class or entry.compound_name.rsplit("_", 1)[0]
        self.quant_combo_entries = sorted(self.entries_for_class(class_name), key=self.isotope_sort_key)
        values = [
            f"{item.compound_name} | RT {item.rt_min:.3f} | m/z {item.precursor_mz:.5f}"
            for item in self.quant_combo_entries
        ]
        self.quant_isotope_combo.configure(values=values)
        if values:
            self.quant_isotope_var.set(values[0])

    def load_quant_from_selected_class(self):
        entry = self.selected_entry()
        if not entry:
            messagebox.showinfo("No target", "Select a library target first.")
            return
        self.refresh_quant_isotopes(entry)
        self.result_tabs.select(self.quant_tab)
        self.load_quant_selected_isotope()

    def load_quant_selected_isotope(self):
        if not self.data_files:
            messagebox.showinfo("No mzML", "Add one or more mzML files first.")
            return
        idx = self.quant_isotope_combo.current()
        if idx < 0:
            self.refresh_quant_isotopes()
            idx = self.quant_isotope_combo.current()
        if idx < 0 or idx >= len(self.quant_combo_entries):
            return
        entry = self.quant_combo_entries[idx]
        self.quant_current_entry = entry
        for data_file in self.data_files:
            key = self.peak_key(entry, data_file)
            self.quant_points.pop(key, None)
            if key in self.peak_selections and self.peak_selections[key].source == "auto":
                self.peak_selections.pop(key, None)
        self.status_var.set(f"Loading peak panels for {entry.compound_name}...")
        self.update_idletasks()
        self.ensure_peak_data_for_entry(entry)
        self.render_quant_panels(entry)
        self.status_var.set(f"Loaded peak panels for {entry.compound_name}.")

    def peak_key(self, entry, data_file):
        return (entry.compound_name, data_file.path)

    @staticmethod
    def entry_rt_window(entry):
        return max(abs(entry.rt_min - entry.rt_begin), abs(entry.rt_end - entry.rt_min), 0.001)

    def smooth_points_for_display(self, points):
        return smooth_eic_points(
            points,
            self.smoothing_method_var.get(),
            int(self.smoothing_level_var.get()),
        )

    def ensure_peak_data_for_entry(self, entry):
        rt_window = self.entry_rt_window(entry)
        ms_level = entry.ms_level
        mz_window = entry.ms2_tolerance if ms_level == 2 else entry.ms1_tolerance
        for data_file in self.data_files:
            key = self.peak_key(entry, data_file)
            if key not in self.quant_points:
                result = data_file.reader.extract(entry, rt_window, mz_window, ms_level)
                self.quant_points[key] = self.smooth_points_for_display(result.eic_points)
            if key not in self.peak_selections:
                self.peak_selections[key] = auto_pick_peak(
                    self.quant_points[key],
                    entry.rt_min,
                    rt_window,
                    min_peak_width=int(self.minimum_peak_width_var.get()),
                )

    def render_quant_panels(self, entry):
        for child in self.quant_body.winfo_children():
            child.destroy()
        max_columns = 2
        for idx, data_file in enumerate(self.data_files):
            key = self.peak_key(entry, data_file)
            points = self.quant_points.get(key, [])
            selection = self.peak_selections.get(key)
            frame = ttk.Frame(self.quant_body, padding=5)
            frame.grid(row=idx // max_columns, column=idx % max_columns, sticky="nsew")
            title = f"{data_file.group}: {os.path.basename(data_file.path)}"
            PeakPickCanvas(
                frame,
                title,
                points,
                selection,
                data_file.color,
                entry.rt_min,
                on_select=lambda start, end, e=entry, d=data_file: self.set_peak_range(e, d, start, end),
                on_clear=lambda e=entry, d=data_file: self.clear_peak_range(e, d),
            ).pack()

    def set_peak_range(self, entry, data_file, rt_start, rt_end):
        key = self.peak_key(entry, data_file)
        points = self.quant_points.get(key, [])
        self.peak_selections[key] = summarize_peak(points, rt_start, rt_end, source="manual", enabled=True)
        self.render_quant_panels(entry)

    def clear_peak_range(self, entry, data_file):
        key = self.peak_key(entry, data_file)
        current = self.peak_selections.get(key)
        if current:
            current.enabled = False
            current.source = "cleared"
            current.peak_height = 0.0
            current.peak_area = 0.0
        else:
            self.peak_selections[key] = PeakSelection(entry.rt_min, entry.rt_min, 0.0, 0.0, entry.rt_min, "cleared", False)
        self.render_quant_panels(entry)

    def auto_pick_all_library(self):
        if not self.library_entries:
            messagebox.showinfo("No library", "Load or build a library first.")
            return
        if not self.data_files:
            messagebox.showinfo("No mzML", "Add one or more mzML files first.")
            return
        total = len(self.library_entries) * len(self.data_files)
        done = 0
        for entry in self.library_entries:
            self.ensure_peak_data_for_entry(entry)
            done += len(self.data_files)
            if done % max(len(self.data_files), 1) == 0:
                self.status_var.set(f"Auto-picked {done}/{total} traces...")
                self.update_idletasks()
        self.status_var.set(f"Auto-picked {total} traces. Manual edits were preserved.")
        if self.quant_current_entry:
            self.render_quant_panels(self.quant_current_entry)

    def export_peak_csv(self):
        if not self.library_entries:
            messagebox.showinfo("No library", "Load or build a library first.")
            return
        if not self.data_files:
            messagebox.showinfo("No mzML", "Add one or more mzML files first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export peak quant CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        self.auto_pick_all_library()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "Class",
                    "Compound name",
                    "File",
                    "Group",
                    "RT min",
                    "Precursor mz",
                    "RT start",
                    "RT end",
                    "Apex RT",
                    "Peak height",
                    "Peak area",
                    "Source",
                    "Enabled",
                ]
            )
            for entry in self.library_entries:
                for data_file in self.data_files:
                    key = self.peak_key(entry, data_file)
                    selection = self.peak_selections.get(key)
                    if selection is None:
                        continue
                    writer.writerow(
                        [
                            entry.compound_class,
                            entry.compound_name,
                            os.path.basename(data_file.path),
                            data_file.group,
                            entry.rt_min,
                            entry.precursor_mz,
                            selection.rt_start,
                            selection.rt_end,
                            selection.apex_rt,
                            selection.peak_height,
                            selection.peak_area,
                            selection.source,
                            selection.enabled,
                        ]
                    )
        self.status_var.set(f"Exported peak quant CSV: {path}")

    def export_csv(self):
        if not self.last_results:
            messagebox.showinfo("No data", "Plot data before exporting.")
            return
        path = filedialog.asksaveasfilename(
            title="Export extracted data",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["type", "group", "color", "file", "rt_min", "mz", "intensity"])
            items = self.last_result_items or [(result, InputFile("", "", "", None)) for result in self.last_results]
            for result, data_file in items:
                for mz, intensity in zip(result.mz_values, result.intensity_values):
                    writer.writerow(["spectrum", data_file.group, data_file.color, result.file_name, "", mz, intensity])
                for rt, intensity in result.eic_points:
                    writer.writerow(["eic", data_file.group, data_file.color, result.file_name, rt, "", intensity])
        self.status_var.set(f"Exported {path}")


def main():
    app = LCMSMSApp()
    app.mainloop()


if __name__ == "__main__":
    main()
