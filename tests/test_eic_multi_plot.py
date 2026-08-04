import unittest

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from eic_plot import build_multi_compound_plot_payload, render_eic_plot


def _candidate(spot_id, name, ontology, rt, mz):
    return {
        "spot_id": spot_id, "name": name, "ontology": ontology,
        "adduct": "[M+H]+", "rt": rt, "mz": mz, "height_average": 1.0,
    }


def _spot(spot_id, rt, mz, file_id, points, peak_top, max_intensity):
    samples = []
    if file_id is not None:
        samples.append({
            "file_id": file_id,
            "peak_left": points[0][0],
            "peak_top": peak_top,
            "peak_right": points[-1][0],
            "num_points": len(points),
            "mean_intensity": sum(p[1] for p in points) / len(points),
            "max_intensity": max_intensity,
            "chromatogram": [list(point) for point in points],
        })
    return {
        "spot_id": spot_id, "rt": rt, "ri": 0.0, "mz": mz, "drift": -1.0,
        "main_type": 0, "num_samples": len(samples),
        "selected_samples": len(samples), "selected_points": len(points),
        "samples": samples,
    }


class MultiCompoundPayloadTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            _candidate(0, "PC(12:0/13:0)", "PC", 13.5, 636.4),
            _candidate(1, "Ceramide (d18:1/25:0)", "Cer", 22.1, 650.6),
        ]
        self.spots = [
            _spot(0, 13.5, 636.4, 7, [(13.4, 2.0), (13.5, 40.0), (13.6, 3.0)], 13.5, 40.0),
            _spot(1, 22.1, 650.6, 7, [(22.0, 5.0), (22.1, 90.0), (22.2, 4.0)], 22.1, 90.0),
        ]

    def _build(self, **overrides):
        candidates = overrides.pop("candidates", self.candidates)
        spots = overrides.pop("spots", self.spots)
        params = {
            "file_id": 7,
            "file_path": "alignment.EIC.aef",
            "arf2_path": "alignment.arf2",
            "queries": ["PC(12:0", "Ceramide"],
            "ontologies": [],
        }
        params.update(overrides)
        return build_multi_compound_plot_payload(candidates, spots, **params)

    def test_series_are_labelled_by_lipid_name_and_sorted_by_rt(self):
        payload = self._build()
        self.assertEqual(payload["plot_schema"], "lipidmix.eic.multi.v1")
        self.assertEqual(payload["plot_type"], "line")
        self.assertEqual(payload["sample"]["file_id"], 7)
        self.assertEqual(
            [item["label"] for item in payload["series"]],
            ["PC(12:0/13:0)", "Ceramide (d18:1/25:0)"],
        )
        self.assertEqual([item["spot_id"] for item in payload["series"]], [0, 1])
        self.assertEqual(payload["axes"]["x"]["unit"], "min")
        self.assertEqual(payload["axes"]["y"]["label"], "Intensity")

    def test_annotation_sits_on_the_apex_data_point(self):
        payload = self._build()
        annotation = payload["series"][1]["annotation"]
        self.assertEqual(annotation["text"], "Ceramide (d18:1/25:0) / 22.100")
        self.assertAlmostEqual(annotation["x"], 22.1, places=5)
        self.assertAlmostEqual(annotation["y"], 90.0, places=5)

    def test_annotation_y_follows_normalization(self):
        payload = self._build(normalize="per_trace_max")
        self.assertEqual(payload["axes"]["y"]["label"], "Relative intensity")
        self.assertAlmostEqual(payload["series"][1]["annotation"]["y"], 1.0, places=5)

    def test_rt_mismatch_is_dropped_with_reason(self):
        candidates = [self.candidates[0], _candidate(1, "Ceramide", "Cer", 30.0, 650.6)]
        payload = build_multi_compound_plot_payload(
            candidates, self.spots, file_id=7,
            file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
        )
        self.assertEqual([item["spot_id"] for item in payload["series"]], [0])
        self.assertEqual(
            payload["selection"]["dropped"],
            [{"spot_id": 1, "name": "Ceramide", "reason": "rt_mismatch"}],
        )
        self.assertTrue(any("rt_mismatch" in note for note in payload["caveats"]))

    def test_missing_spot_and_missing_file_id_are_distinguished(self):
        candidates = self.candidates + [_candidate(2, "TG(x)", "TG", 25.0, 800.0)]
        spots = self.spots + [_spot(2, 25.0, 800.0, None, [(25.0, 1.0)], 25.0, 1.0)]
        payload = build_multi_compound_plot_payload(
            candidates + [_candidate(9, "DG(y)", "DG", 9.0, 500.0)],
            spots, file_id=7,
            file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
        )
        reasons = {item["spot_id"]: item["reason"] for item in payload["selection"]["dropped"]}
        self.assertEqual(reasons[2], "file_id_absent")
        self.assertEqual(reasons[9], "spot_out_of_range")

    def test_top_n_keeps_strongest_and_records_the_rest(self):
        payload = self._build(top_n=1)
        self.assertEqual([item["spot_id"] for item in payload["series"]], [1])
        self.assertEqual(
            payload["selection"]["dropped"],
            [{"spot_id": 0, "name": "PC(12:0/13:0)", "reason": "below_top_n"}],
        )
        self.assertEqual(payload["selection"]["plotted"], 1)
        self.assertEqual(payload["selection"]["candidates"], 2)
        self.assertEqual(payload["selection"]["top_n"], 1)

    def test_unknown_name_falls_back_to_spot_and_mz_label(self):
        candidates = [_candidate(0, "Unknown", "", 13.5, 636.4)]
        payload = build_multi_compound_plot_payload(
            candidates, self.spots[:1], file_id=7,
            file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
        )
        self.assertEqual(payload["series"][0]["label"], "spot 0 (m/z 636.4000)")

    def test_no_verified_candidate_raises(self):
        candidates = [_candidate(0, "PC", "PC", 99.0, 636.4)]
        with self.assertRaisesRegex(ValueError, "rt_mismatch"):
            build_multi_compound_plot_payload(
                candidates, self.spots[:1], file_id=7,
                file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
            )

    def test_invalid_arguments_raise(self):
        with self.assertRaisesRegex(ValueError, "normalize"):
            self._build(normalize="zscore")
        with self.assertRaisesRegex(ValueError, "top_n"):
            self._build(top_n=0)


