import io
import unittest

import lz4.block
import msgpack

import arf_reader


def make_peak_row(file_id: int, sample_name: str, height: float, mz: float, rt: float) -> list:
    row = [None] * 38
    row[0] = file_id
    row[1] = sample_name
    row[2] = file_id + 100
    row[3] = file_id + 1000
    row[10] = {}
    row[15] = [[1, [rt]], [3, [mz]]]
    row[18] = height
    row[20] = height * 2
    row[21] = height * 1.5
    row[22] = mz
    row[23] = 0
    row[24] = f"PC {mz:.4f}"
    row[37] = [1.0, 20.0]
    return row


def make_group(seed: int) -> list:
    mz = 700.0 + seed
    rt = 3.0 + seed / 10
    return [
        make_peak_row(0, "sample_A", 1000.0 + seed, mz, rt),
        make_peak_row(1, "sample_B", 1200.0 + seed, mz, rt),
        make_peak_row(2, "sample_C", 1400.0 + seed, mz, rt),
    ]


def pack_payload(inner_objects: list) -> bytes:
    inner_stream = b"".join(
        msgpack.packb(item, use_bin_type=True) for item in inner_objects
    )
    return msgpack.packb(len(inner_stream), use_bin_type=True) + lz4.block.compress(
        inner_stream,
        store_size=False,
    )


def pack_legacy_block(inner_objects: list) -> bytes:
    return msgpack.packb(["header", pack_payload(inner_objects)], use_bin_type=True)


def pack_ext_block(inner_objects: list) -> bytes:
    ext = msgpack.ExtType(99, pack_payload(inner_objects))
    return msgpack.packb(ext, use_bin_type=True)


class ArfMultiblockTests(unittest.TestCase):
    def test_deserialize_legacy_single_block_container(self):
        data = pack_legacy_block([[0, 0, make_group(1), make_group(2)]])

        features = arf_reader.deserialize(io.BytesIO(data))

        self.assertEqual(len(features), 2)
        self.assertEqual([spot["MasterAlignmentID"] for spot in features], [0, 1])
        self.assertEqual([spot["SourceBlockIndex"] for spot in features], [0, 0])
        self.assertEqual(features[0]["SourceLocalIndex"], 0)
        self.assertEqual(features[0]["MassCenter"], 701.0)
        self.assertEqual(len(features[0]["AlignedPeakProperties"]), 3)

    def test_deserialize_concatenated_exttype_blocks(self):
        data = (
            pack_ext_block([[0, 0, make_group(1), make_group(2)]])
            + pack_ext_block([[0, 0, make_group(3)]])
        )

        features = arf_reader.deserialize(io.BytesIO(data))

        self.assertEqual(len(features), 3)
        self.assertEqual([spot["MasterAlignmentID"] for spot in features], [0, 1, 2])
        self.assertEqual([spot["SourceBlockIndex"] for spot in features], [0, 0, 1])
        self.assertEqual([spot["SourceLocalIndex"] for spot in features], [0, 1, 0])
        self.assertEqual([spot["MassCenter"] for spot in features], [701.0, 702.0, 703.0])

    def test_deserialize_lz4_packed_msgpack_returns_raw_inner_objects(self):
        data = pack_ext_block([[0, 0, make_group(1)]]) + pack_ext_block([make_group(2)])

        raw = arf_reader.deserialize_lz4_packed_msgpack(data)

        self.assertEqual(len(raw), 2)
        self.assertTrue(arf_reader._is_arf_feature_container_object(raw[0]))
        self.assertEqual(raw[1][0][24], "PC 702.0000")


if __name__ == "__main__":
    unittest.main()
