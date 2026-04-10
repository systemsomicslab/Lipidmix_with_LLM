import io
import msgpack
import os
import glob
import struct


def _map_spot_data(spot):
    """(キー, 値)のペアリスト形式に対応したマッピング"""
    def d(val): 
        if isinstance(val, tuple): val = val[1] # 拡張型対応
        return val.decode('utf-8', errors='ignore') if isinstance(val, bytes) else val
    
    def get_v(obj, idx):
        # リスト型ならインデックスで、ペアリストなら探索して取得
        if not obj: return 0
        if isinstance(obj, list):
            if len(obj) > 0 and isinstance(obj[0], (list, tuple)):
                for k, v in obj:
                    if k == idx or k == str(idx).encode(): return v
            elif idx < len(obj):
                return obj[idx]
        return 0

    # RTの抽出
    rt = 0
    tc = get_v(spot, 4) # TimesCenter
    rt = get_v(tc, 0) # RT

    return {
        "master_id": get_v(spot, 0),
        "alignment_id": get_v(spot, 1),
        "rt": rt,
        "m/z": get_v(spot, 5),
        "name": d(get_v(spot, 12))
    }


def parse_aef_css1(file_path):
    # 未知の型が来ても (コード番号, バイナリデータ) のタプルとして保持させる
    def ext_handler(code, data):
        return (code, data)
    
    limit = 2**31 - 1  # msgpack のデフォルトの制限を解除

    spots = []
    with open(file_path, 'rb') as f:
        # 1. ヘッダー14バイトをスキップ
        f.seek(14)
        
        # 2. 1つずつ「独立したオブジェクト」として取り出す
        while True:
            try:
                # msgpack.unpack(f, ...) は、ストリームから1つ読み取ってポインタを進める
                spot_raw = msgpack.unpack(
                    f, 
                    raw=True, 
                    strict_map_key=False, 
                    object_pairs_hook=list,
                    ext_hook=ext_handler,
                    max_array_len=limit,
                    max_map_len=limit
                )
                
                # スポットとして妥当な形式（要素数がある程度あるリスト）かチェック
                if isinstance(spot_raw, list) and len(spot_raw) >= 14:
                    spots.append(_map_spot_data(spot_raw))
                
                if len(spots) >= 1394: # 予定数に達したら終了
                    break
                    
            except EOFError: # ファイルの終端
                break
            except Exception:
                # 同期がズレた場合、次の配列開始マーカー(0x9e)が見つかるまで1バイトずつ進む
                next_byte = f.read(1)
                if not next_byte: break
                while next_byte != b'\x9e': # 0x9e は要素数14の固定配列の合図
                    next_byte = f.read(1)
                    if not next_byte: break
                continue # 次のループで再挑戦

    return spots


# --- 実行 ---
if __name__ == "__main__":
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    data_dir = os.path.join(project_root, 'data')
    aef_files = glob.glob(os.path.join(data_dir, "*.aef"))
    path = aef_files[0] if aef_files else None

    if os.path.exists(path):
        results = parse_aef_css1(path)
        print(f"解析完了: {len(results)} 件のスポットを取得")
        if results:
            import pprint
            pprint.pprint(results[0])


