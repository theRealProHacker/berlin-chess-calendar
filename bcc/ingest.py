"""Fetch the Berlin chess RSS feeds, dedup, and print draft records for review.

    python -m bcc.ingest             # live feeds
    python -m bcc.ingest --fixtures  # committed fixtures (offline)

Both feeds are identical Contao RSS, so one parser serves both. Anti-anti-bot UA +
de-DE on every request. Output is a JSON array of NEW candidate records (auto-guessed,
tagged_by=auto). Review them, fix the auto-guessed tags, then insert the ones you want
via bcc.add so the file keeps its canonical format (NOT a hand-paste, which drifts the
formatting):

    python -m bcc.ingest --fixtures > drafts.json      # then prune/fix drafts.json
    python -m bcc.add insert drafts.json               # validates + inserts the batch

`bcc.add insert` runs validate() on every record and refuses the whole batch on any
failure, so a bad tag can never reach data/tournaments.json. That review + insert +
validate loop is the whole Berlin curation flow — no server, no database.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .build import ROOT, load, slugify

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
SOURCES = {
    "dsb-berlin": "https://www.schachbund.de/share/feed-turnierdatenbank-berlin.xml",
    "bsv-termin": "https://www.berlinerschachverband.de/share/bsv-terminkalender.xml",
}

_TITLE = re.compile(
    r"^\s*(\d{2})\.(\d{2})\.(\d{4})"
    r"(?:\s*[–-]\s*(\d{2})\.(\d{2})\.(\d{4}))?"
    r"(?:\s*\(([^)]*)\))?\s*(.*\S)\s*$"
)
_EURO = re.compile(r"(\d[\d.\s]*)\s*(?:€|Euro|EUR)")


@dataclass
class Raw:
    source: str
    name: str
    start: str
    end: str
    edition: int | None
    link: str
    desc: str
    enclosure: str | None


def parse_title(title):
    m = _TITLE.match(title)
    if not m:
        raise ValueError(f"unparseable title: {title!r}")
    d1, m1, y1, d2, m2, y2, _tod, name = m.groups()
    start = f"{y1}-{m1}-{d1}"
    end = f"{y2}-{m2}-{d2}" if y2 else start
    return start, end, name.strip()


def parse_edition(name):
    m = re.match(r"^\s*(\d{1,3})\.\s", name)
    return int(m.group(1)) if m else None


def norm_name(name):
    s = re.sub(r"\b(?:19|20)\d{2}\b", "", re.sub(r"\b[IVXLC]+\.\s*", "", re.sub(r"\b\d{1,3}\.\s*", "", name))).lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def parse_feed(xml_text, source):
    out = []
    for it in ET.fromstring(xml_text).iter("item"):
        title = (it.findtext("title") or "").strip()
        start, end, name = parse_title(title)
        enc = it.find("enclosure")
        out.append(Raw(source, name, start, end, parse_edition(name),
                       (it.findtext("link") or "").strip(), it.findtext("description") or "",
                       enc.get("url") if enc is not None else None))
    return out


def http_get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"})
    with urllib.request.urlopen(req, timeout=20) as r:  # nosec - fixed https feed urls
        return r.read().decode("utf-8")


def fetch_all(fixtures=False):
    evs = []
    if fixtures:
        fx = ROOT / "tests" / "fixtures"
        evs += parse_feed((fx / "dsb-berlin.xml").read_text(encoding="utf-8"), "dsb-berlin")
        evs += parse_feed((fx / "bsv-terminkalender.xml").read_text(encoding="utf-8"), "bsv-termin")
    else:
        for k, url in SOURCES.items():
            evs += parse_feed(http_get(url), k)
    return evs


def dedup(events, window=3):
    """Group by normalized-name + start within `window` days. Returns merged groups."""
    groups = []
    for ev in sorted(events, key=lambda e: (e.start, e.name)):
        nn = norm_name(ev.name)
        for g in groups:
            if g["nn"] == nn and abs((date.fromisoformat(ev.start) - date.fromisoformat(g["evs"][0].start)).days) <= window:
                g["evs"].append(ev)
                break
        else:
            groups.append({"nn": nn, "evs": [ev]})
    return groups


def draft(group, today):
    """Build a paste-ready draft dict from a merged group (auto-guessed, tagged_by=auto)."""
    evs = group["evs"]
    first = min(evs, key=lambda e: e.start)
    name = first.name
    n = name.lower()
    blob = " ".join(e.desc for e in evs).lower()
    variant = "tandem" if ("tandem" in n or "bughouse" in n) else ("chess960" if "960" in n or "freestyle" in n else "standard")
    kind = ("memorial" if "gedenk" in n else "league" if any(k in n for k in ("liga", "bmm", "bfl", "mannschaftsmeisterschaft"))
            else "festival" if "festival" in n else "championship" if ("meisterschaft" in n or "-em" in n or "-mm" in n) else "open")
    part = "duo" if variant == "tandem" else ("team" if any(k in n for k in ("mannschaft", "liga", "bmm", "bfl", "team", "pokal")) else "single")
    tc = ("blitz" if "blitz" in blob or "blitz" in n else "rapid" if "schnell" in blob or "schnell" in n or "rapid" in blob
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
    if first.edition:
        d["edition"] = first.edition
    link = next((e.link for e in evs if e.link), None)
    if link:
        d["source_url"] = link
    if prizes:
        d["prize_pool"] = {"amount": max(prizes), "currency": "EUR"}
    return d


def main():
    fixtures = "--fixtures" in sys.argv
    today = datetime.now(timezone.utc).date().isoformat()
    events = fetch_all(fixtures)
    groups = dedup(events)
    existing = {r["id"] for r in load()}
    rejected_path = ROOT / "data" / "rejected.json"
    rejected = set(json.loads(rejected_path.read_text(encoding="utf-8"))) if rejected_path.exists() else set()

    drafts = [draft(g, today) for g in groups if g["nn"] not in rejected]
    new = [d for d in drafts if d["id"] not in existing]
    print(f"# {len(events)} raw events -> {len(groups)} unique -> {len(new)} NEW "
          f"(review/fix tags, then: python -m bcc.add insert <file>)\n", file=sys.stderr)
    print(json.dumps(new, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
