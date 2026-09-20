"""採点エンジン。上流 `MsScanMatching.cs` の定義の写し。

**ここで固定している値は上流の挙動そのもの**で、素直な実装に直すと落ちる。
瑕疵を含めて写しているのは mzTab の数値と比較可能にするため（spec §6.3）。
"""
import math

import pytest

from lipidmix.analysis import spectral_match as sm

# 参照窓のうち「正規化強度 > 0.1」が 5 個以上になるようにしてある。4 個以下だと
# weighted / reverse に peakCountPenalty が掛かり、同一スペクトルでも 1.0 にならない。
_A = [[100.0, 999.0], [200.0, 800.0], [300.0, 600.0], [400.0, 400.0], [500.0, 200.0]]


def test_an_identical_spectrum_scores_one():
    for fn in (sm.simple_dot_product, sm.weighted_dot_product, sm.reverse_dot_product):
        assert fn(_A, _A, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)


def test_the_penalty_applies_even_to_an_identical_spectrum():
    """参照窓が 4 個以下なら同一でも 1.0 にならない。penalty は「似ていなさ」ではなく
    「参照の情報量の乏しさ」への減点なので、一致度とは独立に掛かる。"""
    three = [[100.0, 999.0], [200.0, 800.0], [300.0, 600.0]]
    assert sm.weighted_dot_product(three, three, bin_width=0.01) == pytest.approx(0.94, abs=1e-9)
    assert sm.simple_dot_product(three, three, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)


def test_a_disjoint_spectrum_scores_zero():
    other = [[150.0, 999.0], [250.0, 500.0]]
    assert sm.simple_dot_product(_A, other, bin_width=0.01) == pytest.approx(0.0, abs=1e-9)


def test_an_empty_spectrum_returns_the_not_computed_sentinel():
    """上流は比較不能を 0 ではなく -1 で返す。0（＝合わない）と混同しない。"""
    assert sm.simple_dot_product([], _A, bin_width=0.01) == -1.0
    assert sm.weighted_dot_product(_A, [], bin_width=0.01) == -1.0
    assert sm.matched_peaks_scores(_A, [], bin_width=0.01) == (-1.0, -1.0)
    assert sm.spectral_entropy_similarity([], [], bin_width=0.01) == -1.0


def test_a_single_peak_reference_is_penalised():
    """正規化強度 > 0.1 の参照窓が 1 個なら penalty 0.75（weighted / reverse のみ）。"""
    one = [[100.0, 999.0]]
    assert sm.weighted_dot_product(one, one, bin_width=0.01) == pytest.approx(0.75, abs=1e-9)
    assert sm.reverse_dot_product(one, one, bin_width=0.01) == pytest.approx(0.75, abs=1e-9)
    # simple には penalty が無い。
    assert sm.simple_dot_product(one, one, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)


def test_the_penalty_ladder_matches_upstream():
    for n, expected in ((1, 0.75), (2, 0.88), (3, 0.94), (4, 0.97), (5, 1.0)):
        spec = [[100.0 + 10 * i, 999.0] for i in range(n)]
        assert sm.weighted_dot_product(spec, spec, bin_width=0.01) == pytest.approx(expected, abs=1e-9)


def test_reverse_ignores_measured_peaks_absent_from_the_reference():
    """reverse は参照グリッドだけを歩く。測定側の余分なピークは効かない。"""
    reference = [[100.0, 999.0], [200.0, 999.0], [300.0, 999.0], [400.0, 999.0], [500.0, 999.0]]
    measured = reference + [[777.0, 999.0]]
    assert sm.reverse_dot_product(measured, reference, bin_width=0.01) == pytest.approx(
        sm.reverse_dot_product(reference, reference, bin_width=0.01), abs=1e-9)


def test_simple_is_reduced_by_an_extra_measured_peak():
    """simple は両方のグリッドを歩くので、測定側の余分なピークが効く。"""
    reference = [[100.0, 999.0], [200.0, 999.0]]
    measured = reference + [[777.0, 999.0]]
    assert sm.simple_dot_product(measured, reference, bin_width=0.01) < sm.simple_dot_product(
        reference, reference, bin_width=0.01)


def test_matched_peaks_counts_reference_windows_above_one_percent():
    reference = [[100.0, 999.0], [200.0, 999.0], [300.0, 1.0]]   # 3 本目は 1% 未満
    measured = [[100.0, 10.0]]
    percentage, count = sm.matched_peaks_scores(measured, reference, bin_width=0.01)
    assert count == 1.0
    assert percentage == pytest.approx(0.5)     # libCounter は 2


