"""Merge RawEvents from multiple feeds into unique tournament candidates.

The recon showed DSB and BSV overlap on the big opens, but their <link>s point to
DIFFERENT detail pages (schachbund.de vs berlinerschachverband.de), so we can't
dedup on the link. The reliable key is normalized-name + start-date within a few
days (handles a one-day date drift between sources / editions). This is the easy
half of the design doc's heuristic; the recon proved the harder rapidfuzz step
isn't needed for these two feeds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .fetch import RawEvent
from .normalize import norm_name


@dataclass
class Candidate:
    name: str                 # original name from the earliest-dated source
    start_date: str
    end_date: str
    edition: int | None
    sources: list[str]
    links: list[str]
    events: list[RawEvent] = field(default_factory=list)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def dedup(events: list[RawEvent], window_days: int = 3) -> list[Candidate]:
    """Group events that are the same tournament (norm_name + start within window_days)."""
    groups: list[dict] = []
    for ev in sorted(events, key=lambda e: (e.start_date, e.name)):
        nn = norm_name(ev.name)
        for g in groups:
            if g["nn"] == nn and abs((_d(ev.start_date) - _d(g["start"])).days) <= window_days:
                g["events"].append(ev)
                break
        else:
            groups.append({"nn": nn, "start": ev.start_date, "events": [ev]})

    out: list[Candidate] = []
    for g in groups:
        evs = g["events"]
        first = min(evs, key=lambda e: e.start_date)
        edition = next((e.edition for e in evs if e.edition is not None), None)
        out.append(
            Candidate(
                name=first.name,
                start_date=first.start_date,
                end_date=max(e.end_date for e in evs),
                edition=edition,
                sources=sorted({e.source for e in evs}),
                links=sorted({e.link for e in evs if e.link}),
                events=evs,
            )
        )
    out.sort(key=lambda c: (c.start_date, c.name))
    return out
