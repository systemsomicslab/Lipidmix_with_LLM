import asyncio
import os
import struct
import tempfile
import unittest
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import mcp_core
import server
import session_state
from eic_aef_reader import read_eic_spot_css1


def _write_css1(path: Path, spots: list[dict]) -> None:
    chunks = []
    for spot in spots:
        chunk = bytearray(struct.pack(
            "<ffffbi",
            spot.get("rt", 0.0), spot.get("ri", 0.0), spot.get("mz", 0.0),
            spot.get("drift", 0.0), spot.get("main_type", 0), len(spot["samples"]),
        ))
        for sample in spot["samples"]:
            points = sample["points"]
            chunk.extend(struct.pack(
                "<iifff", sample["file_id"], len(points), sample["peak_top"],
                sample["peak_left"], sample["peak_right"],
            ))
            for x, y in points:
                chunk.extend(struct.pack("<ff", x, y))
        chunks.append(bytes(chunk))

    header_size = 14 + 8 * len(chunks)
    offsets = []
    cursor = header_size
    for chunk in chunks:
        offsets.append(cursor)
        cursor += len(chunk)

    body = bytearray(b"CSS1" + b"\x00" * 6)
    body.extend(struct.pack("<i", len(chunks)))
    for offset in offsets:
        body.extend(struct.pack("<q", offset))
    for chunk in chunks:
        body.extend(chunk)
    path.write_bytes(body)


class EicRandomAccessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "alignment.EIC.aef"
        _write_css1(self.path, [
            {"rt": 1.0, "mz": 100.0, "samples": [{
                "file_id": 1, "peak_top": 1.0, "peak_left": 0.9,
                "peak_right": 1.1, "points": [(0.9, 10.0), (1.0, 20.0)],
            }]},
            {"rt": 2.0, "mz": 200.0, "samples": [
                {"file_id": 10, "peak_top": 2.0, "peak_left": 1.8,
                 "peak_right": 2.2,
                 "points": [(1.8, 2.0), (2.0, 8.0), (2.2, 3.0)]},
                {"file_id": 11, "peak_top": 2.1, "peak_left": 1.9,
                 "peak_right": 2.3, "points": [(1.9, 4.0), (2.1, 12.0)]},
            ]},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_one_spot_and_selected_file_id(self):
        spot = read_eic_spot_css1(self.path, 1, file_ids=[11])
        self.assertEqual(spot["spot_id"], 1)
        self.assertAlmostEqual(spot["mz"], 200.0)
        self.assertEqual(spot["selected_samples"], 1)
        self.assertEqual(spot["selected_points"], 2)
        self.assertEqual(spot["samples"][0]["file_id"], 11)
        self.assertAlmostEqual(spot["samples"][0]["peak_top"], 2.1, places=5)
        self.assertAlmostEqual(spot["samples"][0]["chromatogram"][0][0], 1.9, places=5)
        self.assertAlmostEqual(spot["samples"][0]["chromatogram"][1][0], 2.1, places=5)
        self.assertEqual(
            [point[1] for point in spot["samples"][0]["chromatogram"]],
            [4.0, 12.0],
        )

    def test_rejects_missing_file_id_and_out_of_range_spot(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            read_eic_spot_css1(self.path, 1, file_ids=[999])
        with self.assertRaisesRegex(ValueError, "out of range"):
            read_eic_spot_css1(self.path, 2)

    def test_requires_selection_when_trace_limit_is_exceeded(self):
        crowded = Path(self._tmp.name) / "crowded.EIC.aef"
        samples = [{
            "file_id": index, "peak_top": 1.0, "peak_left": 0.9,
            "peak_right": 1.1, "points": [(1.0, float(index))],
        } for index in range(13)]
        _write_css1(crowded, [{"rt": 1.0, "mz": 100.0, "samples": samples}])
        with self.assertRaisesRegex(ValueError, "Specify file_ids"):
            read_eic_spot_css1(crowded, 0)


class EicPlotToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "alignment.EIC.aef"
        _write_css1(self.path, [{
            "rt": 3.25, "mz": 512.3456, "samples": [{
                "file_id": 7, "peak_top": 3.25, "peak_left": 3.1,
                "peak_right": 3.4,
                "points": [(3.1, 5.0), (3.25, 10.0), (3.4, 2.0)],
            }],
        }])
        self._saved_data_dir = mcp_core.DATA_DIR
        self._saved_reports = os.environ.get("LIPIDMIX_REPORTS_DIR")
        self._saved_plot = session_state.session.last_eic_plot
        mcp_core.DATA_DIR = self.tmp
        os.environ["LIPIDMIX_REPORTS_DIR"] = str(self.tmp / "fallback")
        session_state.session.last_eic_plot = None

    def tearDown(self):
        mcp_core.DATA_DIR = self._saved_data_dir
        session_state.session.last_eic_plot = self._saved_plot
        if self._saved_reports is None:
            os.environ.pop("LIPIDMIX_REPORTS_DIR", None)
        else:
            os.environ["LIPIDMIX_REPORTS_DIR"] = self._saved_reports
        self._tmp.cleanup()

    def test_returns_structured_plot_data_without_writing_png(self):
        payload = server.eicaef_plot_chromatograms(
            0, file_path=str(self.path), file_ids=[7], normalize="per_trace_max",
        )
        self.assertEqual(payload["plot_schema"], "lipidmix.eic.v1")
        self.assertEqual(payload["plot_type"], "line")
        self.assertEqual(payload["axes"]["x"]["unit"], "min")
        for actual, expected in zip(payload["series"][0]["x"], [3.1, 3.25, 3.4]):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertEqual(payload["series"][0]["y"], [0.5, 1.0, 0.2])
        self.assertIs(session_state.session.last_eic_plot, payload)
        self.assertEqual(list(self.tmp.rglob("*.png")), [])

    def test_fastmcp_exposes_output_schema(self):
        tools = asyncio.run(server.mcp.list_tools())
        tool = next(item for item in tools if item.name == "eicaef_plot_chromatograms")
        self.assertIsNotNone(tool.outputSchema)
        self.assertIn("plot_schema", tool.outputSchema.get("properties", {}))

    def test_png_is_written_only_by_explicit_save_tool(self):
        server.eicaef_plot_chromatograms(0, file_path=str(self.path), file_ids=[7])
        message = server.save_eic_figure("sample-eic")
        png = self.tmp / "reports" / "figures" / "sample-eic_eic.png"
        self.assertTrue(png.is_file())
        self.assertIn("figures/sample-eic_eic.png", message)

    def test_save_tool_guides_when_plot_is_missing(self):
        message = server.save_eic_figure("sample-eic")
        self.assertIn("eicaef_plot_chromatograms", message)
        self.assertEqual(list(self.tmp.rglob("*.png")), [])


if __name__ == "__main__":
    unittest.main()
