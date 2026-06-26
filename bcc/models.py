"""Tournament schema + validation for the Berlin Chess Calendar.

Phase-0 deliberately uses only the standard library — no pydantic — so the build
scripts run anywhere with a bare Python 3.10+ and zero install. When the FastAPI
curation tool lands (Phase 2) we move to pydantic in a venv; until then this module
is the single source of truth for what a valid tournament record looks like.

Validation happens at load/write time: a malformed record raises ValidationError
with a precise message, so a bad tag can never reach the published tournaments.json.

The six filter axes (variant, time_control, age_limit, participation, organizer,
schedule_format) are first-class fields. `kind` and the optional `rounds` list let
one record represent a multi-round series (a league season, an evening tournament)
without exploding it into one row per round.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any

# ---- controlled vocabularies (the only legal values per field) -------------

KIND = {"open", "championship", "memorial", "league", "club-series", "youth", "festival"}
VARIANT = {"standard", "chess960", "tandem", "other"}
TIME_CONTROL = {"classical", "rapid", "blitz", "mixed"}
PARTICIPATION = {"single", "duo", "team"}
SCHEDULE_FORMAT = {"block", "weekly", "biweekly", "other"}
REGION = {"berlin", "nearby", "national"}
STATUS = {"confirmed", "expected", "stale", "cancelled"}
# age_limit is a list; each entry must be one of these
AGE = {"open", "U8", "U10", "U12", "U14", "U16", "U18", "U20", "U25", "Ü60", "Ü65"}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ValidationError(ValueError):
    """Raised when a tournament record violates the schema."""


@dataclass
class Tournament:
    # identity
    id: str                       # stable slug = normalized-name + year; this is the iCal UID
    name: str                     # original (German) name, not translated
    kind: str                     # one of KIND
    start_date: str               # ISO yyyy-mm-dd; for a series = first round
    end_date: str                 # ISO yyyy-mm-dd; for a series = last round
    # six filter axes
    variant: str                  # one of VARIANT
    time_control: str             # one of TIME_CONTROL
    age_limit: list[str]          # subset of AGE, non-empty
    participation: str            # one of PARTICIPATION
    schedule_format: str          # one of SCHEDULE_FORMAT
    # placement & provenance
    region: str                   # one of REGION
    status: str                   # one of STATUS
    sources: list[str]            # non-empty list of source keys
    last_verified: str            # ISO date
    # optional / best-effort
    name_en: str | None = None
    edition: int | None = None    # parsed from "22." title prefix; drives prediction++
    rounds: list[str] | None = None   # multi-round: list of ISO dates; None = single block
    organizer: str | None = None
    venue: str | None = None
    city: str | None = None
    source_url: str | None = None
    registration_deadline: str | None = None
    registration_url: str | None = None
    prize_pool: dict[str, Any] | None = None   # {"amount": int, "currency": "EUR"}
    tagged_by: str = "human"      # human | auto
    notes: str | None = None

    # -- validation ---------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Tournament":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ValidationError(f"{d.get('id', '?')}: unknown field(s): {sorted(unknown)}")
        try:
            t = cls(**d)
        except TypeError as e:  # missing required field
            raise ValidationError(f"{d.get('id', '?')}: {e}") from None
        t.validate()
        return t

    def validate(self) -> "Tournament":
        def err(msg: str) -> None:
            raise ValidationError(f"{self.id!r}: {msg}")

        if not _SLUG.match(self.id):
            err(f"id is not a clean slug: {self.id!r}")
        if not self.name.strip():
            err("name is empty")
        _enum(self.kind, KIND, "kind", err)
        _enum(self.variant, VARIANT, "variant", err)
        _enum(self.time_control, TIME_CONTROL, "time_control", err)
        _enum(self.participation, PARTICIPATION, "participation", err)
        _enum(self.schedule_format, SCHEDULE_FORMAT, "schedule_format", err)
        _enum(self.region, REGION, "region", err)
        _enum(self.status, STATUS, "status", err)

        if not isinstance(self.age_limit, list) or not self.age_limit:
            err("age_limit must be a non-empty list")
        for a in self.age_limit:
            if a not in AGE:
                err(f"age_limit has invalid value {a!r} (allowed: {sorted(AGE)})")

        if not isinstance(self.sources, list) or not self.sources:
            err("sources must be a non-empty list")

        sd = _parse_date(self.start_date, "start_date", err)
        ed = _parse_date(self.end_date, "end_date", err)
        if ed < sd:
            err(f"end_date {self.end_date} is before start_date {self.start_date}")
        _parse_date(self.last_verified, "last_verified", err)

        if self.rounds is not None:
            if not isinstance(self.rounds, list) or not self.rounds:
                err("rounds, if present, must be a non-empty list of ISO dates")
            prev = None
            for r in self.rounds:
                rd = _parse_date(r, "rounds[]", err)
                if prev and rd < prev:
                    err(f"rounds are not in ascending order near {r}")
                prev = rd

        if self.registration_deadline is not None:
            _parse_date(self.registration_deadline, "registration_deadline", err)
        if self.edition is not None and (not isinstance(self.edition, int) or self.edition < 1):
            err(f"edition must be a positive int, got {self.edition!r}")
        if self.prize_pool is not None:
            if "amount" not in self.prize_pool or "currency" not in self.prize_pool:
                err("prize_pool needs 'amount' and 'currency'")
        if self.tagged_by not in {"human", "auto"}:
            err(f"tagged_by must be 'human' or 'auto', got {self.tagged_by!r}")
        return self

    def to_dict(self) -> dict[str, Any]:
        # drop None/optional defaults for a clean, diffable JSON
        out = {}
        for k, v in asdict(self).items():
            if v is None:
                continue
            if k == "tagged_by" and v == "human":
                continue
            out[k] = v
        return out


def _enum(value: str, allowed: set[str], field_name: str, err) -> None:
    if value not in allowed:
        err(f"{field_name}={value!r} not in {sorted(allowed)}")


def _parse_date(value: str, field_name: str, err) -> date:
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        err(f"{field_name} must be ISO yyyy-mm-dd, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        err(f"{field_name} is not a real date: {value!r}")
        raise  # unreachable, keeps type-checkers happy


# ---- slug + IO -------------------------------------------------------------

_ORDINAL = re.compile(r"\b\d{1,3}\.\s*")          # "22. " German ordinal
_ROMAN = re.compile(r"\b[IVXLC]+\.\s*")            # "IX. "
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def slugify(name: str, year: int | str) -> str:
    """Deterministic slug = normalized name + year. Stable across runs (it is the iCal UID).

    Strips German ordinals ('22.'), Roman numerals ('IX.'), and 4-digit years from the
    name, lowercases, and reduces to [a-z0-9-]. Collisions (A-Open/B-Open) are resolved by
    the caller appending the section; this function is pure name->slug.
    """
    s = name
    s = _ORDINAL.sub("", s)
    s = _ROMAN.sub("", s)
    s = _YEAR.sub("", s)
    s = s.lower()
    # German umlaut transliteration so slugs stay ascii
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-+", "-", s)
    return f"{s}-{year}"


def load(path: str | Path) -> list[Tournament]:
    """Load + validate tournaments.json. Raises ValidationError on the first bad record."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValidationError("tournaments.json must be a JSON array")
    out: list[Tournament] = []
    seen: set[str] = set()
    for i, rec in enumerate(data):
        t = Tournament.from_dict(rec)
        if t.id in seen:
            raise ValidationError(f"duplicate id {t.id!r} at index {i}")
        seen.add(t.id)
        out.append(t)
    return out
