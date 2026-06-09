import argparse
import glob
import io
import os
from enum import Enum
import sys
import re

import lz4.block
import msgpack
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
import matplotlib
matplotlib.use('Agg')  # 非インタラクティブバックエンドを使用
import matplotlib.pyplot as plt
import pprint


class IonMode(Enum):
    Positive = 0
    Negative = 1
    Both = 2
    Unknown = -1

    @classmethod
    def from_int(cls, val):
        try:
            return cls(val).name
        except Exception:
            return cls.Unknown.name


def _convert_to_times(data: list) -> dict:
    result = {}
    for t in data[:4]:
        if t[0] == 1 and isinstance(t[1], (list, tuple)) and len(t[1]) > 0:
            result["rt"] = t[1][0]
        elif t[0] == 2 and isinstance(t[1], (list, tuple)) and len(t[1]) > 0:
            result["ri"] = t[1][0]
        elif t[0] == 3 and isinstance(t[1], (list, tuple)) and len(t[1]) > 0:
            result["m/z"] = t[1][0]
        elif t[0] == 4 and isinstance(t[1], (list, tuple)) and len(t[1]) > 0:
            result["dt"] = t[1][0]
    return result


def _decode(value):
    return value.decode('utf-8', errors='ignore') if isinstance(value, bytes) else value


