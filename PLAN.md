# Berlin Chess Calendar — Engineering Plan

Status: architecture locked via /plan-eng-review (2026-06-25). Greenfield.
Companion docs: `RECON-data-sources.md` (the data-source recon this plan is built on),
and the approved /office-hours design doc (product thesis, scope, taxonomy).

Product in one line: a **predictive, calendar-native** Berlin chess tournament calendar —
shows tournaments *before* they're announced (annual recurrence) and ends in a subscribable
`.ics`. The wedge (confirmed by recon): the dominant competitor is a reactive skin over the
DSB feed; predicted lead-time + Berlin-filtered iCal is an open lane.

> **Build status (2026-06-30) — read before the design below.** The tool is three stdlib
> modules: `bcc/build.py` (schema + `validate()` + static site + `.ics`), `bcc/ingest.py`
> (DSB/BSV RSS + Schachjugend youth REST + the BSV "Aktuelle Links" hub harvester + dedup/draft),
> and `bcc/add.py` (insert/set/skeleton editor). The "Data flow", "Modules", and "Build sequence"
> sections below are the **original design** and have drifted from the code: the separate
> `fetch/`, `normalize.py`, `dedup.py`, `noise.py`, `store.py`, `review/`, `emit/`, `serve.py`
> were **consolidated into those three modules**, and the **prediction engine
> (`recurring.yaml` + `predict.py` + promote-on-match) is NOT built** — the `status:expected`
> records are hand-entered, and promotion is the manual `bcc.add set` / `check-announcements`
> flow. The current automation-first plan is the **`/autoplan verdict`** section near the end.

---

## Locked decisions (this review)

1. **Local Python curation tool that emits static files** (NOT a hosted backend). FastAPI app
   you run on demand; output is git-tracked JSON/YAML consumed by a free static site. Zero ops,
   zero cost, open + diffable data. The server is a build tool, not a runtime dependency.
2. **Two-feed automated spine**: DSB Berlin RSS + BSV Terminkalender RSS, merged + deduped +
   noise-filtered. Leagues/Tier-2 hand-entered from the Terminplan PDF; youth (WordPress REST) is
   a fast follow. (Recon proved no single source is complete → multi-source is mandatory.)
3. **Anti-anti-bot by default**: every HTTP fetch sends a realistic desktop-Chrome User-Agent +
   `Accept-Language: de-DE`, polite rate-limit, ETag/Last-Modified caching. (Validated: this flips
   chessmanager 403→200.) chess-results stays manual-only (viewstate + node-hopping, not a UA issue).
4. **Multi-round series = ONE entry** with a `kind` field, season `start_date`/`end_date`, and an
   optional `rounds: [dates]` list. Dataset stays small; `.ics` can emit a season range or one
   VEVENT per round.
5. **Noise filter = human-in-the-loop + learned rejects**: ingest everything, pre-sort
   likely-tournament vs likely-noise, remember rejects so recurring noise (the weekly course)
   auto-hides after one decision.
6. **Predictions** from `recurring.yaml`: resolve next date as "same ISO-week slot as last confirmed
   edition," auto-increment the edition ordinal parsed from the name, 12–18 month horizon.
7. **Cancellation** handled in the review queue (confirmed→vanished→date passed ⇒ mark cancelled).
8. **Slug** = normalized-name + year; collisions get the section suffix (A-Open/B-Open) then `-2`.

---

## Data flow

```
                  LOCAL CURATION TOOL  (FastAPI · `python serve.py`)
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │  fetch/            normalize.py       dedup.py   noise.py     review/ (web UI) │
  │  ──────            ────────────       ────────   ────────     ──────────────── │
  │  dsb_rss   ─┐                                                  ┌─────────────┐ │
  │  bsv_rss   ─┼─► RawEvent ─► parse ─► group by ─► pre-sort ───► │ queue:      │ │
  │  bsv_page  ─┘   {title,    title→     ext-link    tournament/  │ tag 6 axes  │ │
  │  (httpx,         desc,     date(s),   OR name+    noise +      │ approve/    │ │
  │   UA+de-DE,      link}     name,      date±3d     learned      │ reject/edit │ │
  │   ETag cache)              edition,                rejects     └──────┬──────┘ │
  │                            best-effort fields                        │        │
  │  recurring.yaml ─► predict.py ─► expected events ──promote on match──┤        │
  │                                                                      ▼        │
  │                            store.py (pydantic) → tournaments.json + recurring.yaml + learned.json │
  └──────────────────────────────────────────────────────────────────────────────┘
                                       │  git commit + push
                                       ▼
                       emit/  →  index.html (filter 6 axes + date) + calendar.ics
                       STATIC · FREE (GitHub Pages / Vercel static)
```

