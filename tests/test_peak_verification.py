"""peak_verification 純ロジックの検証。"""

import unittest

import peak_verification as pv


class FormulaMassTests(unittest.TestCase):
    def test_parse_formula_multi_element_and_multi_digit(self):
        self.assertEqual(
            pv.parse_formula("C42H82NO8P"),
            {"C": 42, "H": 82, "N": 1, "O": 8, "P": 1},
        )

    def test_parse_formula_single_atom_defaults_to_one(self):
        self.assertEqual(pv.parse_formula("CH4"), {"C": 1, "H": 4})

    def test_parse_formula_empty_raises(self):
        with self.assertRaises(ValueError):
            pv.parse_formula("")

    def test_monoisotopic_mass_pc_34_1(self):
        mass = pv.monoisotopic_mass(pv.parse_formula("C42H82NO8P"))
        self.assertAlmostEqual(mass, 759.5778, places=2)

    def test_monoisotopic_mass_unknown_element_raises(self):
        with self.assertRaises(KeyError):
            pv.monoisotopic_mass({"Xx": 1})


class AdductMassErrorTests(unittest.TestCase):
    def test_adduct_mz_protonated(self):
        neutral = pv.monoisotopic_mass(pv.parse_formula("C42H82NO8P"))
        self.assertAlmostEqual(pv.adduct_mz(neutral, "[M+H]+"), 760.5851, places=2)

    def test_adduct_mz_ammonium(self):
        self.assertAlmostEqual(pv.adduct_mz(100.0, "[M+NH4]+"), 118.03383, places=4)

    def test_adduct_mz_deprotonated(self):
        self.assertAlmostEqual(pv.adduct_mz(100.0, "[M-H]-"), 98.99272, places=4)

    def test_adduct_mz_unknown_returns_none(self):
        self.assertIsNone(pv.adduct_mz(100.0, "[M+ZZ]+"))

    def test_mass_error_pass(self):
        result = pv.mass_error_ppm(760.5851, "C42H82NO8P", "[M+H]+")
        self.assertEqual(result["band"], "PASS")
        self.assertAlmostEqual(result["ppm"], 0.0, delta=5.0)

    def test_mass_error_borderline(self):
        neutral = pv.monoisotopic_mass(pv.parse_formula("C42H82NO8P"))
        theo = pv.adduct_mz(neutral, "[M+H]+")
        observed = theo * (1 + 8e-6)  # +8 ppm
        result = pv.mass_error_ppm(observed, "C42H82NO8P", "[M+H]+")
        self.assertEqual(result["band"], "BORDERLINE")

    def test_mass_error_fail(self):
        neutral = pv.monoisotopic_mass(pv.parse_formula("C42H82NO8P"))
        theo = pv.adduct_mz(neutral, "[M+H]+")
        observed = theo * (1 + 20e-6)  # +20 ppm
        result = pv.mass_error_ppm(observed, "C42H82NO8P", "[M+H]+")
        self.assertEqual(result["band"], "FAIL")

    def test_mass_error_unknown_formula(self):
        result = pv.mass_error_ppm(760.0, "Unknown", "[M+H]+")
        self.assertEqual(result["band"], "UNKNOWN")
        self.assertIsNone(result["ppm"])

    def test_mass_error_unknown_adduct(self):
        result = pv.mass_error_ppm(760.0, "C42H82NO8P", "Unknown")
        self.assertEqual(result["band"], "UNKNOWN")


class AdductConsistencyTests(unittest.TestCase):
    def test_polarity_match_positive(self):
        result = pv.adduct_consistency("[M+H]+", "Positive", "PC")
        self.assertTrue(result["polarity_ok"])
        self.assertEqual(result["band"], "PASS")
        self.assertTrue(result["class_typical"])

    def test_polarity_mismatch(self):
        result = pv.adduct_consistency("[M+H]+", "Negative", "PC")
        self.assertFalse(result["polarity_ok"])
        self.assertEqual(result["band"], "FAIL")

    def test_class_atypical_adduct_is_advisory_not_fail(self):
        result = pv.adduct_consistency("[M+Na]+", "Positive", "PC")
        self.assertTrue(result["polarity_ok"])
        self.assertEqual(result["band"], "PASS")
        self.assertFalse(result["class_typical"])

    def test_unknown_class_gives_none_typical(self):
        result = pv.adduct_consistency("[M+H]+", "Positive", "ZZZ")
        self.assertIsNone(result["class_typical"])

    def test_unknown_adduct_is_unknown_band(self):
        result = pv.adduct_consistency("Unknown", "Positive", "PC")
        self.assertEqual(result["band"], "UNKNOWN")
        self.assertIsNone(result["polarity_ok"])

    def test_unrecognized_ion_mode_is_unknown_not_fail(self):
        result = pv.adduct_consistency("[M+H]+", "Both", "PC")
        self.assertEqual(result["band"], "UNKNOWN")
        self.assertIsNone(result["polarity_ok"])
        self.assertTrue(result["class_typical"])  # class advisory still computed


