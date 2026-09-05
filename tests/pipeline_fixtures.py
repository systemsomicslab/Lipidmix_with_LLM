"""pipeline関連テストの合成fixture置き場。

実rawや既存成果物をfixtureにしない（CLAUDE.md方針）。ここは純粋なdictビルダーの集合で、
テストごとに独自の形を作らせないための唯一の正準とする。Task 3・6・9・13・19がここへ
helperを足していく前提なので、既存helperの必須フィールドは減らさない。
"""
from __future__ import annotations

from pathlib import Path


def execution_record(**overrides):
    """console-execution.v1 の妥当な最小レコードを返す。

    overridesで一部フィールドを壊し、validate_execution_recordの拒否条件を
    テストするために使う。
    """
    record = {
        "schema": "console-execution.v1", "execution_id": "exec-1", "job_id": "job-1",
        "started_at": "2026-09-05T00:00:00Z", "ended_at": "2026-09-05T00:00:01Z",
        "pid": 1234, "process_identity": {"pid": 1234, "creation_time": 100},
        "command_sha256": "a" * 64, "method_sha256": "b" * 64, "exe_sha256": "c" * 64,
        "exit_code": 0, "termination": "exited", "timeout_s": 21600,
        "collection": {"status": "succeeded"}, "validation": {"status": "pending"},
    }
    record.update(overrides)
    return record


# ---------- write_mztab ----------

# 少なくとも3特徴、識別済み(with_inchikey時)・InChIKeyを想定した合成値。
# 実データの脂質名文法に寄せる必要はない（構造検証のための合成値のため）。
_FEATURES = (
    ("PC 34:1", "IPCSVZSSVZVIGE-UHFFFAOYSA-N", 760.5851, 620.0),
    ("PE 36:2", "XKMRRTOUMJRJIA-UHFFFAOYSA-N", 742.5395, 590.0),
    ("TG 52:3", "DGGXCMYPQAOAJC-UHFFFAOYSA-N", 878.7789, 910.0),
)


def write_mztab(path: Path, sources: list[Path], *, with_inchikey: bool = True) -> Path:
    """実際のparse_mztab/validate_mztabに通る合成mzTab-M 2.0ファイルを書く。

    sources の各要素を ms_run[N] として file URI 化し、assay[N]-ms_run_ref で
    結ぶ（1 source = 1 assay）。tests/test_mztab_tools.py の SFH 構文（SFH ヘッダ
    行 + SMF データ行）を再利用し、少なくとも3特徴・SML・SMEを含む。文字列
    "x" のような偽物ではなく、実物の構造検証・定量行列抽出が通る内容にする。

    Parameters
    ----------
    with_inchikey:
        False なら database_identifier を全行 "null"（未識別）にする。
    """
    path = Path(path)
    n = len(sources)
    if n < 1:
        raise ValueError("sources には少なくとも1件のPathが必要です")

    lines = [
        "MTD\tmzTab-version\t2.0.0-M",
        "MTD\tmzTab-mode\tComplete",
        "MTD\tmzTab-type\tQuantification",
        "MTD\tsoftware[1]\t[MS, MS:1003082, MS-DIAL, Msdial console 5.5.241113]",
        "MTD\tquantification_method\t[MS, MS:1001829, label-free raw feature quantitation, ]",
        "MTD\tsmall_molecule-quantification_unit\t[PRIDE, PRIDE:0000429, Abundance, ]",
    ]
    for i, src in enumerate(sources, start=1):
        uri = Path(src).resolve().as_uri()
        lines.append(f"MTD\tms_run[{i}]-location\t{uri}")
        lines.append(f"MTD\tassay[{i}]-ms_run_ref\tms_run[{i}]")
        lines.append(f"MTD\tassay[{i}]\tS{i}")

    abundance_cols = "\t".join(f"abundance_assay[{i}]" for i in range(1, n + 1))
    lines.append(
        "SFH\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\t"
        f"exp_mass_to_charge\tretention_time_in_seconds\t{abundance_cols}"
    )
    for idx, (name, inchikey, mz, rt) in enumerate(_FEATURES, start=1):
        identifier = inchikey if with_inchikey else "null"
        abundances = "\t".join(f"{1000.0 * idx + 10.0 * j:.1f}" for j in range(1, n + 1))
        lines.append(
            f"SMF\t{idx}\tSML:{idx}\t{identifier}\t{name}\tnull\tnull\t{mz}\t{rt}\t{abundances}"
        )

    # アダクトは正極性1・負極性1・不明1件にして多数決を意図的に引き分け(None)に
    # する。極性を断定すると、write_mztab を負極性jobで使うテストと衝突する
    # （validate_outputsの極性多数決チェックが致命的不一致として弾いてしまう）。
    _sml_adducts = ("[M+H]1+", "[M-H]1-", "null")
    lines.append("SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tadduct_ions")
    for idx, adduct in enumerate(_sml_adducts[: len(_FEATURES)], start=1):
        lines.append(f"SML\t{idx}\t{idx}\tnull\t{adduct}")

    lines.append("SEH\tSME_ID\tSMF_ID_REFS\tdatabase_identifier")
    for idx in range(1, len(_FEATURES) + 1):
        lines.append(f"SME\t{idx}\tSMF:{idx}\tnull")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