---

## Schema (pydantic `Tournament`)

```jsonc
{
  "id": "werner-ott-open-2026",     // slug = norm-name + year; STABLE (it is the iCal UID)
  "name": "Kreuzberger Schachsommer – Werner-Ott-Open 2026",  // original German, not translated
  "name_en": null,                  // optional EN gloss; null ⇒ show `name`
  "kind": "open",                   // open|championship|memorial|league|club-series|youth|festival
  "edition": 22,                    // parsed from "22." title prefix; null if none; drives prediction++
  "start_date": "2026-08-01",       // ISO; for a series = first round
  "end_date": "2026-08-09",         // ISO; for a series = last round; defaults to start_date
  "rounds": null,                   // multi-round: ["2026-09-20","2026-10-11",...]; null = single block

  // six filter axes (all first-class)
  "variant": "standard",            // standard|chess960|tandem|other
  "time_control": "classical",      // classical|rapid|blitz|mixed
  "age_limit": ["open"],            // open|U8|U10|U12|U14|U18|U25|Ü60...
  "participation": "single",        // single|duo|team
  "organizer": "SC Kreuzberg e.V.",
  "schedule_format": "block",       // block|weekly|biweekly|other

  // placement & provenance
  "region": "berlin",               // berlin|nearby|national
  "venue": "Berlin-Kreuzberg",
  "city": "Berlin",
  "status": "confirmed",            // confirmed|expected|stale|cancelled
  "sources": ["dsb-berlin","bsv-termin"],
  "source_url": "https://www.schachbund.de/turnierdetails/...",   // Webseite
  "registration_deadline": null,    // best-effort
  "registration": null,             // Anmeldung: sign-up URL, or "über den Verein"/"über Qualifikation"
  "chess_results_url": null,        // blank if it doesn't exist
  "ausschreibung_url": null,        // the Ausschreibung PDF
  "prize_pool": { "amount": 3100, "currency": "EUR" },  // hand-entered; stable year-to-year

  // curation metadata
  "last_verified": "2026-06-25",
  "tagged_by": "human",             // human|auto — auto-guesses flagged for review
  "notes": null
}
```
Required: id, name, kind, start_date, end_date, variant, time_control, age_limit, participation,
schedule_format, region, status, sources, last_verified. Rest best-effort. Pydantic validates the
enums at write time, so a bad tag can never reach `tournaments.json`.

`recurring.yaml` (prediction templates) carries the same axes + `typical_slot` (e.g. "ISO week 32"
or "first weekend of July") + `confidence`. predict.py resolves slot→dates for the target year and
auto-increments `edition` from the last confirmed sighting.

---

## Modules

- `fetch/` — source adapters, each returns `list[RawEvent]`; shared httpx client (UA, de-DE,
  rate-limit, ETag cache). `dsb_rss.py`, `bsv_rss.py`, `bsv_page.py`, later `schachjugend_wp.py`.
- `normalize.py` — title → (start,end,name); strip ordinal → `edition`; regex desc prose for
  best-effort time_control / format / prize (flagged `tagged_by:auto`).
- `dedup.py` — group RawEvents into one Tournament by external-link OR norm_name+start±3d.
- `noise.py` — pre-sort tournament vs noise; load/append `learned-rejects.json`.
- `predict.py` — recurring.yaml → expected events (horizon); promote-on-match keeps the same id.
- `store.py` — pydantic models; load/save the three data files.
- `review/` — FastAPI routes + Jinja: queue, edit form, approve/reject/cancel.
- `emit/build_site.py` + `emit/build_ics.py` — static output (all-day VALUE=DATE, DTEND=end+1,
  UID=id; series → season range or per-round).
- `serve.py` — entrypoint.

---

## Edge cases (handle, don't skip — boil the ocean)

