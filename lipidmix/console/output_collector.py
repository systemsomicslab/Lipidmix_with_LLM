"""ジョブ生成物の収集と役割付け。

実行前後のディレクトリスナップショット差分で今回ジョブの生成物を特定する。
更新時刻ではなく「実行前に存在しなかった or サイズ変化したファイル」を使う。
"""
from __future__ import annotations

import os
from pathlib import Path

from lipidmix.handoff.schema import Artifact, MztabEntry, sha256_file

# 拡張子パターン → role のマッピング（長い拡張子を先に評価する）
_ROLE_MAP: list[tuple[str, str, str]] = [
    (".mzTab",    "primary_mztab",     "mztab"),
    (".EIC.aef",  "chromatogram",      "eicaef"),
    (".arf2",     "spot_catalog",      "arf2"),
    (".arf",      "peak_matrix_source","arf"),
    (".pai2",     "sample_peaks",      "pai2"),
    (".dcl",      "msms_evidence",     "dcl"),
]


def snapshot(directory: Path) -> dict[str, int]:
    """ディレクトリ以下の全ファイルを {相対パス文字列: サイズ} で返す。"""
    result: dict[str, int] = {}
    if not directory.exists():
        return result
    for root, _, files in os.walk(directory):
        for name in files:
            fp = Path(root) / name
            try:
                result[str(fp.relative_to(directory))] = fp.stat().st_size
            except (OSError, ValueError):
                pass
    return result


def collect_artifacts(
    run_dir: Path,
    before: dict[str, int],
) -> tuple[list[MztabEntry], list[Artifact]]:
    """実行後の run_dir を before スナップショットと比較し、新規・変化ファイルを収集する。

    Returns
    -------
    (mztab_entries, other_artifacts):
        mztab_entries: .mzTab ファイルを MztabEntry として返す（measure/polarity は未確定）。
        other_artifacts: それ以外のファイルを Artifact として返す。
    """
    after = snapshot(run_dir)
    new_or_changed = {
        rel: size
        for rel, size in after.items()
        if rel not in before or before[rel] != size
    }

    mztab_entries: list[MztabEntry] = []
    other_artifacts: list[Artifact] = []

    for rel_str in sorted(new_or_changed):
        fp = run_dir / rel_str
        if not fp.is_file():
            continue
        role, fmt = _assign_role(rel_str)
        checksum = sha256_file(fp)

        if fmt == "mztab":
            polarity, measure = _infer_mztab_meta(fp.name)
            mztab_entries.append(MztabEntry(
                path=rel_str,
                polarity=polarity,
                measure=measure,
                sha256=checksum,
                validation={},
            ))
        else:
            other_artifacts.append(Artifact(
                path=rel_str,
                role=role,
                format=fmt,
                sha256=checksum,
            ))

    return mztab_entries, other_artifacts


def _assign_role(rel_str: str) -> tuple[str, str]:
    lower = rel_str.lower()
    for suffix, role, fmt in _ROLE_MAP:
        if lower.endswith(suffix.lower()):
            return role, fmt
    return "unknown", Path(rel_str).suffix.lstrip(".") or "bin"


def _infer_mztab_meta(filename: str) -> tuple[str, str]:
    """ファイル名プレフィックスから極性と定量種別を推定する。

    MS-DIAL GUI の命名規則: `Height_AlignmentResult_...mzTab` / `Area_AlignmentResult_...mzTab`
    極性は `Neg_` / `Pos_` または `neg` / `pos` をファイル名から探す。
    確定できない場合は `positive` / `peak_height` を既定とし、validation で上書きする。
    """
    name_lower = filename.lower()

    if "area" in name_lower:
        measure: str = "peak_area_above_zero"
    else:
        measure = "peak_height"

    if "neg" in name_lower:
        polarity: str = "negative"
    elif "pos" in name_lower:
        polarity = "positive"
    else:
        polarity = "positive"

    return polarity, measure
