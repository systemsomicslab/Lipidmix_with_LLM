import unittest

from msdial_tags import normalize_sample_name
from sample_factors import (
    SampleFacet,
    arf_sample_names,
    build_sample_facets,
    expand_sample_specs,
    sample_tokens,
    split_tokens,
)


def name_class_index(name_to_class: dict[str, str]) -> dict:
    """discover_arf_class_index の戻りのうち、本モジュールが使う部分だけを作る。"""
    records = []
    by_file_name = {}
    for file_id, (name, class_id) in enumerate(name_to_class.items()):
        record = {"file_id": file_id, "file_name": name, "class_id": class_id}
        records.append(record)
        by_file_name[normalize_sample_name(name, strip_processing_timestamp=False)] = record
    return {"records": records, "by_file_id": {}, "by_file_name": by_file_name}


class SplitTokensTests(unittest.TestCase):
    def test_splits_on_underscore_and_casefolds(self):
        self.assertEqual(split_tokens("Cerebellum_gf_AIN"), frozenset({"cerebellum", "gf", "ain"}))

    def test_none_and_empty_yield_empty_set(self):
        self.assertEqual(split_tokens(None), frozenset())
        self.assertEqual(split_tokens("__"), frozenset())


class SampleTokensTests(unittest.TestCase):
    def test_strips_trailing_processing_timestamp(self):
        # 末尾12桁は MS-DIAL の処理タイムスタンプ（再処理ごとに変わる）で実験因子ではない。
        self.assertEqual(
            sample_tokens("20220902_RAW_ILG_6h_2_NEG_202605151012"),
            frozenset({"20220902", "raw", "ilg", "6h", "2", "neg"}),
        )

    def test_strips_known_measurement_suffix(self):
        self.assertEqual(sample_tokens("sample_A.wiff"), frozenset({"sample", "a"}))

    def test_merges_class_id_tokens(self):
        self.assertEqual(
            sample_tokens("s1", class_id="Cerebellum_gf_AIN"),
            frozenset({"s1", "cerebellum", "gf", "ain"}),
        )

    def test_merges_extra_label_tokens(self):
        self.assertEqual(sample_tokens("s1", extra="24M_GF"), frozenset({"s1", "24m", "gf"}))


class BuildSampleFacetsTests(unittest.TestCase):
    def test_multi_token_value_splits_into_two_tokens(self):
        # G_uralensis は2トークンに割れる。だから位置インデックスでの因子指定は使わない。
        name = "20220902_RAW_G_uralensis_6h_2_NEG"
        facets = build_sample_facets([name], name_class_index({name: "G"}))
        self.assertEqual(
            facets[name].tokens,
            frozenset({"20220902", "raw", "g", "uralensis", "6h", "2", "neg"}),
        )
        self.assertEqual(facets[name].class_id, "G")
        self.assertEqual(facets[name].file_id, 0)
        self.assertEqual(facets[name].role, "sample")

    def test_works_without_class_index(self):
        # .mddata が無いフォルダでもサンプル名トークンだけで成立する。
        facets = build_sample_facets(["20220901_RAW_LPS_6h_1_NEG"], None)
        facet = facets["20220901_RAW_LPS_6h_1_NEG"]
        self.assertIsNone(facet.class_id)
        self.assertIsNone(facet.file_id)
        self.assertIn("lps", facet.tokens)
        self.assertIn("6h", facet.tokens)

    def test_detects_qc_and_blank_roles(self):
        facets = build_sample_facets(["20240311_QC_Cerebellum_NEG_1", "Blank_01", "s1"], None)
        self.assertEqual(facets["20240311_QC_Cerebellum_NEG_1"].role, "qc")
        self.assertEqual(facets["Blank_01"].role, "blank")
        self.assertEqual(facets["s1"].role, "sample")

    def test_sample_meta_supplies_role_and_group_tokens(self):
        # arf_differential は class_index を持たず sample_meta["group"] だけを持つ経路。
        facets = build_sample_facets(
            ["s0", "qc1"],
            None,
            sample_meta={"s0": {"group": "24M_GF", "role": "sample"},
                         "qc1": {"group": "24M_GF", "role": "qc"}},
        )
        self.assertEqual(facets["s0"].tokens, frozenset({"s0", "24m", "gf"}))
        self.assertEqual(facets["qc1"].role, "qc")

    def test_preserves_input_order(self):
        facets = build_sample_facets(["b", "a", "c"], None)
        self.assertEqual(list(facets), ["b", "a", "c"])

    def test_is_frozen_dataclass(self):
        facet = build_sample_facets(["s1"], None)["s1"]
        self.assertIsInstance(facet, SampleFacet)
        with self.assertRaises(Exception):
            facet.name = "other"


