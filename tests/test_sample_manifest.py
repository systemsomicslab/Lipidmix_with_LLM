"""sample-manifest.v1 の厳密読込・対応・出所・原子的適用（spec §7）。

実験情報シートは前処理・比較の入力そのものなので、曖昧な行を機械的に埋めて
先へ進むと、群やバッチの取り違えが数値に混ざる。ここで縛るのは 3 段:

1. `parse_manifest` — シート自体の厳密検証（列・型・重複・予定rawとの対応）。
   1件でも不正なら**何も返さず**例外にする（部分的な行リストを返さない）。
2. `resolve_metadata` — 明示シート（あれば）と dataset 由来の推定を統合し、
   `ds.sample_names` と同じ順に並べる。シートの行順・assayの列順は一致していなくてよい
   （`ds.assay_sources` の対応表で結ぶ）。
3. `apply_metadata` — 対応・型を全件検証してから、Task 6 の指紋比較で
   「何が変わったか」を求め、無効化と代入を一括で行う。検証で落ちれば
   ds には一切触れない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lipidmix.analysis.dataset_analysis import build_dataset_pp_inputs
from lipidmix.analysis.sample_manifest import (
    apply_metadata,
    parse_injection_order,
    parse_manifest,
    resolve_metadata,
)
from lipidmix.console.validation import map_assays
from lipidmix.core.atomic_io import DomainError
from lipidmix.mztab.dataset_state import build_dataset_state
from lipidmix.mztab.reader import parse_mztab
from tests.pipeline_fixtures import make_dataset, metadata_rows, write_mztab

_HEADER = ("sample_id\tsource_file\trole\tgroup\tbatch\tinjection_order\t"
          "qc_pool\tinclude")


def _sheet(tmp_path, rows_text: str) -> Path:
    """schemaヘッダ+列見出し+本文行だけを与えて手早くシートを作る。"""
    sheet = tmp_path / "sample-manifest.tsv"
    sheet.write_text(
        "# schema = sample-manifest.v1\n" + _HEADER + "\n" + rows_text,
        encoding="utf-8",
    )
    return sheet


def _row(sample_id="s0", source_file="S0.wiff", role="sample", group="",
        batch="B1", injection_order="1", qc_pool="", include="true") -> str:
    return "\t".join([sample_id, source_file, role, group, batch,
                      injection_order, qc_pool, include]) + "\n"


def _touch(root: Path, *names: str) -> None:
    for name in names:
        (root / name).write_bytes(b"fake")


# ---------- parse_injection_order / RED（brief step 1） ----------

@pytest.mark.parametrize("order", ["NaN", "1.5", "0", "-1", "Infinity"])
def test_bad_order_is_rejected_before_application(tmp_path, order):
    root = tmp_path / "source"
    root.mkdir()
    (root / "S0.wiff").write_bytes(b"fake")
    sheet = root / "sample-manifest.tsv"
    sheet.write_text(
        "# schema = sample-manifest.v1\n"
        "sample_id\tsource_file\trole\tgroup\tbatch\tinjection_order\tqc_pool\tinclude\n"
        f"S0\tS0.wiff\tsample\tcontrol\tB1\t{order}\t\ttrue\n", encoding="utf-8")
    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_parse_injection_order_accepts_blank_and_positive_int():
    assert parse_injection_order("") is None
    assert parse_injection_order("7") == 7


# ---------- parse_manifest: 正常系 ----------

def test_parse_manifest_builds_confirmed_provenance(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff")
    sheet = _sheet(tmp_path, _row())

    rows = parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])

    assert len(rows) == 1
    row = rows[0]
    assert row["sample_id"] == "s0"
    assert row["role"] == "sample"
    assert row["batch"] == "B1"
    assert row["injection_order"] == 1
    assert row["include"] is True
    assert row["group"] is None  # 空欄は欠落値
    assert row["provenance"]["role"] == {
        "value": "sample", "source": "user_manifest", "confidence": "confirmed"}
    assert row["provenance"]["group"] == {"value": None, "source": None, "confidence": None}
    assert row["conflicts"] == []


def test_parse_manifest_blank_include_defaults_true_but_is_not_confirmed(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff")
    sheet = _sheet(tmp_path, _row(include=""))

    rows = parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])

    assert rows[0]["include"] is True
    assert rows[0]["provenance"]["include"]["source"] == "default"
    assert rows[0]["provenance"]["include"]["confidence"] == "inferred"


# ---------- C01: 実行前拒否・部分反映なし ----------

def test_duplicate_sample_id_is_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff", "S1.wiff")
    text = _row(sample_id="dup", source_file="S0.wiff") + \
        _row(sample_id="dup", source_file="S1.wiff")
    sheet = _sheet(tmp_path, text)

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root,
                       expected_sources=["S0.wiff", "S1.wiff"])


def test_missing_row_for_a_planned_raw_is_rejected(tmp_path):
    """行欠落: 予定rawの一部にシート行がない。"""
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff", "S1.wiff")
    sheet = _sheet(tmp_path, _row(source_file="S0.wiff"))

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root,
                       expected_sources=["S0.wiff", "S1.wiff"])


def test_unknown_source_file_is_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff", "Extra.wiff")
    sheet = _sheet(tmp_path, _row(source_file="Extra.wiff"))

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_duplicate_raw_across_rows_is_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff")
    text = _row(sample_id="a", source_file="S0.wiff") + \
        _row(sample_id="b", source_file="S0.wiff")
    sheet = _sheet(tmp_path, text)

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_case_insensitive_raw_collision_is_rejected_on_windows(tmp_path):
    """大文字小文字違いのsource_fileが同一rawへ衝突する場合も拒否する。"""
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff")
    text = _row(sample_id="a", source_file="S0.wiff") + \
        _row(sample_id="b", source_file="s0.WIFF")
    sheet = _sheet(tmp_path, text)

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_path_escaping_the_source_root_is_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside.wiff"
    outside.write_bytes(b"fake")
    text = _row(source_file="../outside.wiff")
    sheet = _sheet(tmp_path, text)

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root,
                       expected_sources=["../outside.wiff"])


def test_invalid_role_is_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff")
    sheet = _sheet(tmp_path, _row(role="control_group"))

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_invalid_include_value_is_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff")
    sheet = _sheet(tmp_path, _row(include="yes"))

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_extra_or_missing_columns_are_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    sheet = tmp_path / "sample-manifest.tsv"
    sheet.write_text(
        "# schema = sample-manifest.v1\n"
        "sample_id\tsource_file\trole\tgroup\tbatch\tinjection_order\tinclude\n"
        "s0\tS0.wiff\tsample\t\tB1\t1\ttrue\n", encoding="utf-8")

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_missing_schema_header_is_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff")
    sheet = tmp_path / "sample-manifest.tsv"
    sheet.write_text(_HEADER + "\n" + _row(), encoding="utf-8")

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_duplicate_injection_order_within_a_confirmed_batch_is_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff", "S1.wiff")
    text = _row(sample_id="a", source_file="S0.wiff", batch="B1", injection_order="1") + \
        _row(sample_id="b", source_file="S1.wiff", batch="B1", injection_order="1")
    sheet = _sheet(tmp_path, text)

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root,
                       expected_sources=["S0.wiff", "S1.wiff"])


def test_duplicate_injection_order_within_an_unconfirmed_batch_is_rejected(tmp_path):
    """batch空欄でも、同じ未確認batch内の注入順重複は拒否する。"""
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff", "S1.wiff")
    text = _row(sample_id="a", source_file="S0.wiff", batch="", injection_order="1") + \
        _row(sample_id="b", source_file="S1.wiff", batch="", injection_order="1")
    sheet = _sheet(tmp_path, text)

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root,
                       expected_sources=["S0.wiff", "S1.wiff"])


def test_include_false_rows_are_still_checked_for_correspondence(tmp_path):
    """include=falseは下流除外の指定であって、対応検証の免除ではない。"""
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff", "Extra.wiff")
    sheet = _sheet(tmp_path, _row(source_file="Extra.wiff", include="false"))

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])


def test_include_false_row_is_still_returned(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _touch(root, "S0.wiff")
    sheet = _sheet(tmp_path, _row(include="false"))

    rows = parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])

    assert rows[0]["include"] is False
    assert rows[0]["provenance"]["include"] == {
        "value": False, "source": "user_manifest", "confidence": "confirmed"}


# ---------- resolve_metadata: 明示シートなし（従来のdetect_sample_roles経路） ----------

def test_resolve_metadata_without_a_sheet_uses_detect_sample_roles(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    raws = [root / "Control_01.wiff", root / "QC_01.wiff"]
    for r in raws:
        r.write_bytes(b"fake")
    mztab_path = write_mztab(tmp_path / "result.mzTab", raws)
    parse_result = parse_mztab(mztab_path)
    ds = build_dataset_state(parse_result, mztab_path.name, mztab_path)
    ds.assay_sources = map_assays(parse_result, [str(r.resolve()) for r in raws])

    resolved = resolve_metadata(ds, None)

    assert len(resolved) == len(ds.sample_names)
    for row in resolved:
        assert row["group"] is None
        assert row["qc_pool"] is None
        assert row["provenance"]["role"]["source"] == "filename_token"
        assert row["provenance"]["role"]["confidence"] == "inferred"
    assert resolved[0]["sample_id"] == ds.sample_names[0]  # 表示名を上書きしない


def test_resolve_metadata_without_a_sheet_marks_mztab_injection_order_unverified(tmp_path):
    """C03: mzTabの注入順は読込順由来か判定できないのでunverifiedに留める。"""
    root = tmp_path / "source"
    root.mkdir()
    raws = [root / "S1.wiff", root / "S2.wiff"]
    for r in raws:
        r.write_bytes(b"fake")
    mztab_path = write_mztab(tmp_path / "result.mzTab", raws)
    _inject_assay_custom(mztab_path, injection_orders=[1, 2])
    parse_result = parse_mztab(mztab_path)
    ds = build_dataset_state(parse_result, mztab_path.name, mztab_path)
    ds.assay_sources = map_assays(parse_result, [str(r.resolve()) for r in raws])

    resolved = resolve_metadata(ds, None)

    for row in resolved:
        assert row["injection_order"] in (1, 2)
        assert row["provenance"]["injection_order"] == {
            "value": row["injection_order"], "source": "mztab", "confidence": "unverified"}


# ---------- resolve_metadata: 明示シートあり ----------

def _ds_from_raws(tmp_path, names, *, injection_orders=None, batches=None):
    root = tmp_path / "source"
    root.mkdir()
    raws = [root / name for name in names]
    for r in raws:
        r.write_bytes(b"fake")
    mztab_path = write_mztab(tmp_path / "result.mzTab", raws)
    if injection_orders or batches:
        _inject_assay_custom(mztab_path, injection_orders=injection_orders,
                             batches=batches)
    parse_result = parse_mztab(mztab_path)
    ds = build_dataset_state(parse_result, mztab_path.name, mztab_path)
    ds.assay_sources = map_assays(parse_result, [str(r.resolve()) for r in raws])
    return ds, root, raws


def _inject_assay_custom(mztab_path: Path, *, injection_orders=None, batches=None):
    """検証済みmzTabへ assay[N]-custom[...] のMTD行を後付けする（テスト専用）。"""
    text = mztab_path.read_text(encoding="utf-8")
    extra = []
    if injection_orders:
        for i, order in enumerate(injection_orders, start=1):
            extra.append(
                f"MTD\tassay[{i}]-custom[1]\t[MS,MS:4000089,injection sequence label,{order}]")
    if batches:
        for i, batch in enumerate(batches, start=1):
            extra.append(
                f"MTD\tassay[{i}]-custom[2]\t[MS,MS:4000088,batch label,{batch}]")
    marker = "SFH\t"
    assert marker in text
    text = text.replace(marker, "\n".join(extra) + "\n" + marker, 1)
    mztab_path.write_text(text, encoding="utf-8")


def test_resolve_metadata_reorders_the_sheet_to_match_assay_order(tmp_path):
    """C02: シートの行順とassayの列順が違っても対応表で正しく並べ替える。"""
    ds, root, raws = _ds_from_raws(tmp_path, ["a.wiff", "b.wiff", "c.wiff"])
    # シートは c, a, b の順で書く（assay順=a, b, cとは逆転させる）。
    text = (_row(sample_id="cc", source_file="c.wiff", injection_order="1")
           + _row(sample_id="aa", source_file="a.wiff", injection_order="2")
           + _row(sample_id="bb", source_file="b.wiff", injection_order="3"))
    sheet = _sheet(tmp_path, text)
    rows = parse_manifest(sheet, source_root=root,
                          expected_sources=["a.wiff", "b.wiff", "c.wiff"])

    resolved = resolve_metadata(ds, rows)

    assert [r["sample_id"] for r in resolved] == ["aa", "bb", "cc"]


def test_resolve_metadata_blank_role_in_a_sheet_defaults_to_sample_without_token_guessing(tmp_path):
    """シートがある以上、blank roleをQC/blankトークン推定へ回さない（本taskの範囲）。"""
    ds, root, raws = _ds_from_raws(tmp_path, ["QC_01.wiff"])
    text = _row(sample_id="s0", source_file="QC_01.wiff", role="")
    sheet = _sheet(tmp_path, text)
    rows = parse_manifest(sheet, source_root=root, expected_sources=["QC_01.wiff"])

    resolved = resolve_metadata(ds, rows)

    assert resolved[0]["role"] == "sample"
    assert resolved[0]["provenance"]["role"]["source"] == "default"


def test_resolve_metadata_does_not_reinfer_an_explicit_unknown_role(tmp_path):
    """明示role=unknownをsampleへ再推定しない。"""
    ds, root, raws = _ds_from_raws(tmp_path, ["QC_01.wiff"])
    text = _row(sample_id="s0", source_file="QC_01.wiff", role="unknown")
    sheet = _sheet(tmp_path, text)
    rows = parse_manifest(sheet, source_root=root, expected_sources=["QC_01.wiff"])

    resolved = resolve_metadata(ds, rows)

    assert resolved[0]["role"] == "unknown"
    assert resolved[0]["provenance"]["role"]["confidence"] == "confirmed"


def test_resolve_metadata_keeps_explicit_batch_and_records_mztab_conflict(tmp_path):
    """明示batchが優先されるが、異なるmzTab候補はconflictsに残す。"""
    ds, root, raws = _ds_from_raws(tmp_path, ["S0.wiff"], batches=["mztab_batch"])
    text = _row(sample_id="s0", source_file="S0.wiff", batch="sheet_batch")
    sheet = _sheet(tmp_path, text)
    rows = parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])

    resolved = resolve_metadata(ds, rows)

    assert resolved[0]["batch"] == "sheet_batch"
    assert resolved[0]["provenance"]["batch"]["source"] == "user_manifest"
    conflict = resolved[0]["conflicts"][0]
    assert conflict["field"] == "batch"
    assert conflict["value"] == "mztab_batch"
    assert conflict["source"] == "mztab"


def test_resolve_metadata_raises_when_a_sample_has_no_matching_row(tmp_path):
    """予定raw一覧が実assayと食い違う（対応が取れない）場合は安全側に倒す。"""
    ds, root, raws = _ds_from_raws(tmp_path, ["S0.wiff", "S1.wiff"])
    text = _row(sample_id="s0", source_file="S0.wiff")
    sheet = _sheet(tmp_path, text)
    # S1.wiffの行がないシートを、意図的にexpected_sourcesを緩めて通す
    # （parse_manifest自体は行欠落を拒否するので、ここではrows操作で直接検証する）。
    rows = parse_manifest(sheet, source_root=root, expected_sources=["S0.wiff"])

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        resolve_metadata(ds, rows)


# ---------- apply_metadata ----------

def test_apply_metadata_sets_sample_ids_without_overwriting_display_names():
    ds = make_dataset()
    rows = metadata_rows(ds)

    summary = apply_metadata(ds, rows)

    assert ds.sample_ids == [r["sample_id"] for r in rows]
    assert ds.sample_names == [f"S{i}" for i in range(8)]  # 表示名は不変
    assert ds.sample_metadata_rows == rows
    assert ds.metadata_revision == 1
    assert summary["metadata_revision"] == 1
    assert set(summary["changed_fields"]) == set(
        ["sample_id", "source_file", "role", "group", "batch",
         "injection_order", "qc_pool", "include"])


@pytest.mark.parametrize("n_qc", [0, 1, 2, 3, 4])
def test_apply_metadata_accepts_the_shared_fixture_contract(n_qc):
    ds = make_dataset()
    rows = metadata_rows(ds, n_qc=n_qc)

    summary = apply_metadata(ds, rows)

    assert summary["sample_ids"] == [r["sample_id"] for r in rows]


def test_apply_metadata_rejects_row_count_mismatch_and_leaves_dataset_untouched():
    ds = make_dataset()
    rows = metadata_rows(ds)[:-1]  # 1件足りない

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        apply_metadata(ds, rows)

    assert ds.sample_metadata_rows is None
    assert ds.metadata_revision == 0


def test_apply_metadata_rejects_bad_role_value_and_leaves_dataset_untouched():
    ds = make_dataset()
    rows = metadata_rows(ds)
    rows[0]["role"] = "not_a_role"

    with pytest.raises(DomainError, match="SAMPLE_MANIFEST_INVALID"):
        apply_metadata(ds, rows)

    assert ds.sample_metadata_rows is None


def test_apply_metadata_group_only_change_invalidates_only_the_comparison():
    """B04: groupだけの訂正はPCAを残し、差次的解析だけを無効化する。"""
    from lipidmix.analysis.dataset_service import compare_dataset, pca_dataset, preprocess_dataset

    ds = make_dataset()
    apply_metadata(ds, metadata_rows(ds, n_qc=0))
    preprocess_dataset(ds, {"normalize": "none", "impute": "half_min"})
    pca_dataset(ds)
    compare_dataset(ds, ds.sample_names[:4], ds.sample_names[4:])
    pp_matrix_before = ds.pp_matrix

    new_rows = metadata_rows(ds, n_qc=0)
    new_rows[4]["group"] = "changed"
    new_rows[4]["provenance"]["group"] = {
        "value": "changed", "source": "user_manifest", "confidence": "confirmed"}
    summary = apply_metadata(ds, new_rows)

    assert summary["changed_fields"] == ["group"]
    assert ds.pp_matrix is pp_matrix_before
    assert ds.last_pca is not None
    assert ds.last_differential is None


def test_apply_metadata_batch_change_invalidates_preprocessing():
    """B02系: 前処理に効く列が変わればpp_matrix以下を丸ごと無効化する。"""
    from lipidmix.analysis.dataset_service import pca_dataset, preprocess_dataset

    ds = make_dataset()
    apply_metadata(ds, metadata_rows(ds))
    preprocess_dataset(ds, {"normalize": "none", "impute": "half_min"})
    pca_dataset(ds)

    new_rows = metadata_rows(ds)
    new_rows[0]["batch"] = "B2"
    new_rows[0]["provenance"]["batch"] = {
        "value": "B2", "source": "user_manifest", "confidence": "confirmed"}
    apply_metadata(ds, new_rows)

    assert ds.pp_matrix is None
    assert ds.last_pca is None


def test_apply_metadata_reapplying_identical_rows_reports_no_change():
    ds = make_dataset()
    rows = metadata_rows(ds)
    apply_metadata(ds, rows)

    summary = apply_metadata(ds, metadata_rows(ds))

    assert summary["changed_fields"] == []
    assert ds.metadata_revision == 2


# ---------- dataset_analysis: 明示メタデータを優先する（従来経路は保持） ----------

def test_build_dataset_pp_inputs_prefers_explicit_metadata_when_applied():
    ds = make_dataset()
    apply_metadata(ds, metadata_rows(ds, n_qc=2))

    _, sample_names, _, roles, sample_meta = build_dataset_pp_inputs(ds)

    assert roles[sample_names[0]] == "qc"
    assert roles[sample_names[2]] == "sample"
    assert sample_meta[sample_names[0]]["batch"] == "B1"
    assert sample_meta[sample_names[0]]["run_order"] == 1
    assert sample_meta[sample_names[0]]["run_order_source"] == "user_manifest"


def test_build_dataset_pp_inputs_keeps_the_legacy_path_when_no_metadata_applied():
    """明示メタデータ未適用なら、既存のトークン/mzTab推定経路をそのまま使う。"""
    ds = make_dataset()  # sample_metadata_rows は None のまま

    _, sample_names, _, roles, sample_meta = build_dataset_pp_inputs(ds)

    assert set(roles.values()) <= {"sample", "qc", "blank"}
    assert all(sample_meta[name]["run_order_source"] != "user_manifest"
              for name in sample_names)


def test_build_dataset_pp_inputs_passes_unknown_role_and_excluded_rows_through():
    """role="unknown"・include=falseの除外はTask 12（比較ガード、spec §7.4）の
    責務であり、build_dataset_pp_inputsはそれを先取りしない。

    このテストは**現状の**素通り挙動を固定する（役割の絞り込みも除外もしない）。
    Task 12が比較からの除外を実装したら、このテストの前提（"unknown"と
    include=falseの行が前処理入力にそのまま残ること）ごと意図的に見直す必要が
    あるので、無言で挙動が変わらないための回帰テストとして残す。
    """
    ds = make_dataset()
    rows = metadata_rows(ds, n_qc=0)
    rows[0]["role"] = "unknown"
    rows[0]["provenance"]["role"] = {
        "value": "unknown", "source": "user_manifest", "confidence": "confirmed"}
    rows[1]["include"] = False
    rows[1]["provenance"]["include"] = {
        "value": False, "source": "user_manifest", "confidence": "confirmed"}
    apply_metadata(ds, rows)

    matrix, sample_names, _, roles, sample_meta = build_dataset_pp_inputs(ds)

    # role="unknown" はここでは "sample"/"qc"/"blank" のどれにも矯正されず、
    # そのまま preprocessing.preprocess() へ渡る（弾かれもしない）。
    assert roles[sample_names[0]] == "unknown"
    assert sample_meta[sample_names[0]]["role"] == "unknown"
    # include=false は前処理入力からの除外ではない（比較からの除外のみ）ので、
    # 行列・サンプル名からも落ちず、role も明示値のまま残る。
    assert sample_names[1] in sample_names
    assert roles[sample_names[1]] == "sample"
    assert matrix.shape[0] == len(ds.sample_names) == 8
