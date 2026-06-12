import io
import msgpack
import os
import glob
import struct


def parse_eic_aef_css1(file_path, include_chromatogram=False):
    results = []

    with open(file_path, 'rb') as f:
        # 1. ヘッダー (10 bytes)
        magic = f.read(4)
        if magic != b'CSS1':
            raise ValueError(f"Not a CSS1 file. Magic: {magic}")
        f.seek(10)  # 10バイト目までスキップ

        # 2. スポット数 (4 bytes, int)
        num_spots = struct.unpack('<i', f.read(4))[0]

        # 3. シークポインタの読み込み (8 bytes * num_spots, long long)
        pointers = [struct.unpack('<q', f.read(8))[0] for _ in range(num_spots)]

        # 4. 各スポットデータの解析
        for i, ptr in enumerate(pointers):
            f.seek(ptr)

            # Spot Chunk 読み込み
            # RT(f), RI(f), Mass(f), Drift(f) -> 4 floats (16 bytes)
            # MainType(b) -> 1 byte
            # NumSamples(i) -> 4 bytes
            rt, ri, mass, drift = struct.unpack('<ffff', f.read(16))
            main_type = struct.unpack('<b', f.read(1))[0]
            num_samples = struct.unpack('<i', f.read(4))[0]

            samples_data = []
            for _ in range(num_samples):
                # Peak Info Chunk
                # FileID(i), NumPeaks(i), Top(f), Left(f), Right(f) -> 20 bytes
                file_id, num_peaks, top, left, right = struct.unpack('<iifff', f.read(20))

                total_intensity = 0.0
                max_intensity = 0.0
                chrom_points = []
                for _ in range(num_peaks):
                    # Horiz(f), Intensity(f) -> 8 bytes
                    h_val, intensity = struct.unpack('<ff', f.read(8))
                    total_intensity += intensity
                    max_intensity = max(max_intensity, intensity)
                    if include_chromatogram:
                        chrom_points.append((h_val, intensity))

                mean_intensity = total_intensity / num_peaks if num_peaks else 0.0
                sample_info = {
                    "file_id": file_id,
                    "peak_top": top,
                    "num_peaks": num_peaks,
                    "mean_intensity": mean_intensity,
                    "max_intensity": max_intensity,
                }
                if include_chromatogram:
                    sample_info["chromatogram"] = chrom_points

                samples_data.append(sample_info)

            results.append({
                "spot_id": i,
                "rt": rt,
                "ri": ri,
                "mz": mass,
                "drift": drift,
                "main_type": main_type,
                "num_samples": num_samples,
                "samples": samples_data,
            })

    return results


def summarize_eic_data(results):
    if not results:
        return {
            "total_spots": 0,
            "rt_range": None,
            "mz_range": None,
            "total_samples": 0,
            "total_peaks": 0,
            "peak_top_mean": 0.0,
            "peak_top_max": 0.0,
            "unique_file_ids": [],
        }

    rt_values = [spot["rt"] for spot in results]
    mz_values = [spot["mz"] for spot in results]
    total_samples = sum(spot["num_samples"] for spot in results)
    sample_entries = [sample for spot in results for sample in spot["samples"]]
    total_peaks = sum(sample["num_peaks"] for sample in sample_entries)
    peak_tops = [sample["peak_top"] for sample in sample_entries if sample.get("peak_top") is not None]
    unique_file_ids = sorted({sample["file_id"] for sample in sample_entries})

    return {
        "total_spots": len(results),
        "rt_range": (min(rt_values), max(rt_values)) if rt_values else None,
        "mz_range": (min(mz_values), max(mz_values)) if mz_values else None,
        "total_samples": total_samples,
        "total_peaks": total_peaks,
        "peak_top_mean": sum(peak_tops) / len(peak_tops) if peak_tops else 0.0,
        "peak_top_max": max(peak_tops) if peak_tops else 0.0,
        "unique_file_ids": unique_file_ids,
    }


def search_eic_by_mz_range(results, min_mz, max_mz):
    return [spot for spot in results if min_mz <= spot.get("mz", 0.0) <= max_mz]


def search_eic_by_rt_range(results, min_rt, max_rt):
    return [spot for spot in results if min_rt <= spot.get("rt", 0.0) <= max_rt]


def top_eic_spots_by_peak_top(results, top_n=20):
    spots_with_max = []
    for spot in results:
        sample_max = max((sample.get("peak_top", 0.0) for sample in spot["samples"]), default=0.0)
        spots_with_max.append({
            "spot_id": spot["spot_id"],
            "rt": spot["rt"],
            "mz": spot["mz"],
            "num_samples": spot["num_samples"],
            "max_peak_top": sample_max,
        })
    return sorted(spots_with_max, key=lambda x: x["max_peak_top"], reverse=True)[:top_n]


# --- 実行 ---
if __name__ == "__main__":
    from data_config import get_data_dir
    aef_files = glob.glob(os.path.join(str(get_data_dir()), "*.aef"))  # 探索先は環境変数で上書き可
    path = aef_files[0] if aef_files else None

    if os.path.exists(path):
        results = parse_eic_aef_css1(path)
        print(f"解析完了: {len(results)} 件のスポットを取得")
        if results:
            import pprint
            pprint.pprint(results[0])

