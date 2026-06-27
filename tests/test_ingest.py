"""Feed parsing + dedup tests, incl. golden fixtures. Run: python -m unittest discover -s tests -t ."""
import unittest

from bcc import ingest
from bcc.build import validate


class TestTitle(unittest.TestCase):
    def test_range(self):
        self.assertEqual(ingest.parse_title("25.06.2026–28.06.2026 Berliner Tandemschach-Treffen"),
                         ("2026-06-25", "2026-06-28", "Berliner Tandemschach-Treffen"))

    def test_single(self):
        s, e, n = ingest.parse_title("27.06.2026 22. Internationales Berliner Tandemschach-Open")
        self.assertEqual((s, e, n), ("2026-06-27", "2026-06-27", "22. Internationales Berliner Tandemschach-Open"))

    def test_time_parens(self):
        s, e, n = ingest.parse_title("26.06.2026 (19:30–23:00) SC Kreuzberg Monatsturnier Schnellschach")
        self.assertEqual(n, "SC Kreuzberg Monatsturnier Schnellschach")

    def test_name_with_parens(self):
        s, e, n = ingest.parse_title("02.07.2026 10. Harald-Lieb-Gedenkturnier (24. SK Zehlendorf Open)")
        self.assertEqual(n, "10. Harald-Lieb-Gedenkturnier (24. SK Zehlendorf Open)")

    def test_unparseable(self):
        self.assertRaises(ValueError, ingest.parse_title, "no date just a name")

    def test_edition(self):
        self.assertEqual(ingest.parse_edition("22. Foo"), 22)
        self.assertIsNone(ingest.parse_edition("Foo"))

    def test_norm(self):
        self.assertEqual(ingest.norm_name("22. Lichtenberger Sommer 2026"), "lichtenberger sommer")


class TestDedup(unittest.TestCase):
    def test_merge_close(self):
        a = ingest.Raw("a", "Foo Open", "2026-08-01", "2026-08-01", None, "", "", None)
        b = ingest.Raw("b", "Foo Open", "2026-08-03", "2026-08-03", None, "", "", None)
        g = ingest.dedup([a, b])
        self.assertEqual(len(g), 1)

    def test_split_far(self):
        a = ingest.Raw("a", "Foo Open", "2026-08-01", "2026-08-01", None, "", "", None)
        b = ingest.Raw("b", "Foo Open", "2026-08-20", "2026-08-20", None, "", "", None)
        self.assertEqual(len(ingest.dedup([a, b])), 2)


class TestGolden(unittest.TestCase):
    def setUp(self):
        self.events = ingest.fetch_all(fixtures=True)
        self.groups = ingest.dedup(self.events)

    def test_counts(self):
        self.assertEqual(len(self.events), 20)   # 8 DSB + 12 BSV (RSS spine; youth is separate)
        self.assertEqual(len(self.groups), 13)   # merged

    def test_shared_merged(self):
        tt = next(g for g in self.groups if "Tandemschach-Treffen" in g["evs"][0].name)
        self.assertEqual(sorted({e.source for e in tt["evs"]}), ["bsv-termin", "dsb-berlin"])

    def test_drafts_are_valid(self):
        for g in self.groups:
            validate(ingest.draft(g, "2026-06-26"))  # every auto-draft passes the schema


class TestYouthDate(unittest.TestCase):
    PUB = "2025-11-10"

    def test_single_with_year(self):
        self.assertEqual(ingest.parse_de_date("am 11. Juli 2026", self.PUB), ("2026-07-11", "2026-07-11"))

    def test_range_same_month(self):
        self.assertEqual(ingest.parse_de_date("vom 13. bis 15. Februar 2026", self.PUB),
                         ("2026-02-13", "2026-02-15"))

    def test_year_omitted_rolls_to_future(self):
        # published April 2025, "Juli" with no year -> July of the publish year (still future then)
        self.assertEqual(ingest.parse_de_date("vom 23. bis 25. Juli", "2025-04-08"),
                         ("2025-07-23", "2025-07-25"))

    def test_year_omitted_crosses_year(self):
        # published Dec 2025, "Februar" with no year -> next year (Feb 2025 already past at publish)
        self.assertEqual(ingest.parse_de_date("am 14. Februar", "2025-12-20"), ("2026-02-14", "2026-02-14"))

    def test_weekday_abbrev(self):
        self.assertEqual(ingest.parse_de_date("am Sa., 11. Oktober 2025", "2025-09-28"),
                         ("2025-10-11", "2025-10-11"))

    def test_edition_ordinal_not_matched(self):
        # "5." is an edition ordinal before "Mai", not a day -> must NOT parse as 5 May
        self.assertEqual(ingest.parse_de_date("5. Mai-Pokal der Jugend", "2026-01-01"), (None, None))

    def test_unparseable(self):
        self.assertEqual(ingest.parse_de_date("10 oder Null – Viererturnier bei Caissa", "2026-04-27"),
                         (None, None))


