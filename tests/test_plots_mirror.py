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