class ClassTokenTests(unittest.TestCase):
    def test_extract_class_token_prefers_ontology(self):
        self.assertEqual(pv.extract_class_token("PC 34:1", "PC"), "pc")

    def test_extract_class_token_falls_back_to_name(self):
        self.assertEqual(pv.extract_class_token("TG 52:2", ""), "tg")

    def test_extract_class_token_empty(self):
        self.assertIsNone(pv.extract_class_token("", ""))

    def test_ether_caveat_detected_for_o_prefix(self):
        caveats = pv.ether_caveats("PE O-38:5", "PE")
        self.assertTrue(caveats)
        self.assertIn("P-/O-", caveats[0])

    def test_ether_caveat_detected_for_p_prefix(self):
        self.assertTrue(pv.ether_caveats("PC P-36:4", "PC"))

    def test_no_ether_caveat_for_diacyl(self):
        self.assertEqual(pv.ether_caveats("PC 34:1", "PC"), [])

    def test_vocab_hits_match(self):
        vocab = {"pc": ["phosphatidylcholine"], "tg": ["triacylglycerol"]}
        self.assertEqual(pv.vocab_hits("pc", vocab), ["phosphatidylcholine"])

    def test_vocab_hits_none_token(self):
        self.assertEqual(pv.vocab_hits(None, {"pc": ["x"]}), [])


import json
import tempfile
from pathlib import Path

import server
from test_pai2 import IonMode


def _feat(**over):
    base = {
        "id": 1,
        "name": "PC 34:1",
        "ontology": "PC",
        "formula": "C42H82NO8P",
        "adduct": "[M+H]+",
        "m/z": 760.5851,
        "ion_mode": IonMode.Positive,
        "time": {"rt": 12.34},
        "S/N": 42.0,
    }
    base.update(over)
    return base


class VerifyPeakToolTests(unittest.TestCase):
    def setUp(self):
        self._orig_features = server.session.filtered_features
        self._orig_knowledge = server.KNOWLEDGE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        server.KNOWLEDGE_DIR = Path(self._tmp.name) / "knowledge"
        server.KNOWLEDGE_DIR.mkdir()

    def tearDown(self):
        server.session.filtered_features = self._orig_features
        server.KNOWLEDGE_DIR = self._orig_knowledge
        self._tmp.cleanup()

    def test_error_when_not_loaded(self):
        server.session.filtered_features = None
        out = json.loads(server.verify_peak_annotation(metabolite_id="1"))
        self.assertEqual(out["status"], "error")

    def test_error_when_no_selector(self):
        server.session.filtered_features = [_feat()]
        out = json.loads(server.verify_peak_annotation())
        self.assertEqual(out["status"], "error")

    def test_not_found(self):
        server.session.filtered_features = [_feat()]
        out = json.loads(server.verify_peak_annotation(metabolite_id="999"))
        self.assertEqual(out["status"], "not_found")

    def test_success_shape_and_bands(self):
        server.session.filtered_features = [_feat()]
        out = json.loads(server.verify_peak_annotation(metabolite_id="1"))
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["identity"]["name"], "PC 34:1")
        self.assertEqual(out["identity"]["ion_mode"], "Positive")
        self.assertEqual(out["analytical_checks"]["mass_error"]["band"], "PASS")
        self.assertEqual(out["analytical_checks"]["adduct_consistency"]["band"], "PASS")
        self.assertIn("class_token", out["biological_plausibility"])

    def test_unknown_formula_degrades_to_unknown_band(self):
        server.session.filtered_features = [_feat(formula="Unknown")]
        out = json.loads(server.verify_peak_annotation(metabolite_id="1"))
        self.assertEqual(out["analytical_checks"]["mass_error"]["band"], "UNKNOWN")

    def test_ether_caveat_surfaced(self):
        server.session.filtered_features = [_feat(name="PE O-38:5", ontology="PE", id=2)]
        out = json.loads(server.verify_peak_annotation(metabolite_id="2"))
        self.assertTrue(out["biological_plausibility"]["caveats"])

    def test_candidate_slugs_from_knowledge(self):
        note = server.KNOWLEDGE_DIR / "pe-p-vs-pe-o-annotation.md"
        note.write_text(
            "---\ntype: knowledge\n"
            "description: PE の P-/O- 表記の混同に注意\n"
            "claim_strength: established\n"
            "tags: [annotation, plasmalogen]\n---\n\n本文\n",
            encoding="utf-8",
        )
        server.session.filtered_features = [_feat(name="PE 38:5", ontology="PE", id=3)]
        out = json.loads(server.verify_peak_annotation(metabolite_id="3"))
        self.assertIn(
            "pe-p-vs-pe-o-annotation",
            out["biological_plausibility"]["candidate_knowledge_slugs"],
        )

    def test_multiple_matches_wrapped(self):
        server.session.filtered_features = [_feat(id=1), _feat(id=2)]
        out = json.loads(server.verify_peak_annotation(metabolite_name="PC"))
        self.assertEqual(out["status"], "success")
        self.assertIn("matches", out)
        self.assertEqual(len(out["matches"]), 2)


if __name__ == "__main__":
    unittest.main()