def _is_arf_feature_container(datas) -> bool:
    if not isinstance(datas, list) or len(datas) < 1:
        return False
    first = datas[0]
    if not isinstance(first, list) or len(first) < 10:
        return False
    if not isinstance(first[0], int) or not isinstance(first[1], int):
        return False

    nested_groups = [item for item in first[2:] if isinstance(item, list) and len(item) >= 3 and all(isinstance(sub, list) for sub in item)]
    if nested_groups:
        sample_group = nested_groups[0]
        print(f"[INFO] Detected ARF format: peak groups have {len(sample_group)} elements", file=sys.stderr)

    return len(nested_groups) >= max(10, len(first[2:]) // 2)


def _convert_arf_peak_group(group: list, group_index: int | None = None) -> dict | None:
    if not isinstance(group, list) or len(group) < 3 or not all(isinstance(row, list) for row in group):
        return None

    representative = group[0]
    times = _convert_to_times(representative[15]) if len(representative) > 15 and isinstance(representative[15], list) else {}

    return {
        "MasterAlignmentID": group_index,
        "AlignmentID": group_index,
        "RT": times.get("rt", None),
        "MassCenter": representative[22] if len(representative) > 22 else None,
        "IonMode": IonMode.from_int(representative[23]) if len(representative) > 23 else IonMode.Unknown.name,
        "Name": _decode(representative[24]) if len(representative) > 24 else None,
        "HeightAverage": representative[18] if len(representative) > 18 else None,
        "AlignedPeakProperties": group,
    }

def make_loading_details(pca_result: dict, deserialized_list: list[dict], feature_names: list[str]) -> str:
    """
    PCAのloadings情報を詳細化して、書き出し用のJSON文字列を返す。

    戻り値: JSON文字列（基本部分はindent=2で整形、loadingsは各アイテムを1行で保持）
    """
    import json

    detailed_loadings = []
    for pc_idx, pc_loadings in enumerate(pca_result.get("loadings", [])):
        pc_loadings_list = []
        for feat_idx, loading_value in enumerate(pc_loadings):
            feat_name = feature_names[feat_idx]
            parts = feat_name.split("_")
            if len(parts) >= 3:
                try:
                    master_id = int(parts[1])
                except Exception:
                    master_id = None

                if master_id is not None and master_id < len(deserialized_list):
                    spot = deserialized_list[master_id]
                    annotation = spot.get("Name", "")
                    m_z = spot.get("MassCenter")
                    rt = spot.get("RT")
                else:
                    annotation = ""
                    m_z = None
                    rt = None

                pc_loadings_list.append({
                    "id": master_id,
                    "value": loading_value,
                    "annotation": annotation,
                    "m_z": m_z,
                    "rt": rt,
                })
            else:
                pc_loadings_list.append({
                    "id": None,
                    "value": loading_value,
                    "annotation": "",
                    "m_z": None,
                    "rt": None,
                })

        pc_loadings_list.sort(key=lambda x: x["value"], reverse=True)
        detailed_loadings.append(pc_loadings_list)

    # base JSON (loadings を除いたもの) を生成
    pca_copy = pca_result.copy()
    if "loadings" in pca_copy:
        pca_copy.pop("loadings")

    base_json = json.dumps(pca_copy, ensure_ascii=False, indent=2)

    # loadings 部分を独自フォーマットで構築
    loadings_json_lines = []
    loadings_json_lines.append('  "loadings": [')
    for i, pc_loadings_list in enumerate(detailed_loadings):
        loadings_json_lines.append('    [')
        for j, item in enumerate(pc_loadings_list):
            id_val = json.dumps(item["id"], ensure_ascii=False)
            val_val = json.dumps(item["value"], ensure_ascii=False)
            ann_val = json.dumps(item["annotation"], ensure_ascii=False)
            mz_val = json.dumps(item["m_z"], ensure_ascii=False)
            rt_val = json.dumps(item["rt"], ensure_ascii=False)

            item_str = f'{{ "id": {id_val}, "value": {val_val}, "m_z": {mz_val}, "rt": {rt_val}, "annotation": {ann_val} }}'
            comma = ',' if j < len(pc_loadings_list) - 1 else ''
            loadings_json_lines.append(f'      {item_str}{comma}')

        comma = ',' if i < len(detailed_loadings) - 1 else ''
        loadings_json_lines.append(f'    ]{comma}')
    loadings_json_lines.append('  ]')

    final_json = base_json[:-2] + ',\n' + '\n'.join(loadings_json_lines) + '\n}'
    return final_json


def deserialize_lz4_packed_msgpack(data: bytes) -> list:
    """LZ4圧縮されたMsgPackデータを解凍し、内部のリストを返します。"""
    _header, compressed_data = msgpack.unpackb(data, raw=False)
    stream = io.BytesIO(compressed_data)
    unpacker = msgpack.Unpacker(stream, raw=False)
    size = next(unpacker)
    read_size = unpacker.tell()
    decompressed_data = lz4.block.decompress(
        compressed_data[read_size:],
        uncompressed_size=size,
    )

    all_spots = []
    inner_unpacker = msgpack.Unpacker(io.BytesIO(decompressed_data), raw=False, strict_map_key=False)
    for spot in inner_unpacker:
        all_spots.append(spot)
    return all_spots


def extract_arf_data(data) -> dict | None:
    if isinstance(data, list) and len(data) >= 3 and all(isinstance(row, list) for row in data):
        return _convert_arf_peak_group(data)

    # .arf ファイルのみを扱うため、他の形式はサポートしない
    return None


def deserialize(file_like_object) -> list[dict]:
    datas = deserialize_lz4_packed_msgpack(file_like_object.read())
    results = []

    if _is_arf_feature_container(datas):
        first = datas[0]
        for group_index, group in enumerate(first[2:] + datas[1:], start=0): # MessagePackの構造に応じて、最初の要素からピークグループを抽出
            formatted = extract_arf_data(group)
            if formatted is not None:
                formatted["MasterAlignmentID"] = group_index
                formatted["AlignmentID"] = group_index
                results.append(formatted)
        return results

    # .arf ファイルのみを扱うため、他の形式はサポートしない
    return results


def _convert_to_alignment_feature(data: list) -> dict:
    if not isinstance(data, list) or len(data) < 3:
        return {}
    
    # .arf の AlignmentChromPeakFeature row
    if len(data) > 25 and isinstance(data[18], (int, float)):
        rt_value = None
        if len(data) > 15 and isinstance(data[15], list):
            rt_value = _convert_to_times(data[15]).get("rt")
        elif len(data) > 16 and isinstance(data[16], list):
            rt_value = _convert_to_times(data[16]).get("rt")

        # 【追加】配列の先頭付近からファイル名（文字列）を探す
        file_name = None
        for item in data[:10]:
            if isinstance(item, (bytes, str)):
                decoded = _decode(item)
                if len(decoded) > 0:
                    file_name = decoded
                    break

        return {
            "peak_id": data[3] if len(data) > 3 else None,
            "file_id": data[1] if len(data) > 1 else None,
            "file_name": file_name, # 追加
            "master_peak_id": data[2] if len(data) > 2 else None,
            "height": data[18] if len(data) > 18 else None,
            "area": data[20] if len(data) > 20 else None,
            "m_z": data[22] if len(data) > 22 else None,
            "rt": rt_value,
            "is_msms_matched": False,
            "is_matched": False,
        }

    # .arf ファイルのみを扱うため、他の形式はサポートしない
    return {}


def extract_peak_properties(deserialized_list: list[dict]) -> pd.DataFrame:
    rows = []
    for spot in deserialized_list:
        aligned = spot.get("AlignedPeakProperties")
        if not isinstance(aligned, list):
            continue
        for sample_index, sample in enumerate(aligned):
            if not isinstance(sample, list) or len(sample) < 3:
                continue
            
            feature = _convert_to_alignment_feature(sample)
            
            # 【追加】抽出したファイル名を優先し、無い場合はSample_0などにフォールバック
            file_name = feature.get("file_name")
            sample_label = file_name if file_name else f"Sample_{sample_index}"

            row = {
                "MasterAlignmentID": spot.get("MasterAlignmentID"),
                "AlignmentID": spot.get("AlignmentID"),
                "SpotRT": spot.get("RT"),
                "SpotMassCenter": spot.get("MassCenter"),
                "IonMode": spot.get("IonMode"),
                "CompoundName": spot.get("Name"),
                "SampleIndex": sample_index,
                "FileName": sample_label, # CSVにもファイル名を追加
                "PeakID": feature.get("peak_id"),
                "FileID": feature.get("file_id"),
                "MasterPeakID": feature.get("master_peak_id"),
                "PeakHeight": feature.get("height"),
                "PeakArea": feature.get("area"),
                "PeakMZ": feature.get("m_z"),
                "PeakRT": feature.get("rt"),
            }
            rows.append(row)
    
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def build_pca_matrix(deserialized_list: list[dict], use_properties: list[str] = None) -> tuple[np.ndarray, list[str], list[str]]:
    """
    指定された複数プロパティから多変量PCA用行列を構築
    """
    # デフォルトではHeightとAreaを使用（m_zやrtも追加可能）
    if use_properties is None:
        use_properties = ["height"]

    sample_data_dict = {}

    for spot in deserialized_list:
        aligned = spot.get("AlignedPeakProperties")
        if not isinstance(aligned, list) or not aligned:
            continue
        
        master_id = spot.get("MasterAlignmentID")
        
        for sample_index, sample in enumerate(aligned):
            if not isinstance(sample, list):
                continue
            
            feature = _convert_to_alignment_feature(sample)
            
            # 【追加】行列のキーとしてファイル名を使用
            file_name = feature.get("file_name")
            sample_key = file_name if file_name else f"Sample_{sample_index}"
            
            if sample_key not in sample_data_dict:
                sample_data_dict[sample_key] = {}
                
            # 指定された複数のプロパティを列として追加
            for prop in use_properties:
                val = feature.get(prop)
                col_name = f"Spot_{master_id}_{prop}"
                # Noneの場合は0.0で埋める（欠損値処理）
                sample_data_dict[sample_key][col_name] = float(val) if val is not None else 0.0

    if not sample_data_dict:
        return np.empty((0, 0)), [], []

    # 行がサンプル、列が「スポット×プロパティ」のデータフレームを作成
    df = pd.DataFrame.from_dict(sample_data_dict, orient='index')
    
    # 改善点2: 欠損値を 0 ではなく「その列(特徴量)の平均値」で埋める (Mean Imputation)
    df = df.fillna(df.mean())
    # ※もし全サンプルで欠損だった列があれば NaN のまま残るので、その場合のみ 0.0 で埋める
    df = df.fillna(0.0)
    
    # 改善点3: 分散が0（全サンプルで同一の値）の列を事前に行列から除外する
    df = df.loc[:, df.var(numeric_only=True) > 0]
    
    return df.to_numpy(dtype=float), list(df.index), list(df.columns)


def run_pca(matrix: np.ndarray, n_components: int | None = None) -> dict:
    """
    標準化（スケーリング）を行った上で多次元PCAを実行する
    """
    n_samples, n_features = matrix.shape
    max_components = min(n_samples, n_features)
    
    if max_components < 2:
        raise ValueError(f"PCAには2つ以上のサンプルと特徴量が必要です。（現在: サンプル={n_samples}, 特徴量={n_features}）")

    # n_componentsが指定されていない場合は最大次元数まで計算
    target_components = max_components if n_components is None else min(n_components, max_components)
    
    # 【重要】スケールの異なる多変量（Height, RTなど）を扱うための標準化
    scaler = StandardScaler()
    scaled_matrix = scaler.fit_transform(matrix)
    
    pca = PCA(n_components=target_components)
    transformed = pca.fit_transform(scaled_matrix)
    
    return {
        "components": transformed.tolist(),
        "explained_variance_ratio": pca.explained_variance_ratio_.tolist(),
        "singular_values": pca.singular_values_.tolist(),
        "loadings": pca.components_.tolist()
    }


def plot_pca(pca_result: dict, sample_names: list[str], props: list[str], file_path: str, output_file: str = "pca_plot.png"):
    """
    PCAスコアプロットを画像ファイルとして保存
    """
    components = np.array(pca_result["components"])
    
    if components.shape[1] < 2:
        f"[WARNING] PCA成分が2つ未満のため、プロットできません。"
        return
    
    plt.figure(figsize=(10, 8))
    plt.scatter(components[:, 0], components[:, 1], alpha=0.7)
    
    # サンプル名をラベルとして表示
    for i, name in enumerate(sample_names):
        plt.annotate(name, (components[i, 0], components[i, 1]), fontsize=8, alpha=0.8)
    
    plt.xlabel(f"PC1 ({pca_result['explained_variance_ratio'][0]*100:.1f}%)")
    plt.ylabel(f"PC2 ({pca_result['explained_variance_ratio'][1]*100:.1f}%)")
    file_name = os.path.basename(file_path)
    plt.title(f"PCA Score Plot - {file_name} (Properties: {', '.join(props)})")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    f"[INFO] PCAプロットを保存しました: {output_file}"


def plot_pca_scores_by_sample(pca_result: dict, sample_names: list[str], output_file: str = "pca_sample_scores.png"):
    """
    PC1とPC2について、横軸にサンプル名、縦軸にスコア(強度)をとったプロットを作成して保存します。
    """
    components = np.array(pca_result.get("components", []))
    
    if components.size == 0 or components.shape[1] < 2:
        print("[WARNING] PCA成分が2つ未満のため、サンプル別スコアをプロットできません。", file=sys.stderr)
        return
        
    pc1_scores = components[:, 0]
    pc2_scores = components[:, 1]
    
    # 2段のサブプロットを作成（x軸を共有）
    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    
    # -------------------------
    # PC1のプロット (上段)
    # -------------------------
    axes[0].bar(sample_names, pc1_scores, color='#4C72B0', edgecolor='black', alpha=0.8)
    axes[0].set_title(f"PC1 Scores by Sample ({pca_result['explained_variance_ratio'][0]*100:.1f}%)")
    axes[0].set_ylabel("PC1 Score")
    axes[0].axhline(0, color='black', linewidth=1.0, linestyle='-') # 基準線（0）
    axes[0].grid(axis='y', linestyle='--', alpha=0.7)
    
    # -------------------------
    # PC2のプロット (下段)
    # -------------------------
    axes[1].bar(sample_names, pc2_scores, color='#55A868', edgecolor='black', alpha=0.8)
    axes[1].set_title(f"PC2 Scores by Sample ({pca_result['explained_variance_ratio'][1]*100:.1f}%)")
    axes[1].set_ylabel("PC2 Score")
    axes[1].axhline(0, color='black', linewidth=1.0, linestyle='-') # 基準線（0）
    axes[1].grid(axis='y', linestyle='--', alpha=0.7)
    
    # X軸のサンプル名が長い場合に備えて斜めに傾ける
    plt.xticks(rotation=45, ha="right")
    
    # レイアウトを整えて保存
    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"[INFO] サンプル別PCAスコアプロットを保存しました: {output_file}", file=sys.stderr)


