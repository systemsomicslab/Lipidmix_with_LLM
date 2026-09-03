# tests/test_mztab_tools.py
import json
import textwrap
import pytest
from lipidmix.core import session_state
from lipidmix.core.mcp_errors import MISSING_STATE

_CONTENT = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tmzTab-mode\tComplete
    MTD\tmzTab-type\tQuantification
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    SMF\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\tabundance_assay[1]
    SMF\t1\tSML:1\tIPCSVZSSVZVIGE-UHFFFAOYSA-N\tPC 36:2\tnull\tnull\t12345.6
""")

_MINIMAL_MZTAB = (
    "MTD\tmzTab-version\t2.0.0-M\n"
    "MTD\tassay[1]-ms_run_ref\tms_run[1]\n"
    "MTD\tassay[1]\tS1\n"
    "SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]\n"
    "SMF\t1\tnull\t786.6\t300.0\t100.0\n"
)


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


def test_dataset_load_no_args_returns_bad_request():
    """引数不足は復旧可能な missing_state ではなく確定エラー。

    missing_state のまま required_tools=["dataset_load"] を返すと、契約どおりに
    動くクライアントが同じツールを再実行して無限ループする（自分自身が自分の
    復旧策になってしまう）。DATASET_BAD_REQUEST は「引数を直して呼び直す」
    種類のエラーであることを明示し、required_tools を持たない。
    """
    from lipidmix.tools.mztab_tools import dataset_load
    result = dataset_load()
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "DATASET_BAD_REQUEST"
    assert "required_tools" not in parsed["error"]


def test_dataset_load_both_args_returns_bad_request(mztab_file):
    """両方指定は「ファイルが無い」ではなく「引数の組み合わせが不正」。

    MZTAB_NOT_FOUND のままだと、そのコードを見て別パスで再試行するクライアントを
    誤誘導する（ファイルはちゃんと存在する）。
    """
    from lipidmix.tools.mztab_tools import dataset_load
    result = dataset_load(str(mztab_file), job_path="/fake/job.json")
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "DATASET_BAD_REQUEST"
    assert "required_tools" not in parsed["error"]


# ---------- dataset_status ----------

def test_dataset_status_no_state():
    from lipidmix.tools.mztab_tools import dataset_status
    result = dataset_status()
    parsed = json.loads(result)
    # Phase 3 で missing_state 契約に修正
    assert parsed["error"]["code"] == MISSING_STATE
    assert "required_tools" in parsed["error"]
    assert "dataset_load" in parsed["error"]["required_tools"]


def test_dataset_status_after_load(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load, dataset_status
    dataset_load(str(mztab_file))
    result = dataset_status()
    assert "mztab" in result.lower() or "source_format" in result


def test_dataset_load_does_not_touch_arf_slot(mztab_file):
    from lipidmix.tools.mztab_tools import dataset_load
    dataset_load(str(mztab_file))
    assert session_state.session.arf.features is None


# ---------- job_path 経路 ----------

def _make_job_json(tmp_path, mztab_rel_path: str, extra_artifacts=None,
                   entries=None) -> tuple:
    """analysis-job.json を tmp_path 内に作り、(job_path, run_dir) を返す。

    entries を渡すと primary_mztab_files をそのまま差し替える
    （複数候補・宣言と食い違う候補のケースを組むため）。
    """
    import json as _json
    from lipidmix.handoff.schema import SCHEMA_VERSION
    run_dir = tmp_path / "runs" / "job_test"
    run_dir.mkdir(parents=True, exist_ok=True)

    job_data = {
        "schema": SCHEMA_VERSION,
        "job_id": "job_test",
        "status": "completed",
        "created_at": "2026-09-02T10:00:00+09:00",
        "updated_at": "2026-09-02T10:45:00+09:00",
        "source": {"dataset_root": str(tmp_path), "input_count": 1},
        "software": {"name": "MS-DIAL", "version": "5.5", "execution_mode": "console", "method_file": "m.msdial"},
        "project": {"omics": "lipidomics", "polarity": "negative", "measure": "peak_height"},
        "run_dir": str(run_dir),
        "primary_mztab_files": entries if entries is not None else [
            {"path": mztab_rel_path, "polarity": "negative", "measure": "peak_height",
             "sha256": "abc", "validation": {}}
        ],
        "artifacts": extra_artifacts or [],
        "sample_manifest": None,
        "warnings": [],
        "error": None,
    }
    job_path = run_dir / "analysis-job.json"
    job_path.write_text(_json.dumps(job_data), encoding="utf-8")
    return job_path, run_dir


def test_dataset_load_via_job_path_success(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    mztab_dir = tmp_path / "runs" / "job_test" / "mztab"
    mztab_dir.mkdir(parents=True)
    mztab_file = mztab_dir / "neg-height.mzTab"
    mztab_file.write_text(_CONTENT, encoding="utf-8")

    job_path, _ = _make_job_json(tmp_path, "mztab/neg-height.mzTab")
    result = dataset_load(job_path=str(job_path))
    assert "dataset_load" in result or "neg-height" in result
    ds = session_state.session.dataset
    assert ds is not None
    assert ds.job_path == str(job_path)


def test_dataset_load_via_job_path_sets_source_format(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    mztab_dir = tmp_path / "runs" / "job_test" / "mztab"
    mztab_dir.mkdir(parents=True)
    (mztab_dir / "neg-height.mzTab").write_text(_CONTENT, encoding="utf-8")

    job_path, _ = _make_job_json(tmp_path, "mztab/neg-height.mzTab")
    dataset_load(job_path=str(job_path))
    assert session_state.session.dataset.source_format == "mztab"


def test_dataset_load_via_job_path_stores_artifacts(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    mztab_dir = tmp_path / "runs" / "job_test" / "mztab"
    mztab_dir.mkdir(parents=True)
    (mztab_dir / "neg-height.mzTab").write_text(_CONTENT, encoding="utf-8")
    (tmp_path / "runs" / "job_test" / "result.arf").write_bytes(b"\x00")

    artifacts = [{"path": "result.arf", "role": "peak_matrix_source", "format": "arf", "sha256": "def"}]
    job_path, _ = _make_job_json(tmp_path, "mztab/neg-height.mzTab", extra_artifacts=artifacts)
    dataset_load(job_path=str(job_path))
    ds = session_state.session.dataset
    assert "peak_matrix_source" in ds.artifact_paths
    assert len(ds.artifact_paths["peak_matrix_source"]) == 1


@pytest.mark.parametrize("primary_root", ["run_dir", "dataset_root"])
def test_dataset_load_resolves_recorded_artifact_roots(tmp_path, primary_root):
    """dataset_root 側の一次 mzTab と sidecar を記録された root から解決する。"""
    from lipidmix.handoff.schema import SCHEMA_VERSION
    from lipidmix.tools.mztab_tools import dataset_load

    run_dir = tmp_path / "runs" / "job1"
    (run_dir / "msdial").mkdir(parents=True)
    run_mztab = run_dir / "msdial" / "AlignResult-1.mzTab"
    dataset_mztab = tmp_path / "AlignResult-1.mzTab"
    (run_mztab if primary_root == "run_dir" else dataset_mztab).write_text(
        _MINIMAL_MZTAB, encoding="utf-8"
    )
    pai2 = tmp_path / "S1_1.pai2"
    pai2.write_bytes(b"x")

    job_path = run_dir / "analysis-job.json"
    job_path.write_text(json.dumps({
        "schema": SCHEMA_VERSION, "job_id": "j1", "status": "completed",
        "created_at": "t", "updated_at": "t",
        "source": {"dataset_root": str(tmp_path), "input_count": 1},
        "software": {"name": "MS-DIAL", "version": "", "execution_mode": "console",
                     "method_file": "m.txt"},
        "project": {"omics": "lipidomics", "polarity": "negative", "measure": "peak_height"},
        "run_dir": str(run_dir),
        "primary_mztab_files": [{
            "path": ("msdial/AlignResult-1.mzTab" if primary_root == "run_dir"
                     else "AlignResult-1.mzTab"),
            "polarity": "negative", "measure": "peak_height", "sha256": "",
            "validation": {}, "root": primary_root,
        }],
        "artifacts": [{"path": "S1_1.pai2", "role": "sample_peaks", "format": "pai2",
                       "sha256": "", "root": "dataset_root"}],
        "execution": {"save_project": True, "timeout_s": 60},
        "warnings": [], "error": None,
    }, ensure_ascii=False), encoding="utf-8")

    out = dataset_load(job_path=str(job_path))
    assert "dataset_load 完了" in out
    ds = session_state.session.dataset
    assert str((run_mztab if primary_root == "run_dir" else dataset_mztab).resolve()) in ds.source_files
    assert ds.artifact_paths["sample_peaks"] == [str(pai2.resolve())]


def test_dataset_load_via_job_path_no_mztab_files(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    import json as _json
    from lipidmix.handoff.schema import SCHEMA_VERSION
    run_dir = tmp_path / "runs" / "job_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    job_data = {
        "schema": SCHEMA_VERSION, "job_id": "empty", "status": "planned",
        "created_at": "2026-09-02T10:00:00+09:00", "updated_at": "2026-09-02T10:00:00+09:00",
        "source": {"dataset_root": str(tmp_path), "input_count": 0},
        "software": {"name": "MS-DIAL", "version": "5.5", "execution_mode": "console", "method_file": "m"},
        "project": {"omics": "lipidomics", "polarity": "positive", "measure": "peak_height"},
        "run_dir": str(run_dir),
        "primary_mztab_files": [],
        "artifacts": [], "sample_manifest": None, "warnings": [], "error": None,
    }
    job_p = run_dir / "analysis-job.json"
    job_p.write_text(_json.dumps(job_data), encoding="utf-8")

    result = dataset_load(job_path=str(job_p))
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_NOT_FOUND"


def test_dataset_load_via_job_path_missing_mztab_file(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    # job は valid だが mzTab ファイルが存在しない
    job_path, _ = _make_job_json(tmp_path, "mztab/nonexistent.mzTab")
    result = dataset_load(job_path=str(job_path))
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_NOT_FOUND"


def test_dataset_load_via_job_path_missing_job_file(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    result = dataset_load(job_path=str(tmp_path / "nonexistent" / "analysis-job.json"))
    parsed = json.loads(result)
    assert parsed["error"]["code"] == "MZTAB_NOT_FOUND"


def test_dataset_status_shows_job_path(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load, dataset_status
    mztab_dir = tmp_path / "runs" / "job_test" / "mztab"
    mztab_dir.mkdir(parents=True)
    (mztab_dir / "neg-height.mzTab").write_text(_CONTENT, encoding="utf-8")
    job_path, _ = _make_job_json(tmp_path, "mztab/neg-height.mzTab")
    dataset_load(job_path=str(job_path))

    status = json.loads(dataset_status())
    assert "job_path" in status


# ---------- 正準 mzTab の選択（job の宣言値で選ぶ） ----------
# 背景: 旧実装は primary_mztab_files[0] を無条件で採用していた。エントリは
# 相対パスの辞書順で並ぶため、実データ（Area_ / Height_ / NormalizedX_ が同居）
# では Area_ が [0] に来る。console_plan が peak_area_above_zero を
# UNSUPPORTED_AREA_CONSOLE で拒否しているのに、dataset_load はまさにその
# Area ファイルを黙って読んでいた。spec §7 は「暗黙の単数選択の廃止」を要求する。

def _entry(path, polarity="negative", measure="peak_height"):
    return {"path": path, "polarity": polarity, "measure": measure,
            "sha256": "x", "validation": {}}


def test_dataset_load_via_job_path_selects_entry_matching_declared_measure(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    mztab_dir = tmp_path / "runs" / "job_test" / "mztab"
    mztab_dir.mkdir(parents=True)
    (mztab_dir / "Area_Alignment.mzTab").write_text(_CONTENT, encoding="utf-8")
    (mztab_dir / "Height_Alignment.mzTab").write_text(_CONTENT, encoding="utf-8")

    job_path, _ = _make_job_json(tmp_path, "", entries=[
        _entry("mztab/Area_Alignment.mzTab", measure="peak_area_above_zero"),
        _entry("mztab/Height_Alignment.mzTab", measure="peak_height"),
    ])
    result = dataset_load(job_path=str(job_path))
    assert "Height_Alignment.mzTab" in result
    ds = session_state.session.dataset
    assert "Height_Alignment.mzTab" in next(iter(ds.source_files))


def test_dataset_load_via_job_path_ambiguous_candidates_stop(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    mztab_dir = tmp_path / "runs" / "job_test" / "mztab"
    mztab_dir.mkdir(parents=True)
    (mztab_dir / "Height_a.mzTab").write_text(_CONTENT, encoding="utf-8")
    (mztab_dir / "Height_b.mzTab").write_text(_CONTENT, encoding="utf-8")

    job_path, _ = _make_job_json(tmp_path, "", entries=[
        _entry("mztab/Height_a.mzTab"),
        _entry("mztab/Height_b.mzTab"),
    ])
    parsed = json.loads(dataset_load(job_path=str(job_path)))
    assert parsed["error"]["code"] == "AMBIGUOUS_PRIMARY_MZTAB"
    assert len(parsed["error"]["details"]["candidates"]) == 2


def test_dataset_load_via_job_path_no_entry_with_declared_measure(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    mztab_dir = tmp_path / "runs" / "job_test" / "mztab"
    mztab_dir.mkdir(parents=True)
    (mztab_dir / "Area_Alignment.mzTab").write_text(_CONTENT, encoding="utf-8")

    job_path, _ = _make_job_json(tmp_path, "", entries=[
        _entry("mztab/Area_Alignment.mzTab", measure="peak_area_above_zero"),
    ])
    parsed = json.loads(dataset_load(job_path=str(job_path)))
    assert parsed["error"]["code"] == "QUANTIFICATION_CONFLICT"


def test_dataset_load_via_job_path_no_entry_with_declared_polarity(tmp_path):
    from lipidmix.tools.mztab_tools import dataset_load
    mztab_dir = tmp_path / "runs" / "job_test" / "mztab"
    mztab_dir.mkdir(parents=True)
    (mztab_dir / "Height_Pos.mzTab").write_text(_CONTENT, encoding="utf-8")

    job_path, _ = _make_job_json(tmp_path, "", entries=[
        _entry("mztab/Height_Pos.mzTab", polarity="positive"),
    ])
    parsed = json.loads(dataset_load(job_path=str(job_path)))
    assert parsed["error"]["code"] == "POLARITY_MISMATCH"


def test_dataset_status_lists_sample_names_with_roles(mztab_file):
    """群指定に必要なサンプル名を dataset_status が返す。

    dataset_differential は正確なサンプル名のリストを要求するが、旧実装の
    dataset_status は n_samples（件数）しか返しておらず、名前を知る手段が
    「わざと群サイズ不足のエラーを起こして details を読む」しか無かった。
    """
    from lipidmix.tools.mztab_tools import dataset_load, dataset_status
    dataset_load(str(mztab_file))
    status = json.loads(dataset_status())
    lines = status["samples"].splitlines()
    assert lines[0] == "name\trole"
    assert lines[1:] == ["abundance_assay[1]\tsample"]


def test_dataset_status_sample_roles_reflect_qc_detection(tmp_path):
    """QC/ブランクは role 列で見分けられる（群に混ぜてはいけない試料）。"""
    from lipidmix.tools.mztab_tools import dataset_load, dataset_status
    content = _CONTENT.replace(
        "MTD\tassay[1]-ms_run_ref\tms_run[1]",
        "MTD\tassay[1]-ms_run_ref\tms_run[1]\nMTD\tassay[1]\t20260901_QC_1",
    )
    p = tmp_path / "Height_qc.mzTab"
    p.write_text(content, encoding="utf-8")
    dataset_load(str(p))
    status = json.loads(dataset_status())
    assert status["samples"].splitlines()[1:] == ["20260901_QC_1\tqc"]
