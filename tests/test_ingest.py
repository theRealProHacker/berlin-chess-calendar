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

    def test_enclosure_pdf_into_ausschreibung_url(self):
        # The Lasker item carries both a .docx and a .pdf enclosure; ausschreibung_url
        # must be the PDF (https-upgraded), never the .docx.
        g = next(g for g in self.groups if "Lasker" in g["evs"][0].name)
        d = ingest.draft(g, "2026-06-26")
        self.assertEqual(
            d["ausschreibung_url"],
            "https://www.berlinerschachverband.de/files/bsv/termine/2026/Ausschreibung_Barlinek_2026.pdf")

    def test_no_enclosure_no_ausschreibung_url(self):
        # An item with no enclosure (Tandemschach-Treffen) gets no ausschreibung_url key.
        g = next(g for g in self.groups if "Tandemschach-Treffen" in g["evs"][0].name)
        self.assertNotIn("ausschreibung_url", ingest.draft(g, "2026-06-26"))


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


class TestHubLinks(unittest.TestCase):
    """The BSV 'Aktuelle Links' block -> named tournament anchors (golden fixture)."""

    def setUp(self):
        from bcc.build import ROOT
        home = (ROOT / "tests" / "fixtures" / "bsv-home.html").read_text(encoding="utf-8")
        self.links = ingest.extract_hub_links(home)
        self.by_name = {n: u for n, u in self.links}

    def test_drops_pdfs_and_non_tournaments(self):
        names = " ".join(self.by_name)
        self.assertNotIn("Terminplan", names)            # the .pdf links are dropped
        self.assertNotIn("Klassenberechtigungen", names)
        self.assertFalse(any(u.lower().endswith(".pdf") for u in self.by_name.values()))

    def test_keeps_named_tournaments(self):
        self.assertIn("Teschner-Gedenkturnier", self.by_name)   # a no-feed gap the hub uniquely covers
        self.assertTrue(any("Berliner EM" in n for n in self.by_name))

    def test_relative_url_made_absolute(self):
        em = next(u for n, u in self.links if "Berliner EM" in n)
        self.assertTrue(em.startswith("https://www.berlinerschachverband.de/"))

    def test_external_url_preserved(self):
        self.assertEqual(self.by_name["Teschner-Gedenkturnier"], "https://www.sglasker.de/teschner-2026/")


class TestHubDate(unittest.TestCase):
    TODAY = "2026-06-30"

    def test_numeric_earliest_future(self):
        # several dates on a page: the registration deadline (past) is skipped, event range wins
        txt = "Runde 1 am 01.07.2026 ... Siegerehrung 02.07.2026 ... Anmeldung bis 16.06.2026"
        self.assertEqual(ingest.hub_event_date(txt, self.TODAY), ("2026-07-01", "2026-07-02"))

    def test_prose_with_year(self):
        self.assertEqual(ingest.hub_event_date("Das Turnier findet am 7. Juni 2027 statt.", self.TODAY),
                         ("2027-06-07", "2027-06-07"))

    def test_past_only_returns_none(self):
        self.assertEqual(ingest.hub_event_date("fand am 01.01.2020 statt", self.TODAY), (None, None))

    def test_nothing_returns_none(self):
        self.assertEqual(ingest.hub_event_date("keine Datumsangabe hier", self.TODAY), (None, None))


class TestHubJsonLd(unittest.TestCase):
    """schema.org Event dates from server-side JSON-LD — the fix for 'client-rendered' JS pages."""

    def test_event_object(self):
        page = ('x<script type="application/ld+json">'
                '{"@type":"Event","startDate":"2026-08-01","endDate":"2026-08-09"}</script>y')
        self.assertEqual(ingest.jsonld_event_dates(page), ("2026-08-01", "2026-08-09"))

    def test_graph_and_iso_timestamp_no_end(self):
        page = ('<script type="application/ld+json">{"@graph":[{"@type":"WebPage"},'
                '{"@type":"Event","startDate":"2027-05-10T09:00:00+02:00"}]}</script>')
        self.assertEqual(ingest.jsonld_event_dates(page), ("2027-05-10", "2027-05-10"))

    def test_none_when_absent_or_not_event(self):
        self.assertEqual(ingest.jsonld_event_dates("<html>no structured data</html>"), (None, None))
        org = '<script type="application/ld+json">{"@type":"Organization","name":"x"}</script>'
        self.assertEqual(ingest.jsonld_event_dates(org), (None, None))

    def test_bad_json_ignored(self):
        self.assertEqual(ingest.jsonld_event_dates('<script type="application/ld+json">{oops}</script>'),
                         (None, None))

    def test_meta_description(self):
        page = '<meta name="description" content="1. bis 9. August 2026" />'
        self.assertEqual(ingest._meta_description(page), "1. bis 9. August 2026")


