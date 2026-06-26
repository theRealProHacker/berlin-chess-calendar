"""Local curation server (Phase 2): review the fetched candidates, tag, approve.

    python -m bcc.serve          # http://127.0.0.1:8765

Flow: fetch the two feeds -> dedup -> pre-fill drafts (draft.py) -> you confirm the six
axes and Approve (writes into data/tournaments.json + rebuilds dist/) or Reject (remembers
the normalized name so recurring noise like the weekly course never comes back). Runs on
YOUR machine on demand; the published site stays static + free.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import build_ics, build_site, dedup as dd, draft as draft_mod, fetch, models
from .normalize import norm_name

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "tournaments.json"
REJECTED = ROOT / "data" / "rejected.json"
TEMPLATE = ROOT / "templates" / "review.html"
FIXTURES = ROOT / "tests" / "fixtures"

app = FastAPI(title="Berliner Schachkalender — Kuratierung")


def _rejected() -> set[str]:
    if REJECTED.exists():
        return set(json.loads(REJECTED.read_text(encoding="utf-8")))
    return set()


def _save_rejected(s: set[str]) -> None:
    REJECTED.write_text(json.dumps(sorted(s), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rebuild() -> None:
    build_site.build_site(
        data=str(DATA),
        template=str(ROOT / "site" / "template.html"),
        out=str(ROOT / "dist" / "index.html"),
    )
    ts = models.load(DATA)
    (ROOT / "dist" / "calendar.ics").write_text(build_ics.build_ics(ts), encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


@app.get("/api/schema")
def schema() -> dict:
    return {
        "kind": sorted(models.KIND),
        "variant": sorted(models.VARIANT),
        "time_control": sorted(models.TIME_CONTROL),
        "participation": sorted(models.PARTICIPATION),
        "schedule_format": sorted(models.SCHEDULE_FORMAT),
        "region": sorted(models.REGION),
        "status": sorted(models.STATUS),
        "age": sorted(models.AGE),
    }


@app.get("/api/candidates")
def candidates(live: int = 0) -> dict:
    if live:
        events = fetch.fetch_all()
    else:
        events = fetch.parse_feed((FIXTURES / "dsb-berlin.xml").read_text(encoding="utf-8"), "dsb-berlin")
        events += fetch.parse_feed((FIXTURES / "bsv-terminkalender.xml").read_text(encoding="utf-8"), "bsv-termin")

    cands = dd.dedup(events)
    existing = {t.id for t in models.load(DATA)} if DATA.exists() else set()
    rejected = _rejected()

    new, known = [], []
    for c in cands:
        if norm_name(c.name) in rejected:
            continue
        d = draft_mod.candidate_to_draft(c)
        d["_new"] = d["id"] not in existing
        d["_key"] = norm_name(c.name)
        (new if d["_new"] else known).append(d)
    return {"new": new, "known": known, "existing": len(existing), "live": bool(live)}


@app.post("/api/approve")
def approve(rec: dict):
    rec.pop("_new", None)
    rec.pop("_key", None)
    try:
        t = models.Tournament.from_dict(rec)
    except models.ValidationError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    ts = models.load(DATA) if DATA.exists() else []
    ts = [x for x in ts if x.id != t.id] + [t]  # replace-or-append by id
    models.save(ts, DATA)
    _rebuild()
    return {"ok": True, "id": t.id, "total": len(ts)}


class RejectBody(BaseModel):
    key: str


@app.post("/api/reject")
def reject(body: RejectBody) -> dict:
    s = _rejected()
    s.add(body.key)
    _save_rejected(s)
    return {"ok": True, "rejected": len(s)}


def main() -> None:
    import uvicorn

    print("Kuratierung läuft auf http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
