# test_arf2.py
import io
import msgpack
import lz4.block
import pandas as pd
import numpy as np
from enum import Enum
from typing import List, Optional, Dict
import os
import glob
import sys
import pprint

class IonMode(Enum):
    Positive = 0
    Negative = 1
    Both = 2
    Unknown = -1

    @classmethod
    def from_int(cls, val: int) -> str:
        try:
            return cls(val).name
        except ValueError:
            return cls.Unknown.name

def _decode(val) -> str:
    if isinstance(val, bytes):
        return val.decode('utf-8', errors='ignore')
    elif isinstance(val, str):
        return val
    return ""

def _extract_times(data: list) -> dict:
    result = {}
    if not isinstance(data, list):
        return result
        
    for t in data[:4]:
        if isinstance(t, list) and len(t) >= 2 and isinstance(t[1], (list, tuple)) and len(t[1]) > 0:
            if t[0] == 1: result["rt"] = float(t[1][0])
            elif t[0] == 2: result["ri"] = float(t[1][0])
            elif t[0] == 3: result["m_z"] = float(t[1][0])
            elif t[0] == 4: result["dt"] = float(t[1][0])
    return result

def deserialize_lz4_packed_msgpack(data: bytes) -> list:
    _header, compressed_data = msgpack.unpackb(data, raw=False)
    stream = io.BytesIO(compressed_data)
    unpacker = msgpack.Unpacker(stream, raw=False)
    size = next(unpacker)
    read_size = unpacker.tell()
    decompressed_data = lz4.block.decompress(compressed_data[read_size:], uncompressed_size=size)

    all_spots_raw = []
    inner_unpacker = msgpack.Unpacker(io.BytesIO(decompressed_data), raw=False, strict_map_key=False)
    for item in inner_unpacker:
        all_spots_raw.append(item)
    
    return all_spots_raw

def extract_arf2_data(data: list) -> Optional[dict]:
    """1つのスポットデータ(.arf2形式)からカタログ情報を抽出する"""
    if not isinstance(data, list) or len(data) < 10:
        return None

    def get(index, default=None):
        return data[index] if len(data) > index else default

    times = _extract_times(get(4)) if isinstance(get(4), list) else {}
    
    return {
        "MasterAlignmentID": int(get(0) or 0),
        "RT": float(times.get("rt", 0.0)), 
        "MassCenter": float(get(5) or 0.0),
        "IonMode": IonMode.from_int(get(11)),
        "Name": _decode(get(12)) or "Unknown",
        "HeightAverage": float(get(31) or 0.0)
    }

def deserialize(file_like_object) -> List[dict]:
    """バイナリストリームから .arf2 データをパースして辞書のリストを返す"""
    datas = deserialize_lz4_packed_msgpack(file_like_object.read())
    results = []
    
    for d in datas:
        if isinstance(d, list) and len(d) < 14:
            for item in d:
                if isinstance(item, list) and len(item) > 0:
                    for spot_raw in item:
                        formatted = extract_arf2_data(spot_raw)
                        if formatted:
                            results.append(formatted)
            continue
            
        formatted = extract_arf2_data(d)
        if formatted:
            results.append(formatted)
            
    return results

def summarize_arf2_data(deserialized_list: List[dict]) -> dict:
    """ARF2データの全体像（メタデータ）の統計的要約を生成"""
    if not deserialized_list:
        return {"error": "データがありません。"}

    df = pd.DataFrame(deserialized_list)
    
    summary = {
        "total_spots": len(df)
    }
    
    if 'HeightAverage' in df.columns:
        heights = df[df['HeightAverage'] > 0]['HeightAverage']
        summary["height_average_median"] = float(heights.median()) if not heights.empty else 0.0
        summary["height_average_max"] = float(heights.max()) if not heights.empty else 0.0
        
    if 'RT' in df.columns:
        summary["rt_range"] = (float(df['RT'].min()), float(df['RT'].max()))
        
    if 'MassCenter' in df.columns:
        summary["mass_range"] = (float(df['MassCenter'].min()), float(df['MassCenter'].max()))
        
    if 'IonMode' in df.columns:
        # IonModeの文字列表現をカウント
        summary["ion_modes"] = df['IonMode'].apply(lambda x: x.name if isinstance(x, IonMode) else str(x)).value_counts().to_dict()
        
    if 'Name' in df.columns:
        # アノテーション済み（Unknown または 空白 でない）の数をカウント
        summary["annotated_count"] = int(df['Name'].apply(lambda x: 1 if x and str(x).lower() != "unknown" and str(x).strip() != "" else 0).sum())
        summary["annotation_rate"] = float(summary["annotated_count"] / len(df) * 100) if len(df) > 0 else 0.0
        
    return summary

def generate_text_summary(deserialized_list: List[dict]) -> str:
    """LLMが読みやすい自然言語での要約を生成"""
    summary = summarize_arf2_data(deserialized_list)
    if "error" in summary: return summary["error"]
    
    text = (
        f"### ARF2 カタログ要約\n"
        f"- **総スポット（ピーク）数**: {summary.get('total_spots', 0):,} 個\n"
    )
    
    annotated = summary.get('annotated_count', 0)
    rate = summary.get('annotation_rate', 0.0)
    text += f"- **アノテーション済み**: {annotated:,} 個 ({rate:.1f}%)\n"
    
    if summary.get('rt_range'):
        text += f"- **保持時間(RT)範囲**: {summary['rt_range'][0]:.2f} 〜 {summary['rt_range'][1]:.2f} min\n"
    if summary.get('mass_range'):
        text += f"- **質量(m/z)範囲**: {summary['mass_range'][0]:.4f} 〜 {summary['mass_range'][1]:.4f}\n"
    if summary.get('height_average_median'):
        text += f"- **平均強度の代表値(中央値)**: {summary['height_average_median']:,.1f}\n"
    if summary.get('ion_modes'):
        modes = ", ".join([f"{k}: {v:,}" for k, v in summary['ion_modes'].items()])
        text += f"- **検出イオンモード**: {modes}\n"
        
    return text

def find_input_file(file_path: str | None = None, index: int = 0) -> str | None:
    if file_path and os.path.exists(file_path):
        return file_path

    project_root = os.path.dirname(__file__)
    data_dir = os.path.join(project_root, "data")

    # .arf2 ファイルのみを検索
    candidates = glob.glob(os.path.join(data_dir, "*.arf2"))
    return candidates[index] if candidates else None

if __name__ == "__main__":
    file_path = find_input_file()

    if not file_path:
        print(".arf2 ファイルが見つかりません。")
        sys.exit(1)

    print(f"Loading {file_path}...")
    with open(file_path, 'rb') as f:
        deserialized_data = deserialize(f)
    
    if deserialized_data:
        text_summary = generate_text_summary(deserialized_data)
        print("\n" + text_summary)