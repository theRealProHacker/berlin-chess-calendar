"""One-shot build: validate data, emit dist/index.html + dist/calendar.ics.

Run with:  python -m bcc.build
This is the whole Phase-0 pipeline. Later phases add the ingestion + review UI in
front of it, but the contract stays: data/*.json -> dist/ static artifacts.
"""
from __future__ import annotations

from pathlib import Path

from . import build_ics as ics_mod
from . import build_site as site_mod
from . import models


def main() -> None:
    n, outp = site_mod.build_site()
    print(f"wrote {outp} ({n} tournaments)")

    ts = models.load("data/tournaments.json")
    ics = ics_mod.build_ics(ts)
    out = Path("dist/calendar.ics")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ics, encoding="utf-8")
    print(f"wrote {out} ({ics.count('BEGIN:VEVENT')} events)")


if __name__ == "__main__":
    main()
