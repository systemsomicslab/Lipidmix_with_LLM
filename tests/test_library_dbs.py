"""`.dbs` / `.lbm2` リーダ。fixture はテスト自身が組む（実データに依存しない）。"""
import struct
import zipfile

import lz4.block
import msgpack
import pytest

from lipidmix.library import dbs


def _pack_chunk(records: list[list]) -> bytes:
    """LargeListMessagePack のチャンク 1 個を組む。

    要素数に応じて array ヘッダが短形式になる点を再現する（本番の罠）。
    要素は常にオフセット 5 から始まる。
    """
    body = b"".join(msgpack.packb(r, use_bin_type=True) for r in records)
    n = len(records)
    if n < 16:
        header = bytes([0x90 | n])
    elif n < 65536:
        header = b"\xdc" + struct.pack(">H", n)
    else:
        header = b"\xdd" + struct.pack(">I", n)
    raw = header + b"\x00" * (5 - len(header)) + body
    comp = lz4.block.compress(raw, store_size=False)
    return (b"\xc9" + struct.pack(">I", len(comp) + 5) + b"\x63"
            + b"\xd2" + struct.pack(">i", len(raw)) + comp)


def _record(name="GABA", mz=104.0706, ion_mode=1, peaks=((87.04, 999.0), (69.03, 500.0))):
    r = [None] * 29
    r[0] = 0
    r[1] = mz
    r[2] = [[1, [1.23, 0, 0]]]
    r[3] = ion_mode
    r[4] = [[m, i] + [None] * 4 + [0, 0] + [None] * 3 + [0, False] for m, i in peaks]
    r[5] = name
    r[6] = None
    r[7] = ""
    r[8] = "NCCCC(=O)O"
    r[9] = "BTCSSZJGUNDROE-UHFFFAOYSA-N"
    r[10] = [1.00782503207, 1, "[M+H]+", 1, 1, True, 0.0, 0.0, False, False]
    r[14] = "AminoAcid"
    return r


def test_a_short_array_header_is_read_correctly(tmp_path):
    """要素数が小さいと array ヘッダが短形式になる。常に 5 バイトと仮定すると壊れる。"""
    chunk = _pack_chunk([_record()] * 3)
    dec = next(dbs.iter_decompressed_chunks(chunk))
    assert dbs.read_array_count(dec) == 3


def test_records_come_back_from_a_multi_chunk_lbm2(tmp_path):
    path = tmp_path / "lib.lbm2"
    path.write_bytes(_pack_chunk([_record(name="A")] * 2) + _pack_chunk([_record(name="B")]))

    records = list(dbs.iter_records(path))

    assert [r["name"] for r in records] == ["A", "A", "B"]
    assert records[0]["precursor_mz"] == pytest.approx(104.0706)
    assert records[0]["ion_mode"] == "negative"      # IonMode=1
    assert records[0]["adduct"] == "[M+H]+"
    assert records[0]["inchikey"] == "BTCSSZJGUNDROE-UHFFFAOYSA-N"
    assert records[0]["compound_class"] == "AminoAcid"
    assert records[0]["spectrum"] == [[87.04, 999.0], [69.03, 500.0]]


def test_a_dbs_zip_is_read_from_its_database_entry(tmp_path):
    path = tmp_path / "Project_Loaded.msp2.dbs"
    storage = {"MetabolomicsDataBases": [{"DataBase": ["mylib", 4, 2, "C:/x/mylib.lbm2"],
                                          "Pairs": [[0, {"SerializableAnnotatorKey":
                                                         [3, {"Parameter": [0.0, 2000.0, 2.0, 100.0, 20.0,
                                                                            0.01, 0.025, 0.0, 0.0, 0.0225,
                                                                            0.0225, 0.09, 0.0, 0.8, 1.0,
                                                                            True, True, False, False, 0.1],
                                                              "SourceType": 4, "Key": "mylib_1",
                                                              "Priority": 1}]}]]}]}
    packed = msgpack.packb(storage, use_bin_type=True)
    comp = lz4.block.compress(packed, store_size=False)
    wrapped = (b"\xc9" + struct.pack(">I", len(comp) + 5) + b"\x63"
               + b"\xd2" + struct.pack(">i", len(packed)) + comp)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("MetabolomicsDB/mylib/DataBase", _pack_chunk([_record(name="Z")]))
        z.writestr("MetabolomicsDB/MS-FINDER/DataBase", b"")
        z.writestr("Storage", wrapped)

    assert [r["name"] for r in dbs.iter_records(path)] == ["Z"]

    meta = dbs.read_storage_meta(path)
    assert meta["library_name"] == "mylib"
    assert meta["source_path"] == "C:/x/mylib.lbm2"
    assert meta["search_params"]["ms2_tolerance"] == pytest.approx(0.025)
    assert meta["search_params"]["mass_range_end"] == pytest.approx(2000.0)
    assert meta["search_params"]["rt_tolerance"] == pytest.approx(2.0)
    # Key 7 / 8。採点前の足切りで、MS-DIAL の正規化の唯一の可変部分（Task 1）。
    assert meta["search_params"]["relative_amp_cutoff"] == pytest.approx(0.0)
    assert meta["search_params"]["absolute_amp_cutoff"] == pytest.approx(0.0)


def test_an_empty_msfinder_entry_is_not_mistaken_for_the_database(tmp_path):
    """MS-FINDER エントリは空で出る。これを DataBase と取り違えない。"""
    path = tmp_path / "P_Loaded.msp2.dbs"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("MetabolomicsDB/MS-FINDER/DataBase", b"")
        z.writestr("MetabolomicsDB/real/DataBase", _pack_chunk([_record(name="R")]))
    assert [r["name"] for r in dbs.iter_records(path)] == ["R"]
