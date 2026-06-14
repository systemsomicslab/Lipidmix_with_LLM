import io
import tempfile
import unittest
from pathlib import Path
import zipfile

import lz4.block
import msgpack

from msdial_classes import (
    attach_class_ids_to_spots,
    decode_msdial_messagepack,
    discover_arf_class_index,
    filter_arf_by_class_ids,
    get_sample_class_id,
    parse_analysis_file_classes,
    resolve_mddata_path,
)


def pack_msdial(value) -> bytes:
    unpacked = msgpack.packb(value, use_bin_type=True)
    size_header = msgpack.packb(len(unpacked))
    compressed = lz4.block.compress(unpacked, store_size=False)
    extension = msgpack.ExtType(99, size_header + compressed)
    return msgpack.packb(extension, use_bin_type=True)


def analysis_file_row(file_id: int, name: str, class_id: str) -> list:
    return [
        f"C:/data/{name}.wiff",
        name,
        0,
        class_id,
        file_id + 1,
        file_id,
        True,
    ]


class MsdialClassTests(unittest.TestCase):
    def test_decode_and_parse_analysis_file_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dataset.mddata"
            storage = [[
                analysis_file_row(0, "sample_A", "control"),
                analysis_file_row(1, "sample_B", "treated"),
            ], [], [], [], [], None, None, None, None]
            path.write_bytes(pack_msdial(storage))

            self.assertEqual(decode_msdial_messagepack(path.read_bytes()), storage)
            records = parse_analysis_file_classes(path)
            self.assertEqual([record["class_id"] for record in records], ["control", "treated"])
            self.assertEqual([record["file_id"] for record in records], [0, 1])

    def test_resolve_mddata_from_mdproject_prefers_current_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            mddata = directory / "dataset.mddata"
            mddata.write_bytes(pack_msdial([[], [], [], [], [], None, None, None, None]))
            project_parameter = [None, None, "version", "C:/old/location", "dataset.mddata"]
            project = {"ProjectParameters": [project_parameter]}
            mdproject = directory / "project.mdproject"
            with zipfile.ZipFile(mdproject, "w") as archive:
                archive.writestr("Project", pack_msdial(project))

            self.assertEqual(resolve_mddata_path(mdproject), mddata.resolve())

    def test_attach_and_filter_variable_sample_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            arf_path = directory / "result_PeakProperties.arf"
            arf_path.touch()
            sample_count = 73
            rows = []
            analysis_files = []
            for file_id in range(sample_count):
                name = f"sample_{file_id}"
                class_id = "A" if file_id % 2 == 0 else "B"
                rows.append([file_id, name, 1000 + file_id])
                analysis_files.append(analysis_file_row(file_id, name, class_id))
            storage = [analysis_files, [], [], [], [], None, None, None, None]
            (directory / "dataset.mddata").write_bytes(pack_msdial(storage))
            features = [{"MasterAlignmentID": 1, "AlignedPeakProperties": rows}]

            index = discover_arf_class_index(arf_path)
            self.assertEqual(index["class_counts"], {"A": 37, "B": 36})
            attach_class_ids_to_spots(features, index)
            self.assertEqual(get_sample_class_id(features[0], 0, "sample_0"), "A")

            filtered, stats = filter_arf_by_class_ids(features, index, ["B"])
            self.assertEqual(len(filtered[0]["AlignedPeakProperties"]), 36)
            self.assertEqual(stats["after_sample_peaks"], 36)

    def test_file_id_and_file_name_mismatch_fails(self):
        class_index = {
            "by_file_id": {0: {"file_id": 0, "file_name": "sample_A", "class_id": "A"}},
            "by_file_name": {
                "sample_b": {"file_id": 1, "file_name": "sample_B", "class_id": "B"},
            },
            "records": [],
        }
        features = [{"AlignedPeakProperties": [[0, "sample_B", 10]]}]
        with self.assertRaisesRegex(ValueError, "different mddata samples"):
            attach_class_ids_to_spots(features, class_index)

    def test_unknown_class_id_fails(self):
        index = {
            "records": [{"class_id": "A"}],
            "by_file_id": {},
            "by_file_name": {},
        }
        with self.assertRaisesRegex(ValueError, "Unknown Class ID"):
            filter_arf_by_class_ids([], index, ["missing"])


if __name__ == "__main__":
    unittest.main()
