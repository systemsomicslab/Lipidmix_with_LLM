"""対向プロット。座標は payload に持ち、描画は PNG で返す。"""
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
