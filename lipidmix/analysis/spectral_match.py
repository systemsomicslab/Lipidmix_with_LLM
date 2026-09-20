"""MS/MS スペクトルの照合スコア（純関数）。

**上流 `MsScanMatching.cs` の写しである。** 定義を揃えているのは mzTab-M の
`id_confidence_measure[4..8]` と同じ土俵で数値を比較するためで、次の「明らかに変な点」は
**意図的にそのまま残している**。直すと比較の土俵が消える（spec §6.3）。

- 上流には `wM` / `wR` の計算があるがどこにも使われていない。移植していない。
- 上流の weighted には中身が同一の if/else 分岐がある。条件ごと落としている。
- simple の `× 999` は比になる時点で打ち消える。意味は無い。
- 窓の走査は固定幅でなく、**同じピークが隣り合う 2 つの窓に二重計上されうる**。
  素直な 1 対 1 アラインメントに書き直してはいけない。
- entropy に Li et al. 2021 の低エントロピー重み変換は入っていない。足さない。

比較不能（どちらかのスペクトルが空）は **0 ではなく -1** を返す。0 は「合わない」を
意味するので、混同すると「照合していない」が「合わなかった」に化ける。

前提: 入力は m/z 昇順。`_prepare()` が並べ替えるので呼び側は気にしなくてよい。

上流ソース: `MsdialWorkbench` (master, HEAD afd5f9522)
`src/Common/CommonStandard/Algorithm/Scoring/MsScanMatching.cs`
  - `GetWeightedDotProduct`  (~4244行目)
  - `GetSimpleDotProduct`    (~4365行目)
  - `GetReverseDotProduct`   (~3958行目)
  - `GetMatchedPeaksScores`  (~731行目)
  - `GetSpectralEntropySimilarity` / `GetSpectralEntropy` (~795行目)
  - `IsComparedAvailable`    (~26行目)
前処理: `src/MSDIAL5/MsdialCore/Utility/DataAccess.cs` の `GetNormalizedMs2Spectra`。
"""
import math

__all__ = [
    "weighted_dot_product",
    "simple_dot_product",
    "reverse_dot_product",
    "matched_peaks_scores",
    "spectral_entropy_similarity",
    "normalize_measured",
    "match_spectrum",
]

_PEAK_COUNT_PENALTY = {1: 0.75, 2: 0.88, 3: 0.94, 4: 0.97}


def _prepare(spectrum):
    """None を空に潰し、m/z 昇順に並べ替えた [[mz, intensity], ...] を返す。"""
    if not spectrum:
        return []
    return sorted(([float(p[0]), float(p[1])] for p in spectrum), key=lambda p: p[0])


def _is_compared_available(peaks1, peaks2):
    """上流 `IsComparedAvailable`: どちらも None でなく、どちらも要素数 0 でないこと。
    ここでは `_prepare()` 済みの入力を受け取るので None は既に [] に潰れている。"""
    return bool(peaks1) and bool(peaks2)


def _scan_sticky(peaks, start_index, focused_mz, bin_width):
    """weighted / reverse / matched-peaks が使う走査。

    上流の `for (int i = remaindIndexM; i < peaks1.Count; i++) { ... else { remaindIndexM = i; break; } }`
    の写し。カーソル（返す index）は「focusedMz + bin 以上の質量」に出会って break したときだけ
    進む。ループが break せずに配列末尾まで達した場合、カーソルは呼び出し時の値のまま変わらない
    （＝次の外側ループでも同じ位置から再スキャンする。これが「隣接窓への二重計上」の元）。
    """
    total = 0.0
    n = len(peaks)
    i = start_index
    while i < n:
        mz = peaks[i][0]
        if mz < focused_mz - bin_width:
            i += 1
            continue
        if mz < focused_mz + bin_width:
            total += peaks[i][1]
            i += 1
            continue
        return total, i
    return total, start_index


def _scan_advancing(peaks, start_index, focused_mz, bin_width):
    """simple だけが使う走査。

    上流の `for (int i = remaindIndexM; i < peaks1.Count; remaindIndexM = ++i) { ... else { break; } }`
    の写し。C# の for 文は `continue` でも増分式 `remaindIndexM = ++i` を必ず実行するため、
    ここではカーソルは要素を処理するたび（continue でも）進み、配列末尾まで達すると
    `len(peaks)` になり得る（sticky 版とはここが違う）。simple 側で末尾到達の分岐が
    明示的に要るのはこのため。
    """
    total = 0.0
    n = len(peaks)
    i = start_index
    while i < n:
        mz = peaks[i][0]
        if mz < focused_mz - bin_width:
            i += 1
            continue
        if mz < focused_mz + bin_width:
            total += peaks[i][1]
            i += 1
            continue
        break
    return total, i


