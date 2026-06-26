# Berliner Schachkalender

A **predictive, calendar-native** chess-tournament calendar for Berlin. It lists every
OTB tournament in Berlin — and, because tournaments recur annually, shows them as
*expected* before they are officially announced, then promotes them to *confirmed* when
the announcement lands. Filter across six axes, then subscribe to a calendar feed (`.ics`)
that contains exactly your filtered selection.

German-first, English toggle. Open data, open source, free static hosting.

## Status: Phase 0 (shippable foundation)

- ✅ Schema with validation (`bcc/models.py`) — six filter axes + `kind` + multi-round `rounds`.
- ✅ Seed data (`data/tournaments.json`) — real Berlin tournaments from the source recon.
- ✅ Static site (`site/template.html` → `dist/index.html`) — Apple-style, German-first,
  data-driven, working faceted filter (modal / mobile bottom-sheet), DE/EN toggle.
- ✅ iCalendar feed (`bcc/build_ics.py` → `dist/calendar.ics`) — all-day events, stable UIDs,
  filter-aware `select()`.
- ✅ Tests (`tests/`, 32 cases, stdlib `unittest`).

See `PLAN.md` for the full roadmap and `RECON-data-sources.md` for the data-source analysis
this is built on.

## Quickstart

Phase 0 needs only Python 3.10+ (standard library only — no install).

```bash
python -m bcc.build                       # -> dist/index.html + dist/calendar.ics
python -m unittest discover -s tests -t .  # run the tests
```

Open `dist/index.html` in a browser, or serve it:

```bash
python -m http.server -d dist 8000        # http://localhost:8000
```

## Layout

```
data/tournaments.json   canonical published data (validated against the schema)
bcc/models.py           the schema + validation (single source of truth)
bcc/build_site.py       inline data into the site template -> dist/index.html
bcc/build_ics.py        generate the .ics feed (+ select() for filtered feeds)
bcc/build.py            one-shot: python -m bcc.build
site/template.html      the static site (HTML/CSS/vanilla JS, __DATA__ placeholder)
tests/                  unittest suite (schema + ics)
dist/                   build output (git-ignored; built in CI)
```

## How it will stay up to date (next phases)

The hard part is freshness. Per `PLAN.md`, a local Python (FastAPI) **curation tool**
fetches the two Berlin chess RSS feeds (DSB + BSV), dedups and filters noise, and gives a
review/edit UI to tag and approve tournaments. It writes back to `data/` (git-tracked,
diffable), and this build turns that into the static site + `.ics`. No hosted backend, no
database, no ongoing cost.

## License

Open source. Tournament data is an open dataset others can build on.
