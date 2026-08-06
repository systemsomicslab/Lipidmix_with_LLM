"""合成 .dcl（MSDecResult）バイト列ビルダ。

実データの .dcl はリポジトリに含めないため、公式 MsdecResultsReader.ReadMSDecResultVer1
と同じレイアウト（dcl_reader のモジュール docstring 参照）をテスト側で組み立てる。
ここがズレるとパーサのテストが実物と乖離するので、バイト幅はコメントで明示する。
"""
import struct


def build_dcl_bytes(results, *, version: int = 1, has_annotation: bool = False) -> bytes:
    """``results`` から .dcl バイト列を作る。

    results の各要素は dict:
        precursor_mz / rt / ion_mode / scan_id / raw_spec_id / sn /
        spectrum=[(mass, intensity), ...]
    """
    count = len(results)
    header = b"DC" + struct.pack("<i", version) + bytes([1 if has_annotation else 0]) \
        + struct.pack("<i", count)
    # seekpointer 配列は結果本体の直前に置かれる（ヘッダ11B + count×8B のあと）。
    body_start = len(header) + 8 * count

    bodies, seekpoints, offset = [], [], body_start
    for item in results:
        spectrum = item.get("spectrum", [])
        block = b"".join([
            # --- Scan (60B) ---
            struct.pack("<q", offset),                      # SeekPoint
            struct.pack("<i", item.get("scan_id", 0)),
            struct.pack("<i", item.get("raw_spec_id", -1)),
            struct.pack("<d", item["precursor_mz"]),
            struct.pack("<i", item.get("ion_mode", 1)),
            struct.pack("<d", item.get("rt", 0.0)),
            struct.pack("<d", item.get("ri", 0.0)),
            struct.pack("<d", item.get("drift", -1.0)),
            struct.pack("<d", item.get("mz", item["precursor_mz"])),
            # --- Quant (40B): ModelPeakMz/Height/Area, IntegratedHeight/Area ---
            struct.pack("<5d", 0.0, item.get("model_height", 1000.0), 0.0, 0.0, 0.0),
            # --- Scoring (20B): Amplitude, Purity, Quality, S/N, EstimatedNoise ---
            struct.pack("<5f", 0.0, 0.0, 0.0, item.get("sn", 12.5), 3.0),
            # --- Counts (12B): spectraNumber, datapointNumber, modelMassNumber ---
            struct.pack("<3i", len(spectrum), 0, 0),
            # --- Spectrum (20B/peak): Mass f64, Intensity f64, PeakQuality i32 ---
            b"".join(
                struct.pack("<2di", mass, intensity, 0) for mass, intensity in spectrum
            ),
        ])
        seekpoints.append(offset)
        offset += len(block)
        bodies.append(block)

    return header + b"".join(struct.pack("<q", sp) for sp in seekpoints) + b"".join(bodies)