def weighted_dot_product(measured, reference, *, bin_width, mass_begin=0.0, mass_end=2000.0):
    """`GetWeightedDotProduct` の写し（**二乗値**）。カーソルは両スペクトルの和集合を進む。"""
    peaks1 = _prepare(measured)
    peaks2 = _prepare(reference)
    if not _is_compared_available(peaks1, peaks2):
        return -1.0

    last1 = peaks1[-1][0]
    last2 = peaks2[-1][0]

    min_mz = min(peaks1[0][0], peaks2[0][0])
    max_mz = max(last1, last2)
    if mass_begin > min_mz:
        min_mz = mass_begin
    if max_mz > mass_end:
        max_mz = mass_end

    focused_mz = min_mz
    idx_m = idx_l = 0
    measured_buf = []
    reference_buf = []
    base_m = base_r = -math.inf

    while focused_mz <= max_mz:
        sum_m, idx_m = _scan_sticky(peaks1, idx_m, focused_mz, bin_width)
        sum_r, idx_l = _scan_sticky(peaks2, idx_l, focused_mz, bin_width)

        # 上流に `if (sumM <= 0 && sumR > 0) {...} else {...}` の分岐があるが両枝の中身は
        # 同一（brief 記載の瑕疵）。分岐ごと落として共通処理だけ残す。
        measured_buf.append([focused_mz, sum_m])
        if sum_m > base_m:
            base_m = sum_m
        reference_buf.append([focused_mz, sum_r])
        if sum_r > base_r:
            base_r = sum_r

        if focused_mz + bin_width > max(last1, last2):
            break
        next_m = peaks1[idx_m][0]
        next_l = peaks2[idx_l][0]
        if focused_mz + bin_width > next_l and focused_mz + bin_width <= next_m:
            focused_mz = next_m
        elif focused_mz + bin_width <= next_l and focused_mz + bin_width > next_m:
            focused_mz = next_l
        else:
            focused_mz = min(next_m, next_l)

    if base_m == 0 or base_r == 0:
        return 0.0

    sum_measure = 0.0
    sum_reference = 0.0
    l_counter = 0
    for entry_m, entry_r in zip(measured_buf, reference_buf):
        entry_m[1] = entry_m[1] / base_m
        entry_r[1] = entry_r[1] / base_r
        sum_measure += entry_m[1]
        sum_reference += entry_r[1]
        if entry_r[1] > 0.1:
            l_counter += 1

    penalty = _PEAK_COUNT_PENALTY.get(l_counter, 1.0)
    # 上流はここで wM = 1/(sumMeasure-0.5), wR = 1/(sumReference-0.5) を計算するが、
    # どちらも以降どこにも使われていない（brief 記載の瑕疵）。移植しない。

    cutoff = 0.01
    scalar_m = scalar_r = covariance = 0.0
    for entry_m, entry_r in zip(measured_buf, reference_buf):
        if entry_m[1] < cutoff:
            continue
        scalar_m += entry_m[1] * entry_m[0]
        scalar_r += entry_r[1] * entry_r[0]
        covariance += math.sqrt(entry_m[1] * entry_r[1]) * entry_m[0]

    if scalar_m == 0 or scalar_r == 0:
        return 0.0
    return (covariance ** 2) / scalar_m / scalar_r * penalty


