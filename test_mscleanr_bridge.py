"""Smoke tests for the MS-CleanR rpy2 bridge and its MCP integration.

These tests do NOT require R, rpy2, or MS-CleanR to be installed. They verify
that:
  * the bridge degrades cleanly (structured errors, never crashes) when the R
    stack is unavailable;
  * the pandas pivot table handed to the bridge has the expected contract
    (id column + ``*_Intensity`` sample columns) -- the "pandas-to-R input
    conversion" fixture using a monkeypatched bridge;
  * ``mscleanp_filter`` preserves backward-compatible MCP behavior (load-ARF
    first error, retained-id mapping, PCA rerun).

Run with: ``python -m pytest test_mscleanr_bridge.py``  (or just execute the
module: ``python test_mscleanr_bridge.py``).
"""

import pandas as pd

import mscleanr_bridge as bridge
import server


def _sample_pivot():
    return pd.DataFrame({
        "MasterAlignmentID": [0, 1, 2],
        "Name": ["PC 34:1", "TG 52:2", "LPC 16:0"],
        "MassCenter": [100.1, 200.2, 300.3],
        "RT": [1.0, 2.0, 3.0],
        "IonMode": ["Positive"] * 3,
        "SampleA_Intensity": [1000, 5000, 300],
        "SampleB_Intensity": [1100, 200, 2900],
    })


# --- bridge: dependency check + graceful degradation -----------------------

def test_check_dependencies_never_raises():
    deps = bridge.check_dependencies()
    assert set(["ok", "rpy2", "r_available", "mscleanr_installed", "message", "errors"]) <= set(deps)
    assert isinstance(deps["ok"], bool)
    # When rpy2 is absent the message must be clear and ok must be False.
    if not deps["rpy2"]:
        assert deps["ok"] is False
        assert "rpy2" in deps["message"].lower()


def test_check_dependencies_uses_cache():
    first = bridge.check_dependencies(force=True)
    second = bridge.check_dependencies()
    assert second.get("cached") is True
    assert second["ok"] == first["ok"]


def test_bootstrap_r_environment_sets_expected_windows_defaults():
    applied = bridge._bootstrap_r_environment()
    if bridge.os.name == "nt":
        assert "PYTHONUTF8" in applied
        assert "R_HOME" in bridge.os.environ
        assert "R_LIBS_USER" in bridge.os.environ


def test_arf_compat_returns_contract_when_r_unavailable():
    result = bridge.run_arf_compat_mode(_sample_pivot())
    # Always the data-contract shape, never a raised exception.
    for key in ("ok", "message", "stats", "retained_master_ids", "output_paths", "warnings", "errors"):
        assert key in result
    if not bridge.check_dependencies()["ok"]:
        assert result["ok"] is False
        assert result["errors"]
        assert result["retained_master_ids"] == []


def test_native_mode_requires_project_dir():
    result = bridge.run_native_project_mode(None)
    assert result["ok"] is False
    assert "mscleanr_project_dir" in result["message"]


def test_native_mode_missing_directory():
    result = bridge.run_native_project_mode("Z:/definitely/not/here_xyz")
    assert result["ok"] is False
    assert "does not exist" in result["message"]


def test_find_id_column_aliases():
    df = pd.DataFrame({"Alignment ID": [1, 2], "x": [3, 4]})
    assert bridge._find_id_column(df) == "Alignment ID"
    df2 = pd.DataFrame({"x": [1]})
    assert bridge._find_id_column(df2) is None


def test_arf_compat_wrapper_does_not_cluster_by_rounded_rt():
    """RT bins must not be used as a fake cluster for top_n filtering."""
    assert "round(rt_vals, 2)" not in bridge._R_ARF_COMPAT_WRAPPER
    assert "Skipped top_n peak selection" in bridge._R_ARF_COMPAT_WRAPPER


def test_native_wrapper_calls_clean_msdial_data_without_project_arg():
    assert "clean_msdial_data(project_dir)" not in bridge._R_NATIVE_WRAPPER
    assert "clean_msdial_data()" in bridge._R_NATIVE_WRAPPER
    assert "MS_peaks-final.csv" in bridge._R_NATIVE_WRAPPER


# --- MCP tool: backward-compatible behavior --------------------------------

def test_mscleanp_filter_requires_arf_first():
    server.session.features = None
    out = server.mscleanp_filter()
    assert isinstance(out, list)
    assert "arf_parser" in out[0] or "warmup" in out[0]


def test_mscleanp_filter_native_mode_missing_dir():
    server._mscleanr_set_state(status="ready", started_at=1.0, finished_at=2.0, result={"ok": True}, error=None)
    out = server.mscleanp_filter(use_native_project_mode=True, mscleanr_project_dir=None)
    assert "mscleanr_project_dir" in out[0]


