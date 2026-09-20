"""MCP ツール。戻り値の量と missing_state 契約を固定する。"""
import json
import textwrap

import pytest

from lipidmix.core import mcp_core, session_state
from lipidmix.library import tools

_MSP = textwrap.dedent("""\
    NAME: GABA
    PRECURSORMZ: 104.0706
    IONMODE: Positive
    Num Peaks: 2
    87.0441 999
    69.0335 500
""")


@pytest.fixture(autouse=True)
def fresh_session(tmp_path, monkeypatch):
    monkeypatch.setattr(session_state, "session", session_state.AnalysisSession())
    monkeypatch.setattr(mcp_core, "DATA_DIR", tmp_path)   # 実物は pathlib.Path
    monkeypatch.setenv("LIPIDMIX_LIBRARY_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "lib.msp").write_text(_MSP, encoding="utf-8")
    return tmp_path


def test_matching_without_a_loaded_library_returns_missing_state():
    payload = json.loads(tools.library_match_feature(104.07))
    assert payload["error"]["code"] == "missing_state"
    assert "library_load" in payload["error"]["required_tools"]


def test_plotting_without_a_match_returns_missing_state():
    payload = json.loads(tools.library_plot_mirror())
    assert payload["error"]["code"] == "missing_state"
    assert "library_match_feature" in payload["error"]["required_tools"]


def test_loading_reports_what_was_loaded(fresh_session):
    text = tools.library_load()
    assert "lib.msp" in text
    assert session_state.session.library.store is not None


def test_the_payload_carries_no_coordinate_arrays(fresh_session, monkeypatch):
    """座標はセッションに持つ。戻り値に点列を載せない（文脈を食うため）。"""
    tools.library_load()
    monkeypatch.setattr(tools, "_measured_spectrum",
                        lambda *a, **k: [[87.0441, 999.0], [69.0335, 500.0]])
    text = tools.library_match_feature(104.0706, ion_mode="positive")

    # 測定側の点列が戻り値に出ていないこと。座標はセッションにだけ持つ。
    assert "69.0335" not in text
    assert "87.0441" not in text
    # 一方でセッションには完全な座標がある（図保存ツールがここから読む）。
    # store のレコードは `.msp` 由来でも **m/z 昇順**（Task 4）。
    stored = session_state.session.library.last_match["candidates"][0]["spectrum"]
    assert stored == [[69.0335, 500.0], [87.0441, 999.0]]


def test_loading_does_not_disturb_the_other_slots(fresh_session):
    before = session_state.session.arf
    tools.library_load()
    assert session_state.session.arf is before


def test_missing_dcl_reports_not_found_without_confusing_it_for_no_match(fresh_session):
    """`.dcl` に MS/MS が無いときは `not_found`。「合わなかった」と読めない文言にする。"""
    tools.library_load()
    payload = json.loads(tools.library_match_feature(104.0706, ion_mode="positive"))
    assert payload["status"] == "not_found"
    assert "未取得" in payload["message"]


def test_mirror_plot_passes_ms2_tol_so_measured_side_gets_colored(fresh_session, monkeypatch):
    """Task 8 で直したバグの再発防止: ms2_tol を渡し忘れると測定側の色分けが消える。"""
    tools.library_load()
    monkeypatch.setattr(tools, "_measured_spectrum",
                        lambda *a, **k: [[87.0441, 999.0], [69.0335, 500.0]])
    tools.library_match_feature(104.0706, ion_mode="positive")

    captured = {}
    from lipidmix.plots import mirror as mirror_plot
    real_build = mirror_plot.build_mirror_payload

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(mirror_plot, "build_mirror_payload", spy)
    tools.library_plot_mirror(output="payload")
    assert captured.get("ms2_tol") is not None
