"""MS-DIAL Console のメソッドファイル解決（LBM 自動解決・既存パラメータ探索）。

MS-DIAL GUI は脂質ライブラリ（`*.lbm2`）をアプリフォルダから自動で拾い、
ユーザーには選ばせない（`MethodSettingModelFactory.cs` /
`DataBaseSettingModel.TrySetLbmLibrary`）。一方 Console はメソッドファイルの
`Lbm file path:` しか見ない（`CommonProcess.cs`）。そして GUI が実行のたびに
自動保存するパラメータ（`MethodModelBase.AutoParametersSave`）は、GUI が
`param.LbmFilePath` に一度も代入しないため**必ず空**で出る。

ここはその段差を埋める層のテスト。GUI と同じ規則（アプリフォルダにちょうど 1 件）
を再現し、GUI が拒否する状況でだけ拒否する。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lipidmix.console.method_file import (
    LbmResolution,
    find_lbm_files,
    find_method_candidates,
    read_method_keys,
    resolve_lbm,
    write_effective_method_file,
)


# ---------- read_method_keys ----------

def test_read_method_keys_parses_colon_pairs(tmp_path):
    p = tmp_path / "param.txt"
    p.write_text("Ion mode: Positive\nTarget omics: Lipidomics\n", encoding="ascii")
    keys = read_method_keys(p)
    assert keys["ion mode"] == "Positive"
    assert keys["target omics"] == "Lipidomics"


def test_read_method_keys_skips_comments_and_blanks(tmp_path):
    p = tmp_path / "param.txt"
    p.write_text("# header\n\nIon mode: Negative\n", encoding="ascii")
    assert read_method_keys(p) == {"ion mode": "Negative"}


def test_read_method_keys_splits_on_first_delimiter(tmp_path):
    """ConfigParser は最初の ':' か '=' のうち先に来るほうで割る。"""
    p = tmp_path / "param.txt"
    p.write_text("File ID=0: 1\n", encoding="ascii")
    assert read_method_keys(p) == {"file id": "0: 1"}


def test_read_method_keys_keeps_empty_value(tmp_path):
    """`Lbm file path:` が空で存在する、が本件の中心なので落としてはいけない。"""
    p = tmp_path / "param.txt"
    p.write_text("Lbm file path: \nIon mode: Positive\n", encoding="ascii")
    keys = read_method_keys(p)
    assert "lbm file path" in keys
    assert keys["lbm file path"] == ""


def test_read_method_keys_preserves_windows_path_value(tmp_path):
    """値側の ':' で割ってはいけない（C:\\... が壊れる）。"""
    p = tmp_path / "param.txt"
    p.write_text("Lbm file path: C:\\lib\\x.lbm2\n", encoding="ascii")
    assert read_method_keys(p)["lbm file path"] == "C:\\lib\\x.lbm2"


# ---------- find_lbm_files ----------

def test_find_lbm_files_matches_lbm_and_lbm2(tmp_path):
    (tmp_path / "a.lbm").touch()
    (tmp_path / "b.lbm2").touch()
    (tmp_path / "c.txt").touch()
    (tmp_path / "d.lbmx").touch()
    found = {p.name for p in find_lbm_files(tmp_path)}
    assert found == {"a.lbm", "b.lbm2"}


def test_find_lbm_files_is_top_directory_only(tmp_path):
    """GUI は SearchOption.TopDirectoryOnly。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.lbm2").touch()
    assert find_lbm_files(tmp_path) == []


def test_find_lbm_files_missing_directory_is_empty(tmp_path):
    assert find_lbm_files(tmp_path / "nope") == []


# ---------- resolve_lbm ----------

def _exe_dir_with(tmp_path, *names):
    d = tmp_path / "msdial"
    d.mkdir()
    for n in names:
        (d / n).touch()
    return d / "MSDIALCUI.exe"