def test_mscleanr_warmup_status_formats_state():
    server._mscleanr_set_state(
        status="ready",
        started_at=1.0,
        finished_at=2.0,
        result={"ok": True, "rpy2": True, "r_available": True, "mscleanr_installed": True},
        error=None,
    )
    out = server.mscleanr_warmup_status()
    assert "MS-CleanR warmup status" in out
    assert "ready" in out


def _make_sample(fname, fid, pid, height, mz):
    row = [0] * 26
    row[0] = fname
    row[1] = fid
    row[2] = pid
    row[3] = pid
    row[18] = float(height)
    row[20] = float(height) * 2
    row[22] = float(mz)
    return row


def _make_spot(mid, mz, rt, name, heights):
    return {
        "MasterAlignmentID": mid,
        "AlignmentID": mid,
        "RT": rt,
        "MassCenter": mz,
        "IonMode": "Positive",
        "Name": name,
        "AlignedPeakProperties": [
            _make_sample("SampleA", 0, mid, heights[0], mz),
            _make_sample("SampleB", 1, mid, heights[1], mz),
        ],
    }


def test_pivot_table_contract():
    """The table handed to the R bridge must carry the id column + *_Intensity."""
    server.session.features = [
        _make_spot(0, 100.1, 1.0, "PC 34:1", [1000, 1100]),
        _make_spot(1, 200.2, 2.0, "TG 52:2", [5000, 200]),
        _make_spot(2, 300.3, 3.0, "LPC 16:0", [300, 2900]),
    ]
    df = server._build_arf_pivot_table()
    assert "MasterAlignmentID" in df.columns
    assert any(str(c).endswith("_Intensity") for c in df.columns)


def test_mscleanp_filter_maps_retained_ids_and_reruns_pca(monkeypatch):
    server._mscleanr_set_state(status="ready", started_at=1.0, finished_at=2.0, result={"ok": True}, error=None)
    server.session.features = [
        _make_spot(0, 100.1, 1.0, "PC 34:1", [1000, 1100]),
        _make_spot(1, 200.2, 2.0, "TG 52:2", [5000, 200]),
        _make_spot(2, 300.3, 3.0, "LPC 16:0", [300, 2900]),
        _make_spot(3, 400.4, 4.0, "", [700, 800]),
    ]
    n = len(server.session.features)

    def fake_run(df, **kwargs):
        assert "MasterAlignmentID" in df.columns
        ids = [0, 2]
        return {
            "ok": True,
            "message": "fake",
            "stats": {
                "initial_peaks": n,
                "final_peaks": len(ids),
                "retention_rate": 50.0,
                "after_blank_subtraction": n,
                "after_rsd_filtering": len(ids),
                "removed_peaks": n - len(ids),
            },
            "retained_master_ids": ids,
            "output_paths": {},
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(bridge, "run_arf_compat_mode", fake_run)
    out = server.mscleanp_filter()
    assert {s["MasterAlignmentID"] for s in server.session.filtered_features} == {0, 2}
    assert "PC1" in out[0] or "PCA" in out[0]


def test_arf_re_pca_mscleanr_maps_retained_ids_and_reruns_pca(monkeypatch):
    server._mscleanr_set_state(status="ready", started_at=1.0, finished_at=2.0, result={"ok": True}, error=None)
    server.session.current_file_path = "sample.arf"
    server.session.features = [
        _make_spot(0, 100.1, 1.0, "PC 34:1", [1000, 1100]),
        _make_spot(1, 200.2, 2.0, "TG 52:2", [5000, 200]),
        _make_spot(2, 300.3, 3.0, "PC 36:2", [300, 2900]),
        _make_spot(3, 400.4, 4.0, "", [700, 800]),
    ]

    def fake_run(df, **kwargs):
        assert "MasterAlignmentID" in df.columns
        assert set(df["MasterAlignmentID"].tolist()) == {0, 2}
        ids = [0, 2]
        return {
            "ok": True,
            "message": "fake",
            "stats": {
                "initial_peaks": len(df),
                "final_peaks": len(ids),
                "retention_rate": 100.0,
                "after_blank_subtraction": len(df),
                "after_rsd_filtering": len(ids),
                "removed_peaks": 0,
            },
            "retained_master_ids": ids,
            "output_paths": {},
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(bridge, "run_arf_compat_mode", fake_run)
    out = server.arf_re_pca_mscleanr(annotation_keyword="PC")
    assert {s["MasterAlignmentID"] for s in server.session.filtered_features} == {0, 2}
    assert "MS-CleanR" in out[0]
    assert "PC1" in out[0] or "PCA" in out[0]


if __name__ == "__main__":
    # Lightweight runner so the suite works without pytest installed.
    import types

    class _MonkeyPatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._undo):
                setattr(obj, name, value)

    passed = 0
    for fn_name, fn in sorted(globals().items()):
        if not fn_name.startswith("test_") or not isinstance(fn, types.FunctionType):
            continue
        mp = _MonkeyPatch()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            passed += 1
            print(f"PASS {fn_name}")
        finally:
            mp.undo()
    print(f"\n{passed} tests passed.")