def plot_peak_height_distribution(peak_df: pd.DataFrame, filter_keyword: str = None, group_regex: str = None, output_file: str = "peak_height_dist.png"):
    """
    指定したキーワード（脂質クラス等）でフィルタリングし、
    横軸にサンプル（またはグループ）、縦軸にPeakHeightをとったジッタープロットを作成します。
    """
    if peak_df.empty:
        print("[WARNING] データフレームが空のためプロットできません。", file=sys.stderr)
        return

    # 1. フィルタリング処理
    if filter_keyword:
        # Warning回避のため .copy() を付与
        df_filtered = peak_df[peak_df['CompoundName'].fillna("").astype(str).str.contains(filter_keyword, case=False)].copy()
        title_text = f"Class/Name: {filter_keyword}"
    else:
        df_filtered = peak_df.copy()
        title_text = "All Annotated Lipids"

    if df_filtered.empty:
        print(f"[WARNING] キーワード '{filter_keyword}' に一致するデータがありません。", file=sys.stderr)
        return

    # 2. レプリケートのグループ化
    if group_regex:
        # 正規表現にマッチした部分（_1 など）を消去してグループ名を作る
        df_filtered['GroupLabel'] = df_filtered['FileName'].apply(lambda x: re.sub(group_regex, '', str(x)))
        title_text += " (Grouped by Replicates)"
    else:
        df_filtered['GroupLabel'] = df_filtered['FileName']

    # 元の順序を保ったままユニークなグループ名を取得
    groups = df_filtered['GroupLabel'].unique()
    
    # 3. 描画設定
    plt.figure(figsize=(max(6, len(groups) * 1.2), 6))
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    
    for i, group in enumerate(groups):
        # グループに属する全レプリケートのデータを抽出
        group_data = df_filtered[df_filtered['GroupLabel'] == group]
        
        y_vals = pd.to_numeric(group_data['PeakHeight'], errors='coerce').dropna().values
        if len(y_vals) == 0:
            continue
            
        # データ点を少し散らす（ジッター）
        x_vals = np.random.normal(i, 0.08, size=len(y_vals))
        
        # プロット（レプリケートがまとまって表示される）
        plt.scatter(x_vals, y_vals, color=colors[i % 10], alpha=0.7, edgecolors='black', linewidths=0.5, s=25)
        
        # 平均値のバーを描画
        mean_val = np.mean(y_vals)
        plt.hlines(mean_val, i - 0.3, i + 0.3, colors='black', linewidth=2.5)

    # 軸とタイトルの設定
    plt.xticks(range(len(groups)), groups, rotation=45, ha="right")
    plt.ylabel("Peak Height")
    plt.title(title_text)
    
    # スタイル調整（上・右の枠線を消す）
    ax = plt.gca()
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"[INFO] 脂質分布プロットを保存しました: {output_file} (データ件数: {len(df_filtered)}, グループ数: {len(groups)})", file=sys.stderr)


