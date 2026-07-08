# Berlin Chess Calendar — Open Work

Forward-looking only. The architecture, schema, and the build/automation history that already
shipped have been pruned from this file — `README.md` and the code are the source of truth for how
the tool works today. What remains here is what's still open.

Companion doc: `RECON-data-sources.md` (the original data-source recon).

## Coverage backlog — tournaments still to add

Audited against `data/tournaments.json` on 2026-07-09. Add each through `bcc.add` with one verified
source — never a hand-paste — so the file keeps its canonical format and passes `validate()`.

- [ ] Lichtenrader Herbst — ≠ Lichtenberger (Tempelhof-Schöneberg district)
- [ ] Teschner-Gedenkturnier (Rudolf Teschner)
- [ ] Offene Vereinsmeisterschaften (club opens)
- [ ] Uckermünde (nearby)
- [ ] Wittstock (nearby)
- [ ] DSAM — Deutsche Amateurmeisterschaft, all regional editions (national)

## Feedback channel (parked 2026-06-30)

Let visitors send feedback. Direction decided: a plain **link** in the footer, *not* an iframe
embed — embedding pulls third-party scripts/cookies onto the page, which clashes with the
no-tracking, dependencies-none ethos and the GitHub Pages static model. Two link-only options
remain:

- (a) link out to a **Google Form** — owner creates it; structured responses land in a Sheet; or
- (b) a **`mailto:`** link — zero third-party, but no structured data and the address gets scraped
  for spam.

Rejected: Web3Forms / Formspree (need an on-page form, not a link), Netlify Forms (require Netlify
hosting; we're on GitHub Pages). **Blocked on:** owner to create the Google Form and hand over the
share URL, or pick mailto. **Also:** any channel that collects personal data should ship with a
minimal Impressum + Datenschutz line — the site has neither today.

## Ideas (not scheduled)

- **BSV news-feed NLP promotion** — a freshness signal that promotes expected→confirmed from BSV
  news posts, on top of the current per-series confirm sources.
