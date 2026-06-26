"""Tests for feed title/field parsing."""
import unittest

from bcc import normalize as nz


class TestParseTitle(unittest.TestCase):
    def test_date_range_en_dash(self):
        s, e, t, n = nz.parse_title("25.06.2026–28.06.2026 Berliner Tandemschach-Treffen")
        self.assertEqual((s, e, t, n), ("2026-06-25", "2026-06-28", None, "Berliner Tandemschach-Treffen"))

    def test_single_date(self):
        s, e, t, n = nz.parse_title("27.06.2026 22. Internationales Berliner Tandemschach-Open")
        self.assertEqual(s, "2026-06-27")
        self.assertEqual(e, "2026-06-27")  # end defaults to start
        self.assertEqual(n, "22. Internationales Berliner Tandemschach-Open")

    def test_time_in_parens(self):
        s, e, t, n = nz.parse_title("26.06.2026 (19:30–23:00) SC Kreuzberg Monatsturnier Schnellschach")
        self.assertEqual(s, "2026-06-26")
        self.assertEqual(t, "19:30–23:00")
        self.assertEqual(n, "SC Kreuzberg Monatsturnier Schnellschach")

    def test_name_with_trailing_parens(self):
        s, e, t, n = nz.parse_title("02.07.2026 10. Harald-Lieb-Gedenkturnier (24. SK Zehlendorf Open)")
        self.assertIsNone(t)
        self.assertEqual(n, "10. Harald-Lieb-Gedenkturnier (24. SK Zehlendorf Open)")

    def test_unparseable_raises(self):
        with self.assertRaises(ValueError):
            nz.parse_title("no date here just a name")


class TestEdition(unittest.TestCase):
    def test_arabic(self):
        self.assertEqual(nz.parse_edition("22. Internationales Berliner Tandemschach-Open"), 22)

    def test_none(self):
        self.assertIsNone(nz.parse_edition("Berliner Tandemschach-Treffen"))


class TestNormName(unittest.TestCase):
    def test_dsb_bsv_same_title_match(self):
        a = nz.norm_name("22. Internationales Berliner Tandemschach-Open")
        b = nz.norm_name("22. Internationales Berliner Tandemschach-Open")
        self.assertEqual(a, b)

    def test_strips_edition_and_year(self):
        self.assertEqual(nz.norm_name("22. Lichtenberger Sommer 2026"), "lichtenberger sommer")

    def test_umlaut(self):
        self.assertEqual(nz.norm_name("Schöneberger Frühjahrs-Open"), "schoeneberger fruehjahrs open")


class TestGuesses(unittest.TestCase):
    def test_time_control(self):
        self.assertEqual(nz.guess_time_control("Spandauer Weihnachts-Blitz, 5 Min"), "blitz")
        self.assertEqual(nz.guess_time_control("Schnellschachturnier, 15 Min"), "rapid")
        self.assertEqual(nz.guess_time_control("9 Runden Schweizer System, 90 Min"), "classical")
        self.assertIsNone(nz.guess_time_control("Mehr Informationen hier"))

    def test_prize(self):
        self.assertEqual(nz.guess_prize_eur("Preise: 5150 Euro"), 5150)
        self.assertEqual(nz.guess_prize_eur("1. Preis 1000 EUR, 2. Preis 800 €"), 1000)
        self.assertIsNone(nz.guess_prize_eur("kein Preisgeld erwähnt"))


if __name__ == "__main__":
    unittest.main()