def extract_top_loading_features(pca_result: dict, deserialized_list: list[dict], feature_names: list[str], n: int = 1):
    """
    PCAのLoadingsからPC1とPC2それぞれについて、
    正に寄与している上位n件と、負に寄与している上位n件を抽出し、対応するピーク情報を出力
    """
    loadings = np.array(pca_result["loadings"])
    
    if loadings.size == 0:
        f"[WARNING] Loadingsが空です。"
        return
    
    # 少なくともPC1とPC2（または存在する分だけ）を処理
    for pc_idx in range(min(2, len(loadings))):
        pc_loadings = loadings[pc_idx]
        pc_name = f"PC{pc_idx + 1}"
        
        # スポット名とロード値のペアを作成
        loading_pairs = [(name, value) for name, value in zip(feature_names, pc_loadings)]
        
        # 実際の値で降順（大きい順）にソート
        sorted_by_value = sorted(loading_pairs, key=lambda x: x[1], reverse=True)
        
        # 正に寄与している上位n件（リストの先頭から）
        positive_top_n = sorted_by_value[:n]
        
        # 負に寄与している上位n件（リストの末尾から取得し、負の方向に大きい順＝昇順にするため反転）
        negative_top_n = sorted_by_value[-n:][::-1]
        
        f"[INFO] {pc_name} Loading分析：正の寄与 上位{n}件"
        f"  説明分散比: {pca_result['explained_variance_ratio'][pc_idx]*100:.2f}%"
        f"[INFO] {pc_name} Loading分析：負の寄与 上位{n}件"
        f"[INFO] {pc_name} Loading分析：負の寄与 上位{n}件"
        f"[INFO] {pc_name} Loading分析：負の寄与 上位{n}件"
        f"【{pc_name} Loading分析：負の寄与 上位{n}件】"
        _print_feature_details(negative_top_n, deserialized_list)


