"""Fetch the Berlin chess RSS feeds, dedup, and print draft records for review.

    python -m bcc.ingest             # live feeds
    python -m bcc.ingest --fixtures  # committed fixtures (offline)

Three Berlin sources, all funneling into one dedup/draft pipeline:
  - DSB Berlin + BSV RSS (identical Contao RSS, one parser serves both)
  - schachjugend-in-berlin.de youth tournaments (WordPress REST; event dates are German
    prose, not a structured field, so they are regex-parsed from the title/excerpt)
Anti-anti-bot UA + de-DE on every request. Output is a JSON array of NEW candidate records
(auto-guessed, tagged_by=auto). Review them, fix the auto-guessed tags, then insert the ones
you want via bcc.add so the file keeps its canonical format (NOT a hand-paste, which drifts
the formatting):

    python -m bcc.ingest --fixtures > drafts.json      # then prune/fix drafts.json
    python -m bcc.add insert drafts.json               # validates + inserts the batch

`bcc.add insert` runs validate() on every record and refuses the whole batch on any
failure, so a bad tag can never reach data/tournaments.json. That review + insert +
validate loop is the whole Berlin curation flow — no server, no database.
"""
from __future__ import annotations

import html
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

# ---- youth source (schachjugend-in-berlin.de WordPress REST) ----------------
# Tournaments are ordinary posts in categories 38 ("Turniere", own) + 24 ("externe").
# No structured event date exists (post.date is the PUBLISH date), so the event date is
# regex-parsed out of the German title/excerpt prose. Categories don't encode age.
YOUTH_WP = "https://www.schachjugend-in-berlin.de/wp-json/wp/v2/posts"
YOUTH_CATS = "38,24"
_MONTHS = {m: i for i, m in enumerate(
    ("januar", "februar", "märz", "april", "mai", "juni", "juli",
     "august", "september", "oktober", "november", "dezember"), start=1)}
_MONTH = "(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)"
# range "vom 23. bis 25. Juli [2026]" — the "bis" makes it self-anchored (no edition false-match)
_DATE_RANGE = re.compile(r"(\d{1,2})\.\s*bis\s+(\d{1,2})\.\s*" + _MONTH + r"\b\s*(\d{4})?", re.I)
# single "am [So.,] 11. Juli [2026]" — anchored on "am" (+ optional weekday) to avoid matching
# an edition ordinal like "9. Kreuzberger ..."; a 4-digit year alone also disambiguates.
_DATE_AM = re.compile(r"\bam\s+(?:[A-Za-zÄÖÜäöüß.,]+\s+){0,2}(\d{1,2})\.\s*" + _MONTH + r"\b\s*(\d{4})?", re.I)
_DATE_YEAR = re.compile(r"(\d{1,2})\.\s*" + _MONTH + r"\s+(\d{4})\b", re.I)
_AGE = re.compile(r"\bU(8|10|12|14|16|18|20|25)\b", re.I)
_TAG = re.compile(r"<[^>]+>")


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


def parse_de_date(text, pub_date):
    """Parse a German event date out of free text. Returns (start_iso, end_iso) or (None, None).

    `pub_date` ("YYYY-MM-DD…", the post's publish date) anchors an OMITTED year: a date that
    would fall before the post was published is rolled to the next year (a Dec post about a
    Feb event). Edition ordinals ("9. Kreuzberger…") never match — the single-date pattern is
    anchored on "am"/a year, and ranges require "bis".
    """
    pub = date.fromisoformat(pub_date[:10])

    def _resolve(d, mo, yr_raw):
        yr = int(yr_raw) if yr_raw else pub.year
        try:
            dt = date(yr, mo, d)
        except ValueError:
            return None
        if not yr_raw and dt < pub:                 # omitted year already past -> next year
            try:
                dt = date(yr + 1, mo, d)
            except ValueError:
                return None
        return dt

    m = _DATE_RANGE.search(text)
    if m:
        s = _resolve(int(m.group(1)), _MONTHS[m.group(3).lower()], m.group(4))
        e = _resolve(int(m.group(2)), _MONTHS[m.group(3).lower()], m.group(4))
        if s and e:
            if e < s:                                # range crosses a year boundary (rare)
                e = date(e.year + 1, e.month, e.day)
            return s.isoformat(), e.isoformat()
    m = _DATE_AM.search(text) or _DATE_YEAR.search(text)
    if m:
        s = _resolve(int(m.group(1)), _MONTHS[m.group(2).lower()], m.group(3))
        if s:
            return s.isoformat(), s.isoformat()
    return None, None


