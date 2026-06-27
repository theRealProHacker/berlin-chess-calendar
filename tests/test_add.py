"""Tests for bcc.add (skeleton / insert / set / compact writer).

Run: python -m unittest discover -s tests -t .

Isolation: every test points `add.DATA` at a fresh temp copy so the real
data/tournaments.json is never touched. `add.cmd_insert`/`cmd_set` read and write
through `add.DATA`, so redirecting that one module global redirects the whole tool.

    setUp                         each test
    ─────                         ──────────
    write SEED -> tmp file        cmd_insert / cmd_set mutate the tmp file only
    add.DATA = tmp                assert on the reloaded tmp file (or its raw bytes
                                  for the "changes nothing on failure" atomicity tests)
"""
import json
import os
import tempfile
import unittest
from datetime import date

from bcc import add, build
from bcc.build import ValidationError, validate

# Two valid records, deliberately out of order so sorted-insert/sort behavior is observable.
SEED = [
    {
        "id": "b-open-2026", "name": "B Open", "kind": "open",
        "start_date": "2026-08-01", "end_date": "2026-08-03", "variant": "standard",
        "time_control": "classical", "age_limit": ["open"], "participation": "single",
        "schedule_format": "block", "region": "berlin", "status": "expected",
        "sources": ["dsb-berlin"], "last_verified": "2026-06-01",
    },
    {
        "id": "a-open-2026", "name": "A Open", "kind": "open",
        "start_date": "2026-07-01", "end_date": "2026-07-02", "variant": "standard",
        "time_control": "rapid", "age_limit": ["open"], "participation": "single",
        "schedule_format": "block", "region": "berlin", "status": "expected",
        "sources": ["bsv-termin"], "last_verified": "2026-06-01",
    },
]

CANDIDATE = {
    "id": "c-open-2026", "name": "C Open", "kind": "open",
    "start_date": "2026-07-15", "end_date": "2026-07-16", "variant": "standard",
    "time_control": "classical", "age_limit": ["open"], "participation": "single",
    "schedule_format": "block", "region": "berlin", "status": "expected",
    "sources": ["dsb-berlin"], "last_verified": "2026-06-01",
}


class AddCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self._real_data = add.DATA
        add.DATA = self.path
        self._seed([dict(r) for r in SEED])

    def tearDown(self):
        add.DATA = self._real_data
        os.unlink(self.path)

    def _seed(self, records):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(add.dumps(records))

    def _load(self):
        return build.load(self.path)

    def _raw(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def _cand_file(self, rec):
        fd, p = tempfile.mkstemp(suffix=".json")
        os.write(fd, json.dumps(rec).encode())
        os.close(fd)
        self.addCleanup(os.unlink, p)
        return p


class TestCompactWriter(unittest.TestCase):
    def test_real_file_roundtrips_identically(self):
        raw = open(build.ROOT / "data" / "tournaments.json", encoding="utf-8").read()
        self.assertEqual(add.dumps(build.load()), raw)

    def test_idempotent(self):
        once = add.dumps(build.load())
        twice = add.dumps(json.loads(once))
        self.assertEqual(once, twice)

    def test_prize_pool_inline_padded(self):
        rec = {**CANDIDATE, "prize_pool": {"amount": 3100, "currency": "EUR"}}
        self.assertIn('"prize_pool": { "amount": 3100, "currency": "EUR" }', add.dumps([rec]))

    def test_array_inline(self):
        out = add.dumps([{**CANDIDATE, "sources": ["dsb-berlin", "bsv-termin"]}])
        self.assertIn('"sources": ["dsb-berlin", "bsv-termin"]', out)


class TestSkeleton(unittest.TestCase):
    def test_required_keys_and_defaults(self):
        rec = add.skeleton("22. Lichtenberger Sommer", 2030)
        self.assertTrue(all(k in rec for k in build.REQUIRED))
        self.assertEqual(rec["id"], "lichtenberger-sommer-2030")
        self.assertEqual(rec["tagged_by"], "auto")
        self.assertEqual(rec["status"], "expected")
        self.assertEqual(rec["last_verified"], date.today().isoformat())
        # enum-valued defaults are legal vocabulary
        for field, allowed in build.ENUMS.items():
            if rec[field]:  # region/dates left blank on purpose
                self.assertIn(rec[field], allowed)

    def test_not_validate_clean(self):
        # blank dates/region + empty sources must fail validate() — it's a fill-in form
        self.assertRaises(ValidationError, validate, add.skeleton("X", 2030))


class TestInsert(AddCase):
    def test_valid_insert_sorted(self):
        add.cmd_insert(self._cand_file(CANDIDATE))
        ids = [r["id"] for r in self._load()]
        self.assertEqual(ids, ["a-open-2026", "c-open-2026", "b-open-2026"])  # by start_date

    def test_insert_reloads_clean(self):
        add.cmd_insert(self._cand_file(CANDIDATE))
        self.assertEqual(len(self._load()), 3)  # build.load() raising would fail the test

    def test_duplicate_id_rejected_no_change(self):
        before = self._raw()
        with self.assertRaises(SystemExit):
            add.cmd_insert(self._cand_file({**CANDIDATE, "id": "a-open-2026"}))
        self.assertEqual(self._raw(), before)  # byte-identical: changed nothing

    def test_invalid_record_rejected_no_change(self):
        before = self._raw()
        with self.assertRaises(ValidationError):
            add.cmd_insert(self._cand_file({**CANDIDATE, "variant": "xiangqi"}))
        self.assertEqual(self._raw(), before)

    def test_batch_insert_array(self):
        # ingest -> review -> insert: a JSON array inserts all records, sorted
        c2 = {**CANDIDATE, "id": "d-open-2026", "start_date": "2026-06-01", "end_date": "2026-06-02"}
        add.cmd_insert(self._cand_file([CANDIDATE, c2]))
        ids = [r["id"] for r in self._load()]
        self.assertEqual(ids, ["d-open-2026", "a-open-2026", "c-open-2026", "b-open-2026"])

    def test_batch_dup_within_batch_rejected_no_change(self):
        before = self._raw()
        with self.assertRaises(SystemExit):
            add.cmd_insert(self._cand_file([CANDIDATE, dict(CANDIDATE)]))
        self.assertEqual(self._raw(), before)


class TestSet(AddCase):
    def test_status_flip_and_dates(self):
        add.cmd_set("a-open-2026", ["status=confirmed", "start_date=2026-07-01", "end_date=2026-07-02"])
        rec = next(r for r in self._load() if r["id"] == "a-open-2026")
        self.assertEqual(rec["status"], "confirmed")

    def test_always_bumps_last_verified(self):
        add.cmd_set("a-open-2026", ["status=confirmed"])
        rec = next(r for r in self._load() if r["id"] == "a-open-2026")
        self.assertEqual(rec["last_verified"], date.today().isoformat())

    def test_prize_pool_json_object(self):
        add.cmd_set("a-open-2026", ['prize_pool={"amount":5150,"currency":"EUR"}'])
        rec = next(r for r in self._load() if r["id"] == "a-open-2026")
        self.assertEqual(rec["prize_pool"], {"amount": 5150, "currency": "EUR"})

    def test_json_first_value_parsing(self):
        # bare number -> int (edition is int); URL with '=' / '&' -> string (json parse fails)
        add.cmd_set("a-open-2026", ["edition=22", "registration=https://x.de/?a=1&b=2"])
        rec = next(r for r in self._load() if r["id"] == "a-open-2026")
        self.assertEqual(rec["edition"], 22)
        self.assertEqual(rec["registration"], "https://x.de/?a=1&b=2")

    def test_id_not_found(self):
        with self.assertRaises(SystemExit):
            add.cmd_set("nope-2026", ["status=confirmed"])

    def test_start_date_change_resorts(self):
        # move a-open after b-open by date -> array re-sorts ascending
        add.cmd_set("a-open-2026", ["start_date=2026-09-01", "end_date=2026-09-02"])
        ids = [r["id"] for r in self._load()]
        self.assertEqual(ids, ["b-open-2026", "a-open-2026"])

    def test_invalid_value_rejected_no_change(self):
        before = self._raw()
        with self.assertRaises(ValidationError):
            add.cmd_set("a-open-2026", ["variant=xiangqi"])
        self.assertEqual(self._raw(), before)  # atomic: refused, file untouched

    def test_unknown_field_rejected_no_change(self):
        before = self._raw()
        with self.assertRaises(ValidationError):
            add.cmd_set("a-open-2026", ["colour=blue"])
        self.assertEqual(self._raw(), before)


if __name__ == "__main__":
    unittest.main()