def _print_feature_details(feature_pairs: list[tuple], deserialized_list: list[dict]):
    """
    各ピークの詳細情報を出力
    """
    for spot_name, loading_value in feature_pairs:
        # スポット名から番号を抽出
        spot_id = int(spot_name.split("_")[1])
        
        if spot_id >= len(deserialized_list):
            f"[WARNING] スポット {spot_id} は見つかりません。"
            continue
        
        spot = deserialized_list[spot_id]
        
        f"[INFO] {spot_name} (Loading = {loading_value:.6f})"
        f"  MasterAlignmentID: {spot.get('MasterAlignmentID')}"
        f"  RT: {spot.get('RT')}"
        f"  MassCenter: {spot.get('MassCenter')}"
        f"  IonMode: {spot.get('IonMode')}"
        f"  CompoundName: {spot.get('Name')}"
        f"[INFO]  HeightAverage: {spot.get('HeightAverage')}"
        
        # AlignedPeakPropertiesの詳細
        aligned_peaks = spot.get("AlignedPeakProperties", [])
        f"[INFO]  AlignedPeakProperties数: {len(aligned_peaks)}"
        
        for sample_idx, sample in enumerate(aligned_peaks):
            if isinstance(sample, list) and len(sample) > 18:
                f"[INFO]    [Sample {sample_idx}]"
                f"[INFO]      Height: {sample[18] if len(sample) > 18 else 'N/A'}"
                f"[INFO]      Area: {sample[20] if len(sample) > 20 else 'N/A'}"
                f"[INFO]      M/Z: {sample[22] if len(sample) > 22 else 'N/A'}"


