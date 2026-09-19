"""参照ライブラリの永続 store。"""
import textwrap

import pytest

from lipidmix.library import store

_MSP = textwrap.dedent("""\
    NAME: A
    PRECURSORMZ: 100.0
    IONMODE: Positive
    Num Peaks: 2
    50.0 999
    80.0 500

    NAME: B
    PRECURSORMZ: 100.005
    IONMODE: Positive
    Num Peaks: 1
    50.0 999

    NAME: C
    PRECURSORMZ: 100.0
    IONMODE: Negative
    Num Peaks: 1
    50.0 999

    NAME: D
    PRECURSORMZ: 200.0
    IONMODE: Positive
    Num Peaks: 1
    50.0 999
""")


@pytest.fixture()
def library(tmp_path, monkeypatch):
    monkeypatch.setenv(store.LIBRARY_CACHE_ENV, str(tmp_path / "cache"))
    path = tmp_path / "lib.msp"
    path.write_text(_MSP, encoding="utf-8")
    return path


def test_the_store_is_built_once_and_reopened(library):
    s1 = store.open_store(library)
    built_at = store.store_path_for(library).stat().st_mtime_ns
    assert s1.record_count == 4
    s1.close()

    s2 = store.open_store(library)
    assert store.store_path_for(library).stat().st_mtime_ns == built_at  # 作り直していない
    assert s2.record_count == 4
    s2.close()


def test_a_changed_source_gets_a_different_store(library, tmp_path):
    first = store.store_path_for(library)
    library.write_text(_MSP + "\nNAME: E\nPRECURSORMZ: 300.0\nNum Peaks: 0\n", encoding="utf-8")
    assert store.store_path_for(library) != first


def test_candidates_are_filtered_by_mz_window_and_polarity(library):
    s = store.open_store(library)
    try:
        hits = s.candidates(100.0, mz_tol=0.01, ion_mode="positive")
        assert sorted(r["name"] for r in hits) == ["A", "B"]   # C は極性違い、D は窓の外
        assert hits[0]["spectrum"] == [[50.0, 999.0], [80.0, 500.0]]
    finally:
        s.close()


def test_omitting_the_ion_mode_keeps_both_polarities(library):
    s = store.open_store(library)
    try:
        assert sorted(r["name"] for r in s.candidates(100.0, mz_tol=0.01)) == ["A", "B", "C"]
    finally:
        s.close()


def test_the_summary_reports_what_was_loaded(library):
    s = store.open_store(library)
    try:
        summary = s.summary()
        assert summary["record_count"] == 4
        assert summary["ion_modes"] == {"positive": 3, "negative": 1}
        assert summary["source_sha256"][:2].isalnum()
        assert summary["search_params"] is None       # .msp には同梱されない
    finally:
        s.close()
