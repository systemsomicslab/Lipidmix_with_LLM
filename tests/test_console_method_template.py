"""console_method_template — その極性で一度も GUI 実行が無いときの最後の手段。

主経路は「GUI が実行のたびに自動保存した `*_param_<ts>.txt` を探す」
（console_plan の method_file 省略）。ここはそれが無いときだけ使う fallback で、
実データの POS がまさにそれだった（NEG は GUI 処理済み、POS は生データのみ）。

やることは、別極性のパラメータから `Ion mode` と `Searched adduct ions` を
その極性の標準セットに差し替えること —— 手作業でやったことの機械化。
検出・アライメント条件はラボの設定をそのまま引き継ぐ（勝手に変えない）。
"""
from __future__ import annotations

import json as _json
from pathlib import Path


def _neg_param(path: Path):
    path.write_text(
        "# MS-DIAL param\n"
        "Ion mode: Negative\n"
        "Target omics: Lipidomics\n"
        "Lbm file path: \n"
        "Searched adduct ions: [M-H]-,[M+Cl]-\n"
        "Minimum peak height: 1000\n"
        "Retention time tolerance for alignment: 0.1\n",
        encoding="ascii")
    return path


def _ready(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    (app / "lib.lbm2").touch()
    monkeypatch.setenv("MSDIAL_EXE", str(app / "MSDIALCUI.exe"))
    monkeypatch.delenv("MSDIAL_LBM", raising=False)


def test_template_switches_polarity_and_adducts(tmp_path, monkeypatch):
    from lipidmix.tools.console_tools import console_method_template
    _ready(tmp_path, monkeypatch)
    src = _neg_param(tmp_path / "neg_param_1.txt")
    out = tmp_path / "param_POS.txt"
    parsed = _json.loads(console_method_template(
        out_path=str(out), polarity="positive", based_on=str(src)))
    assert parsed["status"] == "written"
    text = out.read_text(encoding="ascii")
    assert "Ion mode: Positive" in text
    assert "[M+NH4]+" in text
    assert "[M+Cl]-" not in text


def test_template_keeps_the_labs_detection_settings(tmp_path, monkeypatch):
    """極性とアダクト以外は触らない。検出条件を勝手に変えたら別の解析になる。"""
    from lipidmix.tools.console_tools import console_method_template
    _ready(tmp_path, monkeypatch)
    src = _neg_param(tmp_path / "neg_param_1.txt")
    out = tmp_path / "param_POS.txt"
    console_method_template(out_path=str(out), polarity="positive", based_on=str(src))
    text = out.read_text(encoding="ascii")
    assert "Minimum peak height: 1000" in text
    assert "Retention time tolerance for alignment: 0.1" in text


def test_template_fills_the_resolved_lbm_path(tmp_path, monkeypatch):
    """GUI 由来のパラメータは Lbm file path が必ず空。テンプレートで埋めておく。"""
    from lipidmix.tools.console_tools import console_method_template
    _ready(tmp_path, monkeypatch)
    src = _neg_param(tmp_path / "neg_param_1.txt")
    out = tmp_path / "param_POS.txt"
    parsed = _json.loads(console_method_template(
        out_path=str(out), polarity="positive", based_on=str(src)))
    assert parsed["lbm"]["source"] == "exe_dir"
    assert "lib.lbm2" in out.read_text(encoding="ascii")


def test_template_discovers_a_source_of_the_other_polarity(tmp_path, monkeypatch):
    """POS が無いから作る、という状況なので、探す相手は別極性でよい。"""
    from lipidmix.tools.console_tools import console_method_template
    _ready(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    _neg_param(data / "Dataset_2026_param_202605151055.txt")
    out = tmp_path / "param_POS.txt"
    parsed = _json.loads(console_method_template(
        out_path=str(out), polarity="positive", dataset_root=str(data)))
    assert parsed["status"] == "written"
    assert parsed["based_on"].endswith("Dataset_2026_param_202605151055.txt")


def test_template_errors_without_any_source(tmp_path, monkeypatch):
    from lipidmix.tools.console_tools import console_method_template
    _ready(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    parsed = _json.loads(console_method_template(
        out_path=str(tmp_path / "o.txt"), polarity="positive", dataset_root=str(data)))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_GIVEN"


def test_template_rejects_binary_source(tmp_path, monkeypatch):
    """.mdproject は ZIP。渡すと全パラメータ既定値で走る種を作ってしまう。"""
    from lipidmix.tools.console_tools import console_method_template
    _ready(tmp_path, monkeypatch)
    src = tmp_path / "project.mdproject"
    src.write_bytes(b"PK\x03\x04\x00\x00binary")
    parsed = _json.loads(console_method_template(
        out_path=str(tmp_path / "o.txt"), polarity="positive", based_on=str(src)))
    assert parsed["error"]["code"] == "METHOD_FILE_NOT_TEXT"


def test_template_output_then_plans_cleanly(tmp_path, monkeypatch):
    """作ったテンプレートが console_plan をそのまま通ること。"""
    from lipidmix.core import session_state
    session_state.session = session_state.AnalysisSession()
    from lipidmix.tools.console_tools import console_method_template, console_plan
    _ready(tmp_path, monkeypatch)
    monkeypatch.setattr("lipidmix.console.runner.is_console_exe", lambda *a, **k: True)
    src = _neg_param(tmp_path / "neg_param_1.txt")
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.wiff").touch()
    out = tmp_path / "param_POS.txt"
    console_method_template(out_path=str(out), polarity="positive", based_on=str(src))
    parsed = _json.loads(console_plan(dataset_root=str(data), method_file=str(out),
                                      polarity="positive", measure="peak_height"))
    assert parsed["status"] == "planned"
