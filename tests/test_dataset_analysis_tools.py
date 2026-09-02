import json

import numpy as np
import pytest

from lipidmix.core import session_state
from lipidmix.core.mcp_errors import MISSING_STATE
from lipidmix.mztab.dataset_state import DatasetState


@pytest.fixture(autouse=True)
def reset_session():
    session_state.session = session_state.AnalysisSession()
    yield
    session_state.session = session_state.AnalysisSession()


@pytest.fixture(autouse=True)
def _unregister_tools_after_test():
    """FastMCP の @mcp.tool は import 時（デコレータ評価時）に共有シングルトン
    `mcp_core.mcp` へ即登録する。本ファイルが dataset_analysis_tools を import
    すると、server.py がまだこのモジュールを import していなくても（Task 6 以前）
    プロセス寿命のあいだ登録済み扱いになり、pytest を1プロセスで全件実行したときに
    tests/test_server_registration.py・test_tool_annotations.py・
    test_workflow_docs.py のスナップショット/腐敗防止テストを汚染する
    （関数を直接呼ぶ本ファイルのテストは mcp 経由で解決しないため、ここで
    tool_manager から外しても各テストの呼び出しには影響しない）。

    ツール名を __all__ から導出することで、モジュールが成長して新しいツール
    が追加されても対象が自動で追いつく（ハードコード名の追加漏れによる登録汚染
    の再発防止）。本フィクスチャは Task 6 で server.py がこのモジュールを登録
    するまでの暫定対策である。
    """
    yield
    from lipidmix.core.mcp_core import mcp
    from lipidmix.tools import dataset_analysis_tools
    for name in dataset_analysis_tools.__all__:
        try:
            mcp._tool_manager.remove_tool(name)
        except Exception:
            pass


