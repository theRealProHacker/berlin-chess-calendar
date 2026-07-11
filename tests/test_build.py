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

    def test_rounds_count_positive_int(self):
        validate(mk(rounds_count=9))
        self.assertRaises(ValidationError, validate, mk(rounds_count=0))
        self.assertRaises(ValidationError, validate, mk(rounds_count="9"))

    def test_rounds_count_matches_rounds(self):
        validate(mk(schedule_format="weekly", rounds=["2026-08-03", "2026-08-10"], rounds_count=2))
        self.assertRaises(ValidationError, validate,
                          mk(schedule_format="weekly", rounds=["2026-08-03", "2026-08-10"], rounds_count=3))

    def test_series_must_be_slug(self):
        validate(mk(series="lichtenberger-sommer"))
        self.assertRaises(ValidationError, validate, mk(series="Lichtenberger Sommer"))


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


class TestVisible(unittest.TestCase):
    """visible() = what the site lists AND writes to the .ics — must stay identical."""

    def _recs(self):
        return [
            mk(id="a-2026", series="a", status="confirmed", start_date="2026-08-01", end_date="2026-08-02"),
            mk(id="a-2027", series="a", status="expected", start_date="2027-08-01", end_date="2027-08-02"),
            mk(id="b-2027", series="b", status="expected", start_date="2027-05-01", end_date="2027-05-02"),
            mk(id="b-2028", series="b", status="expected", start_date="2028-05-01", end_date="2028-05-02"),
        ]

    def test_confirmed_suppresses_its_predicts_but_unshown_series_keeps_one(self):
        vis = [t["id"] for t in build.visible(self._recs(), TODAY)]
        self.assertEqual(vis, ["a-2026", "b-2027"])   # confirmed a; earliest predict of unshown b
        self.assertNotIn("a-2027", vis)               # a is shown -> its predict hidden
        self.assertNotIn("b-2028", vis)               # only one predict per series

    def test_ics_covers_visible_records(self):
        # Each visible record contributes >=1 VEVENT (a rounds record may emit several);
        # non-visible records contribute none. These fixtures have no rounds, so one each.
        recs = self._recs()
        ics = build_ics(recs, today=TODAY)
        self.assertEqual({t["id"] for t in build.visible(recs, TODAY)}, {"a-2026", "b-2027"})
        for tid in ("a-2026", "b-2027"):
            self.assertIn(f"UID:{tid}@berliner-schachkalender", ics)
        for tid in ("a-2027", "b-2028"):
            self.assertNotIn(tid, ics)


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


class TestRounds(unittest.TestCase):
    """A record with a `rounds` list emits its rounds, not one span (bcc.build._vevent):
    evenly-spaced rounds -> one recurring VEVENT (RRULE); uneven -> one VEVENT per round."""

    def test_round_rule_even_weekly(self):
        self.assertEqual(build._round_rule(["2026-05-21", "2026-05-28", "2026-06-04"]), "FREQ=WEEKLY;COUNT=3")

    def test_round_rule_even_biweekly(self):
        self.assertEqual(build._round_rule(["2026-01-04", "2026-01-18", "2026-02-01"]), "FREQ=WEEKLY;COUNT=3;INTERVAL=2")

    def test_round_rule_even_daily(self):
        self.assertEqual(build._round_rule(["2026-01-01", "2026-01-04", "2026-01-07"]), "FREQ=DAILY;COUNT=3;INTERVAL=3")

    def test_round_rule_uneven_is_none(self):
        self.assertIsNone(build._round_rule(["2026-09-27", "2026-10-11", "2026-11-08"]))

    def test_round_rule_single_is_none(self):
        self.assertIsNone(build._round_rule(["2026-01-01"]))

    def test_round_days_one_day(self):
        t = mk(rounds=["2026-09-27", "2026-11-08"], rounds_count=2, start_date="2026-09-27", end_date="2026-11-08")
        self.assertEqual(build._round_days(t), 1)

    def test_round_days_weekend(self):
        t = mk(rounds=["2026-09-05", "2026-11-14"], rounds_count=2, start_date="2026-09-05", end_date="2026-11-15")
        self.assertEqual(build._round_days(t), 2)

    def _even(self):
        return mk(id="hl-2026", rounds=["2026-05-21", "2026-05-28", "2026-06-04", "2026-06-11",
                  "2026-06-18", "2026-06-25", "2026-07-02"], rounds_count=7,
                  start_date="2026-05-21", end_date="2026-07-02")

    def test_even_is_one_recurring_vevent(self):
        ics = build_ics([self._even()], today=TODAY)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 1)
        self.assertIn("UID:hl-2026@berliner-schachkalender", ics)   # UID unchanged (no -r suffix)
        self.assertNotIn("-r1@", ics)
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=7", ics)
        self.assertIn("DTSTART;VALUE=DATE:20260521", ics)
        self.assertIn("DTEND;VALUE=DATE:20260522", ics)             # round_days=1 -> +1 exclusive

    def _uneven(self, **o):
        base = dict(id="bmm-2026", kind="league", rounds=["2026-09-27", "2026-10-11", "2026-11-08"],
                    rounds_count=3, start_date="2026-09-27", end_date="2026-11-08")
        base.update(o)
        return mk(**base)

    def test_uneven_splits_one_vevent_per_round(self):
        ics = build_ics([self._uneven()], today=TODAY)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 3)
        self.assertNotIn("RRULE", ics)
        for n, d in [(1, "20260927"), (2, "20261011"), (3, "20261108")]:
            self.assertIn(f"UID:bmm-2026-r{n}@berliner-schachkalender", ics)
            self.assertIn(f"DTSTART;VALUE=DATE:{d}", ics)

    def test_uneven_one_day_summary_is_runde(self):
        ics = build_ics([self._uneven()], today=TODAY)
        self.assertIn("1. Runde", ics)
        self.assertNotIn("Spieltag", ics)

    def test_uneven_weekend_summary_is_spieltag_and_spans_two_days(self):
        t = self._uneven(id="bjmm-2026", kind="youth", participation="team", schedule_format="other",
                         rounds=["2026-09-05", "2026-10-03", "2026-11-14"], rounds_count=3,
                         start_date="2026-09-05", end_date="2026-11-15")
        ics = build_ics([t], today=TODAY)
        self.assertIn("1. Spieltag", ics)
        self.assertNotIn("Runde", ics)
        self.assertIn("DTSTART;VALUE=DATE:20260905", ics)
        self.assertIn("DTEND;VALUE=DATE:20260907", ics)             # Sa+So, round_days=2 -> +2 exclusive


if __name__ == "__main__":
    unittest.main()