class ArfSampleNamesTests(unittest.TestCase):
    def test_collects_names_in_first_appearance_order_without_duplicates(self):
        features = [
            {"AlignedPeakProperties": [[0, "sB", 1.0], [1, "sA", 2.0]]},
            {"AlignedPeakProperties": [[0, "sB", 3.0], [1, "sA", 4.0]]},
        ]
        self.assertEqual(arf_sample_names(features), ["sB", "sA"])

    def test_decodes_bytes_names_and_skips_malformed_rows(self):
        features = [{"AlignedPeakProperties": [[0, b"sA", 1.0], "not-a-row", [1]]}]
        self.assertEqual(arf_sample_names(features), ["sA"])

    def test_empty_input(self):
        self.assertEqual(arf_sample_names([]), [])
        self.assertEqual(arf_sample_names(None), [])


class ExpandSampleSpecsTests(unittest.TestCase):
    def setUp(self):
        self.facets = build_sample_facets([
            "20220901_RAW_control_6h_1_NEG",
            "20220901_RAW_LPS_6h_1_NEG",
            "20220902_RAW_ILG_0h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
        ], None)

    def test_single_token_matches_all_samples_with_token(self):
        matches, _ = expand_sample_specs(["ILG"], self.facets)
        self.assertEqual(matches["ILG"], [
            "20220902_RAW_ILG_0h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
        ])

    def test_multi_token_spec_is_and(self):
        matches, _ = expand_sample_specs(["ILG_6h"], self.facets)
        self.assertEqual(matches["ILG_6h"], [
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
        ])

    def test_token_absent_from_class_id_still_matches(self):
        # 6h は Class ID に無くサンプル名にしかない因子。これが本機能の眼目。
        matches, _ = expand_sample_specs(["6h"], self.facets)
        self.assertEqual(len(matches["6h"]), 4)

    def test_multiple_specs_are_independent(self):
        matches, _ = expand_sample_specs(["ILG_6h", "control_6h"], self.facets)
        self.assertEqual(len(matches["ILG_6h"]), 2)
        self.assertEqual(matches["control_6h"], ["20220901_RAW_control_6h_1_NEG"])

    def test_case_insensitive(self):
        matches, _ = expand_sample_specs(["ilg_6H"], self.facets)
        self.assertEqual(len(matches["ilg_6H"]), 2)

    def test_full_sample_name_matches_only_itself(self):
        matches, _ = expand_sample_specs(["20220902_RAW_ILG_6h_2_NEG"], self.facets)
        self.assertEqual(matches["20220902_RAW_ILG_6h_2_NEG"], ["20220902_RAW_ILG_6h_2_NEG"])

    def test_zero_match_raises_with_available_tokens(self):
        with self.assertRaises(ValueError) as ctx:
            expand_sample_specs(["24h"], self.facets)
        message = str(ctx.exception)
        self.assertIn("matched", message)
        self.assertIn("24h", message)
        self.assertIn("ilg", message)

    def test_blank_specs_are_skipped(self):
        matches, _ = expand_sample_specs(["ILG", "", "  "], self.facets)
        self.assertEqual(list(matches), ["ILG"])


class ExpandSampleSpecsRoleTests(unittest.TestCase):
    def setUp(self):
        self.facets = build_sample_facets([
            "20240311_Cerebellum_ICR_NEG_1",
            "20240311_Cerebellum_ICR_NEG_2",
            "20240311_QC_Cerebellum_ICR_NEG_1",
        ], None)

    def test_qc_is_excluded_by_default_and_reported(self):
        matches, excluded = expand_sample_specs(["cerebellum"], self.facets)
        self.assertEqual(len(matches["cerebellum"]), 2)
        self.assertEqual(excluded["cerebellum"], ["20240311_QC_Cerebellum_ICR_NEG_1"])

    def test_include_roles_can_bring_qc_back(self):
        matches, excluded = expand_sample_specs(
            ["cerebellum"], self.facets, include_roles=("sample", "qc"))
        self.assertEqual(len(matches["cerebellum"]), 3)
        self.assertEqual(excluded["cerebellum"], [])

    def test_include_roles_none_disables_role_filtering(self):
        matches, _ = expand_sample_specs(["cerebellum"], self.facets, include_roles=None)
        self.assertEqual(len(matches["cerebellum"]), 3)

    def test_all_hits_dropped_by_role_raises_and_names_include_roles(self):
        with self.assertRaises(ValueError) as ctx:
            expand_sample_specs(["qc"], self.facets)
        self.assertIn("include_roles", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