def _load_ds(n_features=20, n_samples=8):
    rng = np.random.default_rng(0)
    ds = DatasetState()
    ds.feature_matrix = rng.random((n_features, n_samples)) * 1000.0
    ds.sample_names = ([f"ctrl_{i}" for i in range(n_samples // 2)]
                       + [f"treat_{i}" for i in range(n_samples - n_samples // 2)])
    ds.feature_ids = [f"f{i}" for i in range(n_features)]
    ds.feature_metadata = {
        f"f{i}": {"name": f"Compound {i}", "mz": 100.0 + i, "rt": 1.0 + i * 0.1,
                  "inchikey": f"AAAAAAAAAAAAAA-BBBBBBBBFB-{i%10}",
                  "inchikey_source": "database_identifier"}
        for i in range(n_features)
    }
    ds.validation_result = {"ok": True, "errors": [], "warnings": []}
    session_state.session.dataset = ds
    return ds


def _groups(ds):
    return ([n for n in ds.pp_sample_names if n.startswith("ctrl")],
            [n for n in ds.pp_sample_names if n.startswith("treat")])


# ---------- dataset_preprocess ----------

def test_dataset_preprocess_without_dataset_returns_missing_state():
    from lipidmix.tools.dataset_analysis_tools import dataset_preprocess
    parsed = json.loads(dataset_preprocess())
    assert parsed["error"]["code"] == MISSING_STATE
    assert parsed["error"]["required_tools"] == ["dataset_load"]


def test_dataset_preprocess_success_sets_pp_matrix():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_preprocess
    parsed = json.loads(dataset_preprocess())
    assert parsed["status"] == "success"
    assert parsed["n_samples"] == 8
    assert parsed["n_features"] == 20
    ds = session_state.session.dataset
    assert ds.pp_matrix is not None
    assert ds.preprocessing_recipe["normalize"] == "none"


def test_dataset_preprocess_bad_recipe_is_not_missing_state():
    """引数エラーは missing_state ではない（リプレイしても直らない）。"""
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_preprocess
    parsed = json.loads(dataset_preprocess(normalize="not_a_method"))
    assert parsed["error"]["code"] != MISSING_STATE
    assert "not_a_method" in json.dumps(parsed, ensure_ascii=False)


# ---------- dataset_pca ----------

def test_dataset_pca_requires_preprocess():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_pca
    parsed = json.loads(dataset_pca())
    assert parsed["error"]["code"] == MISSING_STATE
    assert parsed["error"]["required_tools"] == ["dataset_preprocess"]


def test_dataset_pca_success_omits_loadings_from_payload():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_pca, dataset_preprocess
    dataset_preprocess()
    parsed = json.loads(dataset_pca(n_components=2))
    assert len(parsed["explained_variance_ratio"]) == 2
    assert len(parsed["scores"]) == 8
    assert "loadings" not in parsed          # 全量は戻り値に載せない
    assert session_state.session.dataset.last_pca["loadings"]  # セッションには保持


# ---------- dataset_differential ----------

def test_dataset_differential_requires_preprocess():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_differential
    parsed = json.loads(dataset_differential(group_a=["ctrl_0"], group_b=["treat_0"]))
    assert parsed["error"]["code"] == MISSING_STATE
    assert parsed["error"]["required_tools"] == ["dataset_preprocess"]


def test_dataset_differential_success_returns_summary_only():
    ds = _load_ds()
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_preprocess,
    )
    dataset_preprocess()
    a, b = _groups(session_state.session.dataset)
    parsed = json.loads(dataset_differential(group_a=a, group_b=b))
    assert parsed["status"] == "success"
    assert "n_tested" in parsed["summary"]
    assert len(parsed["summary"]["top"]) <= 15
    assert "results" not in parsed           # 全量は戻り値に載せない
    assert "volcano" not in parsed
    stored = session_state.session.dataset.last_differential
    assert len(stored["results"]) == 20      # セッションには全量


def test_dataset_differential_small_group_is_bad_request():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_preprocess,
    )
    dataset_preprocess()
    parsed = json.loads(dataset_differential(group_a=["ctrl_0"], group_b=["treat_0"]))
    assert parsed["error"]["code"] != MISSING_STATE
    assert "群サイズ" in parsed["error"]["message"]


def test_dataset_differential_does_not_touch_arf_slot():
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_preprocess,
    )
    dataset_preprocess()
    a, b = _groups(session_state.session.dataset)
    dataset_differential(group_a=a, group_b=b)
    assert session_state.session.arf.feature_matrix is None
    assert getattr(session_state.session.arf, "last_differential", None) is None


# ---------- dataset_export_differential ----------

def test_dataset_export_requires_differential(tmp_path):
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import dataset_export_differential
    parsed = json.loads(dataset_export_differential(str(tmp_path / "out.tsv")))
    assert parsed["error"]["code"] == MISSING_STATE
    assert parsed["error"]["required_tools"] == ["dataset_differential"]


def test_dataset_export_writes_contract_format(tmp_path):
    from lipidmix.analysis.export_contract import CONTRACT_VERSION, EXPORT_COLUMNS
    _load_ds()
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_export_differential, dataset_preprocess,
    )
    dataset_preprocess()
    a, b = _groups(session_state.session.dataset)
    dataset_differential(group_a=a, group_b=b)

    out = tmp_path / "diff.tsv"
    parsed = json.loads(dataset_export_differential(str(out)))
    assert parsed["status"] == "success"
    assert parsed["contract_version"] == CONTRACT_VERSION

    lines = out.read_text(encoding="utf-8").splitlines()
    meta = [l for l in lines if l.startswith("#")]
    assert f"# contract_version = {CONTRACT_VERSION}" in meta
    assert any("id_space = mztab_smf_id" in l for l in meta)
    header = next(l for l in lines if not l.startswith("#"))
    assert header.split("\t") == EXPORT_COLUMNS
    body = [l for l in lines if not l.startswith("#")][1:]
    assert len(body) == 20
    # p_value / q_value が空欄でないこと（初版の欠陥の回帰テスト）
    cols = body[0].split("\t")
    assert cols[EXPORT_COLUMNS.index("p_value")] != ""
    assert cols[EXPORT_COLUMNS.index("q_value")] != ""
    assert cols[EXPORT_COLUMNS.index("inchikey")] != ""


def test_dataset_export_refuses_without_inchikey(tmp_path):
    """InChIKey が 0 件なら書かずに拒否する（arf_export_differential と同じ理由）。"""
    ds = _load_ds()
    ds.feature_metadata = {fid: {"name": None, "mz": None, "rt": None,
                                 "inchikey": None, "inchikey_source": "none"}
                           for fid in ds.feature_ids}
    from lipidmix.tools.dataset_analysis_tools import (
        dataset_differential, dataset_export_differential, dataset_preprocess,
    )
    dataset_preprocess()
    a, b = _groups(session_state.session.dataset)
    dataset_differential(group_a=a, group_b=b)
    out = tmp_path / "diff.tsv"
    parsed = json.loads(dataset_export_differential(str(out)))
    assert parsed["status"] == "error"
    assert not out.exists()