def find_input_file(file_path: str | None = None, index: int = 0) -> str | None:
    if file_path and os.path.exists(file_path):
        return file_path

    project_root = os.path.dirname(__file__)
    data_dir = os.path.join(project_root, "data")

    # .arf ファイルのみを検索
    candidates = glob.glob(os.path.join(data_dir, "*.arf"))
    return candidates[index] if candidates else None

def summarize_arf_data(deserialized_list):
    """
    ARFデータの統計的要約を生成
    """
    if not deserialized_list:
        
        return "データがありません。"

    df = pd.DataFrame(deserialized_list)
    
    summary = {
        "total_peaks": len(df),
        "rt_range": (df['RT'].min(), df['RT'].max()) if 'RT' in df.columns else None,
        "mass_range": (df['MassCenter'].min(), df['MassCenter'].max()) if 'MassCenter' in df.columns else None,
        "height_average_mean": df['HeightAverage'].mean() if 'HeightAverage' in df.columns else None,
        "height_average_max": df['HeightAverage'].max() if 'HeightAverage' in df.columns else None,
        "ion_modes": df['IonMode'].value_counts().to_dict() if 'IonMode' in df.columns else {},
        "named_compounds": df['Name'].notna().sum() if 'Name' in df.columns else 0
    }
    
    return summary

