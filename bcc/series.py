"""Recurring-tournament class layer: predict future editions, confirm them from real sources.

    python3 -m bcc.series predict [--apply]    # ensure the horizon of expected editions exists
    python3 -m bcc.series confirm [--apply]    # fetch real dates -> promote expected -> confirmed
    python3 -m bcc.series check [--refetch]    # backtest: is each confirmed edition reproduced now?
    python3 -m bcc.series missing              # what a human must still fill / hand-confirm
    python3 -m bcc.series suggest              # feed events matching no known series (the scout)

Sources are fetched through a 2-week on-disk snapshot cache (bcc/feeds.cached_get); pass
`--refetch` to bypass it and pull every source live ("the real deal").

One base `Series` + one singleton subclass per recurring series in the explicit REGISTRY.
The fat base does the common case (same ISO-week slot next year, edition+1, look me up in a
machine source); a subclass overrides only the bespoke bits. Recurrence logic is Python, the
editions stay in data/tournaments.json, and every generated record passes bcc.build.validate.

Confirmation is automated as far as each source allows, per series:
  - feed        default fetch(): find me on the BSV/DSB RSS by normalized name
  - web fetch   Grenke (JSON-LD), Harald-Lieb (Ausschreibung PDF via pdftotext),
                Lichtenberger (organizer HTML)
  - manual      fetch() returns None -> `missing` flags it for hand-confirmation
`confirm` proposes a reviewable batch of `add set` / `add insert` commands (diffs by default,
--apply opt-in); a human approves before anything is written. `confirm` + `missing` are meant
to be run together each month.

`id = f"{series_id}-{year}"` is the iCal UID and matches every id already in the file, so a
predicted edition and its confirmed self share one UID — promotion never duplicates a
subscriber's event. `pdftotext` is an OPTIONAL curation-time tool: absent it, the PDF fetch
degrades to None (manual), and nothing that ships or that CI runs needs it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta

from . import add
from .build import ROOT, load, validate
from .feeds import (_MONTH, _MONTHS, _meta_description, cached_get, chess_results_search,
                    fetch_all, fetch_youth, jsonld_event_dates, norm_name, parse_de_date,
                    set_refetch, strip_html)


# ---- date math (stdlib computus, no dependency) -----------------------------

def easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous Gregorian / 'Meeus/Jones/Butcher' algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (1=Mon..7=Sun) of `month` in `year` (n>=1)."""
    first = date(year, month, 1)
    offset = (weekday - first.isoweekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` of `month` in `year`."""
    nextm = date(year + (month == 12), (month % 12) + 1, 1)
    last = nextm - timedelta(days=1)
    return last - timedelta(days=(last.isoweekday() - weekday) % 7)


def month_weekday_slot(d: date):
    """(month, weekday, k) for date `d`. k = the occurrence-from-start of that weekday in the
    month, or -1 when `d` is the LAST such weekday (so Christmas-week events anchor to
    "last <weekday> of December" and never drift into January)."""
    weekday = d.isoweekday()
    from_start = (d.day - 1) // 7 + 1
    return d.month, weekday, (-1 if d == last_weekday(d.year, d.month, weekday) else from_start)


def weekday_of_month(year: int, month: int, weekday: int, k: int) -> date:
    """Resolve a (month, weekday, k) slot to a date in `year`. k<0 counts from the month end
    (-1 = last); a k that overflows the month clamps to the last occurrence — so the result
    always stays inside `month`."""
    if k < 0:
        return last_weekday(year, month, weekday) - timedelta(weeks=-k - 1)
    d = nth_weekday(year, month, weekday, k)
    return d if d.month == month else last_weekday(year, month, weekday)


def thursdays_from_mid_may(year: int, n: int) -> list[date]:
    """`n` consecutive Thursday evenings, anchored on the 3rd Thursday of May.

    The Harald-Lieb schedule. The real start Thursday drifts ~2 weeks year to year (it dodges
    Ascension/Pentecost), so this is only the guess — `confirm` reads the true dates from the
    Ausschreibung PDF when they differ. Kept deterministic so the prediction never surprises.
    """
    start = nth_weekday(year, 5, 4, 3)          # 4 = Thursday, 3rd of May
    return [start + timedelta(weeks=w) for w in range(n)]


# ---- base class -------------------------------------------------------------

class Series:
    # identity & fixed axes (override per subclass)
    series_id: str = ""
    name: str = ""                    # single display stem, in the page's own language
    edition_numbered: bool = True     # "22. Lichtenberger Sommer" vs "Grenke Chess Open 2026"
    organizer = None
    venue = None                      # the specific playing site, never a bare district
    city = "Berlin"
    source_url = None
    cr_organizer = None               # chess-results organizer-PERSON (stable per series), a match hint
    kind = "open"
    variant = "standard"
    time_control = "classical"
    participation = "single"
    region = "berlin"
    schedule_format = "block"
    age_limit = ("open",)
    # recurrence hints for the default predictor (the "Kth weekday of a month" anchor).
    # All None -> derive the slot from the latest known edition. Pin them to override a noisy
    # latest edition (e.g. Lichtenberger's 2025 slip): anchor_week may be negative (-1 = last).
    anchor_month: int | None = None   # 1-12
    anchor_week: int | None = None    # Kth occurrence in the month (1..5, or -1 = last)
    weekday: int | None = None        # start weekday (1=Mon..7=Sun)
    span_days = 0                     # end = start + span_days
    n_rounds: int | None = None
    horizon_years = 2

    # ---- edition lookup ----
    def editions(self, records):
        """Records belonging to this series: the explicit `series` field, else the UID prefix."""
        pat = re.compile(rf"^{re.escape(self.series_id)}-(\d{{4}})$")
        return [r for r in records
                if r.get("series") == self.series_id or (r.get("series") is None and pat.match(r["id"]))]

    def latest_edition(self, records):
        eds = self.editions(records)
        if not eds:
            return None
        # a confirmed edition is a stronger base than an expected one at the same date
        return max(eds, key=lambda r: (r["start_date"], r.get("status") == "confirmed"))

    def name_for(self, year, edition=None) -> str:
        if not self.edition_numbered:
            return f"{self.name} {year}"
        return f"{edition}. {self.name}" if edition else self.name

    # ---- record builder ----
    def _edition(self, year, start, end, *, edition=None, rounds=None, status="expected",
                 sources=None, source_url=None, today=None) -> dict:
        """Build one validate()-clean edition record. id is series_id-derived, not name-derived."""
        today = today or date.today().isoformat()
        rec = {
            "id": f"{self.series_id}-{year}",
            "series": self.series_id,
            "name": self.name_for(year, edition),
            "kind": self.kind,
            "start_date": start.isoformat() if isinstance(start, date) else start,
            "end_date": end.isoformat() if isinstance(end, date) else end,
            "variant": self.variant,
            "time_control": self.time_control,
            "age_limit": list(self.age_limit),
            "participation": self.participation,
            "schedule_format": self.schedule_format,
            "region": self.region,
            "status": status,
            "sources": list(sources) if sources else ["recurring"],
            "last_verified": today,
            "tagged_by": "auto",
        }
        if edition is not None:
            rec["edition"] = edition
        if rounds:
            rec["rounds"] = [d.isoformat() if isinstance(d, date) else d for d in rounds]
        rc = self.n_rounds if self.n_rounds is not None else (len(rounds) if rounds else None)
        if rc is not None:
            rec["rounds_count"] = rc
        for attr in ("organizer", "venue", "city"):
            v = getattr(self, attr, None)
            if v:
                rec[attr] = v
        su = source_url or self.source_url
        if su:
            rec["source_url"] = su
        return validate(rec)

    # ---- prediction (default: same "Kth weekday of month" slot, edition + delta) ----
    def predict(self, year, records, *, today=None) -> dict | None:
        prev = self.latest_edition(records)
        edition = (prev["edition"] + (year - date.fromisoformat(prev["start_date"]).year)
                   if prev and prev.get("edition") else None)
        if self.anchor_month is not None:         # a pinned slot overrides a noisy latest edition
            month, weekday, k = self.anchor_month, self.weekday, self.anchor_week
        elif prev is not None:                     # derive the slot from the latest known edition
            month, weekday, k = month_weekday_slot(date.fromisoformat(prev["start_date"]))
        else:
            return None                            # no slot to predict from -> `missing` flags it
        start = weekday_of_month(year, month, weekday, k)
        end = start + timedelta(days=self.span_days)
        return self._edition(year, start, end, edition=edition, today=today)

    # ---- confirmation source ----
    def fetch(self, year, *, feed_events=None, cr=None) -> dict | None:
        """Return the real edition's dates {start_date,end_date[,rounds][,source_url]} or None.

        A Berlin series confirms from chess-results first (the forward-looking DB), then falls
        back to the RSS feed. Override for a bespoke machine source; return None = manual-only.
        """
        return self._cr_fetch(year, rows=cr) or self._feed_fetch(year, feed_events=feed_events)

    def _cr_fetch(self, year, *, rows=None) -> dict | None:
        """Match this series on chess-results (Berlin series only), by name-stem + organizer-person."""
        if self.region != "berlin":
            return None
        rows = rows if rows is not None else cr_rows(year)
        return cr_match(rows, self.name, year, organizer=self.cr_organizer)

    def _feed_fetch(self, year, *, feed_events=None) -> dict | None:
        """The BSV/DSB RSS by normalized name within the target year (near-term only)."""
        evs = feed_events if feed_events is not None else fetch_all()
        target = norm_name(self.name)
        hits = [e for e in evs if norm_name(e.name) == target and e.start[:4] == str(year)]
        if not hits:
            return None
        e = min(hits, key=lambda x: x.start)
        return {"start_date": e.start, "end_date": e.end, "source_url": e.link or None}


# ---- fetch adapters (thin, injectable so the backtest runs offline) ---------

def _have_pdftotext() -> bool:
    return shutil.which("pdftotext") is not None


def _download(url: str) -> bytes:
    return cached_get(url, binary=True)


def http_page(url: str) -> str:
    return cached_get(url)


def pdf_text(url: str) -> str | None:
    """`pdftotext -layout` of a downloaded PDF, or None if the binary is absent / conversion fails.

    OPTIONAL dependency by design: no pdftotext -> None -> the caller degrades to manual. Nothing
    that ships or that CI runs invokes this; only the maintainer's local `confirm` does.
    """
    if not _have_pdftotext():
        return None
    try:
        raw = _download(url)
    except Exception:
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(raw)
        f.flush()
        try:
            out = subprocess.run(["pdftotext", "-layout", f.name, "-"],  # nosec - fixed argv
                                 capture_output=True, timeout=30)
        except Exception:
            return None
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


def jsonld_edition(url, year, *, page=None) -> dict | None:
    """Grenke-style: the schema.org Event date on the page, if it falls in `year`."""
    page = page if page is not None else http_page(url)
    s, e = jsonld_event_dates(page)
    if s and s[:4] == str(year):
        return {"start_date": s, "end_date": e, "source_url": url}
    return None


_DDMM = re.compile(r"\b(\d{1,2})\.\s*(\d{1,2})\.(?:\s*(\d{4}))?")


def weekly_dates(text, year, weekday=4) -> list[date]:
    """Longest run of same-`weekday` dates 7 days apart, parsed from DD.MM.[YYYY] tokens in `year`.

    For the Harald-Lieb Ausschreibung: its "Termine / Do. dd.mm." lines list the round Thursdays.
    Robust to stray dates (deadlines, footers): only same-weekday, weekly-spaced dates chain.
    """
    seen = set()
    for dd, mm, yy in _DDMM.findall(text):
        y = int(yy) if yy else year
        if y != year:
            continue
        try:
            d = date(year, int(mm), int(dd))
        except ValueError:
            continue
        if d.isoweekday() == weekday:
            seen.add(d)
    days = sorted(seen)
    best: list[date] = []
    for i, d0 in enumerate(days):
        run = [d0]
        for d1 in days[i + 1:]:
            if (d1 - run[-1]).days == 7:
                run.append(d1)
            elif d1 > run[-1]:
                break
        if len(run) > len(best):
            best = run
    return best


def pdf_edition(url, year, *, text=None, weekday=4, min_rounds=3) -> dict | None:
    """Harald-Lieb-style: the weekly round Thursdays from the Ausschreibung PDF, if in `year`."""
    txt = text if text is not None else pdf_text(url)
    if not txt:
        return None
    rounds = weekly_dates(txt, year, weekday)
    if len(rounds) < min_rounds:
        return None
    return {"start_date": rounds[0].isoformat(), "end_date": rounds[-1].isoformat(),
            "rounds": [d.isoformat() for d in rounds], "source_url": url}


def pdf_link_for(page, year) -> str | None:
    """The year's Ausschreibung PDF href on a landing page (skzehlendorf uses /storage/YYYY/...pdf)."""
    for u in re.findall(r'href="([^"]+\.pdf)"', page, re.I):
        if f"/storage/{year}/" in u:
            return u
    return None


# "08. - 16. August 2026" / "23. – 31. August 2025" — day. [dash] day. Month year (Lichtenberger)
_RANGE_DASH = re.compile(r"(\d{1,2})\.\s*[–\-]\s*(\d{1,2})\.\s*" + _MONTH + r"\s*(\d{4})", re.I)


def dash_range(text, year) -> tuple:
    """First 'DD. – DD. Month YYYY' range whose year == `year`, as (start_iso, end_iso) or (None, None)."""
    for dd1, dd2, mon, yy in _RANGE_DASH.findall(text):
        if int(yy) != year:
            continue
        mo = _MONTHS[mon.lower()]
        try:
            s, e = date(year, mo, int(dd1)), date(year, mo, int(dd2))
        except ValueError:
            continue
        if e >= s:
            return s.isoformat(), e.isoformat()
    return None, None


def html_edition(url, year, *, page=None) -> dict | None:
    """Lichtenberger-style: JSON-LD if present, else a German date range in the page/meta text."""
    page = page if page is not None else http_page(url)
    got = jsonld_edition(url, year, page=page)
    if got:
        return got
    text = _meta_description(page) + " " + strip_html(page)
    s, e = dash_range(text, year)
    if not s:
        s, e = parse_de_date(text, f"{year}-01-01")
        if s and s[:4] != str(year):
            s = None
    if s:
        return {"start_date": s, "end_date": e, "source_url": url}
    return None


_CR_DATE = re.compile(r"\b(\d{4})/(\d{2})/(\d{2})\b")


def _parse_cr_rows(html):
    """Parse a chess-results search-results table -> [{tnr,name,start,end,text}]. Cells are sparse
    (empty ones omitted), so match by content: the tnr-link cell is the name, the first two
    YYYY/MM/DD are from/to, and `text` is the whole row (for an organizer substring match)."""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        m = re.search(r"tnr(\d+)\.aspx", tr)
        dates = _CR_DATE.findall(tr)
        if not m or len(dates) < 2:
            continue
        cell = re.search(r"<td[^>]*>((?:(?!</td>).)*?tnr\d+\.aspx.*?)</td>", tr, re.S)
        name = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell.group(1))).strip() if cell else ""
        out.append({"tnr": m.group(1), "name": name,
                    "start": "%s-%s-%s" % dates[0], "end": "%s-%s-%s" % dates[1],
                    "text": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tr)).strip()})
    return out


def cr_match(rows, name, year, *, organizer=None) -> dict | None:
    """Pick the chess-results row for `name`/`year`. The name-stem is the SELECTOR (so we grab the
    right event); the organizer-person only DISAMBIGUATES when several rows share the stem — it is
    never a standalone match (one organizer runs many events, so org-only would false-match the
    wrong one). Earliest start wins remaining ties (age-group sub-tournaments share the dates)."""
    target = norm_name(name)
    cands = [r for r in rows if r["start"][:4] == str(year) and target in norm_name(r["name"])]
    if organizer and len(cands) > 1:
        org = norm_name(organizer)
        narrowed = [r for r in cands if org in norm_name(r["text"])]
        if narrowed:
            cands = narrowed
    if not cands:
        return None
    r = min(cands, key=lambda x: x["start"])
    return {"start_date": r["start"], "end_date": r["end"],
            "source_url": f"https://chess-results.com/tnr{r['tnr']}.aspx?lan=1"}


def cr_rows(year):
    return _parse_cr_rows(chess_results_search(year))


def wp_edition(name, year, *, raws=None) -> dict | None:
    """Youth confirm: find this series' real edition for `year` on the schachjugend WordPress
    REST source, matched by normalized name. The WP source lists only UPCOMING events, so this
    confirms current/near editions (past youth editions scroll off — youth are not backtested)."""
    raws = raws if raws is not None else fetch_youth()
    target = norm_name(name)
    hits = [r for r in raws if norm_name(r.name) == target and r.start[:4] == str(year)]
    if not hits:
        return None
    r = min(hits, key=lambda x: x.start)
    return {"start_date": r.start, "end_date": r.end, "source_url": r.link or None}


# ---- the three wedge subclasses --------------------------------------------

class GrenkeChessOpen(Series):
    series_id = "grenke-chess-open"
    name = "Grenke Chess Open"
    edition_numbered = False
    region = "national"
    city = "Karlsruhe"
    venue = "Karlsruhe Congress Center"
    organizer = "Schachzentrum Baden-Baden e.V."
    source_url = "https://www.grenkechessopen.de/"
    span_days = 4
    n_rounds = 9

    def predict(self, year, records, *, today=None):
        start = easter(year) - timedelta(days=3)        # Maundy Thursday
        return self._edition(year, start, start + timedelta(days=4), today=today)  # -> Easter Monday

    def fetch(self, year, *, page=None, feed_events=None):
        return jsonld_edition(self.source_url, year, page=page)


class HaraldLieb(Series):
    series_id = "harald-lieb-gedenkturnier"
    name = "Harald-Lieb-Gedenkturnier"
    kind = "memorial"
    organizer = "SK Zehlendorf"
    venue = "Hans-Rosenthal-Haus, Berlin-Zehlendorf"
    schedule_format = "weekly"
    n_rounds = 7
    source_url = "https://skzehlendorf.de/turniere/harald-lieb-gedenkturnier/"

    def predict(self, year, records, *, today=None):
        prev = self.latest_edition(records)
        edition = prev["edition"] + (year - date.fromisoformat(prev["start_date"]).year) \
            if prev and prev.get("edition") else None
        thursdays = thursdays_from_mid_may(year, self.n_rounds)
        return self._edition(year, thursdays[0], thursdays[-1], edition=edition,
                             rounds=thursdays, today=today)

    def fetch(self, year, *, text=None, landing=None, feed_events=None):
        # test/offline: parse injected pdftotext output directly. live: read the landing page,
        # resolve THIS year's Ausschreibung PDF link, then download + parse it.
        if text is not None:
            return pdf_edition(self.source_url, year, text=text)
        page = landing if landing is not None else http_page(self.source_url)
        pdf_url = pdf_link_for(page, year)
        return pdf_edition(pdf_url, year) if pdf_url else None


class LichtenbergerSommer(Series):
    series_id = "lichtenberger-sommer"
    name = "Lichtenberger Sommer"
    organizer = "Schachfreunde Berlin 1903 / SV Friesen Lichtenberg"
    venue = "Tribünenhalle Trabrennbahn Karlshorst"
    source_url = "https://friesen-lichtenberg.de/lichtenberger-sommer/"
    anchor_month = 8            # 2nd Saturday of August (stable 2023/24/26; 2025 slipped to wk34)
    anchor_week = 2
    weekday = 6
    span_days = 8
    n_rounds = 9

    def fetch(self, year, *, page=None, feed_events=None):
        return html_edition(self.source_url, year, page=page)


class YouthSeries(Series):
    """Berlin youth series: chess-results first (forward-looking), then the schachjugend WordPress
    source (near-term). Most Berlin youth events are organized by 'Olaf Sill' on chess-results."""
    kind = "youth"
    cr_organizer = "Olaf Sill"

    def fetch(self, year, *, raws=None, cr=None, feed_events=None):
        return self._cr_fetch(year, rows=cr) or wp_edition(self.name, year, raws=raws)


# ---- ordinary recurring series (thin subclasses; predict + default fetch inherited) ----
# Axes derived from each series' existing edition in data/tournaments.json. Season-long team
# leagues (BMM, BJMM) and the ~10 national championships stay data-adds: no stable annual slot.
# The youth series confirm via the schachjugend WordPress source (YouthSeries), not the RSS feed.

class AbrafaxeTurnier(YouthSeries):
    series_id = 'abrafaxe-turnier'
    name = 'Abrafaxe-Turnier'
    age_limit = ('U8', 'U10', 'U12', 'U14')
    time_control = 'rapid'
    organizer = 'SC Borussia Lichtenberg (Schach-Abrafaxe)'
    source_url = 'https://www.abrafaxe-kinderschachturnier.de/'
    n_rounds = 7
    anchor_month = 6            # actually runs mid-June (2024-06-08, 2025-06-21), not late May
    anchor_week = 2
    weekday = 6
    cr_organizer = 'Thomas Neumann'   # not Olaf Sill; own organizer on chess-results

class BerlinerJugendblitzmeisterschaft(YouthSeries):
    series_id = 'berliner-jugendblitzmeisterschaft'
    name = 'Berliner Jugendblitzmeisterschaft'
    age_limit = ('U8', 'U10', 'U12', 'U14', 'U16', 'U18', 'U25')
    time_control = 'blitz'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    n_rounds = 11

class BerlinerJugendeinzelmeisterschaftBjem(YouthSeries):
    series_id = 'berliner-jugendeinzelmeisterschaft-bjem'
    name = 'Berliner Jugendeinzelmeisterschaft (BJEM)'
    age_limit = ('U8', 'U10', 'U12', 'U14', 'U16', 'U18')
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    venue = 'Andreas-Gymnasium, Berlin-Friedrichshain'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    span_days = 6

class BerlinerJugendeinzelmeisterschaftDerMaedchenBjemw(YouthSeries):
    series_id = 'berliner-jugendeinzelmeisterschaft-der-maedchen-bjemw'
    name = 'Berliner Jugendeinzelmeisterschaft der Mädchen (BJEMw)'
    age_limit = ('U8', 'U10', 'U12', 'U14', 'U16', 'U18')
    schedule_format = 'other'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    span_days = 43
    n_rounds = 3

class BerlinerJugendschnellschachmeisterschaft(YouthSeries):
    series_id = 'berliner-jugendschnellschachmeisterschaft'
    name = 'Berliner Jugendschnellschachmeisterschaft'
    age_limit = ('U8', 'U10', 'U12', 'U14', 'U16', 'U18', 'U25')
    time_control = 'rapid'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    n_rounds = 7

class BerlinerSchnellschachEm(Series):
    series_id = 'berliner-schnellschach-em'
    name = 'Berliner Schnellschach-Einzelmeisterschaft'
    kind = 'championship'
    time_control = 'rapid'
    edition_numbered = False
    organizer = 'Berliner Schachverband'
    source_url = 'https://www.berlinerschachverband.de/termin/berliner-schnellschach-em-2026.html'

class BerlinerSchnellschachMm(Series):
    series_id = 'berliner-schnellschach-mm'
    name = 'Berliner Schnellschach-Mannschaftsmeisterschaft'
    kind = 'championship'
    time_control = 'rapid'
    participation = 'team'
    edition_numbered = False
    organizer = 'Berliner Schachverband'
    source_url = 'https://www.berlinerschachverband.de/termin/berliner-schnellschach-mm-2026.html'

class BerlinerTandemschachTreffen(Series):
    series_id = 'berliner-tandemschach-treffen'
    name = 'Berliner Tandemschach-Treffen'
    kind = 'festival'
    variant = 'tandem'
    time_control = 'blitz'
    participation = 'duo'
    edition_numbered = False
    organizer = 'Bughouse Berlin'
    source_url = 'https://www.schachbund.de/turnierdetails/berliner-tandemschach-treffen.html'
    span_days = 3

class InternationalesBerlinerTandemschachOpen(Series):
    series_id = 'internationales-berliner-tandemschach-open'
    name = 'Internationales Berliner Tandemschach-Open'
    variant = 'tandem'
    time_control = 'blitz'
    participation = 'duo'
    organizer = 'Bughouse Berlin'
    source_url = 'https://www.schachbund.de/turnierdetails/22-internationales-berliner-tandemschach-open.html'

class InternationalesEmanuelLaskerSchachfestival(Series):
    series_id = 'internationales-emanuel-lasker-schachfestival'
    name = 'Internationales Emanuel-Lasker-Schachfestival'
    kind = 'festival'
    region = 'nearby'
    city = 'Barlinek, Polen'
    source_url = 'https://www.berlinerschachverband.de/termin/7-internationales-emanuel-lasker-schachfestival.html'

class JugendEloRapidU20(YouthSeries):
    series_id = 'jugend-elo-rapid-u20'
    name = 'Jugend-Elo-Rapid – U20'
    age_limit = ('U20',)
    time_control = 'rapid'
    edition_numbered = False
    source_url = 'https://www.schachjugend-in-berlin.de/jugend-elo-rapid-u20-am-11-juli-2026/'

class JugendMannschaftsopen(YouthSeries):
    series_id = 'jugend-mannschaftsopen'
    name = 'Jugend-Mannschaftsopen'
    age_limit = ('U8', 'U10', 'U12', 'U14', 'U16', 'U18', 'U25')
    participation = 'team'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    span_days = 1

class Jugendherbstopen(YouthSeries):
    series_id = 'jugendherbstopen'
    name = 'Jugendherbstopen'
    age_limit = ('U8', 'U10', 'U12', 'U14', 'U16', 'U18')
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    venue = 'Andreas-Gymnasium, Berlin-Friedrichshain'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    span_days = 1
    n_rounds = 5

class JugendschnellschachMannschaftsopen(YouthSeries):
    series_id = 'jugendschnellschach-mannschaftsopen'
    name = 'Jugendschnellschach-Mannschaftsopen'
    age_limit = ('U8', 'U10', 'U12', 'U14', 'U16', 'U18', 'U25')
    time_control = 'rapid'
    participation = 'team'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    span_days = 1

class Jugendsommeropen(YouthSeries):
    series_id = 'jugendsommeropen'
    name = 'Jugendsommeropen'
    age_limit = ('U8', 'U10', 'U12', 'U14', 'U16', 'U18')
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    venue = 'Andreas-Gymnasium, Berlin-Friedrichshain'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    span_days = 1
    n_rounds = 5

class Jugendwinteropen(YouthSeries):
    series_id = 'jugendwinteropen'
    name = 'Jugendwinteropen'
    age_limit = ('U25',)
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    venue = 'Andreas-Gymnasium, Berlin-Friedrichshain'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    span_days = 1
    n_rounds = 7

class Kinderfruehlingsturnier(YouthSeries):
    series_id = 'kinderfruehlingsturnier'
    name = 'Kinderfrühlingsturnier'
    age_limit = ('U8', 'U10', 'U12')
    time_control = 'rapid'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    venue = 'Andreas-Gymnasium, Berlin-Friedrichshain'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    span_days = 1

class Kinderherbstopen(YouthSeries):
    series_id = 'kinderherbstopen'
    name = 'Kinderherbstopen'
    age_limit = ('U8', 'U10', 'U12')
    time_control = 'rapid'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    venue = 'Andreas-Gymnasium, Berlin-Friedrichshain'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    n_rounds = 5

class Kindersommeropen(YouthSeries):
    series_id = 'kindersommeropen'
    name = 'Kindersommeropen'
    age_limit = ('U8', 'U10', 'U12')
    time_control = 'rapid'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    venue = 'Andreas-Gymnasium, Berlin-Friedrichshain'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    n_rounds = 5

class Kinderwinteropen(YouthSeries):
    series_id = 'kinderwinteropen'
    name = 'Kinderwinteropen'
    age_limit = ('U8', 'U10', 'U12')
    time_control = 'rapid'
    edition_numbered = False
    organizer = 'Schachjugend in Berlin'
    venue = 'Andreas-Gymnasium, Berlin-Friedrichshain'
    source_url = 'https://www.schachjugend-in-berlin.de/'
    n_rounds = 5

class KreuzbergerSchachsommerWernerOttOpen(Series):
    series_id = 'kreuzberger-schachsommer-werner-ott-open'
    name = 'Kreuzberger Schachsommer – Werner-Ott-Open'
    edition_numbered = False
    organizer = 'SC Kreuzberg e.V.'
    venue = 'SC Kreuzberg e.V., Haus des Sports, Böcklerstr. 1, 10969 Berlin-Kreuzberg'
    source_url = 'https://www.schachbund.de/turnierdetails/kreuzberger-schachsommer-werner-ott-open-2026.html'
    span_days = 8
    n_rounds = 9

class SpandauerWeihnachtsBlitz(Series):
    series_id = 'spandauer-weihnachts-blitz'
    name = 'Spandauer Weihnachts-Blitz'
    time_control = 'blitz'
    edition_numbered = False
    venue = 'Bürgersaal, Rathaus Spandau, Carl-Schurz-Str. 2/6, 13597 Berlin'
    source_url = 'https://www.schachbund.de/turnierdetails/spandauer-weihnachts-blitz-2026.html'

class SpandauerWeihnachtsRapid(Series):
    series_id = 'spandauer-weihnachts-rapid'
    name = 'Spandauer Weihnachts-Rapid'
    time_control = 'rapid'
    edition_numbered = False
    venue = 'Bürgersaal, Rathaus Spandau, Carl-Schurz-Str. 2/6, 13597 Berlin'
    source_url = 'https://www.schachbund.de/turnierdetails/spandauer-weihnachts-rapid-2026.html'

class SpandauerWeihnachtsopenAOpen(Series):
    series_id = 'spandauer-weihnachtsopen-a-open'
    name = 'Spandauer Weihnachtsopen | A-Open'
    venue = 'Bürgersaal, Rathaus Spandau, Carl-Schurz-Str. 2/6, 13597 Berlin'
    source_url = 'https://www.schachbund.de/turnierdetails/9-spandauer-weihnachtsopen-a-open.html'
    span_days = 3

class SpandauerWeihnachtsopenBOpen(Series):
    series_id = 'spandauer-weihnachtsopen-b-open'
    name = 'Spandauer Weihnachtsopen | B-Open'
    venue = 'Bürgersaal, Rathaus Spandau, Carl-Schurz-Str. 2/6, 13597 Berlin'
    source_url = 'https://www.schachbund.de/turnierdetails/9-spandauer-weihnachtsopen-b-open.html'
    span_days = 3

class Spreewaldpokal(Series):
    series_id = 'spreewaldpokal'
    name = 'Spreewaldpokal'
    region = 'nearby'
    edition_numbered = False
    venue = 'Landhaus Duben, Dubener Hauptstraße 5, 15926 Luckau OT Duben'
    city = 'Luckau (OT Duben)'
    source_url = 'https://www.berlinerschachverband.de/termin/spreewaldpokal-2026.html'


REGISTRY = [
    GrenkeChessOpen(), HaraldLieb(), LichtenbergerSommer(),
    AbrafaxeTurnier(),
    BerlinerJugendblitzmeisterschaft(),
    BerlinerJugendeinzelmeisterschaftBjem(),
    BerlinerJugendeinzelmeisterschaftDerMaedchenBjemw(),
    BerlinerJugendschnellschachmeisterschaft(),
    BerlinerSchnellschachEm(),
    BerlinerSchnellschachMm(),
    BerlinerTandemschachTreffen(),
    InternationalesBerlinerTandemschachOpen(),
    InternationalesEmanuelLaskerSchachfestival(),
    JugendEloRapidU20(),
    JugendMannschaftsopen(),
    Jugendherbstopen(),
    JugendschnellschachMannschaftsopen(),
    Jugendsommeropen(),
    Jugendwinteropen(),
    Kinderfruehlingsturnier(),
    Kinderherbstopen(),
    Kindersommeropen(),
    Kinderwinteropen(),
    KreuzbergerSchachsommerWernerOttOpen(),
    SpandauerWeihnachtsBlitz(),
    SpandauerWeihnachtsRapid(),
    SpandauerWeihnachtsopenAOpen(),
    SpandauerWeihnachtsopenBOpen(),
    Spreewaldpokal(),
]


def registry_by_id():
    return {s.series_id: s for s in REGISTRY}


# ---- confirm core (testable: inject `reality` instead of a live fetch) ------

def confirm_command(series, year, records, *, reality=None):
    """One promote/insert command for a series+year, or None if no real edition was found.

    reality = an injected fetch() result (the backtest passes the captured-fixture dates); when
    None, a live series.fetch(year) runs. Returns {"op","id","fields"} — never writes.
    """
    found = reality if reality is not None else series.fetch(year)
    if not found:
        return None
    rid = f"{series.series_id}-{year}"
    fields = {"status": "confirmed", "start_date": found["start_date"], "end_date": found["end_date"]}
    if found.get("rounds"):
        fields["rounds"] = found["rounds"]
    existing = next((r for r in records if r["id"] == rid), None)
    op = "set" if existing is not None else "insert"
    return {"op": op, "id": rid, "fields": fields, "found": found}


# ---- CLI verbs --------------------------------------------------------------

def _horizon_years(base_year, series):
    return range(base_year, base_year + series.horizon_years + 1)


def cmd_predict(apply=False):
    records = load()
    existing = {r["id"] for r in records}
    today = date.today().isoformat()
    base = date.today().year
    new = []
    for s in REGISTRY:
        for year in _horizon_years(base, s):
            rid = f"{s.series_id}-{year}"
            if rid in existing:
                continue
            rec = s.predict(year, records)
            if rec and rec["start_date"] >= today:   # forecast the future, never backfill a past edition
                new.append(rec)
                existing.add(rid)
    print(f"# {len(new)} new predicted edition(s) across {len(REGISTRY)} series", file=sys.stderr)
    if apply and new:
        for rec in new:
            records.append(rec)
        add._write(add._sorted(records))
        load(add.DATA)
        print(f"inserted {', '.join(r['id'] for r in new)}; now {len(records)} records", file=sys.stderr)
    else:
        print(json.dumps(new, ensure_ascii=False, indent=2))
    return new


def _fmt_set(cmd):
    parts = [f"{k}={json.dumps(v, ensure_ascii=False)}" if not isinstance(v, str) else f"{k}={v}"
             for k, v in cmd["fields"].items()]
    verb = "set " + cmd["id"] if cmd["op"] == "set" else "insert"
    return f"python3 -m bcc.add {verb} " + " ".join(parts)


def cmd_check(records=None):
    """Backtest the confirm layer: for every CONFIRMED edition, does its series' fetch reproduce
    those dates from the source *now* (the 2-week cached snapshot; `--refetch` for a live pull)?

    Answer key = our confirmed records; source = the cached feed/WP/organizer. No historical
    archive needed, so it covers every series with a machine source, not just the wedge. A fetch
    that returns None is reported `manual`, not a failure (matches the `missing` path).
    """
    records = records if records is not None else load()
    reg = registry_by_id()
    rows = []
    for r in records:
        if r["status"] != "confirmed" or reg.get(r.get("series")) is None:
            continue
        s = reg[r["series"]]
        year = int(r["start_date"][:4])
        try:
            found = s.fetch(year)
        except Exception as e:
            rows.append((r["id"], "err", f"fetch failed: {e}"))
            continue
        if not found:
            rows.append((r["id"], "manual", "source has no machine-readable edition for this year"))
        elif (found["start_date"], found["end_date"]) == (r["start_date"], r["end_date"]):
            rows.append((r["id"], "match", f"{found['start_date']}..{found['end_date']}"))
        else:
            rows.append((r["id"], "mismatch",
                         f"source={found['start_date']}..{found['end_date']} data={r['start_date']}..{r['end_date']}"))
    tally = {k: sum(1 for _, t, _ in rows if t == k) for k in ("match", "mismatch", "manual", "err")}
    mark = {"match": "OK  ", "mismatch": "!!  ", "manual": ".   ", "err": "ERR "}
    print("CONFIRM CHECK — would each confirmed edition be reproduced from the feed now?\n"
          "(sources come from the 2-week cached snapshot; pass --refetch for a live pull)\n")
    for rid, t, detail in rows:
        print(f"  {mark[t]}{rid}: {detail}")
    print(f"\n{tally['match']} match, {tally['mismatch']} MISMATCH, "
          f"{tally['manual']} manual, {tally['err']} error  (of {len(rows)} confirmed w/ a subclass)")
    return rows


def cmd_confirm(apply=False, check=False):
    records = load()
    if check:
        return cmd_check(records)
    cmds = []
    base = date.today().year
    for s in REGISTRY:
        for year in _horizon_years(base, s):
            try:
                cmd = confirm_command(s, year, records)
            except Exception as e:
                print(f"# {s.series_id} {year}: fetch failed ({e})", file=sys.stderr)
                continue
            if cmd:
                cmds.append(cmd)
    for cmd in cmds:
        if cmd["op"] == "set":
            print(_fmt_set(cmd))
        else:
            print(f"# no expected record for {cmd['id']} — real edition found: {cmd['fields']}")
    if not cmds:
        print("# confirm: no machine-readable real editions found this run", file=sys.stderr)
    if apply:
        for cmd in cmds:
            if cmd["op"] == "set":
                rec = next(r for r in records if r["id"] == cmd["id"])
                rec.update(cmd["fields"])
        for r in records:
            validate(r)
        add._write(add._sorted(records))
        load(add.DATA)
        print(f"# applied {sum(1 for c in cmds if c['op']=='set')} promotion(s)", file=sys.stderr)
    return cmds


def cmd_missing():
    records = load()
    by_id = {r["id"]: r for r in records}
    known = registry_by_id()
    lines = []
    base = date.today().year
    for s in REGISTRY:
        # a series whose fetch is manual-only (feed default + not on the feed counts at run time)
        upcoming = [y for y in _horizon_years(base, s) if f"{s.series_id}-{y}" in by_id]
        if not upcoming:
            lines.append(f"- {s.series_id}: no upcoming edition in the {s.horizon_years}-year horizon")
        if s.n_rounds is None:
            lines.append(f"- {s.series_id}: round count (n_rounds) unknown")
        for y in _horizon_years(base, s):
            rec = by_id.get(f"{s.series_id}-{y}")
            if rec and rec.get("status") == "expected":
                gaps = [f for f in ("rounds", "prize_pool", "registration", "source_url")
                        if f not in rec]
                lines.append(f"- {rec['id']}: expected; still to fill: {', '.join(gaps) or '(complete)'}"
                             f"{'  [tagged_by:auto]' if rec.get('tagged_by') == 'auto' else ''}")
    print("MISSING / TO CONFIRM BY HAND\n" + ("\n".join(lines) if lines else "  (nothing outstanding)"))
    return lines


def cmd_suggest(apply=False):
    """Feed events matching NO known series -> candidates for a new subclass (the scout)."""
    from . import ingest
    known = {norm_name(s.name) for s in REGISTRY}
    events = fetch_all()
    groups = ingest.dedup(events)
    today = date.today().isoformat()
    unknown = [g for g in groups if norm_name(g["evs"][0].name) not in known]
    drafts = [ingest.draft(g, today) for g in unknown]
    print(f"# {len(groups)} feed groups -> {len(drafts)} match no known series (candidates for a subclass)",
          file=sys.stderr)
    print(json.dumps(drafts, ensure_ascii=False, indent=2))
    return drafts


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    apply = "--apply" in argv
    check = "--check" in argv
    if "--refetch" in argv:                 # bypass the cache: pull every source live
        set_refetch(True)
    argv = [a for a in argv if a not in ("--apply", "--check", "--refetch")]
    cmd = argv[0] if argv else ""
    if cmd == "predict":
        cmd_predict(apply)
    elif cmd == "confirm":
        cmd_confirm(apply, check)
    elif cmd == "check":
        cmd_check()
    elif cmd == "missing":
        cmd_missing()
    elif cmd == "suggest":
        cmd_suggest(apply)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