- Title date formats: single `DD.MM.YYYY`, range `DD.MM.YYYY–DD.MM.YYYY` (en-dash U+2013),
  with time-in-parens `(19:30–23:00)`, cross-month/year ranges.
- Dedup: same tournament A/B sections (Spandau A-Open vs B-Open) must NOT merge; same event across
  both feeds (shared bughouse-berlin.de link) MUST merge.
- Noise: the weekly "Anfänger-Schachkurs" (≈50 rows) and monthly club blitz vs a real tournament.
- Prediction with no prior edition; predicted event that never confirms (→ stale at +2 weeks);
  predicted vs an actual sighting on a shifted date (±3d window).
- Cancellation: confirmed event vanishes from all feeds after being seen.
- Multi-round: league with 7 rounds; evening series (Harald-Lieb) across months.
- iCal: exclusive DTEND quirk; UID stability across re-runs and expected→confirmed promotion;
  drop events ended >30 days; exclude stale predictions.

---

## Test strategy (non-negotiable; real-data fixtures)

- **Golden fixtures = today's real payloads**: the captured DSB 8-item feed, BSV 12-item feed, BSV
  page HTML, one Werner-Ott rich `<description>`. Parser/dedup/noise tested against real Berlin data.
- **Unit**: title/date parser (every format above), edition parser, dedup (merge + must-not-merge
  cases), noise classifier (course vs tournament), predict slot resolver + edition++ , ics builder
  (all-day, DTEND+1, UID, multi-round).
- **Integration**: fetch(fixtures) → normalize → dedup → approve → emit → assert tournaments.json +
  calendar.ics.
- **Edge**: empty feed, all-noise feed, no-prior-edition prediction, cancelled event, A/B slug
  collision.

---

## Build sequence (incremental — make the change easy, then make the easy change)

- **Phase 0 — shippable today, zero ingestion.** pydantic schema + the recon seed table hand-entered
  as `tournaments.json` + static `index.html` (filter 6 axes) + `build_ics`. A real, shareable link
  with the predictive *display* — honors the ponytail instinct as the first commit.
- **Phase 1 — ingestion spine.** dsb_rss + bsv_rss adapters + normalize + dedup → CLI review list
  (no UI yet). Golden-fixture tests land here.
- **Phase 2 — review/edit web UI.** FastAPI + Jinja queue/edit/approve. This is the "easy ingestion
  and editing" you asked for.
- **Phase 3 — predictions.** recurring.yaml + resolver + promote-on-match.
- **Phase 4 — coverage + polish.** learned noise rejects, bsv_page full-horizon scrape, youth WP REST,
  best-effort enrichment (deadline/registration via UA-enabled fetch).

Performance: trivial scale (hundreds of events/year); static client-side filter over a small JSON is
instant. The only perf discipline is politeness to sources (rate-limit + ETag cache).

---

## Coverage backlog — requested tournaments vs. data

Owner brief (2026-06-30): the calendar should carry the events below. Audited against the
18-record dataset on 2026-06-30. The six filter axes (variant · time control · age · single/duo/
team · organizer · block/weekly/biweekly) **plus** region are all implemented and correct — this
is a data-coverage gap, not a code gap. Add missing events through `bcc.add` (one verified source
each), never a hand-paste, so the file keeps its canonical format and passes `validate()`.

**In the calendar**
- [x] Kreuzberger Sommer (Werner-Ott-Open)
- [x] Lichtenberger Sommer (2026 + 2027 predicted)
- [x] Weihnachtsturniere — Spandau Rapid / Blitz / A-Open / B-Open
- [x] Harald-Lieb-Gedenkturnier (2026 + 2027 predicted)
- [x] Grenke Chess Open (predicted)

**Partial — extend**
- [ ] All Berlin championships — have Schnellschach-EM + -MM (rapid). Missing: Berliner
  Einzelmeisterschaft (classical), Blitzmeisterschaft, Blitz-MM.
- [ ] All youth tournaments — only Jugend-Elo-Rapid U20 landed though the schachjugend WP source
  runs. Add BJEM (U8–U18), Berliner Jugend-MM; investigate why so few youth events ingest.

