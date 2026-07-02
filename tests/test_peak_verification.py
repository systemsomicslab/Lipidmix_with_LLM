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


if __name__ == "__main__":
    unittest.main()
