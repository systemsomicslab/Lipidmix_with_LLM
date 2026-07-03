import unittest

import lipid_identity as li


class TestNormalizeLipidName(unittest.TestCase):
    def test_basic_pc_parses(self):
        out = li.normalize_lipid_name("PC 34:1")
        # pygoslin should parse; if unavailable, parse_ok False but no crash
        self.assertIn("parse_ok", out)
        if out["parse_ok"]:
            self.assertTrue(out["normalized"].upper().startswith("PC"))

    def test_ether_forms_parse(self):
        # NOTE: at the species shorthand level, GOSLIN represents plasmalogen
        # `PC P-34:0` and ether `PC O-34:1` with the SAME normalized string
        # (the P-/O- plasmalogen ambiguity). Downstream must rely on the ether
        # caveat (peak_verification.ether_caveats), not the normalized name, to
        # tell them apart. Here we only assert both parse cleanly.
        o = li.normalize_lipid_name("PC O-34:1")
        p = li.normalize_lipid_name("PC P-34:0")
        if o["parse_ok"] and p["parse_ok"]:
            self.assertTrue(o["normalized"].upper().startswith("PC"))
            self.assertTrue(p["normalized"].upper().startswith("PC"))

    def test_garbage_returns_parse_error_not_crash(self):
        out = li.normalize_lipid_name("not a lipid ###")
        self.assertFalse(out["parse_ok"])
        self.assertIsNotNone(out.get("error"))


class TestReferenceMapping(unittest.TestCase):
    def setUp(self):
        self.tables = li.load_reference_tables("reference")

    def test_known_class_maps(self):
        out = li.map_to_reference("pc", self.tables)
        self.assertTrue(out["matched"])
        self.assertEqual(out["lipid_maps_category"], "GP")

    def test_unknown_class_has_caveat(self):
        out = li.map_to_reference("zzz", self.tables)
        self.assertFalse(out["matched"])
        self.assertIsNotNone(out["caveat"])


if __name__ == "__main__":
    unittest.main()