**Missing — add (one verified source each)**
- [ ] U25 Turnier — https://u25chess.com/de/
- [ ] Schachfreunde Berlin (club open)
- [ ] Lichtenrader Herbst — ≠ Lichtenberger (Tempelhof-Schöneberg district)
- [ ] Teschner-Gedenkturnier (Rudolf Teschner)
- [ ] Offene Vereinsmeisterschaften (club opens)
- [ ] Uckermünde (nearby)
- [ ] Wittstock (nearby)
- [ ] DSAM — Deutsche Amateurmeisterschaft, all regional editions (national)

**Highest-leverage source gap.** The owner-named source — BSV → Aktuelles → "Aktuelle Links" — is
NOT harvested. `ingest` pulls BSV Terminkalender RSS + DSB Berlin RSS + schachjugend WP only. That
curated links page is where Lichtenrader Herbst, Teschner, U25 and the club championships most
likely live — mine it (or hand-add from it) before chasing events one at a time.

---

## /autoplan verdict (2026-06-30) — automation-first re-plan

Reviewed via /autoplan (CEO + Eng phases; Codex unavailable — both voices Claude, cross-checked).
The owner brief above framed the gap as "hand-add ~8 events." **Owner direction at the gate
overrode that:** maximize automation, build a system that runs yearly on its own, assert
predictions as soon as events are announced, enter as little by hand as possible. Re-planned on
that axis. The two reviewers' "stay manual, zero-ops" stance is explicitly overridden by owner
context (minimize recurring manual labor > minimize code).

### CORRECTION (2026-06-30, verified live)
An earlier pass (and RECON line 96) called the BSV "Aktuelle Links" hub a *dead pointer-index, not
events*. **That was wrong** — verified live with `bcc.ingest.http_get`: the hub is a curated
`<div class="ce_text block">` of ~14 named tournament anchors → per-event pages that carry parseable
dates (Berliner EM page = `01.07.2026`; U25 page = prose `7. Juni 2026`). It contains exactly the
no-feed gaps: **Berliner EM (classical), Teschner-Gedenkturnier, Pokal-MM, Senioren-EM, Off.
Spandauer 960-MM, Norddt. Blitz-MM, BMM/BFL**. So the hub is a real, reliable *discovery* source and
is now the primary new auto-loader, integrated into `ingest.py`. (Date extraction is best-effort
per page; ambiguous ones drop into the existing review queue, not hand-entry.)

### Source reliability tiers (the "if reliably possible" gate)

| Coverage gap | Best machine source | Reliable auto-load? | How |
|---|---|---|---|
| The season's key Berlin events incl. the no-feed ones (Teschner, Berliner EM classical, Pokal-MM, Senioren-EM, 960-MM, Norddt. Blitz-MM, BMM/BFL) | **BSV homepage "Aktuelle Links" block** | yes — discovery reliable, date best-effort | harvest the block's anchors (name + URL) → fetch each page → date via existing `parse_de_date` / DD.MM.YYYY; ambiguous date → review queue. Always captures `source_url` + Ausschreibung PDF. |
| More Berlin opens / memorials | BSV `termine.html` full page | yes — low effort | same `parse_title` as the feed; uncaps the 12-item RSS. Reuse parser + `rejected.json` noise filter. |
| Youth opens (RKST, Abrafaxe, Kinder-Sommer, Elo-Rapid/Blitz) | schachjugend WP `content.rendered` | yes — ~1 line | the parser already fetches the post body and discards it; feed `content.rendered` into `parse_de_date`. NB: U25 + Schachfreunde Berlin are already in the youth feed (dedup). |
| Predict next year + confirm on announcement | `recurring.yaml` + `predict.py` + promote-on-match | build it | the predictive wedge; not built yet (the 3 `status:expected` rows are hand-typed today). |
| Leagues round-by-round detail; DSAM | BMM/BFL pages (linked from the hub); DSAM via hub/news | medium | hub already yields the page URLs — more parseable than the fragile Terminplan PDF grid. |

### Keystone the backlog missed: there is no prediction engine
`recurring.yaml` and `predict.py` **do not exist** — the 3 `status:expected` 2027 rows are hand-typed.
"Predictions asserted as soon as announced" *requires* building **predict.py + promote-on-match**.
Still the wedge keystone — but now *complementary* to the hub harvester, not the workaround for
missing sources.

