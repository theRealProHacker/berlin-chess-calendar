# Berlin Chess Calendar — Manual Data-Source Recon ("The Assignment")

Date: 2026-06-25. No code. Looking at the real data first.
Goal: answer the 3 load-bearing questions that gate the automated-fetch architecture:
- Q1 Source completeness — does DSB Berlin RSS alone cover "everything OTB in Berlin"?
- Q2 Recurrence — do ~80% of tournaments repeat in roughly the same week year-over-year?
- Q3 Payload richness — structured fields, or title+link prose?

---

## SOURCE 1 — DSB Turnierdatenbank Berlin + RSS  [DONE]

Page: https://www.schachbund.de/turnierdatenbank-berlin.html
RSS:  https://www.schachbund.de/share/feed-turnierdatenbank-berlin.xml  (CONFIRMED, 200, valid RSS 2.0)
Also: a master DSB feed https://www.schachbund.de/share/dsb-feed.xml (all-Germany news, not Berlin tournaments)
Generator: Contao Open Source CMS. Per-region feeds exist for every Bundesland (same URL pattern).

### Feed shape (per <item>)
- `<title>`: `DD.MM.YYYY[–DD.MM.YYYY] <Name>`  ← dates + name, CLEANLY PARSEABLE (en-dash U+2013 separates start/end; single date = one-day event)
- `<description>`: CDATA HTML, free-form German prose. Richness varies 98–1543 chars.
- `<link>` / `<guid>`: detail page https://www.schachbund.de/turnierdetails/<slug>.html
- `<enclosure>`: PDF Ausschreibung (when present) — type=application/pdf, has length
- `<media:content>`: poster image (sometimes)
- channel `<pubDate>`: "Mon, 27 Apr 2026" (regenerates when items change)

### The ENTIRE current feed (8 items, 2026-06-25, covering Jun–Dec 2026):
1. 25.06.2026–28.06.2026  Berliner Tandemschach-Treffen           {desc 98 — bare link only}
2. 27.06.2026             22. Int. Berliner Tandemschach-Open      {desc 98 — bare link only}
3. 01.08.2026–09.08.2026  Kreuzberger Schachsommer–Werner-Ott-Open {desc 1543 +PDF — FULL: venue, time control 90min/40+15+30s = classical, 9-rd Swiss, Startgeld 90€, Preisfonds 1000/800/600/400/200/100€, DWZ+ELO}
4. 08.08.2026–16.08.2026  Lichtenberger Sommer 2026                {desc 255 +PDF — "Open, 9 Rd Schweizer, Preise 5150€"}
5. 23.12.2026             Spandauer Weihnachts-Rapid 2026          {desc 1008 +PDF}
6. 27.12.2026–30.12.2026  9. Spandauer Weihnachtsopen | A-Open     {desc 1240 +PDF}
7. 27.12.2026–30.12.2026  9. Spandauer Weihnachtsopen | B-Open     {desc 1219 +PDF}
8. 30.12.2026             Spandauer Weihnachts-Blitz 2026          {desc 1042 +PDF}

