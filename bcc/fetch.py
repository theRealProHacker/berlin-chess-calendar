"""Fetch + parse the Berlin chess RSS feeds (the automated ingestion spine).

Both feeds are Contao RSS with an identical shape, so one parser serves both; the
adapters differ only by URL + source key. stdlib only (urllib) — no httpx until the
Phase-2 venv. Anti-anti-bot is baked in: a realistic desktop-Chrome User-Agent +
Accept-Language: de-DE on every request (validated to flip chessmanager 403->200;
the feeds themselves aren't bot-protected, this is belt-and-suspenders + politeness).

chess-results / chessmanager are intentionally NOT here — they are bot-walled and/or
viewstate-driven, so they stay manual per-event links (see RECON-data-sources.md).
"""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import normalize

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

SOURCES = {
    "dsb-berlin": "https://www.schachbund.de/share/feed-turnierdatenbank-berlin.xml",
    "bsv-termin": "https://www.berlinerschachverband.de/share/bsv-terminkalender.xml",
}


@dataclass
class RawEvent:
    source: str          # source key (dsb-berlin | bsv-termin)
    title: str           # raw feed <title>
    link: str            # detail page url
    description: str      # raw <description> HTML
    pub_date: str | None = None
    enclosure_url: str | None = None
    # parsed from the title:
    name: str = ""
    start_date: str = ""  # YYYY-MM-DD
    end_date: str = ""    # YYYY-MM-DD
    edition: int | None = None
    time_of_day: str | None = None


def http_get(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec - fixed https feed urls
        return r.read().decode("utf-8")


def parse_feed(xml_text: str, source: str) -> list[RawEvent]:
    """Parse Contao RSS into RawEvents with the title fields already split out."""
    root = ET.fromstring(xml_text)
    out: list[RawEvent] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = item.findtext("description") or ""
        pub = item.findtext("pubDate")
        enc = item.find("enclosure")
        enc_url = enc.get("url") if enc is not None else None

        start, end, tod, name = normalize.parse_title(title)
        out.append(
            RawEvent(
                source=source, title=title, link=link, description=desc,
                pub_date=pub, enclosure_url=enc_url,
                name=name, start_date=start, end_date=end,
                edition=normalize.parse_edition(name), time_of_day=tod,
            )
        )
    return out


def fetch_source(key: str) -> list[RawEvent]:
    return parse_feed(http_get(SOURCES[key]), key)


def fetch_all() -> list[RawEvent]:
    events: list[RawEvent] = []
    for key in SOURCES:
        events.extend(fetch_source(key))
    return events