### Build sequence (revised, automation-first)
1. **BSV "Aktuelle Links" hub harvester in `ingest.py`** — extract the block's tournament anchors →
   fetch each event page → best-effort date → existing dedup/draft pipeline. Auto-discovers
   Teschner, Berliner EM (classical), Pokal-MM, Senioren-EM, 960-MM, Norddt. Blitz-MM, BMM/BFL.
2. **BSV `termine.html` full-page scrape** — uncaps the feed horizon (reuses `parse_title`).
3. **Youth body parse** — feed `content.rendered` into `parse_de_date` + golden fixtures.
4. **Prediction engine** — `recurring.yaml` + `predict.py` (slot→dates, edition++) +
   **promote-on-match** (expected→confirmed on a feed sighting, same id).
5. **Default region filter → Berlin** (`site/template.html`) — stop leaking Grenke (Karlsruhe),
   Lasker (Polen), Spreewaldpokal (Brandenburg) into the default list + `.ics`.

### Decision audit trail
| # | Decision | Class | Principle | Rationale |
|---|---|---|---|---|
| 1 | Build the BSV "Aktuelle Links" hub harvester in `ingest.py` | owner-corrected (reverses earlier "don't build") | P1/P2 | VERIFIED the hub anchors are named events linking to dated pages; covers the no-feed gaps; owner directed pulling it into `ingest.py` |
| 2 | DSAM / leagues / championships via the hub's per-event pages, not the PDF | mechanical | P3/P5 | hub links to BMM/BFL/Berliner EM pages (parseable) — better than the fragile Terminplan grid |
| 3 | Build `predict.py` + promote-on-match (engine in scope) | owner directive | P1/P2 | the predictive wedge; doesn't exist today |
| 4 | BSV `termine.html` scrape | mechanical (both voices) | P2 | reuses existing parser; lifts the full forward horizon the capped feed hides |
| 5 | Youth `content.rendered` fix | mechanical (both voices) | P1/P2 | body already fetched + discarded; highest coverage-per-byte |
| 6 | Default region filter → Berlin | owner-selected | P1 | verified leak of non-Berlin events into default list + .ics |
| 7 | "Number of rounds" stays parked | owner directive | P4 | derive from `rounds` + wire the dead `runden` label; do NOT add a `round_count` scalar (DRY) |

### Out of scope (separate plans)
- Terminplan PDF auto-parser — fragile landscape grid; the hub's per-event pages replace it.
- BSV news-feed NLP promotion — Phase 2 freshness signal.

---

## Backlog

- **Number of rounds** (from QA, 2026-06-30). Note/surface a tournament's round count.
  The schema already carries `rounds: [dates]` (count is derivable), but it is not captured
  or shown for most records. Decide where it belongs: a data field to fill during curation,
  the list/`index.html` display, and/or the `.ics` description. Parked — note only for now.
  - Canonical example (owner Ausschreibung, 2026-06-30): **10. Harald-Lieb-Gedenkturnier
    (24. SKZ-Open)** is a 7-round Thursday-evening series, **21.05.–02.07.2026** (rounds 21.05,
    28.05, 04.06, 11.06, 18.06, 25.06, 02.07; 19:00; Hans-Rosenthal-Haus, Berlin-Zehlendorf).
  - **Data bug to fix with this:** `harald-lieb-gedenkturnier-2026` currently has start
    `2026-07-02` (the last round only) and end `2026-09-11` (wrong) and `rounds: null` →
    correct to start `2026-05-21`, end `2026-07-02`, and fill `rounds` with the 7 dates.
- **Feedback channel** (parked 2026-06-30). Let visitors send feedback. Direction decided: a plain
  **link** in the footer, *not* an iframe embed — embedding pulls third-party scripts/cookies onto the
  page, which clashes with the no-tracking, dependencies-none ethos and the GitHub Pages static model.
  Two link-only options remain: (a) link out to a **Google Form** (owner creates it; structured
  responses land in a Sheet; respondents go to a google.com page); or (b) a **`mailto:`** link (zero
  third-party, but no structured data and the address gets scraped for spam). Rejected: Web3Forms /
  Formspree (need an on-page form, not a link), and Netlify Forms (require Netlify hosting; we're on
  GitHub Pages). **Blocked on**: owner to create the Google Form and hand over the share URL, or pick
  mailto. **Also**: shipping any channel that collects personal data should come with a minimal
  Impressum + Datenschutz line — the site has neither today.
