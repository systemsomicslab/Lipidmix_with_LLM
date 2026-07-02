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


if __name__ == "__main__":
    unittest.main()
