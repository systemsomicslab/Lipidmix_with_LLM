"""ライブラリのセッションスロットとパス解決。"""
from lipidmix.core import mcp_core, path_resolvers, session_state


def test_the_new_slot_does_not_disturb_the_others():
    s = session_state.AnalysisSession()
    assert s.library.store is None
    assert s.library.source_path is None
    assert s.library.last_match is None
    # 既存スロットが消えていないこと（無言の破棄の再発防止）。
    for name in ("arf", "arf2", "pai2", "eic"):
        assert getattr(s, name) is not None
    assert s.dataset is None


def test_a_dbs_is_preferred_over_an_msp(tmp_path, monkeypatch):
    (tmp_path / "other.msp").write_text("NAME: x\n", encoding="utf-8")
    (tmp_path / "P_Loaded.msp2.dbs").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(mcp_core, "DATA_DIR", tmp_path)   # 実物は pathlib.Path
    assert path_resolvers.resolve_library_path().endswith("P_Loaded.msp2.dbs")


def test_an_msp_is_used_when_no_dbs_exists(tmp_path, monkeypatch):
    (tmp_path / "lib.msp").write_text("NAME: x\n", encoding="utf-8")
    monkeypatch.setattr(mcp_core, "DATA_DIR", tmp_path)   # 実物は pathlib.Path
    assert path_resolvers.resolve_library_path().endswith("lib.msp")


def test_an_explicit_path_wins(tmp_path, monkeypatch):
    (tmp_path / "P_Loaded.msp2.dbs").write_bytes(b"PK\x03\x04")
    explicit = tmp_path / "explicit.msp"
    explicit.write_text("NAME: x\n", encoding="utf-8")
    monkeypatch.setattr(mcp_core, "DATA_DIR", tmp_path)   # 実物は pathlib.Path
    assert path_resolvers.resolve_library_path(str(explicit)) == str(explicit)


def test_nothing_found_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_core, "DATA_DIR", tmp_path)   # 実物は pathlib.Path
    assert path_resolvers.resolve_library_path() is None


def test_bare_msp2_and_lbm2_are_never_candidates(tmp_path, monkeypatch):
    # .msp2（ASCII .msp 指定時のみ実体が入り、脂質経路では0バイトのことがある）と
    # .lbm2（脂質専用 in-silico ライブラリ、本機能の入口としては出さない）は、
    # 他に何も無くても候補になってはいけない。
    (tmp_path / "Loaded.msp2").write_bytes(b"")
    (tmp_path / "Loaded.lbm2").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(mcp_core, "DATA_DIR", tmp_path)   # 実物は pathlib.Path
    assert path_resolvers.resolve_library_path() is None
