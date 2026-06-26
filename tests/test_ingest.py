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
        self.assertEqual(len(self.events), 20)   # 8 DSB + 12 BSV
        self.assertEqual(len(self.groups), 13)   # merged

    def test_shared_merged(self):
        tt = next(g for g in self.groups if "Tandemschach-Treffen" in g["evs"][0].name)
        self.assertEqual(sorted({e.source for e in tt["evs"]}), ["bsv-termin", "dsb-berlin"])

    def test_drafts_are_valid(self):
        for g in self.groups:
            validate(ingest.draft(g, "2026-06-26"))  # every auto-draft passes the schema


if __name__ == "__main__":
    unittest.main()
