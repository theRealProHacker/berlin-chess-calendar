# Berlin Chess Calendar — Engineering Plan

Status: architecture locked via /plan-eng-review (2026-06-25). Greenfield.
Companion docs: `RECON-data-sources.md` (the data-source recon this plan is built on),
and the approved /office-hours design doc (product thesis, scope, taxonomy).

Product in one line: a **predictive, calendar-native** Berlin chess tournament calendar —
shows tournaments *before* they're announced (annual recurrence) and ends in a subscribable
`.ics`. The wedge (confirmed by recon): the dominant competitor is a reactive skin over the
DSB feed; predicted lead-time + Berlin-filtered iCal is an open lane.

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
