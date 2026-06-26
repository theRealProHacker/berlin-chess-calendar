# Berliner Schachkalender

A **predictive, calendar-native** chess-tournament calendar for Berlin. Lists every OTB
tournament in Berlin and — because tournaments recur annually — shows them as *expected*
before they are officially announced, then promotes them to *confirmed* when the
announcement lands. Filter across six axes; subscribe to an `.ics` feed.

German-first, English toggle. Open data, open source, free static hosting. **Stdlib only —
no dependencies, no virtualenv, no server.**

## Use it

```bash
python3 -m bcc.build               # validate data -> dist/index.html + dist/calendar.ics
python3 -m unittest discover -s tests -t .   # tests
python3 -m http.server -d dist     # preview at http://localhost:8000
```

## Keeping it current (the curation loop)

```bash
python3 -m bcc.ingest > drafts.json    # fetch the 2 feeds, dedup, emit paste-ready records
```

`ingest` prints the NEW tournaments (auto-tagged, `tagged_by: auto`) as JSON. Paste the ones
you want into `data/tournaments.json`, fix the tags an editor is faster at than a guesser,
then `python3 -m bcc.build`. The build validates every record, so a bad tag never ships.
`--fixtures` runs offline against the committed feed snapshots.

## Layout

```
data/tournaments.json   the data (validated dicts)
bcc/build.py            schema + validation + static site + .ics   (python3 -m bcc.build)
bcc/ingest.py           fetch 2 RSS feeds + dedup + draft records  (python3 -m bcc.ingest)
site/template.html      the static site (HTML/CSS/vanilla JS, __DATA__ placeholder)
tests/                  unittest suite + real feed fixtures
dist/                   build output (git-ignored; built in CI)
```

Two modules, ~380 lines, standard library only. Sources: DSB Turnierdatenbank Berlin +
Berliner Schachverband RSS feeds. chess-results / chessmanager are bot-walled, so per-event
links are manual (see `RECON-data-sources.md`). The data files are an open dataset.