def extract_ages(name):
    """All age groups named in the title, sorted (e.g. ['U10','U12']); [] if none."""
    seen = {"U" + m.group(1) for m in _AGE.finditer(name)}
    return sorted(seen, key=lambda a: int(a[1:]))


def clean_youth_name(name):
    """Drop the date/notice tail youth post titles embed, so the name (and its UID slug) stays clean.

    RSS titles carry the date in a separate field; youth titles bake it in ("… – am 11. Juli 2026",
    "… U8 vom 13. bis 15. Februar 2026 – noch 13 Plätze frei"). Call AFTER parsing the date.
    """
    name = re.split(r"\s*[–-]\s*(?:am|vom|noch)\b", name, maxsplit=1, flags=re.I)[0]
    name = re.split(r"\s+(?:am|vom)\s+(?:[A-Za-zÄÖÜäöüß.,]+\s+){0,2}\d", name, maxsplit=1, flags=re.I)[0]
    return name.strip(" –-")


def parse_youth(posts, today):
    """WP-REST posts (JSON string or list) -> [Raw]. Drops posts with no parseable FUTURE date.

    source = "sjib" when the post is in category 38 (org's own tournament, reliably Berlin),
    else "sjib-extern" (category 24 only — may be outside Berlin; draft() flags it for review).
    """
    if isinstance(posts, str):
        posts = json.loads(posts)
    today = today if isinstance(today, str) else today.isoformat()
    out = []
    for p in posts:
        name = html.unescape(p["title"]["rendered"]).strip()
        excerpt = html.unescape(_TAG.sub("", p["excerpt"]["rendered"])).strip()
        start, end = parse_de_date(name + " " + excerpt, p["date"])
        if start is None or start < today:
            continue
        name = clean_youth_name(name)        # strip the embedded date tail before it feeds the slug
        source = "sjib" if 38 in p.get("categories", []) else "sjib-extern"
        out.append(Raw(source, name, start, end, parse_edition(name),
                       p.get("link", ""), excerpt, None))
    return out


def fetch_youth(fixtures=False, today=None, pages=3):
    """Pull youth tournaments. Live: paginate the WP REST API; fixtures: the committed sample."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    if fixtures:
        posts = json.loads((ROOT / "tests" / "fixtures" / "schachjugend.json").read_text(encoding="utf-8"))
    else:
        posts = []
        for page in range(1, pages + 1):
            url = f"{YOUTH_WP}?categories={YOUTH_CATS}&per_page=100&orderby=date&order=desc&page={page}"
            try:
                batch = json.loads(http_get(url))
            except Exception:               # past the last page WP returns 400 — stop cleanly
                break
            if not batch:
                break
            posts += batch
            if len(batch) < 100:
                break
    raws = parse_youth(posts, today)
    if posts:
        print(f"# youth: {len(raws)} upcoming kept, {len(posts) - len(raws)} skipped (past or dateless)",
              file=sys.stderr)
    return raws


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
    if prizes:
        d["prize_pool"] = {"amount": max(prizes), "currency": "EUR"}
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
    groups = dedup(events)                            # youth dedups against the spine for free

    records = load()
    existing_ids = {r["id"] for r in records}
    existing_norms = {norm_name(r["name"]) for r in records}
    rejected_path = ROOT / "data" / "rejected.json"
    rejected = set(json.loads(rejected_path.read_text(encoding="utf-8"))) if rejected_path.exists() else set()

    drafts = [draft(g, today) for g in groups if g["nn"] not in rejected]
    new = [d for d in drafts
           if d["id"] not in existing_ids and norm_name(d["name"]) not in existing_norms]
    print(f"# {n_rss} RSS + {len(youth)} youth -> {len(groups)} unique -> {len(new)} NEW "
          f"(review/fix tags, then: python -m bcc.add insert <file>)\n", file=sys.stderr)
    print(json.dumps(new, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
