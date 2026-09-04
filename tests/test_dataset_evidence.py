"""mzTab-M 経路の evidence sidecar（gap-fill 由来の検出状態）。

mzTab-M は「非ゼロ値」しか持たず、その値が実測ピークか gap-fill による補間かを
区別できない。隣接する `.arf` は (スポット × サンプル) の粒度で
`MasterPeakID < 0` = gap-fill を持っているので、同一アライメントであると
**実測で確認できた場合だけ** 取り込む。

ID 空間の接合は名前ではなく数値で検証する（実データ確認: `.arf` のスポット順が
mzTab の SMF_ID と一致し、1 つずらすと m/z 差が 6.7 mDa → 87 Da に爆発する）。
"""
import numpy as np


def _raw_sample_row(file_id: int, name: str, height: float, mz: float,
                    master_peak_id: int):
    """`.arf` の AlignedPeakProperties 1 行（msgpack 配列）の最小形。

    `_convert_to_alignment_feature` が読む位置だけを埋める:
    Key0=FileID / Key2=MasterPeakID（負なら gap-fill）/ Key15=時刻 /
    Key18=Height / Key22=m/z。ファイル名は Key0..9 の最初の文字列から拾われる。
    """
    row = [None] * 38
    row[0] = file_id
    row[1] = name
    row[2] = master_peak_id
    row[3] = 0
    row[10] = {}
    row[15] = [[1, [1.23]]]          # ChromXs: tag 1 = RT
    row[18] = height
    row[22] = mz
    row[23] = 1
    row[24] = ""
    row[37] = [0.0, 10.0]
    return row


def _spot(mz: float, samples):
    """`deserialize()` が返すスポット 1 件の最小形。"""
    return {"MasterAlignmentID": None, "AlignmentID": None, "RT": 1.0,
            "MassCenter": mz, "AlignedPeakProperties": samples}


def _two_by_two():
    """特徴 2 × サンプル 2。s1 は両方検出、s2 は特徴 1 のみ gap-fill。"""
    return [
        _spot(700.5, [
            _raw_sample_row(0, "s1", 1000.0, 700.5, 10),
            _raw_sample_row(1, "s2", 900.0, 700.5, 11),
        ]),
        _spot(800.25, [
            _raw_sample_row(0, "s1", 500.0, 800.25, 12),
            _raw_sample_row(1, "s2", 50.0, 800.25, -2),
        ]),
    ]


def test_normalize_arf_spots_reads_gap_fill_per_cell():
    from lipidmix.mztab import evidence
    norm = evidence.normalize_arf_spots(_two_by_two())
    assert [s["mz"] for s in norm] == [700.5, 800.25]
    assert [c["name"] for c in norm[0]["samples"]] == ["s1", "s2"]
    assert [c["is_gap_filled"] for c in norm[0]["samples"]] == [False, False]
    assert [c["is_gap_filled"] for c in norm[1]["samples"]] == [False, True]


def test_build_evidence_joins_by_position_and_name():
    from lipidmix.mztab import evidence
    result = evidence.build_evidence(
        evidence.normalize_arf_spots(_two_by_two()),
        feature_mz=[700.5, 800.25],
        sample_names=["s1", "s2"],
    )
    assert result["status"] == "ok"
    mask = result["detected_mask"]
    assert mask.shape == (2, 2)
    assert mask.dtype == np.bool_
    # gap-fill セル（特徴 2 × s2）だけが未検出
    assert mask.tolist() == [[True, True], [True, False]]
    assert result["n_cells"] == 4
    assert result["n_detected"] == 3


def test_build_evidence_reorders_columns_to_match_sample_names():
    """列順は mzTab の assay 順に合わせる（.arf の FileID 順とは限らない）。"""
    from lipidmix.mztab import evidence
    result = evidence.build_evidence(
        evidence.normalize_arf_spots(_two_by_two()),
        feature_mz=[700.5, 800.25],
        sample_names=["s2", "s1"],
    )
    assert result["status"] == "ok"
    assert result["detected_mask"].tolist() == [[True, True], [False, True]]