def main():
    parser = argparse.ArgumentParser(description="MS-DIAL .arf ファイルを解析します。")
    parser.add_argument("--file", "-f", help="解析する .arf ファイルのパス")
    parser.add_argument("--export", "-e", help="CSV に書き出すパス")
    parser.add_argument("--pca", action="store_true", help="PCA を実行する")
    parser.add_argument("--output-pca", help="PCA 結果を JSON で保存するパス")
    parser.add_argument("--output-plot", help="PCA プロットを画像ファイルとして保存するパス (デフォルト: pca_plot.png)")
    parser.add_argument("--output-sample-scores", help="サンプル別PCAスコアプロットを画像ファイルとして保存するパス")
    parser.add_argument("--group-replicates", action="store_true", help="ファイル名からレプリケート番号（_1, _2など）を自動で除外してグループ化する")
    parser.add_argument("--group-regex", type=str, help="特殊なファイル名の場合に、削除したい文字列の正規表現を直接指定")
    parser.add_argument("--plot-distribution", action="store_true", help="脂質クラスごとのPeakHeight分布プロットを作成する")
    parser.add_argument("--filter-name", type=str, help="分布プロットで抽出する脂質名やクラスのキーワード (例: 'EtherPE_P', 'TG')")
    parser.add_argument("--output-dist-plot", type=str, default="peak_height_dist.png", help="分布プロットの保存先 (デフォルト: peak_height_dist.png)")
    parser.add_argument("--top-features", "-t", type=int, default=0, help="PCA Loadingから抽出する上位・下位ピーク件数")
    parser.add_argument("--props", nargs="+", default=["height"], 
                        help="PCAに使用するプロパティ (例: height area). m_zやrtは量ではないため非推奨です。")
    parser.add_argument("--components", type=int, default=None, 
                        help="計算する主成分の数 (デフォルト: 計算可能な最大数)")
    args = parser.parse_args()

    index = int(input("何番目のファイルを解析しますか？ "))
    file_path = find_input_file(args.file, index)
    if not file_path:
        raise FileNotFoundError(".arf ファイルが見つかりません。")

    print(f"[INFO] 解析対象ファイル: {file_path}")
    with open(file_path, "rb") as f:
        deserialized = deserialize(io.BytesIO(f.read()))

    print(f"[INFO] 読み込んだピーク数（Spots）: {len(deserialized)}")

    peak_df = extract_peak_properties(deserialized)
    print(f"[INFO] 抽出した Peak Property レコード数: {len(peak_df)}")
    
    if len(peak_df) > 0:
        avg_samples = len(peak_df) / len(deserialized)
        print(f"[INFO] 平均サンプル数/スポット: {avg_samples:.2f} (抽出が成功していればサンプル数と一致します)")

    if args.export:
        peak_df.to_csv(args.export, index=False)
        print(f"[INFO] CSV に出力しました: {args.export}")


    if args.plot_distribution:
        if len(peak_df) > 0:
            # 正規表現パターンの決定
            regex_pattern = None
            if args.group_regex:
                regex_pattern = args.group_regex
            elif args.group_replicates:
                # デフォルトの優秀な正規表現: 「_数字」の後に「_」か「行末」が続く場合のみ削除
                # 例: "..._0h_1_NEG" -> "..._0h_NEG" に自動変換される。(_0hは削除されない)
                regex_pattern = r'_\d+(?=_|$)'
                
            plot_peak_height_distribution(
                peak_df=peak_df, 
                filter_keyword=args.filter_name, 
                group_regex=regex_pattern,  # 追加
                output_file=args.output_dist_plot
            )
        else:
            print("[WARNING] 抽出されたPeak Propertyレコードが存在しないため、分布プロットをスキップします。", file=sys.stderr)
    

    if args.pca:
        matrix, sample_names, feature_names = build_pca_matrix(deserialized, use_properties=args.props)
        
        if matrix.size == 0:
            print("PCA 用データを構築できませんでした。")
            return

        print(f"[INFO] PCA入力行列の形状: {matrix.shape} (サンプル数 x 特徴量数)")
        print(f"[INFO] 使用プロパティ: {args.props}")
        try:
            pca_result = run_pca(matrix, n_components=args.components)
            print(f"[INFO] PCA 実行完了 (計算された主成分数: {len(pca_result['explained_variance_ratio'])})")
            print(f"Explained variance ratio: {pca_result['explained_variance_ratio']}")
            
            # PCAプロットを表示
            plot_file = args.output_plot or "pca_plot.png"
            plot_pca(pca_result, sample_names, args.props, file_path, plot_file)
            
            if args.output_sample_scores:
                plot_pca_scores_by_sample(pca_result, sample_names, output_file=args.output_sample_scores)

            if args.top_features:
                extract_top_loading_features(pca_result, deserialized, feature_names, n=args.top_features)
            
            if args.output_pca:
                # pca_result にサンプル名を保存しているため、そのまま渡す
                pca_result["samples"] = sample_names
                final_json = make_loading_details(pca_result, deserialized, feature_names)
                with open(args.output_pca, "w", encoding="utf-8") as f:
                    f.write(final_json)
                print(f"[INFO] PCA 結果を保存しました: {args.output_pca}")
                
        except ValueError as e:
            print(f"[ERROR] {e}")

    summary = summarize_arf_data(deserialized)
    pprint.pprint(summary)


if __name__ == "__main__":
    main()