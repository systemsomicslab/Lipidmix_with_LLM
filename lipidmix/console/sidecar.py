"""サイドカーファイル生成（feature-qc.tsv）。

MS-DIAL Console 実行完了後に呼ぶ純関数群。MCP 非依存・セッション非依存。
役割はサンプル名から推定する（session.arf を参照しない。console_run の時点で
ARF はまだ読まれていないし、DatasetState 経路を ARF スロットに再結合しない）。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from lipidmix.analysis import preprocessing

SIDECAR_SUBDIR = "sidecars"
FEATURE_QC_FILENAME = "feature-qc.tsv"
ARTIFACT_ROLE = "sample_qc_sidecar"

COLUMNS = ["sample_name", "role", "batch", "batch_source", "run_order"]

# lipidmix/analysis/dataset_analysis.py の _DATE_RE と同一パターン。
# console 層は analysis 層に依存できるが逆方向は不可なので、共有せずここに複製する
# （この 1 行を factor out すると console<->analysis の相互依存になってしまう）。
_DATE_RE = re.compile(r"(\d{8})")


def build_sample_meta_from_names(sample_names) -> dict:
    """サンプル名から {name: {role, batch, batch_source, run_order}} を組む。

    ロール判定は lipidmix/analysis/preprocessing.py の detect_sample_roles に
    委ねる（ARF 経路・DatasetState 経路と同じ判定を使う）。
    run_order は MS-DIAL の出力から取れないため None。
    """
    names = list(sample_names)
    roles = preprocessing.detect_sample_roles(names)
    meta: dict = {}
    for name in names:
        m = _DATE_RE.search(name)
        meta[name] = {
            "role": roles.get(name, "sample"),
            "batch": m.group(1) if m else None,
            "batch_source": "filename_date" if m else None,
            "run_order": None,
        }
    return meta


def generate_feature_qc_tsv(sample_meta: dict, output_path: Path) -> None:
    """sample_meta から feature-qc.tsv を生成する。None は空欄で書く。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(COLUMNS)
        for name in sorted(sample_meta):
            meta = sample_meta[name]
            writer.writerow([
                name,
                meta.get("role") or "sample",
                meta.get("batch") or "",
                meta.get("batch_source") or "",
                "" if meta.get("run_order") is None else meta["run_order"],
            ])
