"""CLI: fetch the feeds, dedup, and report candidates (new vs already-seeded).

    python -m bcc.ingest            # fetch live feeds
    python -m bcc.ingest --fixtures # parse the committed test fixtures (offline)

Phase 1 stops at reporting. Phase 2 turns each NEW candidate into a draft record in
a review queue where a human tags the six axes and approves it into tournaments.json.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import dedup as dd
from . import fetch, models, normalize


def _candidates(use_fixtures: bool) -> list[dd.Candidate]:
    events = []
    if use_fixtures:
        fx = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
        events += fetch.parse_feed((fx / "dsb-berlin.xml").read_text(encoding="utf-8"), "dsb-berlin")
        events += fetch.parse_feed((fx / "bsv-terminkalender.xml").read_text(encoding="utf-8"), "bsv-termin")
    else:
        events = fetch.fetch_all()
    return dd.dedup(events), events


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest Berlin chess feeds -> candidate list")
    ap.add_argument("--fixtures", action="store_true", help="use committed fixtures instead of the live feeds")
    ap.add_argument("--data", default="data/tournaments.json")
    args = ap.parse_args()

    cands, events = _candidates(args.fixtures)

    try:
        existing = {t.id for t in models.load(args.data)}
    except Exception:
        existing = set()

    n_new = 0
    print(f"{len(events)} raw events -> {len(cands)} unique candidates\n")
    for c in cands:
        slug = models.slugify(c.name, c.start_date[:4])
        is_new = slug not in existing
        n_new += is_new
        tc = normalize.guess_time_control(" ".join(e.description for e in c.events)) or "?"
        flag = "NEW" if is_new else "  ·"
        ed = f" (#{c.edition})" if c.edition else ""
        print(f"  {flag} {c.start_date}  {c.name}{ed}")
        print(f"       sources={','.join(c.sources)}  tc≈{tc}")
    print(f"\n{n_new} new candidate(s) not yet in {args.data}")


if __name__ == "__main__":
    main()
