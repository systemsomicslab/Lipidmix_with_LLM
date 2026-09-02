"""差次的エクスポート契約（lipidmix.analysis.export_contract）の単体テスト。

ARF 経路（arf_export_differential）と DatasetState 経路
（dataset_export_differential）の両方がこのモジュールを参照する。契約の列・版・
メタ行順序はここで固定し、二重定義を作らない（arf/tools.py 側は同一性を確認する）。
"""


def test_contract_constants_are_shared_with_arf():
    """ARF 側が契約モジュールの定数を参照していること（二重定義を作らない）。"""
    from lipidmix.analysis import export_contract
    from lipidmix.arf import tools as arf_tools
    assert arf_tools._EXPORT_COLUMNS is export_contract.EXPORT_COLUMNS
    assert arf_tools._DIFFERENTIAL_CONTRACT_VERSION == export_contract.CONTRACT_VERSION
    assert arf_tools._LOG2FC_SIGN == export_contract.LOG2FC_SIGN


def test_export_columns_are_frozen():
    """列と順序は下流との契約。変更は contract_version の上げ方とセット。"""
    from lipidmix.analysis.export_contract import EXPORT_COLUMNS
    assert EXPORT_COLUMNS == [
        "spot_id", "name", "name_source", "ontology", "inchikey",
        "inchikey_source", "msi_level", "mz", "rt", "log2fc",
        "p_value", "q_value", "mean_a", "mean_b", "significant",
    ]


def test_format_number_blanks_non_finite():
    from lipidmix.analysis.export_contract import format_number
    assert format_number(None, ".4f") == ""
    assert format_number(float("nan"), ".4f") == ""
    assert format_number(float("inf"), ".4f") == ""
    assert format_number(1.23456, ".4f") == "1.2346"


def _meta(**over):
    from lipidmix.analysis.export_contract import build_meta
    kwargs = dict(group_a="ctrl", n_a=4, group_b="treat", n_b=4,
                  q_threshold=0.05, log2fc_threshold=1.0, log_transform=True,
                  n_features_total=100, n_with_inchikey=80, n_unannotated=20,
                  msi_note="# msi_level は注釈確度。MS/MS の有無ではない")
    kwargs.update(over)
    return build_meta(**kwargs)


def test_build_meta_has_mandatory_lines():
    lines = _meta()
    joined = "\n".join(lines)
    assert "# contract_version = 1" in joined
    assert "# log2fc_sign = positive means group_b is higher" in joined
    assert "n_a = 4" in joined and "n_b = 4" in joined
    assert "n_unannotated = 20" in joined
    assert all(line.startswith("#") for line in lines)


def test_build_meta_places_optional_slots_in_contract_order():
    """行の順序も契約。source は exported_at の直後、preprocess は閾値の直後。"""
    lines = _meta(source_lines=["# source_arf = a.arf", "# source_arf2 = a.arf2"],
                  preprocess_line="# preprocess = {'normalize': 'none'}")
    keys = [l.split(" = ")[0].split("\t")[0] for l in lines]
    assert keys == [
        "# contract_version", "# exported_at",
        "# source_arf", "# source_arf2",
        "# group_a", "# group_b", "# log2fc_sign", "# q_threshold",
        "# preprocess", "# n_features_total",
        "# msi_level は注釈確度。MS/MS の有無ではない",
    ]


def test_build_meta_omits_preprocess_when_absent():
    assert not any("preprocess" in l for l in _meta())


def test_run_dataset_differential_tracks_contract_module(monkeypatch):
    """dataset_analysis.py がバージョン/符号ラベルをローカル複製せず、
    export_contract を都度参照していることを行動で確認する。

    値の一致（==）だけを見るテストは、dataset_analysis.py 側に値の等しい
    ローカル定数（旧 _CONTRACT_VERSION/_LOG2FC_SIGN）が残っていても偶然パスして
    しまう。ここでは export_contract 側だけを書き換え、その変更が
    run_dataset_differential の戻り値へ伝播することを見て、参照がコピーでなく
    リンクであることを確認する（コントローラ裁定: これを直さないと、
    CONTRACT_VERSION を上げた瞬間 dataset_export_differential が永久に
    「契約非互換」で拒否し続ける無限ループになる）。
    """
    import numpy as np

    from lipidmix.analysis import export_contract
    from lipidmix.analysis.dataset_analysis import (
        run_dataset_differential, run_dataset_preprocess,
    )
    from lipidmix.mztab.dataset_state import DatasetState

    monkeypatch.setattr(export_contract, "CONTRACT_VERSION", 999)
    monkeypatch.setattr(export_contract, "LOG2FC_SIGN", "patched sign for linkage test")

    rng = np.random.default_rng(0)
    ds = DatasetState()
    ds.feature_matrix = rng.random((20, 8)) * 1000.0
    ds.sample_names = ([f"ctrl_{i}" for i in range(4)]
                       + [f"treat_{i}" for i in range(4)])
    ds.feature_ids = [f"f{i}" for i in range(20)]
    (ds.pp_matrix, ds.pp_sample_names, ds.pp_feature_names,
     ds.roles, ds.sample_meta, _) = run_dataset_preprocess(ds, {})

    result = run_dataset_differential(
        ds,
        group_a_samples=[n for n in ds.pp_sample_names if n.startswith("ctrl")],
        group_b_samples=[n for n in ds.pp_sample_names if n.startswith("treat")],
    )

    assert result["contract_version"] == 999
    assert result["log2fc_sign"] == "patched sign for linkage test"
