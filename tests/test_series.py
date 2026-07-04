"""Recurring-Series engine tests + the backtest benchmark.

Run: python -m unittest discover -s tests -t .

The centerpiece is TestBacktest: it replays the 3 wedge series against REAL announced history
(tests/fixtures/history.json + captured per-source year fixtures) and asserts the *pipeline*
reaches the true dates — predicted right, or confirm-recovered when the guess drifted. It runs
OFFLINE: fetch adapters take injected fixture content, never the network.
"""
import json
import re
import unittest
from datetime import date

from bcc import series as S
from bcc.build import ROOT, validate

FX = ROOT / "tests" / "fixtures"
HIST = json.loads((FX / "history.json").read_text(encoding="utf-8"))["series"]


def _days(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _records_before(sid, year):
    """History editions < year, shaped like tournament records (id, dates, edition)."""
    return [{"id": f"{sid}-{e['year']}", "name": "x", "start_date": e["start_date"],
             "end_date": e["end_date"], "status": "confirmed",
             **({"edition": e["edition"]} if "edition" in e else {})}
            for e in HIST[sid]["editions"] if e["year"] < year]


def _truth(sid, year):
    e = next(e for e in HIST[sid]["editions"] if e["year"] == year)
    return e["start_date"], e["end_date"], e.get("edition")


class TestDateMath(unittest.TestCase):
    def test_easter(self):
        # known Gregorian Easter Sundays
        for y, iso in [(2016, "2016-03-27"), (2024, "2024-03-31"), (2025, "2025-04-20"),
                       (2026, "2026-04-05"), (2027, "2027-03-28")]:
            self.assertEqual(S.easter(y).isoformat(), iso)

    def test_nth_weekday(self):
        self.assertEqual(S.nth_weekday(2026, 5, 4, 3).isoformat(), "2026-05-21")  # 3rd Thu of May
        self.assertEqual(S.nth_weekday(2024, 5, 4, 3).isoformat(), "2024-05-16")

    def test_thursdays_from_mid_may(self):
        ds = S.thursdays_from_mid_may(2026, 7)
        self.assertEqual([d.isoformat() for d in ds],
                         ["2026-05-21", "2026-05-28", "2026-06-04", "2026-06-11",
                          "2026-06-18", "2026-06-25", "2026-07-02"])
        self.assertTrue(all(d.isoweekday() == 4 for d in ds))

    def test_last_weekday(self):
        d = S.last_weekday(2027, 12, 3)                 # last Wednesday of December
        self.assertEqual((d.month, d.isoweekday()), (12, 3))
        self.assertLess((date(2027, 12, 31) - d).days, 7)

    def test_month_weekday_slot(self):
        self.assertEqual(S.month_weekday_slot(date(2026, 8, 8)), (8, 6, 2))   # 2nd Saturday
        last_wed = S.last_weekday(2026, 12, 3)
        self.assertEqual(S.month_weekday_slot(last_wed)[2], -1)              # last-of-month -> -1

    def test_weekday_of_month_stays_in_month(self):
        self.assertEqual(S.weekday_of_month(2027, 12, 3, 5).month, 12)       # overflow clamps to Dec
        got = S.weekday_of_month(2027, 8, 6, 2)
        self.assertEqual((got.month, got.isoweekday()), (8, 6))


class TestParsers(unittest.TestCase):
    def test_dash_range(self):
        self.assertEqual(S.dash_range("22. Lichtenberger Sommer 2026 08. - 16. August 2026", 2026),
                         ("2026-08-08", "2026-08-16"))
        self.assertEqual(S.dash_range("Sommer 2025 23. – 31. August 2025", 2025),
                         ("2025-08-23", "2025-08-31"))
        self.assertEqual(S.dash_range("08. - 16. August 2026", 2027), (None, None))  # wrong year

    def test_weekly_dates_picks_thursday_run(self):
        txt = "Anmeldung bis 15.05. Termine: Do. 21.05. 28.05. 04.06. 11.06. Siegerehrung 02.07."
        got = S.weekly_dates(txt, 2026)
        self.assertEqual([d.isoformat() for d in got],
                         ["2026-05-21", "2026-05-28", "2026-06-04", "2026-06-11"])

    def test_jsonld_edition_year_filter(self):
        page = '<script type="application/ld+json">{"@type":"Event","startDate":"2026-04-02","endDate":"2026-04-06"}</script>'
        self.assertEqual(S.jsonld_edition("x", 2026, page=page),
                         {"start_date": "2026-04-02", "end_date": "2026-04-06", "source_url": "x"})
        self.assertIsNone(S.jsonld_edition("x", 2027, page=page))   # different year -> None

    def test_pdf_edition_against_real_fixture(self):
        for y, truth in [(2024, ("2024-05-30", "2024-07-11")),
                         (2025, ("2025-06-05", "2025-07-17")),
                         (2026, ("2026-05-21", "2026-07-02"))]:
            txt = (FX / f"hl-{y}.txt").read_text(encoding="utf-8")
            got = S.pdf_edition("x", y, text=txt)
            self.assertEqual((got["start_date"], got["end_date"]), truth, f"HL {y}")
            self.assertEqual(len(got["rounds"]), 7)

    def test_pdf_edition_none_when_no_text(self):
        self.assertIsNone(S.pdf_edition("x", 2026, text=None and ""))
        self.assertIsNone(S.pdf_edition("x", 2026, text="no dates here at all"))

    def test_pdf_link_for_resolves_year(self):
        page = (FX / "hl-landing.html").read_text(encoding="utf-8")
        self.assertEqual(S.pdf_link_for(page, 2026),
                         "https://skzehlendorf.de/storage/2026/03/HLG_Open_2026.pdf")
        self.assertEqual(S.pdf_link_for(page, 2025),
                         "https://skzehlendorf.de/storage/2025/03/9_Harald_Lieb_Gedenkturnier-1.pdf")
        self.assertIsNone(S.pdf_link_for(page, 2099))          # no PDF for that year
        self.assertIsNone(S.pdf_link_for("no links", 2026))

    def test_html_edition_against_real_fixture(self):
        for y, truth in [(2025, ("2025-08-23", "2025-08-31")),
                         (2026, ("2026-08-08", "2026-08-16"))]:
            page = (FX / f"lichtenberger-{y}.html").read_text(encoding="utf-8")
            got = S.html_edition("x", y, page=page)
            self.assertEqual((got["start_date"], got["end_date"]), truth, f"Lichtenberger {y}")


class TestRecordBuilder(unittest.TestCase):
    def test_edition_is_validate_clean(self):
        rec = S.LichtenbergerSommer()._edition(
            2027, date(2027, 8, 7), date(2027, 8, 15), edition=23, today="2026-07-04")
        validate(rec)                                   # raises if bad
        self.assertEqual(rec["id"], "lichtenberger-sommer-2027")
        self.assertEqual(rec["name"], "23. Lichtenberger Sommer")
        self.assertEqual(rec["status"], "expected")
        self.assertEqual(rec["sources"], ["recurring"])
        self.assertEqual(rec["tagged_by"], "auto")

    def test_edition_emits_series_and_rounds_count(self):
        rec = S.HaraldLieb()._edition(2027, date(2027, 5, 20), date(2027, 7, 1), edition=11,
                                      rounds=[date(2027, 5, 20) for _ in range(7)], today="2026-07-04")
        self.assertEqual(rec["series"], "harald-lieb-gedenkturnier")
        self.assertEqual(rec["rounds_count"], 7)             # n_rounds, and == len(rounds)
        validate(rec)

    def test_name_for(self):
        self.assertEqual(S.GrenkeChessOpen().name_for(2026), "Grenke Chess Open 2026")
        self.assertEqual(S.LichtenbergerSommer().name_for(2026, 22), "22. Lichtenberger Sommer")

    def test_id_is_series_id_plus_year(self):
        # the UID must match every existing id in the file (grenke-chess-open-2027 etc.)
        for s in S.REGISTRY:
            rec = s._edition(2027, date(2027, 6, 1), date(2027, 6, 2), today="2026-07-04")
            self.assertEqual(rec["id"], f"{s.series_id}-2027")


class TestPredict(unittest.TestCase):
    def test_grenke_is_easter_exact(self):
        rec = S.GrenkeChessOpen().predict(2026, [], today="2026-07-04")
        self.assertEqual((rec["start_date"], rec["end_date"]), ("2026-04-02", "2026-04-06"))

    def test_harald_lieb_edition_increments(self):
        recs = _records_before("harald-lieb-gedenkturnier", 2026)
        rec = S.HaraldLieb().predict(2026, recs, today="2026-07-04")
        self.assertEqual(rec["edition"], 10)
        self.assertEqual(rec["rounds"][0], "2026-05-21")
        self.assertEqual(len(rec["rounds"]), 7)

    def test_lichtenberger_uses_pinned_month_anchor(self):
        recs = _records_before("lichtenberger-sommer", 2026)
        rec = S.LichtenbergerSommer().predict(2026, recs, today="2026-07-04")
        self.assertEqual(rec["start_date"], "2026-08-08")   # 2nd Saturday of August
        self.assertEqual(rec["edition"], 22)

    def test_cold_start_returns_none(self):
        class NoSlot(S.Series):
            series_id = "x"; name = "X"
        self.assertIsNone(NoSlot().predict(2026, []))       # no prior edition, no pinned anchor


class TestConfirm(unittest.TestCase):
    def test_set_when_expected_exists(self):
        records = [{"id": "harald-lieb-gedenkturnier-2026", "status": "expected"}]
        cmd = S.confirm_command(S.HaraldLieb(), 2026, records,
                                reality={"start_date": "2026-05-21", "end_date": "2026-07-02"})
        self.assertEqual(cmd["op"], "set")
        self.assertEqual(cmd["fields"]["status"], "confirmed")
        self.assertEqual(cmd["fields"]["start_date"], "2026-05-21")

    def test_insert_when_no_record(self):
        cmd = S.confirm_command(S.HaraldLieb(), 2026, [],
                                reality={"start_date": "2026-05-21", "end_date": "2026-07-02"})
        self.assertEqual(cmd["op"], "insert")

    def test_none_when_no_reality(self):
        self.assertIsNone(S.confirm_command(S.GrenkeChessOpen(), 2026, [], reality=None and {}))


class TestBacktest(unittest.TestCase):
    """Replay 2024-2026 against real history: did the pipeline reach the true dates?"""

    YEARS = (2024, 2025, 2026)

    def _reality(self, series, year):
        """The captured year-Y source result for a series (offline). None if none captured."""
        sid = series.series_id
        if sid == "harald-lieb-gedenkturnier":
            return series.fetch(year, text=(FX / f"hl-{year}.txt").read_text(encoding="utf-8"))
        if sid == "lichtenberger-sommer":
            return series.fetch(year, page=(FX / f"lichtenberger-{year}.html").read_text(encoding="utf-8"))
        return None   # Grenke: no clean machine source; predict is exact so confirm isn't needed

    def test_pipeline_reaches_truth(self):
        rows, predict_hits, confirm_recover, misses = [], 0, 0, []
        total = 0
        for series in S.REGISTRY:
            sid = series.series_id
            if sid not in HIST:                 # only the wedge series have captured backtest history
                continue
            for year in self.YEARS:
                total += 1
                recs = _records_before(sid, year)
                ts, te, ted = _truth(sid, year)
                pred = series.predict(year, recs, today="2026-01-01")
                predict_hit = bool(pred) and _days(pred["start_date"], ts) <= 3 and \
                    (ted is None or pred.get("edition") == ted)
                confirm_hit = False
                if not predict_hit:
                    reality = self._reality(series, year)
                    confirm_hit = bool(reality) and reality["start_date"] == ts and reality["end_date"] == te
                    if confirm_hit:
                        confirm_recover += 1
                if predict_hit:
                    predict_hits += 1
                pipeline_hit = predict_hit or confirm_hit
                if not pipeline_hit:
                    misses.append(f"{sid} {year}")
                rows.append((sid, year, predict_hit, confirm_hit, pipeline_hit))

        pipeline = sum(1 for r in rows if r[4])
        # HARD GATE: the pipeline must reach truth for every machine-source cell.
        self.assertEqual(misses, [], f"pipeline fell through for: {misses}")
        self.assertEqual(pipeline, total)
        # quality signal (design bar >=70% within +-3d; not a hard gate — drift years lean on confirm)
        print(f"\n  BACKTEST: pipeline {pipeline}/{total} = {100*pipeline//total}% | "
              f"predict {predict_hits}/{total} = {100*predict_hits//total}% | "
              f"confirm recovered {confirm_recover} predict miss(es)")

    def test_grenke_predict_is_the_recovery(self):
        # Grenke has no clean machine source, so its pipeline MUST ride on exact predict.
        for year in self.YEARS:
            ts, te, _ = _truth("grenke-chess-open", year)
            pred = S.GrenkeChessOpen().predict(year, [], today="2026-01-01")
            self.assertEqual(pred["start_date"], ts, f"Grenke {year} predict must be exact")


if __name__ == "__main__":
    unittest.main()
