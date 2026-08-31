# tests/test_mztab_tools.py
import json
import textwrap
import pytest
from lipidmix.core import session_state

_CONTENT = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tabundance_assay[1]
    SMF\t1\tSML:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tPC 36:2\tnull\tnull\t12345.6
""")


@pytest.fixture(autouse=True)
def reset_session():
    session_state.session = session_state.AnalysisSession()
    yield
    session_state.session = session_state.AnalysisSession()


@pytest.fixture
def mztab_file(tmp_path):
    p = tmp_path / "Height_test.mzTab"
    p.write_text(_CONTENT, encoding="utf-8")
    return p


def test_dataset_load_success(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load
    result = dataset_load(str(mztab_file))
    assert "dataset_load" in result or "mzTab" in result
    assert session_state.session.dataset is not None


def test_dataset_load_sets_source_format(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load
    dataset_load(str(mztab_file))
    assert session_state.session.dataset.source_format == "mztab"


def test_dataset_load_missing_file():
    from lipidmix.tools.mztab_tools import dataset_load
    result = dataset_load("/nonexistent/path.mzTab")
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_NOT_FOUND"


def test_dataset_load_invalid_structure(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    bad = tmp_path / "bad.mzTab"
    bad.write_text("MTD\tmzTab-version\t3.0.0-M\n", encoding="utf-8")
    result = dataset_load(str(bad))
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_STRUCTURE_INVALID"


def test_dataset_status_no_state():
    from lipidmix.tools.mztab_tools import dataset_status
    result = dataset_status()
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_NOT_FOUND"


def test_dataset_status_after_load(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load, dataset_status
    dataset_load(str(mztab_file))
    result = dataset_status()
    # status は JSON 文字列または markdown 文字列
    assert "mztab" in result.lower() or "source_format" in result


def test_dataset_load_does_not_touch_arf_slot(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load
    dataset_load(str(mztab_file))
    assert session_state.session.arf.features is None
