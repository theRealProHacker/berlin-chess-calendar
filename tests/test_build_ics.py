"""iCalendar generation tests. Run: python -m unittest discover -s tests"""
import unittest
from datetime import date

from bcc import build_ics
from bcc.models import Tournament

BASE = {
    "id": "test-open-2026",
    "name": "Test Open 2026",
    "kind": "open",
    "start_date": "2026-08-01",
    "end_date": "2026-08-09",
    "variant": "standard",
    "time_control": "classical",
    "age_limit": ["open"],
    "participation": "single",
    "schedule_format": "block",
    "region": "berlin",
    "status": "confirmed",
    "sources": ["dsb-berlin"],
    "last_verified": "2026-06-25",
}
TODAY = date(2026, 6, 26)


def mk(**over):
    return Tournament.from_dict({**BASE, **over})


class TestIcs(unittest.TestCase):
    def test_dtend_is_exclusive(self):
        ics = build_ics.build_ics([mk()], today=TODAY)
        self.assertIn("DTSTART;VALUE=DATE:20260801", ics)
        self.assertIn("DTEND;VALUE=DATE:20260810", ics)  # 09 + 1

    def test_uid_is_stable_slug(self):
        ics = build_ics.build_ics([mk()], today=TODAY)
        self.assertIn("UID:test-open-2026@berliner-schachkalender", ics)

    def test_expected_is_tentative(self):
        ics = build_ics.build_ics([mk(id="x-2027", status="expected", start_date="2027-08-01", end_date="2027-08-09")], today=TODAY)
        self.assertIn("STATUS:TENTATIVE", ics)
        self.assertIn("[erwartet]", ics)

    def test_stale_dropped(self):
        ics = build_ics.build_ics([mk(status="stale")], today=TODAY)
        self.assertNotIn("BEGIN:VEVENT", ics)

    def test_old_event_dropped(self):
        ics = build_ics.build_ics(
            [mk(id="old-2026", start_date="2026-01-01", end_date="2026-01-02")], today=TODAY
        )
        self.assertNotIn("BEGIN:VEVENT", ics)  # ended >30 days ago

    def test_single_calendar_wrapper(self):
        ics = build_ics.build_ics([mk()], today=TODAY)
        self.assertEqual(ics.count("BEGIN:VCALENDAR"), 1)
        self.assertEqual(ics.count("END:VCALENDAR"), 1)
        self.assertTrue(ics.endswith("\r\n"))

    def test_crlf_line_endings(self):
        ics = build_ics.build_ics([mk()], today=TODAY)
        self.assertIn("\r\n", ics)
        self.assertNotIn("\n\n", ics.replace("\r\n", "\n"))


class TestSelect(unittest.TestCase):
    def setUp(self):
        self.items = [
            mk(id="blitz-2026", time_control="blitz"),
            mk(id="classic-2026", time_control="classical"),
            mk(id="team-2026", participation="team"),
            mk(id="nearby-2026", region="nearby"),
        ]

    def test_single_axis(self):
        out = build_ics.select(self.items, {"tc": ["blitz"]})
        self.assertEqual([t.id for t in out], ["blitz-2026"])

    def test_or_within_axis(self):
        out = build_ics.select(self.items, {"tc": ["blitz", "classical"]})
        self.assertEqual(len(out), 4)  # all are blitz or classical

    def test_and_across_axes(self):
        out = build_ics.select(self.items, {"tc": ["classical"], "region": ["berlin"]})
        ids = {t.id for t in out}
        self.assertIn("classic-2026", ids)
        self.assertNotIn("nearby-2026", ids)

    def test_empty_filter_returns_all(self):
        self.assertEqual(len(build_ics.select(self.items, None)), 4)
        self.assertEqual(len(build_ics.select(self.items, {})), 4)

    def test_age_list_membership(self):
        items = [mk(id="youth-2026", age_limit=["U12", "U14"])]
        self.assertEqual(len(build_ics.select(items, {"age": ["U12"]})), 1)
        self.assertEqual(len(build_ics.select(items, {"age": ["U18"]})), 0)


class TestEscapingAndFolding(unittest.TestCase):
    def test_comma_escaped(self):
        ics = build_ics.build_ics([mk(name="Open, Berlin")], today=TODAY)
        self.assertIn("SUMMARY:Open\\, Berlin", ics)

    def test_long_line_folded(self):
        long = "A" * 200
        ics = build_ics.build_ics([mk(name=long)], today=TODAY)
        for line in ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, f"line too long: {line[:20]}...")


if __name__ == "__main__":
    unittest.main()