### VERDICT on DSB
- Q3 PAYLOAD: PARTIAL. Title = reliable structured date+name. Variant/time-control/age/prize
  are present but only as free German HTML prose in `<description>` of the RICH items, and
  ABSENT entirely on the thin items (#1,#2 are link-only). "Inherit categorized tags" premise
  from design doc is FALSE — the feed is NOT pre-categorized into enum fields. Auto-tagging =
  regex/NLP over prose at best, manual at worst.
- Q1 COMPLETENESS: DSB IS NOT COMPLETE. 8 events for the next 6 months, and it's really ~5
  distinct events (3 Spandau Christmas + 2 Tandem). MISSING from DSB entirely: Berliner
  Einzelmeisterschaft (BEM), the leagues BMM/BFL/Pokal-MM, ALL youth tournaments, U25 series,
  Teschner-Gedenkturnier, Harald-Lieb-Gedenkturnier, Lichtenrader Herbst, Offene
  Vereinsmeisterschaften. DSB = "what organizers voluntarily submitted to the national DB,"
  which skews to the big rated opens. Exhaustive Tier-1 goal CANNOT be met by DSB alone.

### Architecture implication (preliminary)
The design doc's Sequencing step 2 ("gate multi-source on the Assignment; if DSB alone is
~complete you avoid the hardest code") just resolved: **DSB alone is NOT complete → multi-source
is REQUIRED, not optional.** The BSV hub + league pages are mandatory, not enrichment. This
raises the floor of v1 complexity. Need to confirm how much BSV adds (Source 2).

---

## SOURCE 2 — Berliner Schachverband (BSV)  [DONE]

BSV is a Contao CMS, same engine as DSB. THREE distinct feeds + the page calendar + a links hub:

1. **BSV Terminkalender feed** (the real structured calendar feed) — THE BEST SPINE CANDIDATE
   https://www.berlinerschachverband.de/share/bsv-terminkalender.xml
   - 12 items (capped — Contao default feed length; near-term only). channel "BSV-Terminkalender".
   - IDENTICAL format to DSB feed: `<title>DD.MM.YYYY[–DD.MM.YYYY] [(HH:MM–HH:MM)] Name</title>`,
     `<description>` CDATA HTML (often empty/just a link), `<link>`/`<guid>` = /termin/<slug>.html
   - Cleanly parseable dates incl. optional time-of-day in parens.
2. **BSV news feed** — https://www.berlinerschachverband.de/share/feed.xml  (channel "BSV-Nachrichten")
   - 25 items, NEWS posts (announcements/results/reminders), NOT date-keyed events.
   - Useful as a *freshness signal* (new tournament announcements appear here first, e.g.
     "Kreuzberger Schachsommer 2026 - Das Tripel - Schach960, Elo-Rapid, Elo-Blitz",
     "Berliner Schnellschachmeisterschaften") but unstructured → would need NLP. Phase-2 at best.
3. **Page calendar** (termine.html) — full forward calendar in HTML, ~Jun2026→Jun2027.
   The feed is capped at 12; the PAGE carries the whole horizon. Scrape the page for full range.

### Completeness measured (termine.html full HTML, Jun2026→Jun2027)
~65 dated rows, of which ~50 are ONE recurring weekly course
"Anfänger-Schachkurs für Erwachsene" (every Monday). **~80% of the BSV calendar is noise.**
Real tournaments in the window (~13):
- Berliner Tandemschach-Treffen, SC Kreuzberg Monatsturnier (monthly club rapid), 22. Int.
  Tandemschach-Open, II. SPREE-Open Air, **10. Harald-Lieb-Gedenkturnier** (memorial, NOT in DSB),
  **Berliner Schnellschach-EM 2026** (championship, NOT in DSB), **Berliner Schnellschach-MM 2026**
  (NOT in DSB), 22. Lichtenberger Sommer, **7. Int. Emanuel-Lasker-Schachfestival** (NOT in DSB),
  Spreewaldpokal (Brandenburg/nearby), Spandauer Weihnachts-Rapid / -Open A/B / -Blitz.

### Still MISSING even from BSV termine (live on separate sources):
- **Leagues BMM / BFL / BPMM / Pokal** — on bmm.html + per-season detail pages, NOT in the calendar.
- **All youth** — on the SEPARATE site schachjugend-in-berlin.de (own calendar). BSV termine has a
  "Termine der Schachjugend" pointer, not the events.
- **Berliner Einzelmeisterschaft (main BEM)** — its own page; ran spring 2026, out of this window.
- **Teschner-Gedenkturnier, Lichtenrader Herbst** — not yet entered for the season / on club sites.

### BSV "Aktuelle Links" hub = the homepage + links.html
The homepage right column links straight to: BMM/BFL, Terminplan 25/26 + 26/27 PDFs, Berliner EM,
Senioren-EM, Teschner (sglasker.de), Harald-Lieb (skzehlendorf.de), youth (schachjugend), each
memorial/club detail page. CONFIRMED easily scrapeable (plain `<a href>`), as the design doc said.
It's a link INDEX (discover where each tournament's page lives), NOT a dated calendar.

### Dedup confirmed necessary (and easy)
DSB feed and BSV Terminkalender feed OVERLAP on the big opens (Tandem, Lichtenberger, Werner-Ott,
Spandau). Both carry normalized name + same dates, and often the SAME external link
(bughouse-berlin.de for Tandem). So match on (external_link) OR (norm_name + start_date ±3d) is
reliable. The design doc's rapidfuzz heuristic is more than enough; could even be simpler.

