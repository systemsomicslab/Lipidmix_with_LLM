"""対向プロット。座標は payload に持ち、描画は PNG で返す。"""
import pytest

from lipidmix.plots import mirror


def test_the_payload_keeps_both_spectra_and_the_matches():
    measured = [[100.0, 999.0], [200.0, 500.0]]
    reference = [[100.0, 999.0], [300.0, 250.0]]
    alignment = [{"mz": 100.0, "measured": 1.0, "reference": 1.0, "matched": True},
                 {"mz": 300.0, "measured": 0.0, "reference": 0.25, "matched": False}]

    payload = mirror.build_mirror_payload(measured, reference, alignment, title="GABA")

    assert payload["title"] == "GABA"
    assert payload["measured"] == [[100.0, 999.0], [200.0, 500.0]]
    assert payload["reference"] == [[100.0, 999.0], [300.0, 250.0]]
    assert payload["matched_mz"] == [100.0]


def test_the_labels_are_capped():
    measured = [[float(i), float(1000 - i)] for i in range(50)]
    payload = mirror.build_mirror_payload(measured, measured, [], title="x", top_labels=3)
    assert len(payload["labels"]) <= 3


def test_render_returns_a_png():
    payload = mirror.build_mirror_payload([[100.0, 999.0]], [[100.0, 999.0]],
                                          [{"mz": 100.0, "measured": 1.0,
                                            "reference": 1.0, "matched": True}], title="t")
    png = mirror.render_mirror(payload)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_measured_side_is_matched_within_tolerance():
    # alignment["mz"] is always a reference-grid m/z (see spectral_match._matched_peaks_walk),
    # so an exact-equality check against measured m/z would basically never fire on real data
    # (the measured peak sits within ms2_tol of the window center, not exactly on it).
    measured = [[100.021, 999.0]]
    reference = [[100.019, 950.0]]
    alignment = [{"mz": 100.019, "measured": 1.0, "reference": 1.0, "matched": True}]

    payload = mirror.build_mirror_payload(measured, reference, alignment, title="t", ms2_tol=0.01)

    assert payload["matched_mz"] == [100.019]
    assert payload["matched_measured_mz"] == [100.021]


def test_measured_side_is_not_matched_without_a_tolerance():
    # Without an explicit ms2_tol we must not guess one, so the measured side stays unmarked.
    measured = [[100.021, 999.0]]
    reference = [[100.019, 950.0]]
    alignment = [{"mz": 100.019, "measured": 1.0, "reference": 1.0, "matched": True}]

    payload = mirror.build_mirror_payload(measured, reference, alignment, title="t")

    assert payload["matched_measured_mz"] == []


def test_labels_are_ranked_per_side_not_by_raw_scale():
    # Measured intensities are raw instrument counts (tens of thousands); reference
    # intensities are library-relative (hundreds). Ranking by raw intensity would let
    # measured peaks crowd out every reference label.
    measured = [[100.0, 45000.0], [150.0, 30000.0], [200.0, 12000.0], [250.0, 8000.0]]
    reference = [[100.0, 999.0], [110.0, 800.0], [120.0, 650.0], [130.0, 500.0]]

    payload = mirror.build_mirror_payload(measured, reference, [], title="x", top_labels=4)

    sides = {label["side"] for label in payload["labels"]}
    assert sides == {"measured", "reference"}


# --------------------------------------------------------------------------
# 縦軸のスケール。MS-DIAL GUI は Relative / Absolute / Log10 / Sqrt を
# 上下独立に選ばせる（`ObservableMsSpectrum.CreateAxisPropertySelectors2`）。
# precursor がベースピークのスペクトルは Relative だと診断イオンが潰れる。
# --------------------------------------------------------------------------
def _payload():
    return mirror.build_mirror_payload(
        [[100.0, 20.0], [900.0, 999.0]], [[100.0, 25.0], [900.0, 999.0]],
        [{"mz": 100.0, "measured": 0.02, "reference": 0.025, "matched": True}], title="t")


def test_render_accepts_the_upstream_axis_scales():
    for scale in ("relative", "sqrt", "log10"):
        png = mirror.render_mirror(_payload(), scale=scale)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_defaults_to_relative():
    assert mirror.render_mirror(_payload()) == mirror.render_mirror(_payload(), scale="relative")


def test_render_rejects_an_unknown_scale():
    """黙って relative に落とすと「Sqrt で見ている」と誤認したまま読むことになる。"""
    with pytest.raises(ValueError):
        mirror.render_mirror(_payload(), scale="ln")


def test_sqrt_lifts_a_peak_that_relative_flattens():
    """precursor 優勢のスペクトルで小さな診断イオンが見える高さになること。"""
    assert mirror.scale_intensity(0.02, "relative") == pytest.approx(0.02)
    assert mirror.scale_intensity(0.02, "sqrt") == pytest.approx(0.1414213562)
    assert mirror.scale_intensity(0.02, "log10") > 0.02


def test_log10_floors_at_the_documented_decade_and_never_goes_negative():
    """0 と「0.1% 未満」は同じ高さ 0 に落とす（対数は下へ発散するため）。"""
    assert mirror.scale_intensity(0.0, "log10") == 0.0
    assert mirror.scale_intensity(1e-9, "log10") == 0.0
    assert mirror.scale_intensity(1.0, "log10") == pytest.approx(1.0)
