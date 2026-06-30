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
from urllib.parse import urljoin
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
# The BSV homepage carries a curated "Aktuelle Links" block: named tournament anchors, each
# pointing at a per-event page. It is the only source for the events that live on no feed
# (Teschner, Berliner EM classical, Pokal-MM, Senioren-EM, 960-MM, BMM/BFL ...).
BSV_HOME = "https://www.berlinerschachverband.de/"

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


def pick_enclosure(encs):
    """The Ausschreibung PDF URL from an item's <enclosure>s, or None.

    Items can carry several (a .docx and a .pdf of the same Ausschreibung); take the PDF —
    that is what ausschreibung_url means. Feed enclosures are http://; upgrade to https:// to
    match the site (and the feed's own <link>s, which are already https).
    """
    for e in encs:
        url = e.get("url") or ""
        if e.get("type") == "application/pdf" or url.lower().endswith(".pdf"):
            return "https://" + url[len("http://"):] if url.startswith("http://") else url
    return None


def parse_feed(xml_text, source):
    out = []
    for it in ET.fromstring(xml_text).iter("item"):
        title = (it.findtext("title") or "").strip()
        start, end, name = parse_title(title)
        out.append(Raw(source, name, start, end, parse_edition(name),
                       (it.findtext("link") or "").strip(), it.findtext("description") or "",
                       pick_enclosure(it.findall("enclosure"))))
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


# ---- BSV "Aktuelle Links" hub (homepage) ------------------------------------
# Discovery is reliable (the block is hand-curated named anchors); the per-event date lives on
# the linked page and is best-effort. Events that don't auto-date are reported on stderr for
# manual follow-up rather than guessed — discovery still surfaces them.
_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def strip_html(page):
    """HTML -> collapsed plain text (drops <script>/<style> bodies first)."""
    page = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", page, flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", page))).strip()


def extract_hub_links(home_html):
    """The 'Aktuelle Links' block -> [(name, absolute_url)] tournament anchors.

    Scoped to the heading's section (stops at the next <h2>). Drops the non-tournament entries:
    the Terminplan PDFs and the Klassenberechtigungen page.
    """
    m = re.search(r"Aktuelle\s*Links", home_html, re.I)
    if not m:
        return []
    seg = home_html[m.end():]
    nxt = re.search(r"<h2\b", seg, re.I)
    if nxt:
        seg = seg[:nxt.start()]
    out = []
    for href, text in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', seg, re.I | re.S):
        name = html.unescape(_TAG.sub(" ", text)).strip()
        if not name or href.lower().endswith(".pdf") or "klassenberechtigung" in name.lower():
            continue
        out.append((name, urljoin(BSV_HOME, href)))
    return out


def hub_event_date(text, today):
    """Best-effort (start, end) for an event page; (None, None) if nothing dated in the future.

    Prose first (parse_de_date is anchored on 'am' / 'vom…bis' / an explicit year, so it rarely
    false-matches), then numeric DD.MM.YYYY: earliest FUTURE date, end = latest within ~16 days.
    """
    today = today if isinstance(today, date) else date.fromisoformat(today)
    s, e = parse_de_date(text, today.isoformat())
    if s and s >= today.isoformat():
        return s, e
    fut = sorted(d for d in {date(int(y), int(mo), int(dd))
                             for dd, mo, y in _DMY.findall(text) if _valid_dmy(dd, mo, y)}
                 if d >= today)
    if fut:
        start = fut[0]
        end = max((x for x in fut if 0 <= (x - start).days <= 16), default=start)
        return start.isoformat(), end.isoformat()
    return None, None


def _valid_dmy(d, mo, y):
    try:
        date(int(y), int(mo), int(d))
        return True
    except ValueError:
        return False


def jsonld_event_dates(page):
    """schema.org Event (start, end) from <script type=application/ld+json>; (None, None) if absent.

    The reliable signal for JS-built tournament pages (chessmanager, schachevent): the date sits in
    server-side structured data even when the visible HTML renders it client-side — so no headless
    browser is needed. Handles a single object, a list, or an @graph; dates may be 'YYYY-MM-DD' or a
    full ISO timestamp (sliced to the date).
    """
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block)
        except Exception:
            continue
        stack = list(data) if isinstance(data, list) else [data]
        while stack:
            n = stack.pop()
            if not isinstance(n, dict):
                continue
            stack.extend(n.get("@graph", []))
            if "Event" in str(n.get("@type", "")) and n.get("startDate"):
                s = str(n["startDate"])[:10]
                e = str(n.get("endDate") or n["startDate"])[:10]
                if _ISO_DATE.match(s) and _ISO_DATE.match(e):
                    return s, e
    return None, None


def _meta_description(page):
    """The <meta name=description> content (a common home for a German date range), or ''."""
    m = (re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', page, re.I)
         or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']description', page, re.I))
    return html.unescape(m.group(1)) if m else ""


def fetch_hub(fixtures=False, today=None):
    """Harvest the BSV 'Aktuelle Links' hub -> [Raw]. Each named anchor -> fetch its page -> date.

    Fixtures mode is offline: link extraction is unit-tested directly against the captured block,
    and there is no per-page network, so it returns []. Live mode fetches each event page and
    keeps the ones it can date; undated ones are logged on stderr for manual follow-up.
    """
    today = today or datetime.now(timezone.utc).date()
    today = today if isinstance(today, date) else date.fromisoformat(today)
    if fixtures:
        return []
    links = extract_hub_links(http_get(BSV_HOME))
    out, skipped = [], []
    iso_today = today.isoformat()
    for name, url in links:
        try:
            page = http_get(url)
        except Exception:
            skipped.append((name, url))
            continue
        start, end = jsonld_event_dates(page)              # structured data first (JS pages)
        if not start:                                      # then the meta description + visible text
            start, end = hub_event_date(_meta_description(page) + " " + strip_html(page), today)
        if not start or end < iso_today:                   # undated, or a past edition (→ predictor's job)
            skipped.append((name, url))
            continue
        desc = _meta_description(page)
        out.append(Raw("bsv-hub", name, start, end, parse_edition(name), url, desc, None))
    if links:
        print(f"# hub: {len(links)} links -> {len(out)} dated, {len(skipped)} no upcoming date",
              file=sys.stderr)
    if skipped:
        print("# hub: no upcoming date (past edition, or not machine-readable — check manually): "
              + "; ".join(f"{n} <{u}>" for n, u in skipped), file=sys.stderr)
    return out


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
    enc = next((e.enclosure for e in evs if e.enclosure), None)
    if enc:
        d["ausschreibung_url"] = enc
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
