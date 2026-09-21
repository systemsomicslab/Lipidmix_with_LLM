"""`.msp` リーダ。別名表は上流 `MspFileParcer.cs` の switch を写している。"""
import textwrap

import pytest

from lipidmix.library import msp

_MSP = textwrap.dedent("""\
    NAME: GABA
    PRECURSORMZ: 104.0706
    PRECURSORTYPE: [M+H]+
    IONMODE: Positive
    FORMULA: C4H9NO2
    INCHIKEY: BTCSSZJGUNDROE-UHFFFAOYSA-N
    SMILES: NCCCC(=O)O
    RETENTIONTIME: 1.23
    Num Peaks: 2
    87.0441\t999
    69.0335\t500

    Name: Glutamate
    precursor_m/z: 148.0604
    precursor_type: [M+H]+
    ion_mode: Negative
    num_peaks: 1
    130.0499 800
""")


def test_both_records_are_read(tmp_path):
    path = tmp_path / "lib.msp"
    path.write_text(_MSP, encoding="utf-8")

    records = list(msp.iter_records(path))

    assert [r["name"] for r in records] == ["GABA", "Glutamate"]


def test_the_first_record_keeps_every_field(tmp_path):
    path = tmp_path / "lib.msp"
    path.write_text(_MSP, encoding="utf-8")

    r = list(msp.iter_records(path))[0]

    assert r["precursor_mz"] == pytest.approx(104.0706)
    assert r["adduct"] == "[M+H]+"
    assert r["ion_mode"] == "positive"
    assert r["formula"] == "C4H9NO2"
    assert r["inchikey"] == "BTCSSZJGUNDROE-UHFFFAOYSA-N"
    assert r["rt"] == pytest.approx(1.23)
    # brief 記載の生ファイル順（87.0441 → 69.0335）はそのままだと m/z 降順になる。
    # brief 本文・Step 3・セルフレビュー項目のいずれも「ピークは m/z 昇順に並べ替えて
    # から返す」ことを要求しており（Task 6 の走査が昇順前提）、この期待値はその要求と
    # 矛盾していた。要求どおり昇順に並べた期待値へ修正する。
    assert r["spectrum"] == [[69.0335, 500.0], [87.0441, 999.0]]


def test_the_field_name_dialects_are_accepted(tmp_path):
    """`precursor_m/z` `num_peaks` `ion_mode` も上流が受ける別名。"""
    path = tmp_path / "lib.msp"
    path.write_text(_MSP, encoding="utf-8")

    r = list(msp.iter_records(path))[1]

    assert r["precursor_mz"] == pytest.approx(148.0604)
    assert r["ion_mode"] == "negative"
    assert r["spectrum"] == [[130.0499, 800.0]]


def test_comment_lines_and_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "lib.msp"
    path.write_text("# a comment\n\nNAME: X\nPRECURSORMZ: 1.0\nNum Peaks: 0\n", encoding="utf-8")
    assert [r["name"] for r in msp.iter_records(path)] == ["X"]


def test_declared_peak_count_is_not_trusted_when_fewer_peaks_are_present(tmp_path):
    """宣言本数 5 だが実際は 2 行しかない。宣言値を信用せず 2 本だけ返す。"""
    path = tmp_path / "lib.msp"
    path.write_text(
        "NAME: Y\nPRECURSORMZ: 2.0\nNum Peaks: 5\n60.0 100\n50.0 200\n",
        encoding="utf-8",
    )
    r = list(msp.iter_records(path))[0]
    assert r["spectrum"] == [[50.0, 200.0], [60.0, 100.0]]