def reverse_dot_product(measured, reference, *, bin_width, mass_begin=0.0, mass_end=2000.0):
    """`GetReverseDotProduct` の写し（**二乗値**）。カーソルは参照グリッドだけを進む。

    weighted との違いは 2 つ: (1) カーソルが `peaks2[idx_l]`（参照）だけで進む、
    (2) 最終合算のカットオフ判定を測定側でなく**参照側**の強度で行う。
    """
    peaks1 = _prepare(measured)
    peaks2 = _prepare(reference)
    if not _is_compared_available(peaks1, peaks2):
        return -1.0

    last2 = peaks2[-1][0]

    min_mz = peaks2[0][0]
    max_mz = last2
    if mass_begin > min_mz:
        min_mz = mass_begin
    if max_mz > mass_end:
        max_mz = mass_end

    focused_mz = min_mz
    idx_m = idx_l = 0
    measured_buf = []
    reference_buf = []
    base_m = base_r = -math.inf

    while focused_mz <= max_mz:
        sum_l, idx_l = _scan_sticky(peaks2, idx_l, focused_mz, bin_width)
        sum_m, idx_m = _scan_sticky(peaks1, idx_m, focused_mz, bin_width)

        # 上流の `if (sumM <= 0) {...} else { counter++; }` は counter を使うが、
        # counter 自体は戻り値に影響しない（バッファへの記録は両枝で同一）ので、
        # 分岐ごと落として共通処理だけ残す。
        measured_buf.append([focused_mz, sum_m])
        if sum_m > base_m:
            base_m = sum_m
        reference_buf.append([focused_mz, sum_l])
        if sum_l > base_r:
            base_r = sum_l

        if focused_mz + bin_width > last2:
            break
        focused_mz = peaks2[idx_l][0]

    if base_m == 0 or base_r == 0:
        return 0.0

    sum_measure = 0.0
    sum_reference = 0.0
    l_counter = 0
    for entry_m, entry_r in zip(measured_buf, reference_buf):
        entry_m[1] = entry_m[1] / base_m
        entry_r[1] = entry_r[1] / base_r
        sum_measure += entry_m[1]
        sum_reference += entry_r[1]
        if entry_r[1] > 0.1:
            l_counter += 1

    penalty = _PEAK_COUNT_PENALTY.get(l_counter, 1.0)
    # wM / wR は weighted と同様、上流でも未使用。移植しない。

    cutoff = 0.01
    scalar_m = scalar_r = covariance = 0.0
    for entry_m, entry_r in zip(measured_buf, reference_buf):
        if entry_r[1] < cutoff:      # weighted と違い、参照側でカットオフを掛ける。
            continue
        scalar_m += entry_m[1] * entry_m[0]
        scalar_r += entry_r[1] * entry_r[0]
        covariance += math.sqrt(entry_m[1] * entry_r[1]) * entry_m[0]

    if scalar_m == 0 or scalar_r == 0:
        return 0.0
    return (covariance ** 2) / scalar_m / scalar_r * penalty


def simple_dot_product(measured, reference, *, bin_width, mass_begin=0.0, mass_end=2000.0):
    """`GetSimpleDotProduct` の写し（**二乗値**）。penalty も m/z 重みも無い。

    カーソルは `_scan_advancing` を使うので、両方の配列末尾に達すると
    `len(peaks)` に等しくなり得る。上流同様、その越境を明示的に処理する。
    """
    peaks1 = _prepare(measured)
    peaks2 = _prepare(reference)
    if not _is_compared_available(peaks1, peaks2):
        return -1.0

    last1 = peaks1[-1][0]
    last2 = peaks2[-1][0]

    max_mz = max(last1, last2)
    if max_mz > mass_end:
        max_mz = mass_end

    idx_m = 0
    while idx_m < len(peaks1) and peaks1[idx_m][0] < mass_begin - bin_width:
        idx_m += 1
    idx_l = 0
    while idx_l < len(peaks2) and peaks2[idx_l][0] < mass_begin - bin_width:
        idx_l += 1

    focused_mz = min(
        peaks1[idx_m][0] if idx_m < len(peaks1) else math.inf,
        peaks2[idx_l][0] if idx_l < len(peaks2) else math.inf,
    )

    measured_buf = []
    reference_buf = []
    base_m = base_r = -math.inf

    while focused_mz <= max_mz:
        sum_m, idx_m = _scan_advancing(peaks1, idx_m, focused_mz, bin_width)
        sum_r, idx_l = _scan_advancing(peaks2, idx_l, focused_mz, bin_width)

        measured_buf.append([focused_mz, sum_m])
        if sum_m > base_m:
            base_m = sum_m
        reference_buf.append([focused_mz, sum_r])
        if sum_r > base_r:
            base_r = sum_r

        if focused_mz + bin_width > max(last1, last2):
            break
        if idx_m >= len(peaks1) or idx_l >= len(peaks2):
            focused_mz = peaks1[idx_m][0] if idx_l >= len(peaks2) else peaks2[idx_l][0]
            continue
        next_m = peaks1[idx_m][0]
        next_l = peaks2[idx_l][0]
        if focused_mz + bin_width > next_l and focused_mz + bin_width <= next_m:
            focused_mz = next_m
        elif focused_mz + bin_width <= next_l and focused_mz + bin_width > next_m:
            focused_mz = next_l
        else:
            focused_mz = min(next_m, next_l)

    if base_m == 0 or base_r == 0:
        return 0.0

    for entry in measured_buf:
        # 上流の `* 999` は比になる時点で打ち消えるが、忠実に写す（意味は無い）。
        entry[1] = entry[1] / base_m * 999
    for entry in reference_buf:
        entry[1] = entry[1] / base_r * 999

    scalar_m = sum(entry[1] for entry in measured_buf)
    scalar_r = sum(entry[1] for entry in reference_buf)
    covariance = sum(
        math.sqrt(entry_m[1] * entry_r[1]) for entry_m, entry_r in zip(measured_buf, reference_buf)
    )

    if scalar_m == 0 or scalar_r == 0:
        return 0.0
    return (covariance ** 2) / scalar_m / scalar_r