def test_entropy_similarity_of_an_identical_spectrum_is_one():
    assert sm.spectral_entropy_similarity(_A, _A, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)


def test_match_spectrum_returns_the_square_rooted_values():
    """mzTab に出ているのは平方根側。比較の土俵を合わせる（spec §5.1）。"""
    result = sm.match_spectrum(_A, _A, ms2_tol=0.01)
    assert result["simple_dot_product"] == pytest.approx(
        math.sqrt(sm.simple_dot_product(_A, _A, bin_width=0.01)), abs=1e-9)
    assert result["matched_peaks_count"] == 5.0
    assert result["entropy_similarity"] == pytest.approx(1.0, abs=1e-9)


def test_the_not_computed_sentinel_survives_the_square_root():
    """-1 は「比較していない」。sqrt(-1) の NaN に化けさせない。"""
    result = sm.match_spectrum([], _A, ms2_tol=0.01)
    assert result["simple_dot_product"] == -1.0
    assert result["weighted_dot_product"] == -1.0
    assert result["reverse_dot_product"] == -1.0


def test_match_spectrum_reports_the_alignment():
    """数値には出ない「どの窓が合ったか」。対向プロットの注釈がこれを読む。"""
    reference = [[100.0, 999.0], [200.0, 500.0]]
    measured = [[100.0, 999.0]]
    alignment = sm.match_spectrum(measured, reference, ms2_tol=0.01)["alignment"]
    assert [(round(a["mz"], 3), a["matched"]) for a in alignment] == [(100.0, True), (200.0, False)]


def test_unsorted_input_is_sorted_before_scoring():
    """上流の走査は m/z 昇順を前提にしている。並べ替えずに渡すと静かに壊れる。"""
    shuffled = list(reversed(_A))
    assert sm.simple_dot_product(shuffled, _A, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------------------
# Important 4（最終レビュー）: 縮退した参照/測定スペクトルで例外を投げない。
# --------------------------------------------------------------------------
def test_entropy_similarity_with_a_reference_containing_a_zero_intensity_peak():
    """`spectral_entropy_similarity([[100,10]], [[100,0],[200,5]])` は以前
    `math.log2(0)` の定義域エラー（ValueError）を投げていた。総強度は非ゼロ
    （0+5=5）だが個々のピーク強度に 0 が混ざるケース。0*log2(0)=0 として
    寄与なし扱いにするだけで、比較不能（-1）にはしない。"""
    result = sm.spectral_entropy_similarity([[100.0, 10.0]], [[100.0, 0.0], [200.0, 5.0]], bin_width=0.01)
    assert result != -1.0
    assert not math.isnan(result)


def test_match_spectrum_with_an_all_zero_reference_does_not_raise():
    """`match_spectrum([[100,10]], [[100,0]])` は以前 entropy 計算内で
    ZeroDivisionError を投げていた（参照の総強度が 0）。空ではないので `-1`
    （比較不能）ではなく、他の 3 dot product が同じ状況（base_r==0）で返す
    `0.0`（＝合わなかった）に揃える。"""
    result = sm.match_spectrum([[100.0, 10.0]], [[100.0, 0.0]], ms2_tol=0.01)
    assert result["entropy_similarity"] == 0.0
    assert result["weighted_dot_product"] == 0.0
    assert result["simple_dot_product"] == 0.0
    assert result["reverse_dot_product"] == 0.0


def test_entropy_similarity_with_an_all_zero_measured_spectrum_does_not_raise():
    """測定側が縮退（総強度 0）の場合も同様に例外を投げず 0.0 を返す。"""
    result = sm.spectral_entropy_similarity([[100.0, 0.0]], [[100.0, 10.0], [200.0, 5.0]], bin_width=0.01)
    assert result == 0.0


def test_entropy_similarity_still_returns_the_sentinel_for_a_genuinely_empty_spectrum():
    """縮退（総強度 0 だが非空）と比較不能（空）を混同しない回帰テスト。"""
    assert sm.spectral_entropy_similarity([], [[100.0, 10.0]], bin_width=0.01) == -1.0
    assert sm.spectral_entropy_similarity([[100.0, 0.0]], [], bin_width=0.01) == -1.0