class MultiCompoundRenderTests(unittest.TestCase):
    def setUp(self):
        candidates = [
            _candidate(0, "PC(12:0/13:0)", "PC", 13.5, 636.4),
            _candidate(1, "Ceramide (d18:1/25:0)", "Cer", 22.1, 650.6),
        ]
        spots = [
            _spot(0, 13.5, 636.4, 7, [(13.4, 2.0), (13.5, 40.0), (13.6, 3.0)], 13.5, 40.0),
            _spot(1, 22.1, 650.6, 7, [(22.0, 5.0), (22.1, 90.0), (22.2, 4.0)], 22.1, 90.0),
        ]
        self.payload = build_multi_compound_plot_payload(
            candidates, spots, file_id=7,
            file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
        )

    def test_renders_one_line_and_one_annotation_per_series(self):
        fig = render_eic_plot(self.payload)
        try:
            ax = fig.axes[0]
            self.assertEqual(len(ax.get_lines()), 2)
            texts = [annotation.get_text() for annotation in ax.texts]
            self.assertIn("Ceramide (d18:1/25:0) / 22.100", texts)
            self.assertEqual(ax.get_xlabel(), "RT (min)")
        finally:
            plt.close(fig)

    def test_annotations_can_be_switched_off(self):
        self.payload["render_hints"]["show_annotations"] = False
        fig = render_eic_plot(self.payload)
        try:
            self.assertEqual(list(fig.axes[0].texts), [])
        finally:
            plt.close(fig)

    def test_unknown_schema_raises(self):
        with self.assertRaisesRegex(ValueError, "schema"):
            render_eic_plot({"plot_schema": "lipidmix.eic.v99", "series": []})


