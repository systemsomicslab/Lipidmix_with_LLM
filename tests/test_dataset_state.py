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


# ---------- sample_names 解決（assay 表示名） ----------
# 実際の MS-DIAL 出力は MTD assay[N] の素の行に表示名を持つ
# （例: `MTD  assay[1]  20220901_RAW_control_0h_1_NEG`）。
# 以前はこれを拾えず sample_names が abundance_assay[N] のまま残っていた。

_MZTAB_WITH_NAMES = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tms_run[2]-location\tfile:///s2.raw
    MTD\tassay[1]\tzzz_last_NEG
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    MTD\tassay[2]\taaa_first_NEG
    MTD\tassay[2]-ms_run_ref\tms_run[2]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tabundance_assay[1]\tabundance_assay[2]
    SMF\t1\tSML:1\tnull\tPC 36:2\tnull\tnull\t100.0\t200.0
    SMF\t2\tSML:2\tnull\tTG 54:3\tnull\tnull\t50.0\t60.0
""")


@pytest.fixture
def mztab_named_file(tmp_path):
    p = tmp_path / "Height_named.mzTab"
    p.write_text(_MZTAB_WITH_NAMES, encoding="utf-8")
    return p


def test_dataset_state_resolves_bare_assay_display_name(mztab_named_file):
    """素の `assay[N]` 行の表示名が sample_names に反映される。"""
    pr = parse_mztab(mztab_named_file)
    ds = build_dataset_state(pr, mztab_named_file.name, str(mztab_named_file))
    assert ds.sample_names == ["zzz_last_NEG", "aaa_first_NEG"]


def test_dataset_state_display_name_reachable_from_assay_metadata(mztab_named_file):
    """表示名は assay_metadata からも参照できる（dataset_status 等の将来利用のため）。"""
    pr = parse_mztab(mztab_named_file)
    ds = build_dataset_state(pr, mztab_named_file.name, str(mztab_named_file))
    assert ds.assay_metadata["assay[1]"]["name"] == "zzz_last_NEG"
    assert ds.assay_metadata["assay[2]"]["name"] == "aaa_first_NEG"
    # ms_run_ref など既存の suffix 情報も引き続き読める
    assert ds.assay_metadata["assay[1]"]["ms_run_ref"] == "ms_run[1]"


def test_dataset_state_sample_names_aligned_with_feature_matrix_columns(mztab_named_file):
    """sample_names[i] は feature_matrix[:, i]（= abundance_assay[i+1] 列）と対応していること。

    assay 番号昇順のまま列は並ぶので、表示名のアルファベット順（aaa < zzz）とは
    一致しない列0=zzz_last_NEG を選んで、順序の取り違えが起きていないことを確認する。
    """
    pr = parse_mztab(mztab_named_file)
    ds = build_dataset_state(pr, mztab_named_file.name, str(mztab_named_file))
    assert ds.sample_names[0] == "zzz_last_NEG"
    assert ds.feature_matrix[:, 0].tolist() == [100.0, 50.0]
    assert ds.sample_names[1] == "aaa_first_NEG"
    assert ds.feature_matrix[:, 1].tolist() == [200.0, 60.0]


def test_dataset_state_sample_names_fallback_without_display_name(mztab_file):
    """表示名が無い assay（既存フィクスチャ全体がこれに該当）は列識別子のままにする。"""
    pr = parse_mztab(mztab_file)
    ds = build_dataset_state(pr, mztab_file.name, str(mztab_file))
    assert ds.sample_names == ["abundance_assay[1]"]


_MZTAB_DUPLICATE_NAMES = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tms_run[2]-location\tfile:///s2.raw
    MTD\tassay[1]\tdup_sample
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    MTD\tassay[2]\tdup_sample
    MTD\tassay[2]-ms_run_ref\tms_run[2]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tabundance_assay[1]\tabundance_assay[2]
    SMF\t1\tSML:1\tnull\tPC 36:2\tnull\tnull\t100.0\t200.0
""")


@pytest.fixture
def mztab_duplicate_names_file(tmp_path):
    p = tmp_path / "Height_dup.mzTab"
    p.write_text(_MZTAB_DUPLICATE_NAMES, encoding="utf-8")
    return p


def test_dataset_state_duplicate_display_name_warns_but_does_not_raise(mztab_duplicate_names_file):
    """不正ファイルで表示名が重複しても例外にはせず、warning に記録する。

    重複を検出できないと group_a/group_b の名前解決（sample_names.index 相当）が
    どちらのアッセイを指しているか曖昧になり、群選択が黙って誤る恐れがある。
    """
    pr = parse_mztab(mztab_duplicate_names_file)
    ds = build_dataset_state(pr, mztab_duplicate_names_file.name, str(mztab_duplicate_names_file))
    assert ds.sample_names == ["dup_sample", "dup_sample"]
    assert any("dup_sample" in w for w in ds.validation_result["warnings"])


# ---------- end-to-end: 表示名解決が QC/blank ロール検出を機能させる ----------
# detect_sample_roles はサンプル名のトークンで QC/blank を判定するため、
# sample_names が abundance_assay[N] のままだと QC-RSD フィルタ・ブランク除去が
# 常に不発になる（バグの結論2）。build_dataset_state → run_dataset_preprocess の
# 実経路を通して初めて「直った」と言える。

_MZTAB_WITH_QC_NAME = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tms_run[2]-location\tfile:///s2.raw
    MTD\tms_run[3]-location\tfile:///s3.raw
    MTD\tassay[1]\tcontrol_1
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    MTD\tassay[2]\ttreat_1
    MTD\tassay[2]-ms_run_ref\tms_run[2]
    MTD\tassay[3]\tQC_1
    MTD\tassay[3]-ms_run_ref\tms_run[3]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tabundance_assay[1]\tabundance_assay[2]\tabundance_assay[3]
    SMF\t1\tSML:1\tnull\tPC 36:2\tnull\tnull\t100.0\t200.0\t150.0
    SMF\t2\tSML:2\tnull\tTG 54:3\tnull\tnull\t50.0\t60.0\t55.0
""")


@pytest.fixture
def mztab_qc_named_file(tmp_path):
    p = tmp_path / "Height_qc.mzTab"
    p.write_text(_MZTAB_WITH_QC_NAME, encoding="utf-8")
    return p


def test_dataset_preprocess_detects_qc_role_via_resolved_display_name(mztab_qc_named_file):
    """dataset_load 相当（build_dataset_state）→ dataset_preprocess 相当（run_dataset_preprocess）
    を実際に通し、QC_1 という表示名を持つアッセイが role="qc" に分類されることを確認する。
    直る前は sample_names が abundance_assay[3] のままで、detect_sample_roles が
    "qc" トークンを見つけられず全サンプルが "sample" のままだった。
    """
    from lipidmix.analysis.dataset_analysis import run_dataset_preprocess

    pr = parse_mztab(mztab_qc_named_file)
    ds = build_dataset_state(pr, mztab_qc_named_file.name, str(mztab_qc_named_file))
    assert ds.sample_names == ["control_1", "treat_1", "QC_1"]

    _, _, _, roles, _, _ = run_dataset_preprocess(ds, {})

    assert roles["QC_1"] == "qc"
    assert roles["control_1"] == "sample"
    assert roles["treat_1"] == "sample"
