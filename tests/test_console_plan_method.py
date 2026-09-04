"""console_plan のメソッドファイル解決（LBM 自動解決・自動保存パラメータの探索）。

純ロジックは tests/test_console_method_file.py。ここは MCP ツール層の結線を見る。
"""
from __future__ import annotations

import json as _json
from pathlib import Path


def _fake_exe_with_lbm(tmp_path, *names):
    """MSDIAL_EXE と同じフォルダに脂質ライブラリを置く（GUI と同じ配置）。"""
    d = tmp_path / "msdial_app"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).touch()
    return d / "MSDIALCUI.exe"


def _plan_ready(tmp_path, monkeypatch, exe):
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    monkeypatch.setenv("MSDIAL_EXE", str(exe))
    monkeypatch.delenv("MSDIAL_LBM", raising=False)
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    (tmp_path / "a.wiff").touch()


def test_console_plan_rejects_lipidomics_without_lbm(tmp_path, monkeypatch):
    """LBM 空のまま走らせると警告なしで同定 0 件になる（実測 60 サンプル 31 分）。"""
    exe = _fake_exe_with_lbm(tmp_path)  # .lbm2 を置かない
    _plan_ready(tmp_path, monkeypatch, exe)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\nLbm file path: \n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "LBM_NOT_FOUND"


def test_console_plan_resolves_lbm_from_exe_directory(tmp_path, monkeypatch):
    """GUI と同じく MSDIAL_EXE と同じフォルダの *.lbm2 を 1 件だけ自動採用する。"""
    exe = _fake_exe_with_lbm(tmp_path, "Msp_lipids.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\nLbm file path: \n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["status"] == "planned"
    assert parsed["lbm"]["source"] == "exe_dir"
    assert Path(parsed["lbm"]["path"]).name == "Msp_lipids.lbm2"


def test_console_plan_writes_effective_method_file_without_touching_source(tmp_path, monkeypatch):
    """解決した LBM は run_dir の写しに書く。ユーザーのファイルは変えない。"""
    exe = _fake_exe_with_lbm(tmp_path, "Msp_lipids.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    method = tmp_path / "params.txt"
    original = "Ion mode: Negative\nLbm file path: \n"
    method.write_text(original, encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    effective = Path(parsed["method_file"])
    assert effective != method
    assert effective.parent == Path(parsed["run_dir"])
    assert "Msp_lipids.lbm2" in effective.read_text(encoding="ascii")
    assert method.read_text(encoding="ascii") == original


def test_console_plan_job_points_at_effective_method_file(tmp_path, monkeypatch):
    """console_run は job.method_file を Console へ渡すので、そこが写しでなければ意味がない。"""
    from lipidmix.console.job_manager import load_job
    exe = _fake_exe_with_lbm(tmp_path, "Msp_lipids.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\nLbm file path: \n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    job = load_job(Path(parsed["job_path"]))
    assert "Msp_lipids.lbm2" in Path(job.method_file).read_text(encoding="ascii")


def test_console_plan_rejects_ambiguous_lbm(tmp_path, monkeypatch):
    """GUI も候補が 1 件でなければ実行を止める。"""
    exe = _fake_exe_with_lbm(tmp_path, "a.lbm2", "b.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "LBM_AMBIGUOUS"


def test_console_plan_lbm_file_argument_overrides(tmp_path, monkeypatch):
    exe = _fake_exe_with_lbm(tmp_path, "installed.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    chosen = tmp_path / "chosen.lbm2"
    chosen.touch()
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height",
                                      lbm_file=str(chosen)))
    assert Path(parsed["lbm"]["path"]).name == "chosen.lbm2"
    assert parsed["lbm"]["source"] == "argument"


def test_console_plan_metabolomics_does_not_require_lbm(tmp_path, monkeypatch):
    exe = _fake_exe_with_lbm(tmp_path)
    _plan_ready(tmp_path, monkeypatch, exe)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="negative", measure="peak_height",
                                      omics="metabolomics"))
    assert parsed["status"] == "planned"
    assert parsed["lbm"]["source"] == "not_required"


def test_console_plan_rejects_polarity_mismatch(tmp_path, monkeypatch):
    """メソッドの Ion mode と宣言極性がずれたまま走ると、別極性の結果が黙って出る。"""
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    method = tmp_path / "params.txt"
    method.write_text("Ion mode: Negative\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path), method_file=str(method),
                                      polarity="positive", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_POLARITY_MISMATCH"


def test_console_plan_discovers_auto_saved_method_file(tmp_path, monkeypatch):
    """GUI は実行のたびに *_param_<ts>.txt を自動保存する。渡さなくても見つける。"""
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    (tmp_path / "Dataset_2026_param_202605151055.txt").write_text(
        "Ion mode: Negative\nTarget omics: Lipidomics\nLbm file path: \n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path),
                                      polarity="negative", measure="peak_height"))
    assert parsed["status"] == "planned"
    assert parsed["method_source"]["discovered_from"].endswith(
        "Dataset_2026_param_202605151055.txt")


def test_console_plan_discovery_ignores_other_polarity(tmp_path, monkeypatch):
    """NEG の自動保存パラメータを POS の計画に流用してはいけない。"""
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    (tmp_path / "Dataset_2026_param_202605151055.txt").write_text(
        "Ion mode: Negative\nTarget omics: Lipidomics\n", encoding="ascii")
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path),
                                      polarity="positive", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_GIVEN"


def test_console_plan_missing_method_file_reports_no_candidates(tmp_path, monkeypatch):
    exe = _fake_exe_with_lbm(tmp_path, "x.lbm2")
    _plan_ready(tmp_path, monkeypatch, exe)
    from lipidmix.tools.console_tools import console_plan
    parsed = _json.loads(console_plan(dataset_root=str(tmp_path),
                                      polarity="negative", measure="peak_height"))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_GIVEN"