class EicPlotCompoundsToolTests(unittest.TestCase):
    def setUp(self):
        import os
        import struct
        import tempfile
        from pathlib import Path

        import mcp_core
        import session_state

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "alignment.EIC.aef"
        self.arf2 = self.tmp / "alignment.arf2"
        self.arf2.write_bytes(b"placeholder")

        from tests.test_eic_plot import _write_css1

        _write_css1(self.path, [
            {"rt": 13.5, "mz": 636.4, "samples": [{
                "file_id": 7, "peak_top": 13.5, "peak_left": 13.4,
                "peak_right": 13.6,
                "points": [(13.4, 2.0), (13.5, 40.0), (13.6, 3.0)],
            }]},
            {"rt": 22.1, "mz": 650.6, "samples": [{
                "file_id": 7, "peak_top": 22.1, "peak_left": 22.0,
                "peak_right": 22.2,
                "points": [(22.0, 5.0), (22.1, 90.0), (22.2, 4.0)],
            }]},
        ])

        self.records = [
            {"AlignmentID": 0, "Name": "PC(12:0/13:0)", "Ontology": "PC",
             "AdductType": "[M+H]+", "RT": 13.5, "MassCenter": 636.4,
             "HeightAverage": 10.0},
            {"AlignmentID": 1, "Name": "Ceramide (d18:1/25:0)", "Ontology": "Cer",
             "AdductType": "[M+H]+", "RT": 22.1, "MassCenter": 650.6,
             "HeightAverage": 20.0},
        ]

        self._saved_data_dir = mcp_core.DATA_DIR
        self._saved_reports = os.environ.get("LIPIDMIX_REPORTS_DIR")
        self._saved_plot = session_state.session.last_eic_plot
        mcp_core.DATA_DIR = self.tmp
        os.environ["LIPIDMIX_REPORTS_DIR"] = str(self.tmp / "fallback")
        session_state.session.last_eic_plot = None

        import tools_eic

        self._saved_loader = tools_eic.load_arf2_records
        tools_eic.load_arf2_records = lambda _path: self.records

    def tearDown(self):
        import os

        import mcp_core
        import session_state
        import tools_eic

        tools_eic.load_arf2_records = self._saved_loader
        mcp_core.DATA_DIR = self._saved_data_dir
        session_state.session.last_eic_plot = self._saved_plot
        if self._saved_reports is None:
            os.environ.pop("LIPIDMIX_REPORTS_DIR", None)
        else:
            os.environ["LIPIDMIX_REPORTS_DIR"] = self._saved_reports
        self._tmp.cleanup()

    def test_returns_multi_payload_and_writes_no_png(self):
        import server
        import session_state

        payload = server.eic_plot_compounds(
            7, names=["ceramide"], ontologies=["PC"],
            file_path=str(self.path), arf2_path=str(self.arf2),
        )
        self.assertEqual(payload["plot_schema"], "lipidmix.eic.multi.v1")
        self.assertEqual(
            [item["label"] for item in payload["series"]],
            ["PC(12:0/13:0)", "Ceramide (d18:1/25:0)"],
        )
        self.assertEqual(payload["sample"]["file_id"], 7)
        self.assertIs(session_state.session.last_eic_plot, payload)
        self.assertEqual(list(self.tmp.rglob("*.png")), [])

    def test_no_query_raises(self):
        import server

        with self.assertRaisesRegex(ValueError, "names"):
            server.eic_plot_compounds(
                7, file_path=str(self.path), arf2_path=str(self.arf2),
            )

    def test_query_without_any_arf2_hit_raises(self):
        import server

        with self.assertRaisesRegex(ValueError, "一致"):
            server.eic_plot_compounds(
                7, names=["no-such-lipid"],
                file_path=str(self.path), arf2_path=str(self.arf2),
            )

    def test_save_eic_figure_accepts_the_multi_payload(self):
        import server

        server.eic_plot_compounds(
            7, ontologies=["PC", "Cer"],
            file_path=str(self.path), arf2_path=str(self.arf2),
        )
        message = server.save_eic_figure("overlay-eic")
        png = self.tmp / "reports" / "figures" / "overlay-eic_eic.png"
        self.assertTrue(png.is_file())
        self.assertIn("figures/overlay-eic_eic.png", message)

    def test_fastmcp_exposes_output_schema(self):
        import asyncio

        import server

        tools = asyncio.run(server.mcp.list_tools())
        tool = next(item for item in tools if item.name == "eic_plot_compounds")
        self.assertIsNotNone(tool.outputSchema)
        self.assertIn("plot_schema", tool.outputSchema.get("properties", {}))


if __name__ == "__main__":
    unittest.main()
