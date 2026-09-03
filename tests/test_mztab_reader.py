# tests/test_mztab_reader.py
import math
import textwrap
from pathlib import Path
import pytest
from lipidmix.mztab.reader import parse_mztab, extract_abundance_matrix, get_assay_count

# 最小 mzTab-M 2.0 fixture（2 assay × 3 feature）
_MINIMAL_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\ttitle\tTest
    MTD\tdescription\tTest dataset
    MTD\tms_run[1]-location\tfile:///data/s1.raw
    MTD\tms_run[2]-location\tfile:///data/s2.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    MTD\tassay[2]-ms_run_ref\tms_run[2]
    MTD\tstudy_variable[1]-assay_refs\tassay[1]
    MTD\tstudy_variable[1]-description\tcontrol
    MTD\tstudy_variable[2]-assay_refs\tassay[2]
    MTD\tstudy_variable[2]-description\ttreated
    MTD\tquantification_method\t[MS, MS:1001829, label-free raw feature quantitation, ]
    MTD\tsmall_molecule-quantification_unit\t[PRIDE, PRIDE:0000429, Abundance, ]
    SML\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tbest_id_confidence_value
    SML\t1\tSMF:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tPC 36:2\tCCC\tInChI=1S/test\t0.95
    SML\t2\tSMF:2\tnull\tTG 54:3\tnull\tnull\t0.70
    SML\t3\tSMF:3\tBADFORMAT\tPE 34:1\tnull\tnull\t0.60
    SMF\tSMF_ID\tSML_ID_REFS\tchemical_name\tsmiles\tinchi\tdatabase_identifier\tcharge\tmz_exp\trt_mean\tabundance_assay[1]\tabundance_assay[2]
    SMF\t1\tSML:1\tPC 36:2\tCCC\tInChI=1S/test\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\t1\t758.5684\t5.23\t12345.6\t23456.7
    SMF\t2\tSML:2\tTG 54:3\tnull\tnull\tnull\t1\t896.7923\t8.45\t45678.9\tnull
    SMF\t3\tSML:3\tPE 34:1\tnull\tnull\tBADFORMAT\t-1\t716.5245\t3.10\tnull\t56789.0
    SEH\tSME_ID\tSMF_ID_REFS\tdatabase_identifier\tchemical_formula\tsmiles\tinchi
    SME\t1\tSMF:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tC42H80NO8P\tCCC\tInChI=1S/test
    SME\t2\tSMF:2\tnull\tC57H104O6\tnull\tnull\t
""")

_SME_TRAILING_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///data/s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tabundance_assay[1]
    SMF\t1\tSML:1\tnull\t9999.0
    SEH\tSME_ID\tSMF_ID_REFS\tdatabase_identifier
    SME\t1\tSMF:1\tnull\t
""")


@pytest.fixture
def minimal_file(tmp_path):
    p = tmp_path / "test.mzTab"
    p.write_text(_MINIMAL_MZTAB, encoding="utf-8")
    return p


@pytest.fixture
def trailing_file(tmp_path):
    p = tmp_path / "trailing.mzTab"
    p.write_text(_SME_TRAILING_MZTAB, encoding="utf-8")
    return p


def test_parse_metadata(minimal_file):
    result = parse_mztab(minimal_file)
    assert result["metadata"]["mzTab-version"] == "2.0.0-M"
    assert result["metadata"]["assay[1]-ms_run_ref"] == "ms_run[1]"

def test_parse_smf_header(minimal_file):
    result = parse_mztab(minimal_file)
    header = result["sections"]["SMF"]["header"]
    assert "SMF_ID" in header
    assert "abundance_assay[1]" in header

def test_parse_smf_rows(minimal_file):
    result = parse_mztab(minimal_file)
    rows = result["sections"]["SMF"]["rows"]
    assert len(rows) == 3
    assert rows[0]["SMF_ID"] == "1"
    assert rows[0]["abundance_assay[1]"] == "12345.6"
    assert rows[1]["abundance_assay[2]"] is None  # "null" → None

def test_parse_sme_rows(minimal_file):
    result = parse_mztab(minimal_file)
    rows = result["sections"]["SME"]["rows"]
    assert len(rows) == 2

def test_sme_trailing_column_normalized(trailing_file):
    result = parse_mztab(trailing_file)
    sme_warnings = result["sections"]["SME"]["warnings"]
    assert any("trailing" in w.lower() for w in sme_warnings)

def test_null_string_becomes_none(minimal_file):
    result = parse_mztab(minimal_file)
    rows = result["sections"]["SML"]["rows"]
    assert rows[1]["database_identifier"] is None

def test_get_assay_count(minimal_file):
    result = parse_mztab(minimal_file)
    assert get_assay_count(result["metadata"]) == 2

def test_extract_abundance_matrix(minimal_file):
    result = parse_mztab(minimal_file)
    matrix, samples, features = extract_abundance_matrix(result)
    assert matrix.shape == (3, 2)
    assert matrix[0, 0] == pytest.approx(12345.6)
    assert math.isnan(matrix[1, 1])
    assert math.isnan(matrix[2, 0])
    assert features == ["1", "2", "3"]


def test_sme_trailing_warnings_are_aggregated_into_one(tmp_path):
    """末尾空列の警告は 1 セクション 1 件に集約する。

    旧実装は「除去した列 1 つにつき 1 件」を積んでいた。実データ（NEG, 714 特徴）
    では良性のこの警告だけで 484 件になり、dataset_load の要約が先頭 3 件しか
    出さないため、後から足される重要な警告（assay 表示名の重複など）が
    構造上ぜったいに見えなくなっていた。
    """
    content = textwrap.dedent("""\
        MTD\tmzTab-version\t2.0.0-M
        MTD\tmzTab-mode\tComplete
        MTD\tmzTab-type\tQuantification
        MTD\tms_run[1]-location\tfile:///data/s1.raw
        MTD\tassay[1]-ms_run_ref\tms_run[1]
        SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tabundance_assay[1]
        SMF\t1\tSML:1\tnull\t9999.0
        SEH\tSME_ID\tSMF_ID_REFS\tdatabase_identifier
        SME\t1\tSMF:1\tnull\t\t
        SME\t2\tSMF:1\tnull\t
        SME\t3\tSMF:1\tnull\t
    """)
    p = tmp_path / "many_trailing.mzTab"
    p.write_text(content, encoding="utf-8")

    warnings = parse_mztab(p)["sections"]["SME"]["warnings"]
    assert len(warnings) == 1
    assert "3" in warnings[0]          # 3 行分をまとめた件数が読める
    assert "trailing" in warnings[0].lower()
