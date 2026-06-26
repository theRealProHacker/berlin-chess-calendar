"""Tests for merging RawEvents across feeds."""
import unittest
from pathlib import Path

from bcc import dedup, fetch
from bcc.fetch import RawEvent

FX = Path(__file__).parent / "fixtures"


def _ev(name, start, end=None, source="x", link=""):
    return RawEvent(source=source, title="", link=link, description="",
                    name=name, start_date=start, end_date=end or start)


class TestDedupSynthetic(unittest.TestCase):
    def test_same_name_close_dates_merge(self):
        evs = [_ev("Foo Open", "2026-08-01", source="a"), _ev("Foo Open", "2026-08-03", source="b")]
        out = dedup.dedup(evs, window_days=3)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].sources, ["a", "b"])

    def test_same_name_far_dates_split(self):
        evs = [_ev("Foo Open", "2026-08-01"), _ev("Foo Open", "2026-08-20")]
        self.assertEqual(len(dedup.dedup(evs, window_days=3)), 2)

    def test_edition_kept_from_either(self):
        a = _ev("22. Foo Open", "2026-08-01", source="a")
        a.edition = 22
        b = _ev("Foo Open", "2026-08-01", source="b")  # BSV copy without ordinal
        out = dedup.dedup([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].edition, 22)


class TestDedupGolden(unittest.TestCase):
    def setUp(self):
        evs = []
        evs += fetch.parse_feed((FX / "dsb-berlin.xml").read_text(encoding="utf-8"), "dsb-berlin")
        evs += fetch.parse_feed((FX / "bsv-terminkalender.xml").read_text(encoding="utf-8"), "bsv-termin")
        self.cands = dedup.dedup(evs)

    def test_unique_count(self):
        # 8 DSB + 12 BSV = 20 raw -> 13 unique after merging the shared opens
        self.assertEqual(len(self.cands), 13)

    def test_shared_open_merged_from_both_feeds(self):
        tt = next(c for c in self.cands if "Tandemschach-Treffen" in c.name)
        self.assertEqual(tt.sources, ["bsv-termin", "dsb-berlin"])

    def test_dsb_only_event_single_source(self):
        wo = next(c for c in self.cands if "Werner-Ott" in c.name)
        self.assertEqual(wo.sources, ["dsb-berlin"])

    def test_no_duplicate_names_in_output(self):
        from bcc.normalize import norm_name
        keys = [(norm_name(c.name), c.start_date) for c in self.cands]
        # the two Spandau sections (A-Open / B-Open) are legitimately distinct names
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
