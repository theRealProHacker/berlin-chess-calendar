"""Discover Berlin chess tournaments and print draft records for review (the `suggest` scout).

    python -m bcc.ingest             # live feeds
    python -m bcc.ingest --fixtures  # committed fixtures (offline)

Three Berlin sources, all funneling into one dedup/draft pipeline:
  - DSB Berlin + BSV RSS (identical Contao RSS, one parser serves both)
  - schachjugend-in-berlin.de youth tournaments (WordPress REST; event dates are German
    prose, not a structured field, so they are regex-parsed from the title, excerpt, and body)
Anti-anti-bot UA + de-DE on every request. Output is a JSON array of NEW candidate records
(auto-guessed, tagged_by=auto). Review them, fix the auto-guessed tags, then insert the ones
you want via bcc.add so the file keeps its canonical format (NOT a hand-paste, which drifts
the formatting):

    python -m bcc.ingest --fixtures > drafts.json      # then prune/fix drafts.json
    python -m bcc.add insert drafts.json               # validates + inserts the batch

`bcc.add insert` runs validate() on every record and refuses the whole batch on any
failure, so a bad tag can never reach data/tournaments.json. That review + insert +
validate loop is the whole Berlin curation flow — no server, no database.

The feed parsers live in bcc.feeds (shared with bcc.series' fetch adapters). Only the
prose axis-guessing draft() stays here: the Series model replaces guessed axes with fixed
class attributes, so draft() belongs with this discovery/suggest scout, not with the
shared parsers.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone

from .build import ROOT, load, slugify
from .feeds import (
    Raw, SOURCES, BSV_HOME, http_get, parse_title, parse_edition, norm_name,
    pick_enclosure, parse_feed, fetch_all, parse_de_date, extract_ages,
    clean_youth_name, parse_youth, fetch_youth, strip_html, extract_hub_links,
    hub_event_date, jsonld_event_dates, _meta_description, fetch_hub, dedup,
)

_EURO = re.compile(r"(\d[\d.\s]*)\s*(?:€|Euro|EUR)")
# A Berlin-scene event can be hosted abroad (e.g. the Lasker festival in Barlinek/Polen). When the
# title/description names a foreign host country in parentheses ("... (Polen)"), draft() drops any
# Berlin city label and flags it for review — parenthesized so it won't fire on "Teilnehmer aus Polen".
_ABROAD = re.compile(
    r"\((?:Polen|Polska|Poland|Tschechien|Österreich|Austria|Dänemark|Niederlande|"
    r"Frankreich|Schweiz|Belgien|Luxemburg|Schweden|Norwegen|Italien|Spanien|"
    r"Großbritannien|Vereinigtes Königreich)\)", re.I)


def draft(group, today):
    """Build a paste-ready draft dict from a merged group (auto-guessed, tagged_by=auto)."""
    evs = group["evs"]
    first = min(evs, key=lambda e: e.start)
    name = first.name
    n = name.lower()
    blob = " ".join(e.desc for e in evs).lower()
    abroad = bool(_ABROAD.search(name)) or bool(_ABROAD.search(blob))
    variant = "tandem" if ("tandem" in n or "bughouse" in n) else ("chess960" if "960" in n or "freestyle" in n else "standard")
    kind = ("memorial" if "gedenk" in n else "league" if any(k in n for k in ("liga", "bmm", "bfl", "mannschaftsmeisterschaft"))
            else "festival" if "festival" in n else "championship" if ("meisterschaft" in n or "-em" in n or "-mm" in n) else "open")
    part = "duo" if variant == "tandem" else ("team" if any(k in n for k in ("mannschaft", "liga", "bmm", "bfl", "team", "pokal")) else "single")
    tc = ("blitz" if "blitz" in blob or "blitz" in n
          else "rapid" if "schnell" in blob or "schnell" in n or "rapid" in blob or "rapid" in n
          else "classical")
    prizes = [int(re.sub(r"[.\s]", "", x)) for x in _EURO.findall(blob) if re.sub(r"[.\s]", "", x).isdigit()]
    d = {
        "id": slugify(name, first.start[:4]), "name": name, "kind": kind,
        "start_date": first.start, "end_date": max(e.end for e in evs),
        "variant": variant, "time_control": tc, "age_limit": ["open"],
        "participation": part, "schedule_format": "biweekly" if kind == "league" else "block",
        "region": "berlin", "status": "confirmed",
        "sources": sorted({e.source for e in evs}), "last_verified": today, "tagged_by": "auto",
    }
    if any(e.source.startswith("sjib") for e in evs):     # youth source overrides
        d["kind"] = "youth"
        d["age_limit"] = extract_ages(name) or ["open"]
        d["city"] = "Berlin"
        if not any(e.source == "sjib" for e in evs):      # externe-only -> region uncertain
            d["notes"] = "externe Jugend-Turnier — Region/Stadt prüfen"
    if first.edition:
        d["edition"] = first.edition
    link = next((e.link for e in evs if e.link), None)
    if link:
        d["source_url"] = link
    enc = next((e.enclosure for e in evs if e.enclosure), None)
    if enc:
        d["ausschreibung_url"] = enc
    if prizes:
        d["prize_pool"] = {"amount": max(prizes), "currency": "EUR"}
    if abroad:                        # held abroad -> never carry a Berlin city label; flag for review
        d.pop("city", None)
        d.setdefault("notes", "im Ausland ausgetragen — Region/Stadt prüfen")
    return d


def main():
    fixtures = "--fixtures" in sys.argv
    today = datetime.now(timezone.utc).date().isoformat()
    events = fetch_all(fixtures)
    n_rss = len(events)
    try:                                              # a youth outage must not kill the RSS run
        youth = fetch_youth(fixtures, today)
    except Exception as e:
        youth = []
        print(f"# youth fetch failed: {e}", file=sys.stderr)
    events += youth
    try:                                              # likewise a hub outage must not kill the run
        hub = fetch_hub(fixtures, today)
    except Exception as e:
        hub = []
        print(f"# hub fetch failed: {e}", file=sys.stderr)
    events += hub
    groups = dedup(events)                            # youth + hub dedup against the spine for free

    records = load()
    existing_ids = {r["id"] for r in records}
    existing_norms = {norm_name(r["name"]) for r in records}
    rejected_path = ROOT / "data" / "rejected.json"
    rejected = set(json.loads(rejected_path.read_text(encoding="utf-8"))) if rejected_path.exists() else set()

    drafts = [draft(g, today) for g in groups if g["nn"] not in rejected]
    new = [d for d in drafts
           if d["id"] not in existing_ids and norm_name(d["name"]) not in existing_norms]
    print(f"# {n_rss} RSS + {len(youth)} youth + {len(hub)} hub -> {len(groups)} unique -> "
          f"{len(new)} NEW (review/fix tags, then: python -m bcc.add insert <file>)\n", file=sys.stderr)
    print(json.dumps(new, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
