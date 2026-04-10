import struct
import msgpack
import os
import glob

def parse_aef_css1_simple(file_path):
    # すべてのガードを解除
    def ext_handler(code, data): return (code, data)
    limit = 2**31 - 1

    with open(file_path, 'rb') as f:
        # 1. ヘッダー14バイトをスキップ
        header = f.read(14)
        if header[:4] != b'CSS1':
            raise ValueError("CSS1形式ではありません")

        # 2. 残りの全データを一気にMessagePackとして解凍する
        # ループを回さず、unpackbでファイル全体(ヘッダー以降)を1つのオブジェクトとして読み込む
        try:
            raw_payload = f.read()
            # ここでファイル全体が「1つのリスト」として解釈されるか試す
            all_data = msgpack.unpackb(
                raw_payload, 
                raw=True, 
                strict_map_key=False, 
                object_pairs_hook=list,
                ext_hook=ext_handler,
                max_array_len=limit,
                max_map_len=limit,
                max_bin_len=limit,
                max_str_len=limit
            )
            
            # 読み込まれたデータが「リストのリスト」であることを期待
            if isinstance(all_data, list):
                # もし最初の要素がスポットの数(1394など)なら、それ以降がデータ
                if len(all_data) > 0 and all_data[0] == 1394:
                    print("ヘッダー付きリストを検出しました。")
                    return [_map_spot_data(s) for s in all_data[1:] if isinstance(s, list)]
                else:
                    print(f"純粋なリストを検出しました。要素数: {len(all_data)}")
                    return [_map_spot_data(s) for s in all_data if isinstance(s, list)]
            else:
                print(f"予期しないデータ型が読み込まれました: {type(all_data)}")
                return []

        except Exception as e:
            print(f"一括解凍エラー: {e}")
            return []

def _map_spot_data(spot):
    """配列から安全に値を抽出する"""
    def d(val): return val.decode('utf-8', errors='ignore') if isinstance(val, bytes) else val
    def get_v(arr, idx): return arr[idx] if len(arr) > idx else 0

    # TimesCenterの解析
    rt = 0
    tc = get_v(spot, 4)
    if isinstance(tc, list) and len(tc) > 0:
        rt = tc[0]

    return {
        "master_id": get_v(spot, 0),
        "alignment_id": get_v(spot, 1),
        "rt": rt,
        "m/z": get_v(spot, 5),
        "name": d(get_v(spot, 12))
    }

if __name__ == "__main__":
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    data_dir = os.path.join(project_root, 'data')
    aef_files = glob.glob(os.path.join(data_dir, "*.aef"))
    path = aef_files[0] if aef_files else None

    if os.path.exists(path):
        results = parse_aef_css1_simple(path)
        if results:
            print(f"解析成功！ {len(results)} 件のデータを取得しました。")
            import pprint
            pprint.pprint(results[0])