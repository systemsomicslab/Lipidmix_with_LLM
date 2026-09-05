"""Console完了ゲート: 終了証跡・主mzTab-M・入力対応の検証（spec §5.2）。

exit_code=0・termination=exited だけでは「実行が終わった」以上の意味を持たない。
`completion_status`はconsole-execution.v1受信後にジョブの終端状態を決める唯一の
場所であり、構造的に妥当な主mzTab-Mが一意に選べ、定量行列に有限値があり、
予定した準備済みraw全件がassayへ1対1で対応していることまで`validate_outputs`が
確認した結果を要求する。拡張子が`.mzTab`であるだけ、exit codeが0であるだけでは
completedにならない。

`map_assays`はMTDの`assay[N]-ms_run_ref` → `ms_run[N]-location`を辿り、file URIを
decodeして準備済みrawへ厳密対応させる。**表示名（assay[N]の値）では一切結合
しない**——表示名はConsole/GUIが自由に付けるラベルで、同名衝突・空白トリムの
揺れがあっても実体の対応関係を語らない。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import numpy as np

from lipidmix.console.output_collector import read_adduct_polarity
from lipidmix.handoff.schema import AnalysisJob
from lipidmix.mztab.reader import extract_abundance_matrix, parse_mztab
from lipidmix.mztab.validator import detect_quantification_measure, validate_mztab

# MTD の ms_run[N]-location / assay[N]-ms_run_ref キーパターン。
# lipidmix.mztab.reader の同名正規表現はモジュール private なのでここでは
# 複製する（output_collector.py の RUNS_SUBDIR 複製と同じ方針。層を跨いだ
# private 参照を避ける）。
_MS_RUN_LOCATION_RE = re.compile(r"^ms_run\[(\d+)\]-location$")
_ASSAY_REF_RE = re.compile(r"^assay\[(\d+)\]-ms_run_ref$")


def _decode_file_uri(value: str) -> str:
    """mzTabのfile URIをローカルパス文字列へdecodeする（%XXを実文字へ戻す）。

    `file:///C:/...`（Windows）と `file:///data/...`（POSIX）の両方を扱う。
    file: スキームでない値はそのままURL decodeだけ行う（保守的なフォールバック）。
    """
    value = value.strip()
    if value.startswith("file:"):
        path = unquote(urlsplit(value).path)
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]  # "/C:/..." の先頭スラッシュを落とす
    else:
        path = unquote(value)
    # URIは常に "/" 区切り。呼び出し側にOSネイティブな見た目のパスを渡す
    # （Windowsではセパレータをbackslashへ揃える。比較は_normalize_pathが担う）。
    return str(Path(path))


def _normalize_path(value: str) -> str:
    """パス比較専用の正規化。Windows想定で大文字小文字とセパレータを揃える。"""
    return os.path.normcase(os.path.normpath(value))


def map_assays(parsed: dict, staged_sources: list[str]) -> dict[str, str]:
    """assay列名 → 準備済みrawパス、の対応表をMTDから構築する。

    `assay[N]-ms_run_ref` で `ms_run[N]` を引き、その `ms_run[N]-location` の
    file URIをdecodeして `staged_sources` の要素と厳密照合する（大文字小文字・
    セパレータの正規化のみ許容し、部分一致・編集距離・表示名一致はしない）。

    戻り値: `{"abundance_assay[N]": <staged_sourcesの要素 or decode済みパス>}`。
    `staged_sources`のどの要素とも一致しない場合はdecode済みパスをそのまま
    値に入れる——黙って捨てると「予期しない追加サンプル」を上位が検出できなく
    なるため。`ms_run[N]-location`自体が無いassayはmapに含めない（そのassayは
    どの生データにも対応しないので、mapに現れないことで欠落として検出できる）。
    """
    metadata = parsed.get("metadata", {})

    run_locations: dict[str, str] = {}
    for key, value in metadata.items():
        m = _MS_RUN_LOCATION_RE.match(key)
        if m and value:
            run_locations[f"ms_run[{m.group(1)}]"] = _decode_file_uri(value)

    staged_by_norm = {_normalize_path(s): s for s in staged_sources}

    result: dict[str, str] = {}
    for key, value in metadata.items():
        m = _ASSAY_REF_RE.match(key)
        if not m or not value:
            continue
        location = run_locations.get(value.strip())
        if location is None:
            continue
        matched = staged_by_norm.get(_normalize_path(location))
        result[f"abundance_assay[{m.group(1)}]"] = matched if matched is not None else location
    return result


def _artifact_abs_path(job: AnalysisJob, root: str, rel: str) -> Path:
    """analysis-job.v2のrootに応じてrun_dir/dataset_root基準の絶対パスを解決する。

    lipidmix.tools.mztab_tools._artifact_abs_path と同じ規則。
    """
    base = Path(job.dataset_root) if root == "dataset_root" else Path(job.run_dir)
    return (base / rel).resolve()


def _envelope(errors: list[str], warnings: list[str], primary_path: str | None,
              sample_map: dict[str, str]) -> dict:
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "primary_path": primary_path,
        "sample_map": sample_map,
    }


def validate_outputs(job: AnalysisJob, receipt: dict, expected_sources: list[str]) -> dict:
    """主mzTab-Mの構造・定量・入力対応をまとめて検証する（spec §5.2 の3〜5）。

    以下のいずれかでも該当すれば `ok=False`:
      - 主mzTab候補が0件または複数件（一意に選べない）
      - mzTab-Mの構造検証（validate_mztab）が失敗
      - 定量行列が空、または有限値が1件もない
      - ファイル名/MTDから読める定量種別がjob宣言と致命的に矛盾する
      - アダクト多数決の極性がjob宣言と矛盾する
      - 予定した準備済みraw(`expected_sources`)とassayの対応が1対1でない
        （欠落・重複・予期しない追加）

    `receipt`は現状exit_code/terminationを直接は使わない（`completion_status`が
    別途扱う）。実行証跡由来の追加検査を将来足す余地として引数に残す。
    """
    errors: list[str] = []
    warnings: list[str] = []

    candidates = job.primary_mztab_files
    if len(candidates) == 0:
        return _envelope(["PRIMARY_MZTAB_MISSING"], warnings, None, {})
    if len(candidates) > 1:
        return _envelope(["AMBIGUOUS_PRIMARY_MZTAB"], warnings, None, {})

    entry = candidates[0]
    abs_path = _artifact_abs_path(job, entry.root, entry.path)
    if not abs_path.is_file():
        return _envelope(["PRIMARY_MZTAB_MISSING"], warnings, None, {})

    primary_path = str(abs_path)
    parsed = parse_mztab(abs_path)
    structure = validate_mztab(parsed)
    errors.extend(structure["errors"])
    warnings.extend(structure["warnings"])

    matrix, _sample_names, _feature_ids = extract_abundance_matrix(parsed)
    if matrix.size == 0:
        errors.append("EMPTY_ABUNDANCE_MATRIX")
    elif not np.isfinite(matrix).any():
        errors.append("NO_FINITE_ABUNDANCE_VALUES")

    measure, measure_confidence = detect_quantification_measure(parsed, abs_path.name)
    if measure_confidence == "conflict" or (measure is not None and measure != entry.measure):
        errors.append("MEASURE_MISMATCH")

    majority = read_adduct_polarity(abs_path)["adduct_majority"]
    if majority is not None and majority != entry.polarity:
        errors.append("POLARITY_MISMATCH")

    sample_map = map_assays(parsed, expected_sources)
    expected_set = set(expected_sources)
    matched_values = list(sample_map.values())
    missing = expected_set - set(matched_values)
    extra = set(matched_values) - expected_set
    duplicated = {v for v in matched_values if matched_values.count(v) > 1}

    if missing:
        errors.append("SAMPLE_MAPPING_MISSING")
    if extra:
        errors.append("SAMPLE_MAPPING_EXTRA")
    if duplicated:
        errors.append("SAMPLE_MAPPING_DUPLICATE")

    return _envelope(errors, warnings, primary_path, sample_map)


def completion_status(receipt: dict, validation: dict, has_artifacts: bool) -> str:
    """終了証跡と出力検証からConsole jobの終端状態を決める（spec §5.2）。

    completedの必要条件はtermination=exited かつ exit_code=0(int) かつ
    validation["ok"]。boolはintのサブクラスだが`type(rc) is int`で弾く
    （lipidmix.console.execution.validate_execution_recordと同じ厳密さ）。
    条件を満たさなくても有効な成果物が残っていればpartial、無ければfailed。
    """
    verified = (receipt["termination"] == "exited"
                and type(receipt["exit_code"]) is int
                and receipt["exit_code"] == 0 and validation["ok"])
    return "completed" if verified else ("partial" if has_artifacts else "failed")
