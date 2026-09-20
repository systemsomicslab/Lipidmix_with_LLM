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


def test_library_id_and_record_index_round_trip(library):
    """record.RECORD_FIELDS は 13 フィールドが「採点エンジンの入力契約」（spec）。
    library_id / record_index を落とすと、どの元レコードが根拠かを辿れなくなる。"""
    s = store.open_store(library)
    try:
        hits = {r["name"]: r for r in s.candidates(100.0, mz_tol=0.01)}
        assert sorted(hits) == ["A", "B", "C"]
        # .msp は library_id を持たない（単一ファイル内なので区別が要らない）
        assert all(r["library_id"] is None for r in hits.values())
        # record_index は .msp 内の出現順の通し番号（A, B, C, D の順）
        assert hits["A"]["record_index"] == 0
        assert hits["B"]["record_index"] == 1
        assert hits["C"]["record_index"] == 2
    finally:
        s.close()


_MSP_WITH_RT = textwrap.dedent("""\
    NAME: RT_KNOWN
    PRECURSORMZ: 150.0
    IONMODE: Positive
    RETENTIONTIME: 5.0
    Num Peaks: 1
    50.0 999

    NAME: RT_UNKNOWN
    PRECURSORMZ: 150.0
    IONMODE: Positive
    Num Peaks: 1
    50.0 999

    NAME: RT_FAR
    PRECURSORMZ: 150.0
    IONMODE: Positive
    RETENTIONTIME: 50.0
    Num Peaks: 1
    50.0 999
""")


_MSP_CLASSES = textwrap.dedent("""\
    NAME: A1
    PRECURSORMZ: 100.0
    IONMODE: Positive
    COMPOUNDCLASS: LPE
    Num Peaks: 1
    50.0 999

    NAME: A2
    PRECURSORMZ: 100.0
    IONMODE: Positive
    COMPOUNDCLASS: LPE
    Num Peaks: 1
    50.0 999

    NAME: B1
    PRECURSORMZ: 100.0
    IONMODE: Positive
    COMPOUNDCLASS: PC
    Num Peaks: 1
    50.0 999

    NAME: C1
    PRECURSORMZ: 100.0
    IONMODE: Positive
    Num Peaks: 1
    50.0 999
""")


def test_compound_class_counts_reports_top_classes_descending(tmp_path, monkeypatch):
    """レコードに compound_class 未設定（None）が混ざっても集計から除外される。"""
    monkeypatch.setenv(store.LIBRARY_CACHE_ENV, str(tmp_path / "cache"))
    path = tmp_path / "classes.msp"
    path.write_text(_MSP_CLASSES, encoding="utf-8")

    s = store.open_store(path)
    try:
        assert s.compound_class_counts(top_n=10) == [
            {"name": "LPE", "count": 2},
            {"name": "PC", "count": 1},
        ]
        assert s.compound_class_counts(top_n=1) == [{"name": "LPE", "count": 2}]
    finally:
        s.close()


def test_compound_class_counts_does_not_rescan_the_source_on_cache_hit(tmp_path, monkeypatch):
    """`library_load` を 2 回呼んでも、2 回目はキャッシュを開くだけで元ファイルを
    再走査してはいけない（SQLite store を作った目的そのものが壊れるため）。"""
    monkeypatch.setenv(store.LIBRARY_CACHE_ENV, str(tmp_path / "cache"))
    path = tmp_path / "classes.msp"
    path.write_text(_MSP_CLASSES, encoding="utf-8")

    s1 = store.open_store(path)
    s1.close()

    def _boom(*_args, **_kwargs):
        raise AssertionError("iter_records が呼ばれた（cache hit のはずが元ファイルを再走査した）")

    monkeypatch.setattr(store.msp_reader, "iter_records", _boom)
    monkeypatch.setattr(store.dbs_reader, "iter_records", _boom)

    s2 = store.open_store(path)  # rebuild=False: キャッシュを開くだけのはず
    try:
        assert s2.compound_class_counts(top_n=10) == [
            {"name": "LPE", "count": 2},
            {"name": "PC", "count": 1},
        ]
    finally:
        s2.close()


def test_rt_filter_keeps_null_rt_but_drops_rt_outside_tolerance(tmp_path, monkeypatch):
    """brief 名指しの要件: `.msp` の RT は別の LC 条件で測られていることがあるので、
    RT を持たないレコードを rt 指定クエリで落としてはいけない
    （`AND (rt IS NULL OR ABS(rt - ?) <= ?)`）。RT が窓の外のレコードは落ちることも
    同じテストで押さえ、`rt IS NULL` だけが特別扱いされることを示す。"""
    monkeypatch.setenv(store.LIBRARY_CACHE_ENV, str(tmp_path / "cache"))
    path = tmp_path / "rt_lib.msp"
    path.write_text(_MSP_WITH_RT, encoding="utf-8")

    s = store.open_store(path)
    try:
        hits = s.candidates(150.0, mz_tol=0.01, rt=5.0, rt_tol=0.5)
        assert sorted(r["name"] for r in hits) == ["RT_KNOWN", "RT_UNKNOWN"]  # RT_FAR は窓の外
    finally:
        s.close()
