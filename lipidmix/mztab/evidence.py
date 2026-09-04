"""mzTab-M 経路の evidence sidecar —— gap-fill 由来の検出状態を `.arf` から補う。

**なぜ必要か**: mzTab-M の `abundance_assay[N]` は非ゼロでも、それが実測ピークか
gap-fill（未検出セルの補間）かを区別しない。実データ（60 サンプル × 714 特徴）では
**セルの 70.0% が gap-fill** だったので、非ゼロを検出と数えると検出率を 3 倍以上に
過大評価する。spec §10.1 の evidence sidecar が要求するのはこの区別で、初期実装は
`.arf` から作ってよいとされている。

**ID 空間の接合**: `.arf` のスポット順が mzTab の `SMF_ID`（0 起点の連番）と一致する。
これは MS-DIAL がアライメント順にエクスポートする結果だが、**ファイル名では検証しない**。
名前が合っていても別のアライメントということが起こりうるうえ、誤接合すると
「検出/未検出」を特徴間で入れ替えたまま静かに嘘をつく。代わりに数値で確かめる:

  - スポット数 == 特徴数
  - 全特徴で |m/z(.arf 代表値) − m/z(mzTab 平均値)| <= 許容（既定 0.01 Da）

実データでの裏取り: 位置一致では最大 6.7 mDa に収まり、1 つずらすと 87 Da・RT 20 分に
爆発する。したがってこの 2 条件は偶然には成立しない。

サンプル軸は `.arf` の Key0..9 に入るファイル名と mzTab の assay 表示名が一致する
（実データで完全一致）。列順は `.arf` の FileID 順ではなく **mzTab の assay 順**に
そろえる。`feature_matrix` と同じ並びでないとマスクを重ねられない。

TSV は書かない。2026-09-03 に「消費者のいない `feature-qc.tsv`」を廃止した経緯があり、
同じものを再びファイルとして作っても読み手がいない。検出状態は `DatasetState` の
`detected_mask`（bool 行列）と `feature_qc`（要約）として持つ。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from lipidmix.arf import reader as arf_reader

#: m/z 一致の許容。`.arf` はスポット代表サンプルの m/z、mzTab はアライメント平均値を
#: 書くため、同一特徴でも完全一致はしない。実データの最大差 6.7 mDa に対して
#: 1 桁の余裕を取り、隣接特徴（実データ最小間隔は Da オーダー）とは混同しない幅。
MZ_TOLERANCE = 0.01

#: gap-fill 判定に使えない `.arf` の候補を弾くための除外語。MS-DIAL 自身の綴り
#: 揺れ（DriftSopts）も含める。イオンモビリティのドリフトスポットは
#: アライメント特徴と 1:1 対応しない。
_EXCLUDED_ARF_TOKENS = ("driftspots", "driftsopts")


def normalize_arf_spots(spots: list[dict]) -> list[dict]:
    """`arf_reader.deserialize()` の出力を (m/z, サンプル別 gap-fill) だけに落とす。

    以降の接合ロジックが `.arf` の msgpack Key 番号を知らずに済むようにする境界。
    """
    normalized = []
    for spot in spots:
        aligned = spot.get("AlignedPeakProperties")
        if not isinstance(aligned, list):
            continue
        cells = []
        for index, raw in enumerate(aligned):
            feature = arf_reader._convert_to_alignment_feature(raw)
            if not feature:
                continue
            name = feature.get("file_name") or f"Sample_{index}"
            cells.append({
                "name": name,
                "is_gap_filled": bool(feature.get("is_gap_filled")),
            })
        normalized.append({"mz": spot.get("MassCenter"), "samples": cells})
    return normalized


def build_evidence(
    normalized_spots: list[dict],
    *,
    feature_mz: list[float | None],
    sample_names: list[str],
    mz_tolerance: float = MZ_TOLERANCE,
) -> dict:
    """検出マスクを組む。接合が確認できなければ `status="rejected"` を返す。

    例外ではなく封筒で返す。取り込めないことは異常事態ではなく（別アライメントの
    `.arf` が同じフォルダに居るのは普通）、呼び出し側は warning にして解析を続ける。
    """
    n_features = len(feature_mz)
    if len(normalized_spots) != n_features:
        return {
            "status": "rejected",
            "reason": "feature_count_mismatch",
            "detail": {"n_arf_spots": len(normalized_spots),
                       "n_mztab_features": n_features},
        }

    worst = 0.0
    worst_index = None
    for index, (spot, mz_expected) in enumerate(zip(normalized_spots, feature_mz)):
        mz_actual = spot.get("mz")
        if mz_actual is None or mz_expected is None:
            continue
        delta = abs(float(mz_actual) - float(mz_expected))
        if delta > worst:
            worst, worst_index = delta, index
    if worst > mz_tolerance:
        return {
            "status": "rejected",
            "reason": "mz_mismatch",
            "detail": {"worst_mz_delta": worst, "at_feature_index": worst_index,
                       "tolerance": mz_tolerance},
        }

    arf_names = [cell["name"] for cell in (normalized_spots[0]["samples"]
                                          if normalized_spots else [])]
    position = {name: i for i, name in enumerate(arf_names)}
    missing = [name for name in sample_names if name not in position]
    if missing:
        return {
            "status": "rejected",
            "reason": "sample_axis_mismatch",
            "detail": {"missing_in_arf": missing, "n_arf_samples": len(arf_names)},
        }

    columns = [position[name] for name in sample_names]
    mask = np.ones((n_features, len(sample_names)), dtype=bool)
    for row, spot in enumerate(normalized_spots):
        cells = spot["samples"]
        for col, source in enumerate(columns):
            if source < len(cells):
                mask[row, col] = not cells[source]["is_gap_filled"]

    n_cells = int(mask.size)
    n_detected = int(mask.sum())
    return {
        "status": "ok",
        "detected_mask": mask,
        "n_cells": n_cells,
        "n_detected": n_detected,
        "gap_filled_rate": round(1.0 - n_detected / n_cells, 4) if n_cells else None,
        "detail": {"worst_mz_delta": worst, "tolerance": mz_tolerance,
                   "feature_axis": "arf_spot_index == mztab_smf_id",
                   "sample_axis": "arf_file_name == mztab_assay_name"},
    }


def arf_candidates(mztab_path: str | Path,
                   artifact_paths: dict[str, list[str]] | None = None) -> list[Path]:
    """gap-fill を持ちうる `.arf` の候補を優先順に返す。

    1. handoff の `peak_matrix_source`（Console 実行の記録。出所が確かな順）
    2. mzTab と同じフォルダの `*PeakProperties.arf`
    3. 同じフォルダのその他 `.arf`（DriftSpots 系は除く）

    候補の妥当性はここでは判定しない。名前で決め打ちにせず `build_evidence` の
    数値検証に委ねる。
    """
    candidates: list[Path] = []

    def add(path: Path) -> None:
        name = path.name.lower()
        if not name.endswith(".arf"):
            return
        if any(token in name for token in _EXCLUDED_ARF_TOKENS):
            return
        if path not in candidates and path.is_file():
            candidates.append(path)

    for path_str in (artifact_paths or {}).get("peak_matrix_source", []):
        add(Path(path_str))

    directory = Path(mztab_path).parent
    if directory.is_dir():
        for path in sorted(directory.glob("*.arf")):
            if "peakproperties" in path.name.lower():
                add(path)
        for path in sorted(directory.glob("*.arf")):
            add(path)
    return candidates


def load_arf_evidence(
    arf_path: str | Path,
    *,
    feature_mz: list[float | None],
    sample_names: list[str],
    mz_tolerance: float = MZ_TOLERANCE,
) -> dict:
    """`.arf` を 1 本読んで検出マスクを組む。読めなければ `rejected` を返す。"""
    try:
        with open(arf_path, "rb") as handle:
            spots = arf_reader.deserialize(handle)
    except Exception as exc:                      # noqa: BLE001 - 破損/別形式を包む
        return {"status": "rejected", "reason": "arf_unreadable",
                "detail": {"path": str(arf_path), "error": str(exc)}}
    result = build_evidence(
        normalize_arf_spots(spots),
        feature_mz=feature_mz,
        sample_names=sample_names,
        mz_tolerance=mz_tolerance,
    )
    result.setdefault("detail", {})["path"] = str(arf_path)
    return result


def apply_evidence(ds, result: dict | None) -> None:
    """`build_evidence` の封筒を DatasetState に反映する。

    取り込めなかった場合に **黙って検出 0 として畳まない**のが要点。検出状態が
    「無い」のと「全部未検出」は解釈が正反対で、前者は検出率を語ってはいけない
    状態、後者はデータが空という結論になる。`detected_mask` は None のまま残し、
    `feature_qc["source"] = None` と理由を書き、警告で利用者に伝える。
    """
    if result is None:
        ds.feature_qc = {"source": None, "reason": "no_candidate"}
        return
    if result.get("status") != "ok":
        reason = result.get("reason", "unknown")
        detail = {k: v for k, v in (result.get("detail") or {}).items()}
        ds.feature_qc = {"source": None, "reason": reason, "detail": detail}
        ds.validation_result.setdefault("warnings", []).append(
            f"隣接する .arf から検出状態（gap-fill）を取り込めませんでした"
            f"（理由: {reason}）。この mzTab-M の非ゼロ値には gap-fill による補間値が"
            "含まれる可能性があり、**検出率・欠測率を語れません**。"
            "検出状態が必要なら ARF 経路（arf_parser / arf_preprocess）を使ってください。")
        return

    ds.detected_mask = result["detected_mask"]
    ds.feature_qc = {
        "source": "arf",
        "n_cells": result["n_cells"],
        "n_detected": result["n_detected"],
        "gap_filled_rate": result["gap_filled_rate"],
        "join": result["detail"],
    }


def attach_to_dataset(ds, mztab_path: str | Path) -> None:
    """候補 `.arf` を順に試し、最初に検証を通ったものを DatasetState に載せる。

    候補が無ければ `no_candidate`、全部落ちたら最後の却下理由を残す。
    """
    feature_mz = [ds.feature_metadata.get(fid, {}).get("mz") for fid in ds.feature_ids]
    candidates = arf_candidates(mztab_path, ds.artifact_paths)
    last = None
    for candidate in candidates:
        last = load_arf_evidence(
            candidate, feature_mz=feature_mz, sample_names=ds.sample_names)
        if last.get("status") == "ok":
            break
    apply_evidence(ds, last)
