import unittest

from msdial_tags import normalize_sample_name
from sample_factors import (
    SampleFacet,
    arf_sample_names,
    build_sample_facets,
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


if __name__ == "__main__":
    unittest.main()
