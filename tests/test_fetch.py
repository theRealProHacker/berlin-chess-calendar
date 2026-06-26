"""Golden-file tests against the real captured feed payloads (offline, deterministic)."""
import re
import unittest
from datetime import date
from pathlib import Path

from bcc import fetch

FX = Path(__file__).parent / "fixtures"


def load(name: str, source: str):
    return fetch.parse_feed((FX / name).read_text(encoding="utf-8"), source)


class TestParseFeed(unittest.TestCase):
    def test_dsb_item_count(self):
        self.assertEqual(len(load("dsb-berlin.xml", "dsb-berlin")), 8)

    def test_bsv_item_count(self):
        self.assertEqual(len(load("bsv-terminkalender.xml", "bsv-termin")), 12)

    def test_all_dates_valid_iso(self):
        for name, src in (("dsb-berlin.xml", "dsb-berlin"), ("bsv-terminkalender.xml", "bsv-termin")):
            for ev in load(name, src):
                date.fromisoformat(ev.start_date)  # raises if bad
                date.fromisoformat(ev.end_date)
                self.assertLessEqual(ev.start_date, ev.end_date)
                self.assertTrue(ev.name.strip())
                self.assertEqual(ev.source, src)

    def test_werner_ott_parsed(self):
        evs = load("dsb-berlin.xml", "dsb-berlin")
        wo = next(e for e in evs if "Werner-Ott" in e.name)
        self.assertEqual(wo.start_date, "2026-08-01")
        self.assertEqual(wo.end_date, "2026-08-09")
        self.assertTrue(wo.enclosure_url and wo.enclosure_url.endswith(".pdf"))
        self.assertTrue(wo.link.startswith("https://www.schachbund.de/"))

    def test_edition_extracted(self):
        evs = load("bsv-terminkalender.xml", "bsv-termin")
        lich = next(e for e in evs if "Lichtenberger Sommer" in e.name)
        self.assertEqual(lich.edition, 22)

    def test_bsv_links_point_to_bsv(self):
        for ev in load("bsv-terminkalender.xml", "bsv-termin"):
            if ev.link:
                self.assertIn("berlinerschachverband.de", ev.link)


if __name__ == "__main__":
    unittest.main()
