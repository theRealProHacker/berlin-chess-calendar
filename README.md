# ♞ Berliner Schachkalender

[![Deploy](https://github.com/theRealProHacker/berlin-chess-calendar/actions/workflows/deploy.yml/badge.svg)](https://github.com/theRealProHacker/berlin-chess-calendar/actions/workflows/deploy.yml)
[![Live site](https://img.shields.io/badge/live-therealprohacker.github.io-0071e3)](https://therealprohacker.github.io/berlin-chess-calendar/)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

A calendar of over-the-board chess tournaments in Berlin.

Most tournaments come back every year, so the calendar also lists next year's editions before
they're officially announced. Those show up as *expected* (marked `[erwartet]`) and switch to
*confirmed* with real dates once the announcement lands, so you can plan months ahead.

The site is German with an English toggle. You can filter the list, subscribe to the whole thing
as an `.ics` feed, or add a single tournament to Google, Outlook, or Apple Calendar. It's a static
site built from one JSON file by a few short Python scripts, with no dependencies and no server.

### [Open the calendar](https://therealprohacker.github.io/berlin-chess-calendar/) · [Subscribe (.ics)](https://therealprohacker.github.io/berlin-chess-calendar/calendar.ics)

![The calendar](docs/screenshot.png)

## Tournament details

Click a tournament to open its detail sheet. Each event can carry up to four links (Webseite,
Anmeldung, Chess Results, and the Ausschreibung PDF) plus a registration deadline. `Anmeldung` is
free text: a sign-up URL when there is one, or a note like „über den Verein" for events you can't
just register for online. The same links go into the `.ics` event. A tournament played over
several rounds (a league season, a weekly memorial) shows up as its individual round dates, not one
months-long block.

![Event detail modal](docs/screenshot-modal.png)

## How it works

```
data/tournaments.json → bcc.build → dist/index.html + dist/calendar.ics
```

`validate()` in [`bcc/build.py`](bcc/build.py) checks every record, and a bad one stops the build,
so the published files are always clean. On every push to `main`, GitHub Actions runs the tests,
rebuilds, and publishes `dist/` to GitHub Pages. `dist/` isn't committed; it's regenerated from the
data.

## Data model

A tournament is a plain `dict` in `data/tournaments.json`, with no ORM or schema library.
`validate()` is the only gate.

**Required:** `id` · `name` · `kind` · `start_date` · `end_date` · `variant` · `time_control` ·
`age_limit` · `participation` · `schedule_format` · `region` · `status` · `sources` ·
`last_verified`

**Optional:** `edition` · `rounds` · `rounds_count` · `series` · `organizer` · `venue` · `city` ·
`prize_pool` · `tagged_by` · `notes`, plus the link fields below. `rounds_count` is a plain round
count for block events that have no per-round dates (if `rounds` is also set, the two must agree).
When `rounds` is present the `.ics` emits those dates as calendar events instead of one span —
evenly-spaced rounds collapse to a single repeating event (an `RRULE`), uneven ones become one
event per round. `series` is a slug tying a record to its recurring-series class in `bcc.series`.

| field | meaning |
|------|---------|
| `source_url` | the event's webpage (Webseite) |
| `registration` | Anmeldung: a sign-up URL, or a phrase like `"über den Verein"` / `"über Qualifikation"` |
| `registration_deadline` | ISO date the Anmeldung closes |
| `chess_results_url` | Chess Results link (omit if none) |
| `ausschreibung_url` | the Ausschreibung PDF |

Controlled vocabularies (enforced; see `ENUMS` in `bcc/build.py`):

- `kind`: open · championship · memorial · league · club-series · youth · festival
- `variant`: standard · chess960 · tandem · other
- `time_control`: classical · rapid · blitz · mixed
- `participation`: single · duo · team
- `schedule_format`: block · weekly · biweekly · other
- `region`: berlin · nearby · national
- `status`: confirmed · expected · stale · cancelled
- `age_limit`: open · U8 · U10 · U12 · U14 · U16 · U18 · U20 · U25 · Ü60 · Ü65

## Keeping it current

Three scripts handle the curation:

```bash
python3 -m bcc.ingest > drafts.json     # pull the feeds, dedup, print new tournaments as JSON
python3 -m bcc.add insert drafts.json   # validate and insert them
python3 -m bcc.add set <id> status=confirmed start_date=YYYY-MM-DD end_date=YYYY-MM-DD
```

- `bcc.ingest` reads the Berlin feeds (DSB, BSV, the Schachjugend youth source, and the BSV
  „Aktuelle Links" hub), drops anything already in the file, and prints the rest as paste-ready
  JSON tagged `tagged_by: auto`. `--fixtures` runs offline against committed snapshots.
- `bcc.add` is the editor: `skeleton`, `insert`, `set`, `fmt`. Every write runs `validate()`, so a
  record that fails the schema never reaches the file.
- `bcc.series` handles recurring tournaments, one class per series. `predict` guesses next editions
  from past ones; `confirm` reads real dates from each series' source and proposes promotions for
  you to review; `check` re-checks that confirmed editions still match their source; `terminplan`
  reads the BMM rounds and the Schnellschach EM/MM out of the BSV season Terminplan PDF; `missing`
  and `suggest` flag gaps. Confirmation sources are chess-results.com search (the main one), the
  Schachjugend WordPress source for youth events, organizer RSS/JSON-LD/HTML, and the Ausschreibung
  or Terminplan PDF. Fetched sources are cached for two weeks under `.feedcache/`; `--refetch`
  forces a fresh pull.

Two Claude Code workflows sit on top of `bcc.add`: `add-tournament` (research and insert an
off-feed event, with a second agent checking it against its sources) and `check-announcements` (a
monthly pass over the expected records to promote, correct, or retire them).

## Running it

Standard library only, so there's nothing to install:

```bash
python3 -m unittest discover -s tests -t .   # tests
python3 -m bcc.build                         # build dist/index.html + dist/calendar.ics
python3 -m http.server -d dist               # preview at http://localhost:8000
```

`bcc.series confirm` uses `pdftotext` if it's installed, for PDF-only sources. Nothing else needs
it.

## Layout

```
data/tournaments.json   the data: validated dicts, hand-editable, the open dataset
bcc/build.py            schema + validation + static-site/.ics builder
bcc/feeds.py            source parsers (RSS / REST / JSON-LD / chess-results / Terminplan PDF)
bcc/ingest.py           discover feed events, dedup, draft records
bcc/series.py           recurring-series predict/confirm/check/missing/suggest/terminplan
bcc/add.py              deterministic insert/set/skeleton editor
site/template.html      the static site (HTML/CSS/vanilla JS, __DATA__ placeholder)
tests/                  unittest suite + real feed fixtures
dist/                   build output (git-ignored; built and deployed in CI)
PLAN.md                 open work / backlog
RECON-data-sources.md   data-source notes
```

## Sources

The data comes from the DSB Turnierdatenbank Berlin and Berliner Schachverband RSS feeds and the
Schachjugend Berlin source. chess-results' Berlin search is scraped for series confirmation; its
per-event pages and chessmanager block bots, so those links are added by hand (see
[`RECON-data-sources.md`](RECON-data-sources.md)). Everything in `data/` is an open dataset, free
to reuse.

## License

GPL-3.0. See [`LICENSE`](LICENSE).
