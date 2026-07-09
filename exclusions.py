"""ARF スポットリストに対するサンプル/スポット手動除外（純ロジック層・MCP 非依存）。

preprocessing.py / differential.py と同格の leaf モジュール。行列を組む直前に
filtered_features から除外集合を適用した派生リストを作るために使う。
サンプルキーは各 AlignedPeakProperties エントリの file_name、スポットキーは
MasterAlignmentID。build_pca_matrix と同じ file_name 導出を用いる。
"""
from __future__ import annotations


def _entry_file_name(entry) -> str | None:
    """AlignedPeakProperties の1エントリ（生 list）から file_name を導出する。"""
    from arf_reader import _convert_to_alignment_feature
    try:
        feature = _convert_to_alignment_feature(entry)
        file_name = feature.get("file_name")
        if file_name:
            return file_name
    except Exception:
        pass

    # Fallback: for short entries (e.g., in tests), extract the first string
    if isinstance(entry, list):
        for item in entry:
            if isinstance(item, str):
                return item

    return None


def prune_spots(spots, excluded_samples, excluded_spots):
    """除外集合を適用したスポットリストの非破壊コピーを返す。

    - MasterAlignmentID in excluded_spots のスポットを丸ごと除外。
    - 残スポットの AlignedPeakProperties から file_name in excluded_samples の
      エントリを除去する（スポット dict は浅いコピーし、AlignedPeakProperties を
      フィルタ済みリストへ差し替え。元の spots / エントリは変更しない）。
    - 除外集合が両方空なら入力をそのまま返す（コピー不要・恒等）。
    """
    if not excluded_samples and not excluded_spots:
        return spots
    excluded_samples = set(excluded_samples or ())
    excluded_spots = set(excluded_spots or ())
    out = []
    for spot in spots:
        if spot.get("MasterAlignmentID") in excluded_spots:
            continue
        if not excluded_samples:
            out.append(spot)
            continue
        aligned = spot.get("AlignedPeakProperties")
        if not isinstance(aligned, list):
            out.append(spot)
            continue
        kept = [e for e in aligned if _entry_file_name(e) not in excluded_samples]
        new_spot = dict(spot)
        new_spot["AlignedPeakProperties"] = kept
        out.append(new_spot)
    return out


def roster(spots):
    """現データに存在する (サンプル file_name 集合, MasterAlignmentID 集合) を返す。

    除外指定の未一致検出と list 表示に使う。
    """
    names: set[str] = set()
    ids: set[int] = set()
    for spot in spots or []:
        mid = spot.get("MasterAlignmentID")
        if mid is not None:
            ids.add(mid)
        aligned = spot.get("AlignedPeakProperties")
        if isinstance(aligned, list):
            for e in aligned:
                fn = _entry_file_name(e)
                if fn:
                    names.add(fn)
    return names, ids