class TestYouthAges(unittest.TestCase):
    def test_single(self):
        self.assertEqual(ingest.extract_ages("Jugend-Elo-Rapid – U20"), ["U20"])

    def test_parens(self):
        self.assertEqual(ingest.extract_ages("Kinder-Sommer-Turnier (U12)"), ["U12"])

    def test_multi_sorted(self):
        self.assertEqual(ingest.extract_ages("Turnier U14/U10/U12"), ["U10", "U12", "U14"])

    def test_none(self):
        self.assertEqual(ingest.extract_ages("Offenes Turnier ohne Altersklasse"), [])


class TestYouthName(unittest.TestCase):
    def test_dash_am_tail(self):
        self.assertEqual(ingest.clean_youth_name("Jugend-Elo-Rapid – U20 – am 11. Juli 2026"),
                         "Jugend-Elo-Rapid – U20")

    def test_inline_vom_and_notice(self):
        self.assertEqual(
            ingest.clean_youth_name("Regionales Kinderschachturnier U8 vom 13. bis 15. Februar 2026 – noch 13 Plätze frei"),
            "Regionales Kinderschachturnier U8")

    def test_weekday_am_tail(self):
        self.assertEqual(ingest.clean_youth_name("Elo-Blitzturnier U18 am Sa., 11. Oktober 2025"),
                         "Elo-Blitzturnier U18")

    def test_no_tail_unchanged(self):
        self.assertEqual(ingest.clean_youth_name("10. Kreuzberger Kinder-Sommer-Turnier (U12)"),
                         "10. Kreuzberger Kinder-Sommer-Turnier (U12)")

    def test_clean_name_yields_clean_id(self):
        from bcc.build import slugify
        self.assertEqual(slugify(ingest.clean_youth_name("Jugend-Elo-Rapid – U20 – am 11. Juli 2026"), 2026),
                         "jugend-elo-rapid-u20-2026")


class TestYouthParse(unittest.TestCase):
    TODAY = "2026-01-01"

    def setUp(self):
        self.raws = ingest.fetch_youth(fixtures=True, today=self.TODAY)

    def test_drops_past_and_unparseable(self):
        # fixture has 6 posts; 2 are 2025 (past at TODAY) + 1 has no date -> 3 survive
        self.assertEqual(len(self.raws), 3)

    def test_source_own_vs_extern(self):
        by_name = {r.name: r.source for r in self.raws}
        own = next(s for n, s in by_name.items() if "U20" in n)
        extern = next(s for n, s in by_name.items() if "Abrafaxe" in n)
        self.assertEqual(own, "sjib")             # category 38 -> Berlin
        self.assertEqual(extern, "sjib-extern")   # category 24 only

    def test_dates_parsed(self):
        u8 = next(r for r in self.raws if "U8" in r.name)
        self.assertEqual((u8.start, u8.end), ("2026-02-13", "2026-02-15"))

    def test_youth_drafts_valid_and_tagged(self):
        for g in ingest.dedup(self.raws):
            d = ingest.draft(g, "2026-06-27")
            validate(d)
            self.assertEqual(d["kind"], "youth")
            self.assertTrue(d["sources"][0].startswith("sjib"))
            if d["sources"] == ["sjib-extern"]:   # externe -> region review flag
                self.assertIn("notes", d)


if __name__ == "__main__":
    unittest.main()
