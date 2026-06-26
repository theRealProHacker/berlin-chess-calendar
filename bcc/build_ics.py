"""Generate a subscribable iCalendar (.ics) feed from tournaments.json.

RFC 5545 details handled here:
- All-day multi-day events: DTSTART/DTEND use VALUE=DATE (no time, no timezone).
- iCal DTEND is EXCLUSIVE, so DTEND = end_date + 1 day.
- UID = the tournament id (stable slug). Re-runs and expected->confirmed promotion
  UPDATE the existing calendar entry instead of duplicating it.
- Text fields are escaped (\\, ; , and newlines) and lines folded at 75 octets.
- expected -> STATUS:TENTATIVE; cancelled -> STATUS:CANCELLED; stale -> dropped.
- Events whose end is >30 days in the past are dropped.

`select()` filters by the six axes (+ region/kind/status). The site encodes the
active filters into the subscribe URL, so a filtered subscription resolves to
the same select() applied here -> the .ics contains exactly the filtered slice.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from . import models

PRODID = "-//Berliner Schachkalender//bcc//DE"

# query param name -> tournament attribute. age is matched against the list.
_AXES = {
    "region": "region", "variant": "variant", "tc": "time_control",
    "part": "participation", "format": "schedule_format", "kind": "kind",
    "status": "status", "age": "age_limit", "org": "organizer",
}


def select(tournaments: Iterable[models.Tournament], filters: dict[str, list[str]] | None):
    """Faceted filter: OR within an axis, AND across axes. Empty/None filters => all."""
    items = list(tournaments)
    if not filters:
        return items
    out = []
    for t in items:
        ok = True
        for key, wanted in filters.items():
            if not wanted:
                continue
            attr = _AXES.get(key)
            if not attr:
                continue
            val = getattr(t, attr, None)
            if attr == "age_limit":
                if not (set(val or []) & set(wanted)):
                    ok = False
                    break
            elif val not in wanted:
                ok = False
                break
        if ok:
            out.append(t)
    return out


def _esc(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a content line at 75 octets with CRLF + leading space (RFC 5545 3.1)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, chunk = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > 75:
            out.append(chunk)
            chunk = b" " + b  # continuation lines start with a space
        else:
            chunk += b
    out.append(chunk)
    return "\r\n".join(c.decode("utf-8") for c in out)


def _d(d: str) -> str:
    return d.replace("-", "")  # 2026-08-01 -> 20260801


def _vevent(t: models.Tournament) -> list[str]:
    start = date.fromisoformat(t.start_date)
    end = date.fromisoformat(t.end_date)
    dtend = end + timedelta(days=1)  # exclusive end
    dtstamp = date.fromisoformat(t.last_verified)

    summary = t.name
    if t.status == "expected":
        summary = f"[erwartet] {t.name}"

    desc_bits = [t.kind]
    if t.organizer:
        desc_bits.append(t.organizer)
    if t.prize_pool:
        desc_bits.append(f"{t.prize_pool['amount']} {t.prize_pool['currency']} Preisfonds")
    if t.status == "expected":
        desc_bits.append("erwartet, noch nicht offiziell bestätigt")
    if t.registration_deadline:
        desc_bits.append(f"Anmeldung bis {t.registration_deadline}")
    description = " · ".join(desc_bits)

    location = ", ".join(x for x in (t.venue, t.city) if x)

    ical_status = {"confirmed": "CONFIRMED", "expected": "TENTATIVE", "cancelled": "CANCELLED"}.get(t.status, "CONFIRMED")
    categories = [t.kind, t.variant, t.time_control, t.participation]

    lines = [
        "BEGIN:VEVENT",
        f"UID:{t.id}@berliner-schachkalender",
        f"DTSTAMP:{_d(t.last_verified)}T000000Z",
        f"DTSTART;VALUE=DATE:{_d(t.start_date)}",
        f"DTEND;VALUE=DATE:{dtend.isoformat().replace('-', '')}",
        f"SUMMARY:{_esc(summary)}",
        f"DESCRIPTION:{_esc(description)}",
        f"STATUS:{ical_status}",
        f"CATEGORIES:{_esc(','.join(categories))}",
        "TRANSP:TRANSPARENT",
    ]
    if location:
        lines.append(f"LOCATION:{_esc(location)}")
    if t.source_url:
        lines.append(f"URL:{t.source_url}")
    lines.append("END:VEVENT")
    return lines


def build_ics(
    tournaments: Iterable[models.Tournament],
    filters: dict[str, list[str]] | None = None,
    today: date | None = None,
) -> str:
    today = today or date.today()
    cutoff = today - timedelta(days=30)
    items = select(tournaments, filters)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Berliner Schachkalender",
        "X-WR-CALDESC:Schachturniere in Berlin",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]
    for t in sorted(items, key=lambda x: (x.start_date, x.id)):
        if t.status == "stale":
            continue
        if date.fromisoformat(t.end_date) < cutoff:
            continue
        lines.extend(_vevent(t))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Build calendar.ics from tournaments.json")
    ap.add_argument("--data", default="data/tournaments.json")
    ap.add_argument("--out", default="dist/calendar.ics")
    args = ap.parse_args()

    ts = models.load(args.data)
    ics = build_ics(ts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ics, encoding="utf-8")
    n = ics.count("BEGIN:VEVENT")
    print(f"wrote {out} ({n} events)")


if __name__ == "__main__":
    main()