def test_resolve_lbm_prefers_explicit_method_file_value(tmp_path):
    lib = tmp_path / "explicit.lbm2"
    lib.touch()
    exe = _exe_dir_with(tmp_path, "other.lbm2")
    res = resolve_lbm({"lbm file path": str(lib)}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert res.source == "method_file"
    assert Path(res.path) == lib
    assert res.error_code is None


def test_resolve_lbm_resolves_relative_value_against_method_file(tmp_path):
    """MS-DIAL の ResolvePathFromMethodFile と同じ基準。"""
    lib = tmp_path / "lib.lbm2"
    lib.touch()
    exe = _exe_dir_with(tmp_path, "other.lbm2")
    res = resolve_lbm({"lbm file path": "lib.lbm2"}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert Path(res.path) == lib
    assert res.source == "method_file"


def test_resolve_lbm_errors_when_declared_path_is_missing(tmp_path):
    exe = _exe_dir_with(tmp_path, "other.lbm2")
    res = resolve_lbm({"lbm file path": str(tmp_path / "gone.lbm2")}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert res.error_code == "LBM_NOT_FOUND"
    assert res.path is None


def test_resolve_lbm_falls_back_to_env(tmp_path):
    lib = tmp_path / "env.lbm2"
    lib.touch()
    exe = _exe_dir_with(tmp_path)
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe),
                      env={"MSDIAL_LBM": str(lib)})
    assert res.source == "env"
    assert Path(res.path) == lib


def test_resolve_lbm_errors_when_env_path_is_missing(tmp_path):
    exe = _exe_dir_with(tmp_path, "one.lbm2")
    res = resolve_lbm({}, tmp_path / "param.txt", omics="lipidomics",
                      exe_path=str(exe), env={"MSDIAL_LBM": str(tmp_path / "gone.lbm2")})
    assert res.error_code == "LBM_NOT_FOUND"


def test_resolve_lbm_uses_exe_directory_when_exactly_one(tmp_path):
    """GUI が `Assembly.GetExecutingAssembly().Location` の隣を見るのと同じ。"""
    exe = _exe_dir_with(tmp_path, "only.lbm2")
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert res.source == "exe_dir"
    assert Path(res.path).name == "only.lbm2"


def test_resolve_lbm_errors_when_exe_directory_has_none(tmp_path):
    exe = _exe_dir_with(tmp_path)
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert res.error_code == "LBM_NOT_FOUND"


def test_resolve_lbm_errors_when_exe_directory_is_ambiguous(tmp_path):
    """GUI も 1 件でなければ MessageBox で止める（DatasetParameterSettingModel）。"""
    exe = _exe_dir_with(tmp_path, "a.lbm2", "b.lbm2")
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert res.error_code == "LBM_AMBIGUOUS"
    assert len(res.candidates) == 2


def test_resolve_lbm_not_required_for_metabolomics(tmp_path):
    exe = _exe_dir_with(tmp_path)
    res = resolve_lbm({}, tmp_path / "param.txt", omics="metabolomics",
                      exe_path=str(exe), env={})
    assert res.error_code is None
    assert res.source == "not_required"
    assert res.path is None


# ---------- find_method_candidates ----------

def _param(dir_: Path, name: str, ion_mode: str, *, lbm: str = "", omics: str = "Lipidomics"):
    p = dir_ / name
    p.write_text(
        f"Ion mode: {ion_mode}\nTarget omics: {omics}\nLbm file path: {lbm}\n",
        encoding="ascii")
    return p


def test_find_method_candidates_finds_auto_saved_param_files(tmp_path):
    """GUI は実行のたびに `<project>_param_<endtimestamp>.txt` を自動保存する。"""
    _param(tmp_path, "Dataset_2026_05_15_param_202605151055.txt", "Negative")
    (tmp_path / "unrelated.txt").write_text("hello\n", encoding="ascii")
    found = find_method_candidates([tmp_path])
    assert [Path(c.path).name for c in found] == ["Dataset_2026_05_15_param_202605151055.txt"]
    assert found[0].ion_mode == "negative"


def test_find_method_candidates_filters_by_polarity(tmp_path):
    _param(tmp_path, "a_param_1.txt", "Negative")
    _param(tmp_path, "b_param_2.txt", "Positive")
    found = find_method_candidates([tmp_path], polarity="positive")
    assert [Path(c.path).name for c in found] == ["b_param_2.txt"]


def test_find_method_candidates_sorts_newest_first(tmp_path):
    old = _param(tmp_path, "old_param_1.txt", "Positive")
    new = _param(tmp_path, "new_param_2.txt", "Positive")
    import os
    os.utime(old, (time.time() - 5000, time.time() - 5000))
    found = find_method_candidates([tmp_path], polarity="positive")
    assert [Path(c.path).name for c in found] == [new.name, old.name]


def test_find_method_candidates_reports_missing_lbm(tmp_path):
    _param(tmp_path, "a_param_1.txt", "Positive", lbm="")
    _param(tmp_path, "b_param_2.txt", "Positive", lbm="C:\\x.lbm2")
    by_name = {Path(c.path).name: c for c in find_method_candidates([tmp_path])}
    assert by_name["a_param_1.txt"].has_lbm is False
    assert by_name["b_param_2.txt"].has_lbm is True


def test_find_method_candidates_deduplicates_overlapping_dirs(tmp_path):
    _param(tmp_path, "a_param_1.txt", "Positive")
    found = find_method_candidates([tmp_path, tmp_path])
    assert len(found) == 1


def test_find_method_candidates_skips_unreadable_binary(tmp_path):
    p = tmp_path / "bad_param_1.txt"
    p.write_bytes(b"\x00\x01\x02binary")
    assert find_method_candidates([tmp_path]) == []


# ---------- write_effective_method_file ----------

def test_write_effective_method_file_replaces_existing_key(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("Ion mode: Positive\nLbm file path: \nSolvent type: CH3COONH4\n",
                   encoding="ascii")
    dest = tmp_path / "out" / "effective.txt"
    write_effective_method_file(src, dest, {"Lbm file path": "C:\\lib\\x.lbm2"})
    lines = dest.read_text(encoding="ascii").splitlines()
    assert lines == ["Ion mode: Positive",
                     "Lbm file path: C:\\lib\\x.lbm2",
                     "Solvent type: CH3COONH4"]


def test_write_effective_method_file_appends_absent_key(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("Ion mode: Positive\n", encoding="ascii")
    dest = tmp_path / "effective.txt"
    write_effective_method_file(src, dest, {"Lbm file path": "C:\\lib\\x.lbm2"})
    assert "Lbm file path: C:\\lib\\x.lbm2" in dest.read_text(encoding="ascii").splitlines()


def test_write_effective_method_file_is_ascii_with_lf(tmp_path):
    """ConfigParser は StreamReader(path, Encoding.ASCII) で 1 行ずつ読む。"""
    src = tmp_path / "src.txt"
    src.write_text("Ion mode: Positive\n", encoding="ascii")
    dest = tmp_path / "effective.txt"
    write_effective_method_file(src, dest, {"Lbm file path": "x.lbm2"})
    raw = dest.read_bytes()
    assert b"\r\n" not in raw
    raw.decode("ascii")  # 非 ASCII が混ざっていないこと


def test_write_effective_method_file_leaves_source_untouched(tmp_path):
    src = tmp_path / "src.txt"
    original = "Ion mode: Positive\nLbm file path: \n"
    src.write_text(original, encoding="ascii")
    write_effective_method_file(src, tmp_path / "effective.txt",
                               {"Lbm file path": "x.lbm2"})
    assert src.read_text(encoding="ascii") == original


# ---------- resolve_lbm: ビルドツリー探索 ----------
#
# この環境の Console 実行体は MsdialWorkbench のビルド生成物
# （tests/MSDIAL5/MsdialCoreTestApp/bin/Debug/net48/MSDIALCUI.exe）で、
# その exe フォルダに .lbm2 は無い。ライブラリは GUI アプリ側のビルド出力
# （src/MSDIAL5/MsdialGuiApp/bin/Debug/**/*.lbm2）にあるため、exe フォルダ
# 探索だけでは原理的に当たらない。インストール版（MSDIAL_LBM が指す）より
# ビルド生成物を優先する。

def _build_tree(tmp_path, *, msdial5_tfms=("", "net48", "net481"),
                msdial4_tfms=("net48",), exe_tfm="net48"):
    """MsdialWorkbench のビルド生成物を模したツリーを作り、Console exe のパスを返す。"""
    repo = tmp_path / "MsdialWorkbench"
    for tfm in msdial5_tfms:
        d = repo / "src/MSDIAL5/MsdialGuiApp/bin/Debug"
        if tfm:
            d = d / tfm
        d.mkdir(parents=True, exist_ok=True)
        (d / "Msp20260116160945_NCDK_conventional_converted_dev.lbm2").touch()
    for tfm in msdial4_tfms:
        d = repo / "src/MSDIAL4/MsDial/bin/Debug" / tfm
        d.mkdir(parents=True, exist_ok=True)
        (d / "Msp20221205132019_conventional_converted.lbm2").touch()
    exe_dir = repo / "tests/MSDIAL5/MsdialCoreTestApp/bin/Debug" / exe_tfm
    exe_dir.mkdir(parents=True, exist_ok=True)
    exe = exe_dir / "MSDIALCUI.exe"
    exe.touch()
    return repo, exe


def test_resolve_lbm_prefers_the_build_tree_over_the_installed_env_path(tmp_path):
    repo, exe = _build_tree(tmp_path)
    installed = tmp_path / "MSDIAL.v5.5-net48" / "Msp20251120132005_NCDK_dev.lbm2"
    installed.parent.mkdir(parents=True)
    installed.touch()
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe),
                      env={"MSDIAL_LBM": str(installed)})
    assert res.source == "build_tree"
    assert res.error_code is None
    assert Path(res.path).parent == repo / "src/MSDIAL5/MsdialGuiApp/bin/Debug/net48"


def test_resolve_lbm_build_tree_picks_the_tfm_matching_the_console_exe(tmp_path):
    """同名コピーが TFM ごとに並ぶので、exe 自身の TFM と揃える。"""
    repo, exe = _build_tree(tmp_path, msdial5_tfms=("", "net472", "net48", "net481"),
                            exe_tfm="net481")
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert Path(res.path).parent == repo / "src/MSDIAL5/MsdialGuiApp/bin/Debug/net481"


def test_resolve_lbm_build_tree_falls_back_to_the_plain_debug_copy(tmp_path):
    """exe の TFM に対応するコピーが無ければ素の Debug/ を使う。"""
    repo, exe = _build_tree(tmp_path, msdial5_tfms=("", "net472"), exe_tfm="net48")
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert Path(res.path).parent == repo / "src/MSDIAL5/MsdialGuiApp/bin/Debug"


def test_resolve_lbm_build_tree_is_not_ambiguous_for_tfm_copies(tmp_path):
    """同名の TFM 別コピーは『候補 4 件』ではない。LBM_AMBIGUOUS にしない。"""
    _repo, exe = _build_tree(tmp_path, msdial5_tfms=("", "net472", "net48", "net481"))
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert res.error_code is None
    assert res.candidates == ()


def test_resolve_lbm_build_tree_excludes_msdial4(tmp_path):
    """MSDIAL4 の conventional ライブラリは NCDK 無しの別世代。Console(MSDIAL5) に混ぜない。"""
    _repo, exe = _build_tree(tmp_path, msdial5_tfms=(), msdial4_tfms=("net48", "net481"))
    installed = tmp_path / "installed.lbm2"
    installed.touch()
    res = resolve_lbm({"lbm file path": ""}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe),
                      env={"MSDIAL_LBM": str(installed)})
    assert res.source == "env"
    assert Path(res.path) == installed


def test_resolve_lbm_method_file_declaration_still_beats_the_build_tree(tmp_path):
    """メソッドファイルの明示宣言はビルドツリーより強い（MS-DIAL の規則を維持）。"""
    _repo, exe = _build_tree(tmp_path)
    declared = tmp_path / "declared.lbm2"
    declared.touch()
    res = resolve_lbm({"lbm file path": str(declared)}, tmp_path / "param.txt",
                      omics="lipidomics", exe_path=str(exe), env={})
    assert res.source == "method_file"
    assert Path(res.path) == declared
