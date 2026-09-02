# tests/test_dataset_state.py
import math
import textwrap
import pytest
from lipidmix.mztab.reader import parse_mztab
from lipidmix.mztab.dataset_state import DatasetState, build_dataset_state
from lipidmix.core import session_state

_MZTAB_CONTENT = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tabundance_assay[1]
    SMF\t1\tSML:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tPC 36:2\tCCC\tInChI=1S/test\t12345.6
    SMF\t2\tSML:2\tnull\tTG 54:3\tnull\tnull\t0.0
""")


@pytest.fixture
def mztab_file(tmp_path):
    p = tmp_path / "Height_test.mzTab"
    p.write_text(_MZTAB_CONTENT, encoding="utf-8")
    return p


def test_build_dataset_state_creates_instance(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert isinstance(ds, DatasetState)


def test_dataset_state_source_format(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert ds.source_format == "mztab"


def test_dataset_state_feature_matrix_shape(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert ds.feature_matrix.shape == (2, 1)
    assert ds.feature_matrix[0, 0] == pytest.approx(12345.6)


def test_dataset_state_inchikey_from_database_identifier(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    f1 = ds.feature_metadata["1"]
    assert f1["inchikey"] == "IPCSVZSSVZVIGE-UHFFFAOYSA-N"
    assert f1["inchikey_source"] == "database_identifier"


def test_dataset_state_inchikey_none_when_absent(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    f2 = ds.feature_metadata["2"]
    assert f2["inchikey"] is None
    assert f2["inchikey_source"] == "none"


def test_dataset_state_inchikey_coverage(mztab_file):
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    cov = ds.inchikey_coverage
    assert cov["total_features"] == 2
    assert cov["with_inchikey"] == 1
    assert cov["by_source"]["database_identifier"] == 1
    assert cov["by_source"]["none"] == 1


def test_session_has_dataset_slot():
    sess = session_state.AnalysisSession()
    assert hasattr(sess, "dataset")
    assert sess.dataset is None


def test_session_dataset_does_not_affect_arf_slot():
    sess = session_state.AnalysisSession()
    sess.dataset = "dummy"
    # ARF スロットはそのまま
    assert sess.arf.features is None


def test_dataset_state_has_analysis_fields():
    from lipidmix.mztab.dataset_state import DatasetState
    ds = DatasetState()
    assert ds.pp_matrix is None
    assert ds.pp_sample_names == []
    assert ds.pp_feature_names == []
    assert ds.roles == {}
    assert ds.sample_meta == {}
    assert ds.preprocessing_recipe == {}
    assert ds.last_pca is None
    assert ds.last_differential is None