def test_build_evidence_rejects_feature_count_mismatch():
    from lipidmix.mztab import evidence
    result = evidence.build_evidence(
        evidence.normalize_arf_spots(_two_by_two()),
        feature_mz=[700.5],
        sample_names=["s1", "s2"],
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "feature_count_mismatch"


def test_build_evidence_rejects_mz_mismatch():
    """位置一致が成り立たないなら取り込まない（誤接合は静かに嘘をつく）。"""
    from lipidmix.mztab import evidence
    result = evidence.build_evidence(
        evidence.normalize_arf_spots(_two_by_two()),
        feature_mz=[800.25, 700.5],          # 順序が入れ替わっている
        sample_names=["s1", "s2"],
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "mz_mismatch"
    assert result["detail"]["worst_mz_delta"] > 0.01


def test_build_evidence_accepts_small_mz_deviation():
    """スポット代表値と平均値の差（実データで最大 6.7 mDa）は許容する。"""
    from lipidmix.mztab import evidence
    result = evidence.build_evidence(
        evidence.normalize_arf_spots(_two_by_two()),
        feature_mz=[700.5067, 800.2433],
        sample_names=["s1", "s2"],
    )
    assert result["status"] == "ok"
    assert result["detail"]["worst_mz_delta"] < 0.01


def test_build_evidence_rejects_sample_axis_mismatch():
    from lipidmix.mztab import evidence
    result = evidence.build_evidence(
        evidence.normalize_arf_spots(_two_by_two()),
        feature_mz=[700.5, 800.25],
        sample_names=["s1", "other"],
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "sample_axis_mismatch"
    assert "other" in result["detail"]["missing_in_arf"]


def test_detection_rates_are_per_feature():
    """率の定義は形式非依存なので analysis 側が正準（evidence から再輸出しない）。"""
    from lipidmix.analysis.preprocessing import detection_rates
    mask = np.array([[True, True], [True, False]])
    assert detection_rates(mask).tolist() == [1.0, 0.5]


# --- DatasetState への適用 ---

def _ds_with_features(n_features=2, sample_names=("s1", "s2")):
    from lipidmix.mztab.dataset_state import DatasetState
    ds = DatasetState()
    ds.feature_ids = [str(i) for i in range(n_features)]
    ds.sample_names = list(sample_names)
    ds.feature_matrix = np.ones((n_features, len(sample_names)))
    return ds


def test_apply_evidence_sets_mask_and_summary():
    from lipidmix.mztab import evidence
    ds = _ds_with_features()
    result = evidence.build_evidence(
        evidence.normalize_arf_spots(_two_by_two()),
        feature_mz=[700.5, 800.25], sample_names=["s1", "s2"])
    evidence.apply_evidence(ds, result)

    assert ds.detected_mask is not None
    assert ds.detected_mask.shape == (2, 2)
    assert ds.feature_qc["source"] == "arf"
    assert ds.feature_qc["n_detected"] == 3
    assert ds.feature_qc["gap_filled_rate"] == 0.25


def test_apply_evidence_rejected_leaves_no_mask_and_warns():
    """取り込めなかったことを明示する。黙って検出 0 扱いにしない。"""
    from lipidmix.mztab import evidence
    ds = _ds_with_features()
    evidence.apply_evidence(ds, {
        "status": "rejected", "reason": "mz_mismatch",
        "detail": {"worst_mz_delta": 5.0, "path": "x.arf"},
    })
    assert ds.detected_mask is None
    assert ds.feature_qc["source"] is None
    assert ds.feature_qc["reason"] == "mz_mismatch"
    warnings = ds.validation_result.get("warnings", [])
    assert any("gap-fill" in w or "検出" in w for w in warnings)


def test_apply_evidence_none_records_absence():
    """候補 .arf が 1 つも無い場合も、状態が無いことを記録する。"""
    from lipidmix.mztab import evidence
    ds = _ds_with_features()
    evidence.apply_evidence(ds, None)
    assert ds.detected_mask is None
    assert ds.feature_qc["source"] is None
    assert ds.feature_qc["reason"] == "no_candidate"


def test_arf_candidates_prefers_peak_properties_and_skips_drift(tmp_path):
    from lipidmix.mztab import evidence
    (tmp_path / "A_PeakProperties.arf").write_bytes(b"x")
    (tmp_path / "A_DriftSopts.arf").write_bytes(b"x")     # MS-DIAL の綴りゆれ
    (tmp_path / "A_DriftSpots.arf").write_bytes(b"x")
    (tmp_path / "B_other.arf").write_bytes(b"x")
    mztab = tmp_path / "Height_A.mzTab"
    mztab.write_text("MTD\tx\ty\n", encoding="utf-8")

    names = [p.name for p in evidence.arf_candidates(mztab)]
    assert names[0] == "A_PeakProperties.arf"
    assert "B_other.arf" in names
    assert not any("Drift" in n for n in names)


def test_arf_candidates_puts_handoff_artifact_first(tmp_path):
    from lipidmix.mztab import evidence
    (tmp_path / "A_PeakProperties.arf").write_bytes(b"x")
    recorded = tmp_path / "recorded_PeakProperties.arf"
    recorded.write_bytes(b"x")
    mztab = tmp_path / "Height_A.mzTab"
    mztab.write_text("MTD\tx\ty\n", encoding="utf-8")

    names = [p.name for p in evidence.arf_candidates(
        mztab, {"peak_matrix_source": [str(recorded)]})]
    assert names[0] == "recorded_PeakProperties.arf"
