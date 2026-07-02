---
name: add-tournament
description: Add a chess tournament to data/tournaments.json by name. Researches the event on the open web (official site, Wikipedia, chess-results), builds a schema-valid record, inserts it in sorted order, and has a fresh independent agent verify the new record against its sources. Use when the user says "add <tournament>", "add the <name> open", or wants an off-feed/national event in the calendar that bcc.ingest (Berlin RSS) won't catch.
---

# Add a tournament

The complement to `bcc.ingest` (which harvests the two Berlin RSS feeds). This handles
**off-feed** events — national/nearby, or anything the feeds miss — via open-web research
plus the same enum-tagging judgment a human curator would apply. The intelligence is yours;
the deterministic data edit goes through `bcc/add.py`, which never writes an invalid record.

**Never push.** Output is a working-tree diff the human reviews and commits. Do not `git
commit`/`git push` on your own.

## Step 1 — Scope resolution
- Determine the **target year/edition** and **region tier** (`berlin` | `nearby` | `national`).
  If the named event's current-year edition is already past, target the next edition.
- **Fuzzy-dedup check before anything else:** normalize the name and compare against existing
  records so you don't add a near-duplicate (e.g. "Grenke Open" vs "GRENKE Chess Open"):
  ```bash
  python3 -c "from bcc.ingest import norm_name; from bcc.build import load; \
  n=norm_name('<the name>'); \
  print([r['id'] for r in load() if norm_name(r['name'])==n])"
  ```
  If it prints an existing id, stop and tell the user — it's likely already there (maybe a
  different year). Don't insert a duplicate.
- **Ask the user** on genuine ambiguity: multi-event festivals (e.g. Grenke's two parallel
  opens — main + Freestyle/Chess960), which year, or which region tier. Don't guess on a fork
  that changes the data. For a multi-event festival, run steps 3-4 once per sub-event.

## Step 2 — Research (via `/browse`)
Order: **official site → Wikipedia (recurrence/history) → chess-results**. Capture: start/end
dates, venue, city, organizer, variant, time_control, rounds/format, prize_pool, and a
`source_url`.
- **What is a `venue` (vs `city` vs district):** a `venue` is the specific playing **site** — a
  named building/hall you could type into a maps app and reach the front door, ideally with its
  street (e.g. `"Andreas-Gymnasium, Koppenstraße 76"`, `"Karlsruhe Congress Center"`,
  `"Hans-Rosenthal-Haus"`). A city **district/borough is NOT a venue** — `"Berlin-Kreuzberg"`,
  `"-Karlshorst"`, `"-Spandau"`, `"-Zehlendorf"` are localities, not sites. `city` is the city
  (`"Berlin"`). If you only know the district, **leave `venue` unset** (mention the district in
  `notes` if useful); never put a bare district in `venue`. Once the building is known, the
  district may ride along inside the venue string (`"Andreas-Gymnasium, Berlin-Friedrichshain"`).
  Test: if you can't navigate to a front door with it, it isn't a venue.
- **`name` language:** record the event's name in the language of its **own official page** — do
  not translate (`"Lichtenberger Sommer"` stays German; an English-run event stays English).
  There is a single `name` field (no separate English/German name).
- **Known trap:** DuckDuckGo/Bing/Google **search-result pages** bot-challenge the headless
  browser. Go **direct** to official / Wikipedia / chess-results URLs — those fetch fine.
- For an **unannounced future edition**, derive the predicted window from the recurrence
  pattern (e.g. Grenke tracks the Easter weekend), set `status:"expected"`,
  `sources:["recurring"]`, and write a `notes` line stating the prediction basis (which past
  editions, which slot).

## Step 3 — Draft
```bash
python -m bcc.add skeleton "<name>" <year>      # prints a fill-in record + the enum vocab (stderr)
```
Fill the blanks from your research using **only** values from the printed `ENUMS`/`AGE`. **Name
format:** an edition-numbered series takes its ordinal and **no year** (`"22. Lichtenberger
Sommer"`); a series identified by year takes the year and **no ordinal** (`"Grenke Chess Open
2026"`). Keep `tagged_by:"auto"` (the skeleton sets it — this record is machine-researched). Set
`status:"confirmed"` **only** with real announced dates; otherwise `"expected"`. Save the
finished record to a temp file (e.g. `/tmp/draft.json`).

## Step 4 — Insert + gate
```bash
python -m bcc.add insert /tmp/draft.json        # validates, rejects dup id, sorted insert
python -m bcc.build                             # re-validates everything + builds dist/
python -m unittest discover -s tests -t .       # must stay green
```
`insert` refuses an invalid or duplicate record and changes nothing on failure. The seed test
is a floor (`>= 15`), so a legitimate add does **not** require editing the test.

## Step 5 — Reviewer pass (fresh independent sub-agent)
Spawn an independent agent (Task tool) with **only** the new record + the schema. Critically,
**it finds its OWN sources** — it must not just re-read the URLs you cited, or it will
re-confirm any bad source choice or misread date. Tell it to:
- independently search for the event and confirm dates/venue/organizer against sources **it**
  located;
- check every enum is valid, the `id` is the correct slug, region/variant/time_control/
  participation match reality, and `status` is justified by evidence;
- confirm the cited `source_url` resolves and `notes` is accurate.
It returns **PASS** or a correction list. This catches transcription/enum slips and
cross-checks the facts with independent evidence — it is not a guarantee against a wrong
source, so weigh its findings.

Apply any corrections via `bcc.add set` and re-run the gate (step 4):
```bash
python -m bcc.add set <id> <field>=<value> ...  # JSON-valued for lists/objects, e.g.
# python -m bcc.add set <id> prize_pool='{"amount":5150,"currency":"EUR"}'
```

## Step 6 — Report, don't push
Show the user: the final record, the `git diff`, the test results, and the reviewer verdict.
Offer to open a PR / commit — **never auto-commit or push**.
