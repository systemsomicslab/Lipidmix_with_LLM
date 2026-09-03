"""ジョブ生成物の収集と役割付け。

実行前後のディレクトリスナップショット差分で今回ジョブの生成物を特定する。
更新時刻ではなく「実行前に存在しなかった or サイズ変化したファイル」を使う。

mzTab エントリの polarity / measure は 2 つの独立な証拠から決める:
  1. ファイル名の信号（MS-DIAL GUI の `Height_` / `Area_` prefix、`Neg` / `Pos` トークン）
  2. ジョブが宣言した値（console_plan でユーザーが指定したもの）
ファイル名は**実物の性質**を語り、宣言は**意図**でしかないので、両方あって食い違う
ときはファイル名を採り、食い違い自体を validation に記録する。ファイル名が黙って
いるときだけ宣言値で埋める。どちらも無いときにだけ既定へ落とす。

**信号なしを既定値と混ぜてはいけない**——旧実装は極性トークンを持たない
`Height_AlignmentResult_<timestamp>.mzTab`（MS-DIAL のアライメント出力名の実物）を
無条件に positive としていたため、negative で計画したジョブの全エントリが positive
と記録され、analysis-job.json と console_status が嘘を表示し続けた。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from lipidmix.handoff.schema import Artifact, MztabEntry, sha256_file

# ジョブ運用のためにランディレクトリへ書かれるファイル。MS-DIAL の生成物ではない。
# msdial.log は run_msdial が必ず作るため、除外しないと「出力ゼロ」を検出できない。
# analysis-job.json は実行中に status 遷移で書き換わるため、差分に混入する。
_OPERATIONAL_FILES = frozenset({"msdial.log", "analysis-job.json"})

# 拡張子パターン → role のマッピング（長い拡張子を先に評価する）
_ROLE_MAP: list[tuple[str, str, str]] = [
    (".mzTab",    "primary_mztab",     "mztab"),
    (".EIC.aef",  "chromatogram",      "eicaef"),
    (".arf2",     "spot_catalog",      "arf2"),
    (".arf",      "peak_matrix_source","arf"),
    (".pai2",     "sample_peaks",      "pai2"),
    (".dcl",      "msms_evidence",     "dcl"),
]

# ファイル名の極性トークン。`Neg_` / `_NEG` / `.pos.` のような**語**として現れた
# ものだけを信号とみなす。部分文字列一致にすると Negev のような無関係な語を
# 極性と誤読する。
_POLARITY_TOKEN_RE = re.compile(r"(?:^|[^a-z])(neg|pos)(?:[^a-z]|$)", re.IGNORECASE)
# 定量種別は MS-DIAL GUI の prefix でのみ判断する（validator._HEIGHT_PREFIX_RE と同規則）。
_HEIGHT_PREFIX_RE = re.compile(r"^Height_", re.IGNORECASE)
_AREA_PREFIX_RE = re.compile(r"^Area_", re.IGNORECASE)
# spec §8.1: normalized value は「実装と実データ検証を追加するまで未対応」。
# peak_height / peak_area_above_zero のどちらでもないので、正準候補から外す。
_NORMALIZED_PREFIX_RE = re.compile(r"^Normalized", re.IGNORECASE)

UNSUPPORTED_MZTAB_ROLE = "unsupported_mztab"

_DEFAULT_POLARITY = "positive"
_DEFAULT_MEASURE = "peak_height"


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
    *,
    declared_polarity: str | None = None,
    declared_measure: str | None = None,
) -> tuple[list[MztabEntry], list[Artifact]]:
    """実行後の run_dir を before スナップショットと比較し、新規・変化ファイルを収集する。

    declared_polarity / declared_measure は analysis-job.json の project 値
    （console_plan でユーザーが宣言したもの）。ファイル名が黙っている軸を埋め、
    食い違う軸を記録するために使う。省略時は従来どおり推定と既定値だけで決める。

    Returns
    -------
    (mztab_entries, other_artifacts):
        mztab_entries: 正準候補になれる .mzTab を MztabEntry として返す。
        other_artifacts: それ以外。未対応 measure の .mzTab（Normalized*）も
            role=unsupported_mztab としてここに入る——記録は残すが、
            dataset_load が正準として選べないようにするため。
    """
    after = snapshot(run_dir)
    new_or_changed = {
        rel: size
        for rel, size in after.items()
        if (rel not in before or before[rel] != size)
        and Path(rel).name not in _OPERATIONAL_FILES
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
            if _NORMALIZED_PREFIX_RE.match(fp.name):
                other_artifacts.append(Artifact(
                    path=rel_str,
                    role=UNSUPPORTED_MZTAB_ROLE,
                    format=fmt,
                    sha256=checksum,
                ))
                continue
            polarity, measure, validation = _resolve_mztab_meta(
                fp.name, declared_polarity, declared_measure)
            mztab_entries.append(MztabEntry(
                path=rel_str,
                polarity=polarity,
                measure=measure,
                sha256=checksum,
                validation=validation,
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


def _infer_mztab_meta(filename: str) -> tuple[str | None, str | None]:
    """ファイル名から極性と定量種別を推定する。**推定できなければ None を返す。**

    None は「このファイル名は当該軸について何も語っていない」の意味。既定値へ
    落とすのは呼び出し側の判断であり、ここで既定値を返すと「positive と書いてある」
    と「何も書いていない」が区別できなくなる。

    MS-DIAL GUI の命名規則: `Height_AlignmentResult_...mzTab` / `Area_AlignmentResult_...mzTab`。
    Console のアライメント出力名には極性トークンが無いことが実データで確認済み。
    """
    m = _POLARITY_TOKEN_RE.search(filename)
    polarity: str | None = None
    if m:
        polarity = "negative" if m.group(1).lower() == "neg" else "positive"

    measure: str | None = None
    if _NORMALIZED_PREFIX_RE.match(filename):
        measure = None      # spec §8.1 未対応。height/area のどちらでもない
    elif _HEIGHT_PREFIX_RE.match(filename):
        measure = "peak_height"
    elif _AREA_PREFIX_RE.match(filename):
        measure = "peak_area_above_zero"

    return polarity, measure


def _resolve_mztab_meta(
    filename: str,
    declared_polarity: str | None,
    declared_measure: str | None,
) -> tuple[str, str, dict]:
    """ファイル名の推定とジョブの宣言値から (polarity, measure, validation) を決める。"""
    inferred_polarity, inferred_measure = _infer_mztab_meta(filename)

    polarity, polarity_source = _pick(inferred_polarity, declared_polarity, _DEFAULT_POLARITY)
    measure, measure_source = _pick(inferred_measure, declared_measure, _DEFAULT_MEASURE)

    validation: dict = {
        "polarity_source": polarity_source,
        "measure_source": measure_source,
    }
    conflicts: dict = {}
    if inferred_polarity and declared_polarity and inferred_polarity != declared_polarity:
        conflicts["polarity"] = {"filename": inferred_polarity, "job_declared": declared_polarity}
    if inferred_measure and declared_measure and inferred_measure != declared_measure:
        conflicts["measure"] = {"filename": inferred_measure, "job_declared": declared_measure}
    if conflicts:
        validation["conflicts"] = conflicts
    return polarity, measure, validation


def _pick(inferred: str | None, declared: str | None, default: str) -> tuple[str, str]:
    """ファイル名 → ジョブ宣言 → 既定値 の順に採用し、採用元の名前も返す。"""
    if inferred:
        return inferred, "filename"
    if declared:
        return declared, "job_declared"
    return default, "default"