def _matched_peaks_walk(measured, reference, bin_width, mass_begin, mass_end):
    """`GetMatchedPeaksScores` の走査本体。matched_peaks_scores と match_spectrum の
    alignment が共有する。比較不能なら (None, None, []) を返す。"""
    peaks1 = _prepare(measured)
    peaks2 = _prepare(reference)
    if not _is_compared_available(peaks1, peaks2):
        return None, None, []

    last2 = peaks2[-1][0]

    min_mz = peaks2[0][0]
    max_mz = last2
    if mass_begin > min_mz:
        min_mz = mass_begin
    if max_mz > mass_end:
        max_mz = mass_end

    focused_mz = min_mz
    idx_m = idx_l = 0
    counter = 0
    lib_counter = 0
    max_lib_intensity = max(p[1] for p in peaks2)
    records = []

    while focused_mz <= max_mz:
        sum_l, idx_l = _scan_sticky(peaks2, idx_l, focused_mz, bin_width)
        is_lib_hit = sum_l >= 0.01 * max_lib_intensity
        if is_lib_hit:
            lib_counter += 1

        sum_m, idx_m = _scan_sticky(peaks1, idx_m, focused_mz, bin_width)
        matched = sum_m > 0 and is_lib_hit
        if matched:
            counter += 1

        records.append({"mz": focused_mz, "measured": sum_m, "reference": sum_l, "matched": matched})

        if focused_mz + bin_width > last2:
            break
        focused_mz = peaks2[idx_l][0]

    return counter, lib_counter, records


def matched_peaks_scores(measured, reference, *, bin_width, mass_begin=0.0, mass_end=2000.0):
    """`GetMatchedPeaksScores` の写し。戻り値は `(percentage, count)`。"""
    counter, lib_counter, _records = _matched_peaks_walk(measured, reference, bin_width, mass_begin, mass_end)
    if counter is None:
        return (-1.0, -1.0)
    if lib_counter == 0:
        return (0.0, 0.0)
    return (counter / lib_counter, float(counter))


def _normalized_by_total(peaks):
    """`SpectrumHandler.GetNormalizedByTotalIntensityPeaks` の写し。"""
    total = sum(intensity for _, intensity in peaks)
    return [(mz, intensity / total) for mz, intensity in peaks]


def _binned_spectrum(peaks, bin_width, halve=False):
    """`SpectrumHandler.GetBinnedSpectrum` / `GetCombinedSpectrum` が使う質量フレーム
    への集約の写し。`massframe = int(mz / bin_width)`（C# の `(int)` キャストは 0 方向への
    切り捨てだが、質量は正なので floor と同じ）ごとにグループ化し、代表 m/z はそのグループ内で
    強度最大のピークの m/z、強度は合算（`halve=True` のときだけ 0.5 倍。`GetCombinedSpectrum`
    が両スペクトルを混ぜたときの平均化に使う 0.5 倍で、単独スペクトルの binning には掛からない）。
    """
    buckets = {}
    for mz, intensity in peaks:
        frame = int(mz / bin_width)
        buckets.setdefault(frame, []).append((mz, intensity))

    result = []
    for group in buckets.values():
        max_mz = max(group, key=lambda p: p[1])[0]
        total = sum(intensity for _, intensity in group)
        if halve:
            total *= 0.5
        result.append((max_mz, total))
    return result


def _entropy(peaks):
    """`GetSpectralEntropy` の写し。"""
    total = sum(intensity for _, intensity in peaks)
    return -sum((intensity / total) * math.log2(intensity / total) for _, intensity in peaks)


def spectral_entropy_similarity(measured, reference, *, bin_width):
    """`GetSpectralEntropySimilarity` の写し。Li et al. 2021 の低エントロピー重み変換は
    入っていない（上流にも無い）。足さない。"""
    peaks1 = _prepare(measured)
    peaks2 = _prepare(reference)
    if not _is_compared_available(peaks1, peaks2):
        return -1.0

    norm1 = _normalized_by_total(peaks1)
    norm2 = _normalized_by_total(peaks2)
    combined = _binned_spectrum(norm1 + norm2, bin_width, halve=True)

    entropy12 = _entropy(combined)
    entropy1 = _entropy(_binned_spectrum(peaks1, bin_width))
    entropy2 = _entropy(_binned_spectrum(peaks2, bin_width))

    return 1 - (2 * entropy12 - entropy1 - entropy2) * 0.5