### REVISED completeness model (the real picture)
NO single source is complete. Full Berlin = UNION of:
| Source | Covers | Format | Effort |
|---|---|---|---|
| DSB Berlin feed | big rated opens (8) | clean RSS, rich desc on big ones | trivial |
| BSV Terminkalender feed+page | opens+champ+memorials+club (noisy) | clean RSS (12) + HTML page (full) | easy, needs noise filter |
| BSV news feed | announcements (freshness signal) | RSS, unstructured prose | Phase-2 |
| BMM/BFL league pages | team competitions | HTML tables | medium |
| schachjugend-in-berlin.de | youth | separate site/calendar | medium |
| club/memorial sites | per-event detail/registration | heterogeneous HTML | best-effort |
| Terminplan PDF | season skeleton + DSAM (Tier-2) | PDF | annual hand-entry / parse |

**The hard part is NOT dedup (it's easy here). The hard parts are: (1) NOISE FILTERING the BSV
calendar (80% is one course), and (2) the leagues + youth living on separate sources.**

---

## SOURCE 3 — BSV Terminplan PDF (season skeleton)  [DONE]

URL pattern: /files/Spielausschuss .../1 Kalender/T2627.pdf (26/27), T2526-1S.pdf (25/26), back to 21/22.
Youth has its OWN Terminplan PDF (Jugend 2025/26) linked from schachjugend /termine/.
- MACHINE-READABLE: 5847 chars real text (NOT a scan). pdftotext works.
- BUT it's a wide LANDSCAPE GRID (months × days, cells = league/event codes). `-layout` flattening
  is messy → auto-parsing is real work. Hand-entry once/season into recurring.yaml is the pragmatic
  call (design doc already accepts this).
- Encodes the SEASON SKELETON: BMM ×20, BEM ×17, BFL ×12, women's leagues FBL/FRL/2FBL ×10 each,
  BPMM (Pokal) ×8, DSAM ×11 (the Tier-2 series), plus EM/MM/Schnell/Blitz markers.
- MODELING FLAG: leagues are MULTI-ROUND season-long series (round 1..N across Sep–Apr), not single
  blocks. So is Harald-Lieb (Thursday evenings across months). The schema's single start/end + one
  schedule_format does NOT cleanly represent "a league with 7 round dates." Decide: one row per
  league-season (start=R1, end=last) vs one row per round (better for .ics, worse for the list view).

---

## SOURCE 4 — chess-results.com + chessmanager.com (per-event platforms)  [DONE]

- **chess-results.com**: ASP.NET, viewstate, MULTI-NODE (s1/s2/s3 — a postback hops you to a
  different server node and the search state resets). Form fields: P1_txt_bez (name), P1_txt_ort
  (location), date range, P1_cb_suchen (search), "Download Excel-File" button. In a real browser
  the search is drivable but FLAKY (clicks time out, node hops lose state). For an automated poller
  this is the WORST option. VERDICT: do NOT use as an automated discovery/ingest source. Fine as a
  manual per-tournament results/pairings link, and as a manual recurrence-archive lookup.
- **chessmanager.com**: returns 403 to the headless browser on /tournaments?search (anti-bot).
  VERDICT: best-effort per-event detail/registration link only; not an automated source.
- Net: both per-event platforms are CLOSED to clean automation. Per-event enrichment (deadline,
  entry link, prize) must come from the organizer/club page or the DSB/BSV `<description>`, not these.

---

## SOURCE 5 — Existing aggregators (the "competition" + cross-source benchmark)  [DONE]

- **schachtermine.de** → DEAD (domain parked / for-sale at domainmarkt.de).
- **schachtermine.com** → LIVE, WordPress 6.9.4. THE main Berlin-aware competitor. BUT it openly
  labels its data "Quelle: DSB-Turnierkalender" and its event links point straight back to
  schachbund.de/turnierdetails/... → it is a WordPress/SEO skin over the SAME DSB national feed I
  already ingest. Reactive, NO prediction, NO clean iCal subscribe. Lots of SEO taxonomy pages.
  Benchmark result: the best existing competitor offers NO more Berlin data than the DSB feed, and
  nothing predictive or calendar-native. → The design doc's wedge (predict + .ics subscribe) is OPEN.
- **chess-calendar.eu** → pan-European mega-list; DOES expose per-event calendar-export (.ics) links
  (admin_calender_export.php). So "nobody offers calendar-native delivery" is PARTLY FALSE — but it's
  not Berlin-filtered, not predictive, and the export is per-event not a subscribable Berlin feed.
- **schachinter.net** → old custom Turnierkalender, no generator, thin.

---

## SOURCE 6 — RECURRENCE TEST (the prediction premise / the MOAT)  [DONE]

Two independent lines of evidence, both strong:

1. **Edition numbers embedded in the names** (objective, parseable, prove N consecutive years):
   22. Berliner Tandemschach-Open, 22. Lichtenberger Sommer, 9. Spandauer Weihnachtsopen,
   10. Harald-Lieb-Gedenkturnier (= 24. SK Zehlendorf Open), 7. Emanuel-Lasker-Festival,
   42. Berliner Senioren-EM, 25. Offenes Schnellturnier, 2. SPREE-Open Air, 5. Thallichtenberger.
2. **Primary-source multi-year archive** (skzehlendorf.de): Harald-Lieb editions 6→10 across
   2022, 2023, 2024, 2025, 2026 — five consecutive years, dates documented.

Plus calendar-slot stability (German fixed-slot convention): Spandauer Weihnachts* = Christmas week
(27–30 Dec) by definition; Werner-Ott = first 9 days of August; Lichtenberger = mid-August; Tandem =
late June; Harald-Lieb = July onward. → Same-window recurrence holds.

VERDICT: recurrence premise CONFIRMED, comfortably ≥80%. The forecast feature is real and is the moat.
EUREKA (parse-the-name): the edition ordinal in the title ("22.") is itself a recurrence signal AND
lets you auto-increment next year's predicted edition number. Free prediction metadata from the name.

---

## SYNTHESIS — what this means for the automated fetch architecture

### The 3 gating questions, answered
- Q1 Completeness: **No single source is complete. Multi-source is REQUIRED** (not the optional
  step-2 the design doc hoped to skip). DSB feed alone = ~8 big opens; misses leagues (structurally —
  DSB DB is individual-tournaments only), youth (separate site), memorials, championships.
- Q2 Recurrence: **Confirmed ≥80%.** Forecast feature greenlit.
- Q3 Payload richness: **Semi-structured.** Title = reliable date+name. Variant/time-control/age/prize
  live as free German HTML prose (when present at all). Auto-tagging = regex/heuristics over prose +
  manual override, NOT field inheritance. The six filter axes must be largely hand-tagged at seed time.

### Recommended ingestion model (revised from the design doc)
Tiered by reliability, NOT "DSB-only first":
- **Spine = the TWO Contao RSS feeds**, merged + deduped (identical format, easy dedup on
  external-link or name+date±3d):
  - DSB Berlin feed: https://www.schachbund.de/share/feed-turnierdatenbank-berlin.xml
  - BSV Terminkalender feed: https://www.berlinerschachverband.de/share/bsv-terminkalender.xml
    (feed capped at 12 → also scrape the termine.html PAGE for the full forward horizon)
  - REQUIRES a noise classifier (drop the weekly "Anfänger-Schachkurs", flag club monthly-blitz).
- **Recurring/predictive backbone = recurring.yaml**, hand-seeded from the Terminplan PDF + the
  marquee named events; promotion = match an ingested sighting to a prediction (same easy heuristic).
- **Youth = WordPress REST** on schachjugend-in-berlin.de (/wp-json/wp/v2/posts?categories=38|24).
  Clean JSON. Phase-2-ish but low-risk.
- **Leagues = hand-entered** from the Terminplan PDF (multi-round; live results via liga.nu, not needed
  for a calendar). 
- **Per-event enrichment (deadline/entry/prize) = best-effort** from the feed `<description>` + PDF
  enclosure + organizer page. chess-results/chessmanager are bot-walled → manual only.
- **BSV news feed = optional freshness signal** (announcements land here first) → Phase 2.

### Risk re-rank (vs design doc)
- Design doc thought: dedup heuristic + prediction-promotion were the Med-risk parts.
- Recon says: **dedup is EASY** (two identical-format feeds, often shared external links). The actual
  Med-risk / most-work parts are: (a) the BSV **noise classifier** (80% of that calendar is one
  course), (b) **multi-round league/evening-event modeling** (schema doesn't represent it cleanly),
  (c) **prose→tag extraction** for the six axes. Prediction-promotion stays Med but the recurrence
  data is so clean (edition numbers!) it's de-risked.

### ANTI-ANTI-BOT (user steer, VALIDATED 2026-06-25)
Tested empirically with browse: setting a realistic desktop Chrome User-Agent
("Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/138 Safari/537.36") + Accept-Language
"de-DE,de;q=0.9" flipped chessmanager.com from **403 → 200**. So:
- The ingestion HTTP client (httpx/feedparser) MUST send a realistic UA + Accept-Language: de-DE on
  ALL requests by default. Cheap, and it reopens chessmanager for best-effort enrichment.
- chessmanager is reachable with a UA, BUT its `?search=` isn't a real server-side filter (returns a
  default list) → still not a clean queryable source; use only via known per-event URLs.
- chess-results stays hard for a DIFFERENT reason (ASP.NET viewstate + s1/s2/s3 node-hopping resets
  state) — a UA does NOT fix that. Verdict unchanged: manual-only.
- DSB + BSV Contao feeds were never bot-protected; UA is belt-and-suspenders there.
- Polite-crawler hygiene for the local tool: realistic UA, de-DE, small rate-limit/delay, respect
  robots where reasonable, cache ETag/Last-Modified. Headless stealth (mask navigator.webdriver) only
  if a future source needs a real browser; the feeds/WP-REST need only plain httpx.

### Scheduling reality (confirms design doc's MVP cut)
Data moves on the order of weeks (the DSB feed's channel pubDate was ~2 months old). A daily cron is
overkill; rebuild-on-push or a weekly poll is plenty. No live counts → no need for frequent polling.

### Seed dataset — CONFIRMED upcoming Berlin OTB events (real, from this recon, 2026-06-25)
| start | end | name | venue | variant/TC | source(s) |
|---|---|---|---|---|---|
| 2026-06-25 | 2026-06-28 | Berliner Tandemschach-Treffen | Berlin | tandem/duo | DSB+BSV (bughouse-berlin.de) |
| 2026-06-27 | 2026-06-27 | 22. Int. Berliner Tandemschach-Open | Berlin | tandem/duo | DSB+BSV |
| 2026-07-02 | (multi) | 10. Harald-Lieb-Gedenkturnier (24. SKZ Open) | Berlin-Zehlendorf | standard, Thu-evenings | BSV+skzehlendorf.de |
| 2026-07-04 | 2026-07-04 | Berliner Schnellschach-EM 2026 | Berlin | rapid | BSV |
| 2026-07-05 | 2026-07-05 | Berliner Schnellschach-MM 2026 | Berlin | rapid/team | BSV |
| 2026-08-01 | 2026-08-09 | Kreuzberger Schachsommer – Werner-Ott-Open | Berlin-Kreuzberg | classical (90+30/40,+15) | DSB+BSV+chessmanager; PDF Ausschr. |
| 2026-08-08 | 2026-08-16 | 22. Lichtenberger Sommer 2026 | Berlin-Karlshorst | classical, 9-rd Swiss, 5150€ | DSB+BSV+friesen-lichtenberg.de |
| 2026-08-29 | 2026-08-29 | 7. Int. Emanuel-Lasker-Schachfestival | Berlin | ? | BSV |
| 2026-09-19 | 2026-09-19 | Spreewaldpokal 2026 | Brandenburg (nearby) | ? | BSV |
| 2026-12-23 | 2026-12-23 | Spandauer Weihnachts-Rapid 2026 | Berlin-Spandau | rapid | DSB+BSV; PDF |
| 2026-12-27 | 2026-12-30 | 9. Spandauer Weihnachtsopen A-Open | Berlin-Spandau | classical | DSB+BSV; PDF |
| 2026-12-27 | 2026-12-30 | 9. Spandauer Weihnachtsopen B-Open | Berlin-Spandau | classical | DSB+BSV; PDF |
| 2026-12-30 | 2026-12-30 | Spandauer Weihnachts-Blitz 2026 | Berlin-Spandau | blitz | DSB+BSV; PDF |
Plus club monthly: SC Kreuzberg Monatsturnier Schnellschach (monthly), II. SPREE-Open Air (2026-06-27).
Tier-2 / leagues / youth: BMM/BFL/BPMM (Terminplan PDF, Sep–Apr), DSAM series (PDF), youth via WP REST.

