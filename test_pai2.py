from typing import BinaryIO
from enum import Enum
import io
import msgpack
import os
import argparse
import lz4.block
import pprint
import matplotlib.pyplot as plt
import glob


class IonMode(Enum):
    Positive = 0
    Negative = 1
    Both = 2


def deserialize_lz4_packed_msgpack(data: bytes) -> list | dict:
    """
    Deserialize LZ4 compressed MsgPack data.

    Args:
        data (bytes): The MessagePack formatted data which content is LZ4 compressed.

    Returns:
        list | dict: The deserialized data.
    """
    _header, compressed_data = msgpack.unpackb(data, raw=False)

    stream = io.BytesIO(compressed_data)
    unpacker = msgpack.Unpacker(stream, raw=False)
    size = next(unpacker)
    read_size = unpacker.tell()

    # Decompress the LZ4 data
    decompressed_data = lz4.block.decompress(compressed_data[read_size:], uncompressed_size=size)

    # Unpack the MsgPack data
    unpacked_data = msgpack.unpackb(decompressed_data, raw=False, strict_map_key=False)

    return unpacked_data


def _convert_to_times(data: list) -> dict:
    result = {}
    for t in data[:4]:
        if t[0] == 1:
            if t[1][0] >= 0:
                result["rt"] = t[1][0]
        elif t[0] == 2:
            if t[1][0] >= 0:
                result["ri"] = t[1][0]
        elif t[0] == 3:
            if t[1][0] >= 0:
                result["m/z"] = t[1][0]
        elif t[0] == 4:
            if t[1][0] >= 0:
                result["dt"] = t[1][0]
    return result


def _convert_to_peakfeature(data: list) -> dict:
    return {
        "time": _convert_to_times(data[4]),
        "time_left": _convert_to_times(data[3]),
        "time_right": _convert_to_times(data[5]),
        "peak_height": data[7],
        "peak_height_left": data[6],
        "peak_height_right": data[8],
        "peak_area": data[9],
        "peak_area_above_baseline": data[10],
        "m/z": data[43],
        "id": data[11],
        "ion_mode": IonMode(data[22]),
        "name": data[25],
        "formula": data[26][0],
        "ontology": data[27],
        "smiles": data[28],
        "inchikey": data[29],
        "adduct": data[30][2],
        "collision_cross_section": data[31],
        "comment": data[38],
    }




def deserialize(file: BinaryIO):
    """
    Deserialize a PAI2 file.

    Args:
        file (file like object): PAI2 file object to deserialize.

    Returns:
        list: The deserialized data.
    """
    packed_data = file.read()

    datas = deserialize_lz4_packed_msgpack(packed_data)
    return [_convert_to_peakfeature(data) for data in datas]



def test_pai2_deserialize_and_format():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    file_path = os.path.join(project_root, 'data', '0717_kinetex_wine_50_4min_pos_IDA_A1_202506271229.pai2')

    with open(file_path, 'rb') as f:
        packed_data = f.read()

    # io.BytesIO を使ってファイルオブジェクトをシミュレート
    file_like_object = io.BytesIO(packed_data)

    # デシリアライズと整形を実行
    deserialized_and_formatted_data = deserialize(file_like_object)

    # データがリスト型であることを確認
    assert isinstance(deserialized_and_formatted_data, list)
    # リストが空でないことを確認
    assert len(deserialized_and_formatted_data) > 0
    # リストの最初の要素が辞書型であることを確認
    assert isinstance(deserialized_and_formatted_data[0], dict)

    print("\n--- Deserialized and Formatted Data Sample ---")
    # 整形されたデータの一部をダンプ (最初の3つの要素)
    pprint.pprint(deserialized_and_formatted_data[:3])
    print("----------------------------------------------\n")

# 散布図実装
def plot_ms_map(features: list):
    rts = []
    mzs = []
    intensities = []

    for feat in features:
        rt = feat.get("time", {}).get("rt")
        mz = feat.get("m/z")
        height = feat.get("peak_height", 1)

        if rt is not None and mz is not None:
            rts.append(rt)
            mzs.append(mz)
            intensities.append(height)

    plt.figure(figsize=(12,8))

    scatter = plt.scatter(rts, mzs, c=intensities, cmap='viridis', alpha=0.6, edgecolors='none')
    plt.colorbar(scatter, label='Peak Height')
    plt.xlabel('Retention Time (min)')
    plt.ylabel('m/z')
    plt.title('MS Map')

    plt.show()

# --- 実行セクション ---
if __name__ == "__main__":
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    data_dir = os.path.join(project_root, 'data')
    pai2_files = glob.glob(os.path.join(data_dir, "*.pai2")) # ディレクトリ内の .pai2 ファイルを取得

    if not pai2_files:
        print(f"Error: .pai2 file not found in {data_dir}")
    else:
        # リストの最初に見つかったファイルを使用
        file_path = pai2_files[0]
        print(f"File found: {os.path.basename(file_path)}")

        with open(file_path, 'rb') as f:
            deserialized_data = deserialize(f)
            print(f"Total peaks extracted: {len(deserialized_data)}")
            plot_ms_map(deserialized_data)