def normalize_measured(spectrum, *, relative_amp_cutoff=0.0, absolute_amp_cutoff=0.0):
    """`DataAccess.GetNormalizedMs2Spectra` の写し。**測定側にだけ**掛ける前処理。

    1. 足切り: `intensity > max(original) * relative_amp_cutoff and intensity > absolute_amp_cutoff`
       を満たすピークだけを残す。**並べ替えはしない**（元の並び順を保つ。上流も同じ）。
       閾値の分母は足切り前の元の最大強度であって、生き残ったピークの最大強度ではない
       （上流 `maxIntensity` は足切りループの外で一度だけ計算される）。
    2. 再スケール: 残ったピークを `intensity / max(original) * 100` にする。

    **再スケールは本モジュールの 5 種のスコア関数すべてに対して数学的に no-op である。**
    dot product 3 種は各窓の合算値を自分自身の最大値で割ってから使うので測定強度の定数倍は
    打ち消える。matched peaks は `sumM > 0` の真偽しか見ない。entropy は総和で正規化する。
    どの経路も入力を定数倍しても結果は変わらない。**忠実性のため実装はするが、数値が
    合わないときにここ（再スケール）を疑って時間を溶かさないこと** — 実際に効くのは
    足切りのほうだけである。

    既定 `relative_amp_cutoff=0.0` / `absolute_amp_cutoff=0.0`
    （`MsRefSearchParameterBase` の Key 7 / 8 の既定値）。空スペクトルは空のまま返す。
    """
    if not spectrum:
        return []
    peaks = [(float(p[0]), float(p[1])) for p in spectrum]
    max_intensity = max(intensity for _, intensity in peaks)

    kept = []
    for mz, intensity in peaks:
        if intensity > max_intensity * relative_amp_cutoff and intensity > absolute_amp_cutoff:
            kept.append([mz, intensity / max_intensity * 100.0])
    return kept


def _sqrt_or_sentinel(value):
    """-1（比較不能の番兵）はそのまま通す。sqrt(-1) の NaN に化けさせない。"""
    if value < 0:
        return -1.0
    return math.sqrt(value)


def match_spectrum(measured, reference, *, ms2_tol, mass_begin=0.0, mass_end=2000.0,
                    relative_amp_cutoff=0.0, absolute_amp_cutoff=0.0):
    """5 種のスコアを一括計算する。前処理（`normalize_measured`）は測定側にのみ掛ける。

    dot product 3 種は mzTab の `id_confidence_measure` と揃えるため平方根を返す
    （このモジュールの単体関数は二乗値を返す。spec §5.1）。`-1` の番兵は sqrt を経ても
    そのまま `-1` で通す。`alignment` は参照グリッドの窓ごとの記録
    （`{"mz", "measured", "reference", "matched"}`）で、対向プロットの注釈に使う。
    """
    normalized = normalize_measured(
        measured, relative_amp_cutoff=relative_amp_cutoff, absolute_amp_cutoff=absolute_amp_cutoff)

    simple = simple_dot_product(normalized, reference, bin_width=ms2_tol,
                                 mass_begin=mass_begin, mass_end=mass_end)
    weighted = weighted_dot_product(normalized, reference, bin_width=ms2_tol,
                                     mass_begin=mass_begin, mass_end=mass_end)
    reverse = reverse_dot_product(normalized, reference, bin_width=ms2_tol,
                                   mass_begin=mass_begin, mass_end=mass_end)
    percentage, count = matched_peaks_scores(normalized, reference, bin_width=ms2_tol,
                                              mass_begin=mass_begin, mass_end=mass_end)
    entropy = spectral_entropy_similarity(normalized, reference, bin_width=ms2_tol)
    _, _, alignment = _matched_peaks_walk(normalized, reference, ms2_tol, mass_begin, mass_end)

    return {
        "simple_dot_product": _sqrt_or_sentinel(simple),
        "weighted_dot_product": _sqrt_or_sentinel(weighted),
        "reverse_dot_product": _sqrt_or_sentinel(reverse),
        "matched_peaks_percentage": percentage,
        "matched_peaks_count": count,
        "entropy_similarity": entropy,
        "alignment": alignment,
    }