class TestHubDraft(unittest.TestCase):
    def test_hub_raw_drafts_valid(self):
        raw = ingest.Raw("bsv-hub", "Teschner-Gedenkturnier", "2026-11-07", "2026-11-08",
                         None, "https://www.sglasker.de/teschner-2026/", "gedenkturnier", None)
        d = ingest.draft(ingest.dedup([raw])[0], "2026-06-30")
        validate(d)
        self.assertEqual(d["kind"], "memorial")          # "gedenk" in name -> memorial
        self.assertEqual(d["source_url"], "https://www.sglasker.de/teschner-2026/")
        self.assertEqual(d["sources"], ["bsv-hub"])

    def test_fetch_hub_fixtures_offline(self):
        self.assertEqual(ingest.fetch_hub(fixtures=True), [])   # offline contract: no page fetches


class TestYouthContentBody(unittest.TestCase):
    """parse_youth reads content.rendered (the post body), not just title + excerpt."""

    TODAY = "2026-06-30"

    def _posts(self, **fields):
        base = {"title": {"rendered": "Test"}, "excerpt": {"rendered": ""},
                "content": {"rendered": ""}, "date": "2026-05-01", "categories": [38], "link": "x"}
        base.update(fields)
        return [base]

    def test_date_only_in_body_now_parses(self):
        raws = ingest.parse_youth(self._posts(
            title={"rendered": "Schülerturnier"},
            content={"rendered": "<p>Termin: am 5. August 2026 um 10 Uhr.</p>"}), self.TODAY)
        self.assertEqual(len(raws), 1)
        self.assertEqual((raws[0].start, raws[0].end), ("2026-08-05", "2026-08-05"))

    def test_excerpt_date_wins_over_body(self):
        # event date in the excerpt + an unrelated later date in the body -> excerpt wins (first match)
        raws = ingest.parse_youth(self._posts(
            title={"rendered": "Jugendopen"}, excerpt={"rendered": "am 11. Juli 2026"},
            content={"rendered": "Anmeldung bis 1. August 2026"}), self.TODAY)
        self.assertEqual(raws[0].start, "2026-07-11")

    def test_body_without_date_still_dropped(self):
        self.assertEqual(ingest.parse_youth(self._posts(
            title={"rendered": "Vereinsabend"},
            content={"rendered": "<p>Jeden Mittwoch im Vereinsheim.</p>"}), self.TODAY), [])


class TestAbroadCity(unittest.TestCase):
    """draft() strips a Berlin city label (and flags) when the text names a foreign host country."""

    def _draft(self, raw):
        return ingest.draft(ingest.dedup([raw])[0], "2026-06-30")

    def test_youth_abroad_drops_berlin_city(self):
        d = self._draft(ingest.Raw("sjib", "Kinderturnier in Barlinek (Polen)",
                                   "2026-08-01", "2026-08-02", None, "", "", None))
        self.assertNotIn("city", d)               # the auto Berlin label is removed for abroad
        self.assertIn("notes", d)
        validate(d)

    def test_youth_domestic_keeps_berlin_city(self):
        d = self._draft(ingest.Raw("sjib", "Berliner Kinderturnier",
                                   "2026-08-01", "2026-08-02", None, "", "", None))
        self.assertEqual(d.get("city"), "Berlin")

    def test_abroad_detected_in_description(self):
        d = self._draft(ingest.Raw("bsv-hub", "Emanuel-Lasker-Schachfestival",
                                   "2026-08-29", "2026-08-29", None, "https://x",
                                   "Ausgetragen in Barlinek (Polen).", None))
        self.assertNotIn("city", d)               # no Berlin label, and flagged for review
        self.assertIn("Ausland", d["notes"])

    def test_aus_polen_no_parens_not_flagged(self):
        d = self._draft(ingest.Raw("bsv-hub", "Berlin Open", "2026-08-01", "2026-08-01",
                                   None, "https://x", "Teilnehmer aus Polen willkommen", None))
        self.assertNotIn("notes", d)              # "aus Polen" (no parens) must NOT fire


if __name__ == "__main__":
    unittest.main()
