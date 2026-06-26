"""Schema validation tests. Run: python -m unittest discover -s tests"""
import unittest

from bcc import models
from bcc.models import ValidationError, Tournament, slugify

VALID = {
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


def mk(**over):
    return Tournament.from_dict({**VALID, **over})


class TestValidation(unittest.TestCase):
    def test_valid_record(self):
        self.assertEqual(mk().id, "test-open-2026")

    def test_bad_enum_variant(self):
        with self.assertRaises(ValidationError):
            mk(variant="xiangqi")

    def test_bad_enum_kind(self):
        with self.assertRaises(ValidationError):
            mk(kind="banquet")

    def test_bad_date_format(self):
        with self.assertRaises(ValidationError):
            mk(start_date="01.08.2026")

    def test_end_before_start(self):
        with self.assertRaises(ValidationError):
            mk(start_date="2026-08-09", end_date="2026-08-01")

    def test_empty_age_limit(self):
        with self.assertRaises(ValidationError):
            mk(age_limit=[])

    def test_invalid_age_value(self):
        with self.assertRaises(ValidationError):
            mk(age_limit=["U7"])

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValidationError):
            mk(colour="blue")

    def test_bad_slug_id(self):
        with self.assertRaises(ValidationError):
            mk(id="Test Open 2026")

    def test_rounds_must_be_ascending(self):
        with self.assertRaises(ValidationError):
            mk(schedule_format="weekly", rounds=["2026-09-10", "2026-09-03"])

    def test_rounds_valid(self):
        t = mk(schedule_format="weekly", rounds=["2026-09-03", "2026-09-10"])
        self.assertEqual(len(t.rounds), 2)

    def test_prize_pool_needs_keys(self):
        with self.assertRaises(ValidationError):
            mk(prize_pool={"amount": 1000})

    def test_edition_positive(self):
        with self.assertRaises(ValidationError):
            mk(edition=0)


class TestSlugify(unittest.TestCase):
    def test_strips_ordinal_and_year(self):
        self.assertEqual(slugify("22. Lichtenberger Sommer 2026", 2026), "lichtenberger-sommer-2026")

    def test_umlaut_transliteration(self):
        self.assertEqual(slugify("Schöneberg Ü60 Open", 2025), "schoeneberg-ue60-open-2025")

    def test_roman_numeral(self):
        self.assertEqual(slugify("IX. Spandauer Weihnachtsopen", 2026), "spandauer-weihnachtsopen-2026")


class TestLoad(unittest.TestCase):
    def test_seed_loads(self):
        ts = models.load("data/tournaments.json")
        self.assertEqual(len(ts), 16)
        self.assertTrue(all(t.region in models.REGION for t in ts))

    def test_duplicate_id_rejected(self):
        import json
        import tempfile
        import os

        recs = [VALID, {**VALID}]  # same id twice
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, json.dumps(recs).encode())
        os.close(fd)
        try:
            with self.assertRaises(ValidationError):
                models.load(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
