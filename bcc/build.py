"""Validate the data and build the static site + .ics. Stdlib only.

    python -m bcc.build      # -> dist/index.html + dist/calendar.ics

Tournaments are plain validated dicts (no ORM, no pydantic). validate() is the gate:
a bad record raises and the build stops, so the published files are always clean.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENUMS = {
    "kind": {"open", "championship", "memorial", "league", "club-series", "youth", "festival"},
    "variant": {"standard", "chess960", "tandem", "other"},
    "time_control": {"classical", "rapid", "blitz", "mixed"},
    "participation": {"single", "duo", "team"},
    "schedule_format": {"block", "weekly", "biweekly", "other"},
    "region": {"berlin", "nearby", "national"},
    "status": {"confirmed", "expected", "stale", "cancelled"},
}
REGION = ENUMS["region"]  # used by tests
AGE = {"open", "U8", "U10", "U12", "U14", "U16", "U18", "U20", "U25", "Ü60", "Ü65"}
REQUIRED = ["id", "name", "kind", "start_date", "end_date", "variant", "time_control",
            "age_limit", "participation", "schedule_format", "region", "status",
            "sources", "last_verified"]
# Four event links (all optional). source_url = the event's webpage ("Webseite").
# registration ("Anmeldung") is free text: a sign-up URL, or a phrase like "über den
# Verein" / "über Qualifikation" for non-opens. chess_results_url / ausschreibung_url
# (the PDF) are blank when they don't exist.
OPTIONAL = {"edition", "rounds", "organizer", "venue", "city", "source_url",
            "registration_deadline", "registration", "chess_results_url", "ausschreibung_url",
            "prize_pool", "tagged_by", "notes"}

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ValidationError(ValueError):
    pass


def _date(v, field):
    if not (isinstance(v, str) and _ISO.match(v)):
        raise ValidationError(f"{field}: not an ISO date: {v!r}")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValidationError(f"{field}: impossible date {v!r}")


def validate(r: dict) -> dict:
    """Validate one tournament record (raises ValidationError). Returns it unchanged."""
    rid = r.get("id", "?")
    missing = [k for k in REQUIRED if k not in r]
    if missing:
        raise ValidationError(f"{rid}: missing {missing}")
    extra = set(r) - set(REQUIRED) - OPTIONAL
    if extra:
        raise ValidationError(f"{rid}: unknown field(s) {sorted(extra)}")
    if not _SLUG.match(r["id"]):
        raise ValidationError(f"{rid}: id is not a clean slug")
    if not str(r["name"]).strip():
        raise ValidationError(f"{rid}: empty name")
    for field, allowed in ENUMS.items():
        if r[field] not in allowed:
            raise ValidationError(f"{rid}: {field}={r[field]!r} not in {sorted(allowed)}")
    if not isinstance(r["age_limit"], list) or not r["age_limit"]:
        raise ValidationError(f"{rid}: age_limit must be a non-empty list")
    for a in r["age_limit"]:
        if a not in AGE:
            raise ValidationError(f"{rid}: bad age {a!r}")
    if not isinstance(r["sources"], list) or not r["sources"]:
        raise ValidationError(f"{rid}: sources must be a non-empty list")
    start = _date(r["start_date"], "start_date")
    if _date(r["end_date"], "end_date") < start:
        raise ValidationError(f"{rid}: end_date before start_date")
    _date(r["last_verified"], "last_verified")
    if r.get("registration_deadline"):
        _date(r["registration_deadline"], "registration_deadline")
    if r.get("rounds") is not None:
        if not r["rounds"]:
            raise ValidationError(f"{rid}: rounds, if present, must be non-empty")
        prev = None
        for x in r["rounds"]:
            d = _date(x, "rounds")
            if prev and d < prev:
                raise ValidationError(f"{rid}: rounds not ascending")
            prev = d
    if r.get("edition") is not None and (not isinstance(r["edition"], int) or r["edition"] < 1):
        raise ValidationError(f"{rid}: edition must be a positive int")
    pp = r.get("prize_pool")
    if pp is not None and ("amount" not in pp or "currency" not in pp):
        raise ValidationError(f"{rid}: prize_pool needs 'amount' and 'currency'")
    if r.get("tagged_by", "human") not in ("human", "auto"):
        raise ValidationError(f"{rid}: bad tagged_by")
    return r


def load(path=ROOT / "data" / "tournaments.json") -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValidationError("tournaments.json must be a JSON array")
    seen = set()
    for r in data:
        validate(r)
        if r["id"] in seen:
            raise ValidationError(f"duplicate id {r['id']!r}")
        seen.add(r["id"])
    return data


_ORD = re.compile(r"\b\d{1,3}\.\s*")
_ROM = re.compile(r"\b[IVXLC]+\.\s*")
_YR = re.compile(r"\b(19|20)\d{2}\b")


def slugify(name: str, year) -> str:
    """Stable slug = normalized name + year (it is the iCal UID)."""
    s = _YR.sub("", _ROM.sub("", _ORD.sub("", name))).lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s).strip("-"))
    return f"{s}-{year}"


# ---- iCalendar (RFC 5545) --------------------------------------------------

def _esc(t):
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, chunk = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > 75:
            out.append(chunk)
            chunk = b" " + b
        else:
            chunk += b
    out.append(chunk)
    return "\r\n".join(c.decode("utf-8") for c in out)


def _vevent(t):
    end = date.fromisoformat(t["end_date"]) + timedelta(days=1)  # iCal DTEND is exclusive
    summary = ("[erwartet] " if t["status"] == "expected" else "") + t["name"]
    bits = [t["kind"]]
    if t.get("organizer"):
        bits.append(t["organizer"])
    if t.get("prize_pool"):
        bits.append(f"{t['prize_pool']['amount']} {t['prize_pool']['currency']} Preisfonds")
    if t["status"] == "expected":
        bits.append("erwartet, noch nicht bestätigt")
    loc = ", ".join(x for x in (t.get("venue"), t.get("city")) if x)
    status = {"confirmed": "CONFIRMED", "expected": "TENTATIVE", "cancelled": "CANCELLED"}.get(t["status"], "CONFIRMED")
    # The four event links, labeled, appended to the description (registration may be a URL
    # or a phrase like "über Qualifikation"). source_url also drives URL:, the PDF also ATTACH:.
    links = [(lbl, t[f]) for lbl, f in (("Webseite", "source_url"), ("Anmeldung", "registration"),
              ("Chess Results", "chess_results_url"), ("Ausschreibung", "ausschreibung_url")) if t.get(f)]
    desc = " · ".join(bits)
    if links:
        desc += "\n\n" + "\n".join(f"{lbl}: {val}" for lbl, val in links)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{t['id']}@berliner-schachkalender",
        f"DTSTAMP:{t['last_verified'].replace('-', '')}T000000Z",
        f"DTSTART;VALUE=DATE:{t['start_date'].replace('-', '')}",
        f"DTEND;VALUE=DATE:{end.isoformat().replace('-', '')}",
        f"SUMMARY:{_esc(summary)}",
        f"DESCRIPTION:{_esc(desc)}",
        f"STATUS:{status}",
        f"CATEGORIES:{_esc(','.join([t['kind'], t['variant'], t['time_control'], t['participation']]))}",
        "TRANSP:TRANSPARENT",
    ]
    if loc:
        lines.append(f"LOCATION:{_esc(loc)}")
    if t.get("source_url"):
        lines.append(f"URL:{t['source_url']}")
    if t.get("ausschreibung_url"):
        lines.append(f"ATTACH:{t['ausschreibung_url']}")
    lines.append("END:VEVENT")
    return lines


def build_ics(tournaments, today=None):
    today = today or date.today()
    cutoff = today - timedelta(days=30)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Berliner Schachkalender//bcc//DE",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Berliner Schachkalender",
             "X-WR-TIMEZONE:Europe/Berlin"]
    for t in sorted(tournaments, key=lambda x: (x["start_date"], x["id"])):
        if t["status"] == "stale" or date.fromisoformat(t["end_date"]) < cutoff:
            continue
        lines += _vevent(t)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


# ---- build -----------------------------------------------------------------

def main():
    ts = load()
    payload = json.dumps(ts, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (ROOT / "site" / "template.html").read_text(encoding="utf-8")
    if "__DATA__" not in html:
        sys.exit("template missing __DATA__")
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist" / "index.html").write_text(html.replace("__DATA__", payload), encoding="utf-8")
    (ROOT / "dist" / "calendar.ics").write_text(build_ics(ts), encoding="utf-8")
    print(f"wrote dist/index.html + dist/calendar.ics ({len(ts)} tournaments)")


if __name__ == "__main__":
    main()
