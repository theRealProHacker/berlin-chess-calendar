"""Turn a deduped feed Candidate into a pre-filled draft Tournament record.

This is the bridge from ingestion (machine) to curation (human): we guess every field
we can from the name + description prose, mark the record tagged_by="auto", and hand it
to the review UI where a human confirms or fixes the six axes before it's approved into
tournaments.json. Guesses are deliberately conservative — wrong-but-flagged beats
confidently-wrong.
"""
from __future__ import annotations

from datetime import date

from .dedup import Candidate
from .models import slugify
from .normalize import guess_prize_eur, guess_time_control


def _guess_variant(name: str) -> str:
    n = name.lower()
    if "tandem" in n or "bughouse" in n:
        return "tandem"
    if "960" in n or "chess960" in n or "freestyle" in n:
        return "chess960"
    return "standard"


def _guess_participation(name: str, variant: str) -> str:
    n = name.lower()
    if variant == "tandem":
        return "duo"
    if any(k in n for k in ("mannschaft", "-mm", " mm", "liga", "bmm", "bfl", "team", "pokal")):
        return "team"
    return "single"


def _guess_kind(name: str) -> str:
    n = name.lower()
    if "gedenk" in n or "memorial" in n:
        return "memorial"
    if "liga" in n or "bmm" in n or "bfl" in n or "mannschaftsmeisterschaft" in n:
        return "league"
    if "festival" in n or "schachfest" in n:
        return "festival"
    if "meisterschaft" in n or "-em" in n or " em" in n or "-mm" in n:
        return "championship"
    return "open"


def _guess_schedule(kind: str) -> str:
    return "biweekly" if kind == "league" else "block"


def candidate_to_draft(c: Candidate, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    year = c.start_date[:4]
    desc = " ".join(e.description for e in c.events)

    variant = _guess_variant(c.name)
    kind = _guess_kind(c.name)
    part = _guess_participation(c.name, variant)
    tc = guess_time_control(desc) or ("blitz" if "blitz" in c.name.lower() else
                                      "rapid" if "schnell" in c.name.lower() else "classical")
    prize = guess_prize_eur(desc)
    link = next((e.link for e in c.events if e.link), None)
    enclosure = next((e.enclosure_url for e in c.events if e.enclosure_url), None)

    draft: dict = {
        "id": slugify(c.name, year),
        "name": c.name,
        "kind": kind,
        "start_date": c.start_date,
        "end_date": c.end_date,
        "variant": variant,
        "time_control": tc,
        "age_limit": ["open"],
        "participation": part,
        "schedule_format": _guess_schedule(kind),
        "region": "berlin",
        "status": "confirmed",
        "sources": c.sources,
        "last_verified": today,
        "tagged_by": "auto",
    }
    if c.edition:
        draft["edition"] = c.edition
    if link:
        draft["source_url"] = link
    if enclosure:
        draft["registration_url"] = enclosure
    if prize:
        draft["prize_pool"] = {"amount": prize, "currency": "EUR"}
    return draft
