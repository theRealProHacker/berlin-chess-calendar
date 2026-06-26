"""Parse the semi-structured feed payloads into clean fields.

The DSB and BSV feeds are the same Contao format. The reliable signal is the
<title>, which always looks like:

    DD.MM.YYYY[–DD.MM.YYYY] [(HH:MM–HH:MM)] <Name>

so dates parse deterministically. The variant/time-control/prize live as free
German prose in <description>; we make a best-effort *guess* here (tagged_by=auto)
that a human confirms in the review step — we never trust it blindly.
"""
from __future__ import annotations

import re

# DD.MM.YYYY, optional en-dash end date, optional (time) in parens, then the name.
_TITLE = re.compile(
    r"^\s*(\d{2})\.(\d{2})\.(\d{4})"          # start date
    r"(?:\s*[–-]\s*(\d{2})\.(\d{2})\.(\d{4}))?"  # optional end date (en-dash or hyphen)
    r"(?:\s*\(([^)]*)\))?"                     # optional (HH:MM–HH:MM)
    r"\s*(.*\S)\s*$"                           # the name
)
_ARABIC_ORD = re.compile(r"^\s*(\d{1,3})\.\s")
_NORM_ORD = re.compile(r"\b\d{1,3}\.\s*")
_NORM_ROMAN = re.compile(r"\b[IVXLC]+\.\s*")
_NORM_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_EURO = re.compile(r"(\d[\d.\s]*)\s*(?:€|Euro|EUR)")


def parse_title(title: str) -> tuple[str, str, str | None, str]:
    """('2026-08-01', '2026-08-09', time_or_None, 'Name'). end defaults to start."""
    m = _TITLE.match(title)
    if not m:
        raise ValueError(f"unparseable feed title: {title!r}")
    d1, m1, y1, d2, m2, y2, tod, name = m.groups()
    start = f"{y1}-{m1}-{d1}"
    end = f"{y2}-{m2}-{d2}" if y2 else start
    return start, end, (tod or None), name.strip()


def parse_edition(name: str) -> int | None:
    """Leading arabic ordinal -> edition number ('22. Foo' -> 22). Roman -> None for now."""
    m = _ARABIC_ORD.match(name)
    return int(m.group(1)) if m else None


def norm_name(name: str) -> str:
    """Normalized form for dedup: strip ordinals/years, transliterate umlauts, lowercase.

    '22. Internationales Berliner Tandemschach-Open' and the BSV copy of the same title
    both reduce to 'internationales berliner tandemschach open'.
    """
    s = name
    s = _NORM_ORD.sub("", s)
    s = _NORM_ROMAN.sub("", s)
    s = _NORM_YEAR.sub("", s)
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def guess_time_control(text: str) -> str | None:
    """Best-effort time-control from description prose (auto, confirm in review)."""
    t = text.lower()
    if "blitz" in t:
        return "blitz"
    if "schnellschach" in t or "schnellturnier" in t or "rapid" in t:
        return "rapid"
    if "klassisch" in t or "schweizer" in t or re.search(r"\d+\s*min", t):
        return "classical"
    return None


def guess_prize_eur(text: str) -> int | None:
    """Largest euro amount mentioned (best-effort prize fund)."""
    amounts = []
    for raw in _EURO.findall(text):
        digits = re.sub(r"[.\s]", "", raw)
        if digits.isdigit():
            amounts.append(int(digits))
    return max(amounts) if amounts else None
