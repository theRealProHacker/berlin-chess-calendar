"""Add / edit tournament records deterministically. Stdlib only.

    python -m bcc.add skeleton "<name>" <year>     # print a fill-in record + the enum vocab
    python -m bcc.add insert <file|->              # validate + insert a candidate (sorted)
    python -m bcc.add set <id> field=value ...     # validated field update (promote / correct)
    python -m bcc.add fmt                          # re-normalize the file to the compact format

The schema, validation, and slug live in bcc.build — this module only reuses them. Every
write goes through validate(): a record that fails the schema never reaches the file (fail
loud, change nothing). The file is rewritten in the repo's compact hand-format (inline
arrays + inline prize_pool) so diffs stay minimal. Nothing here pushes — the output is a
working-tree diff a human reviews and commits.

The agent does research + judgment; this tool does the deterministic data edit. `set` is
the one knob that serves BOTH reviewer-corrections AND prediction->confirmed promotions.

    PIPELINE
    skeleton ──► (agent fills) ──► insert ──► build + tests ──► reviewer ──► set (corrections)
                                     │                                         │
                                     └──────────── validate()-or-refuse ───────┘
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from .build import (AGE, ENUMS, OPTIONAL, REQUIRED, ROOT, ValidationError,
                    load, slugify, validate)

DATA = ROOT / "data" / "tournaments.json"


# ---- compact writer --------------------------------------------------------
# Matches the committed hand-format so insert/set produce minimal diffs:
#   - one record per block, one field per line (4-space indent)
#   - arrays inline:        "age_limit": ["open"]   /  ["dsb-berlin", "bsv-termin"]
#   - prize_pool inline, padded inside the braces:  { "amount": 3100, "currency": "EUR" }
# json.dumps' default separators (", " / ": ") already match arrays/scalars; only objects
# need the extra inner padding, hence the dict special-case in _inline.

def _inline(v) -> str:
    if isinstance(v, dict):
        inner = ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(val, ensure_ascii=False)}"
                          for k, val in v.items())
        return "{ " + inner + " }"
    return json.dumps(v, ensure_ascii=False)


def dumps(records) -> str:
    """Serialize the tournament list in the repo's compact hand-format (key order preserved)."""
    blocks = []
    for r in records:
        lines = [f"    {json.dumps(k, ensure_ascii=False)}: {_inline(v)}" for k, v in r.items()]
        blocks.append("  {\n" + ",\n".join(lines) + "\n  }")
    return "[\n" + ",\n".join(blocks) + "\n]\n"


def _write(records) -> None:
    """Atomic rewrite: a crash mid-write can't leave a half-written file."""
    p = Path(DATA)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(dumps(records), encoding="utf-8")
    os.replace(tmp, p)


def _sorted(records):
    return sorted(records, key=lambda r: (r["start_date"], r["id"]))


# ---- commands --------------------------------------------------------------

def skeleton(name: str, year) -> dict:
    """A fill-in record: every REQUIRED key + tagged_by=auto, blank dates/region/sources.

    Intentionally NOT validate()-clean — the agent fills the blanks before insert.
    """
    return {
        "id": slugify(name, year), "name": name, "kind": "open",
        "start_date": "", "end_date": "", "variant": "standard",
        "time_control": "classical", "age_limit": ["open"], "participation": "single",
        "schedule_format": "block", "region": "", "status": "expected",
        "sources": [], "last_verified": date.today().isoformat(), "tagged_by": "auto",
    }


def cmd_skeleton(name: str, year: str) -> None:
    """Print a fill-in record (stdout) + the enum vocabulary (stderr)."""
    print(json.dumps(skeleton(name, year), ensure_ascii=False, indent=2))
    print("\n# vocabulary (fill fields using ONLY these values):", file=sys.stderr)
    for k, vs in ENUMS.items():
        print(f"#   {k}: {sorted(vs)}", file=sys.stderr)
    print(f"#   age_limit items (AGE): {sorted(AGE)}", file=sys.stderr)
    print(f"# REQUIRED: {REQUIRED}", file=sys.stderr)
    print(f"# OPTIONAL: {sorted(OPTIONAL)}", file=sys.stderr)
    print("# Fill start_date/end_date (ISO), region, and sources (non-empty); "
          "status=confirmed only with real announced dates.", file=sys.stderr)


def _read_candidate(src: str) -> dict:
    raw = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    return json.loads(raw)


def cmd_insert(src: str) -> None:
    cand = _read_candidate(src)
    cands = cand if isinstance(cand, list) else [cand]   # accept one record OR a reviewed batch
    if not cands:
        sys.exit("nothing to insert")
    for c in cands:
        validate(c)                      # raises ValidationError on a bad record -> no write
    records = load(DATA)                  # validates the whole file + rejects dup ids
    seen = {r["id"] for r in records}
    for c in cands:                      # reject dups vs the file AND within the batch
        if c["id"] in seen:
            sys.exit(f"duplicate id {c['id']!r} already present (no changes written)")
        seen.add(c["id"])
    records = _sorted(records + cands)   # insert at the ascending (start_date, id) positions
    _write(records)
    load(DATA)                           # re-confirm the file still loads clean
    print(f"inserted {', '.join(c['id'] for c in cands)}; now {len(records)} records")


def _parse_pair(pair: str):
    """field=value, split on the FIRST '='. Value is JSON if it parses, else a plain string."""
    if "=" not in pair:
        sys.exit(f"expected field=value, got {pair!r}")
    field, _, raw = pair.partition("=")
    try:
        return field.strip(), json.loads(raw)
    except json.JSONDecodeError:
        return field.strip(), raw


def cmd_set(rid: str, pairs: list[str]) -> None:
    records = load(DATA)
    rec = next((r for r in records if r["id"] == rid), None)
    if rec is None:
        sys.exit(f"no record with id {rid!r}")
    old_start = rec["start_date"]
    for pair in pairs:
        field, val = _parse_pair(pair)
        rec[field] = val
    rec["last_verified"] = date.today().isoformat()  # a `set` is a re-verification
    for r in records:                                # re-validate whole file; refuse on any failure
        validate(r)
    if rec["start_date"] != old_start:               # keep the file ascending for clean diffs
        records = _sorted(records)
    _write(records)
    load(DATA)
    print(f"updated {rid!r}: {', '.join(pairs)}")


def cmd_fmt() -> None:
    """Re-normalize the file to the compact format (no reorder). No-op if already clean."""
    records = load(DATA)
    before = DATA.read_text(encoding="utf-8")
    _write(records)
    after = DATA.read_text(encoding="utf-8")
    print("reformatted" if before != after else "already in canonical format")


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.exit(__doc__)
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "skeleton":
            if len(rest) != 2:
                sys.exit('usage: skeleton "<name>" <year>')
            cmd_skeleton(rest[0], rest[1])
        elif cmd == "insert":
            if len(rest) != 1:
                sys.exit("usage: insert <file|->")
            cmd_insert(rest[0])
        elif cmd == "set":
            if len(rest) < 2:
                sys.exit("usage: set <id> field=value [field=value ...]")
            cmd_set(rest[0], rest[1:])
        elif cmd == "fmt":
            cmd_fmt()
        else:
            sys.exit(f"unknown command {cmd!r}\n{__doc__}")
    except ValidationError as e:
        sys.exit(f"validation failed (no changes written): {e}")


if __name__ == "__main__":
    main()
