"""Schema + .ics tests. Run: python -m unittest discover -s tests -t ."""
import json
import os
import tempfile
import unittest
from datetime import date

from bcc import build
from bcc.build import ValidationError, validate, slugify, build_ics

VALID = {
    "id": "test-open-2026", "name": "Test Open 2026", "kind": "open",
    "start_date": "2026-08-01", "end_date": "2026-08-09", "variant": "standard",
    "time_control": "classical", "age_limit": ["open"], "participation": "single",
    "schedule_format": "block", "region": "berlin", "status": "confirmed",
    "sources": ["dsb-berlin"], "last_verified": "2026-06-25",
}
TODAY = date(2026, 6, 26)


def mk(**o):
    return {**VALID, **o}


class TestValidate(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate(mk())["id"], "test-open-2026")

    def test_bad_enum(self):
        self.assertRaises(ValidationError, validate, mk(variant="xiangqi"))
        self.assertRaises(ValidationError, validate, mk(kind="banquet"))

    def test_bad_date(self):
        self.assertRaises(ValidationError, validate, mk(start_date="01.08.2026"))

    def test_end_before_start(self):
        self.assertRaises(ValidationError, validate, mk(start_date="2026-08-09", end_date="2026-08-01"))

    def test_empty_age(self):
        self.assertRaises(ValidationError, validate, mk(age_limit=[]))

    def test_bad_age(self):
        self.assertRaises(ValidationError, validate, mk(age_limit=["U7"]))

    def test_unknown_field(self):
        self.assertRaises(ValidationError, validate, mk(colour="blue"))

    def test_bad_slug(self):
        self.assertRaises(ValidationError, validate, mk(id="Test Open 2026"))

    def test_rounds_order(self):
        self.assertRaises(ValidationError, validate, mk(schedule_format="weekly", rounds=["2026-09-10", "2026-09-03"]))

    def test_rounds_ok(self):
        validate(mk(schedule_format="weekly", rounds=["2026-09-03", "2026-09-10"]))

    def test_prize_keys(self):
        self.assertRaises(ValidationError, validate, mk(prize_pool={"amount": 1000}))

    def test_edition(self):
        self.assertRaises(ValidationError, validate, mk(edition=0))


class TestSlug(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(slugify("22. Lichtenberger Sommer 2026", 2026), "lichtenberger-sommer-2026")

    def test_umlaut(self):
        self.assertEqual(slugify("Schöneberg Ü60 Open", 2025), "schoeneberg-ue60-open-2025")


class TestLoad(unittest.TestCase):
    def test_seed(self):
        ts = build.load()
        # Floor, not exact: curation workflows add records routinely. This guards against
        # accidental data loss/truncation without reddening CI on every legitimate add.
        # (load() already validates every record + rejects dup ids — per-record correctness
        # is covered there.)
        self.assertGreaterEqual(len(ts), 15)
        self.assertTrue(all(t["region"] in build.REGION for t in ts))

    def test_dup(self):
        fd, p = tempfile.mkstemp(suffix=".json")
        os.write(fd, json.dumps([VALID, dict(VALID)]).encode())
        os.close(fd)
        try:
            self.assertRaises(ValidationError, build.load, p)
        finally:
            os.unlink(p)


class TestIcs(unittest.TestCase):
    def test_dtend_exclusive(self):
        ics = build_ics([mk()], today=TODAY)
        self.assertIn("DTSTART;VALUE=DATE:20260801", ics)
        self.assertIn("DTEND;VALUE=DATE:20260810", ics)

    def test_uid(self):
        self.assertIn("UID:test-open-2026@berliner-schachkalender", build_ics([mk()], today=TODAY))

    def test_expected_tentative(self):
        ics = build_ics([mk(id="x-2027", status="expected", start_date="2027-08-01", end_date="2027-08-09")], today=TODAY)
        self.assertIn("STATUS:TENTATIVE", ics)
        self.assertIn("[erwartet]", ics)

    def test_stale_dropped(self):
        self.assertNotIn("BEGIN:VEVENT", build_ics([mk(status="stale")], today=TODAY))

    def test_old_dropped(self):
        self.assertNotIn("BEGIN:VEVENT", build_ics([mk(start_date="2026-01-01", end_date="2026-01-02")], today=TODAY))

    def test_comma_escaped(self):
        self.assertIn("SUMMARY:Open\\, Berlin", build_ics([mk(name="Open, Berlin")], today=TODAY))

    def test_folding(self):
        ics = build_ics([mk(name="A" * 200)], today=TODAY)
        for line in ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)

    def test_wrapper(self):
        ics = build_ics([mk()], today=TODAY)
        self.assertEqual(ics.count("BEGIN:VCALENDAR"), 1)
        self.assertTrue(ics.endswith("\r\n"))


if __name__ == "__main__":
    unittest.main()
