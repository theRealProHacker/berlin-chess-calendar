"""Tournament schema + validation — the single source of truth.

Pydantic v2 model (the same engine FastAPI uses for the curation server). The public
API is unchanged from the Phase-0 stdlib version — Tournament.from_dict / .to_dict /
models.load / slugify, the vocab sets (REGION, KIND, ...), and string ISO dates — so
the build scripts, the .ics generator, and the tests keep working untouched.

Validation happens at construction time: a malformed record raises ValidationError
(our ValueError subclass), so a bad tag can never reach the published tournaments.json.

The six filter axes (variant, time_control, age_limit, participation, organizer,
schedule_format) are first-class. `kind` + the optional `rounds` list let one record
represent a multi-round series without exploding it into one row per round.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import ValidationError as _PydanticError


class ValidationError(ValueError):
    """Raised when a tournament record violates the schema."""


# ---- controlled vocabularies (Literal = automatic validation; sets for membership) ----

KindT = Literal["open", "championship", "memorial", "league", "club-series", "youth", "festival"]
VariantT = Literal["standard", "chess960", "tandem", "other"]
TimeControlT = Literal["classical", "rapid", "blitz", "mixed"]
ParticipationT = Literal["single", "duo", "team"]
ScheduleFormatT = Literal["block", "weekly", "biweekly", "other"]
RegionT = Literal["berlin", "nearby", "national"]
StatusT = Literal["confirmed", "expected", "stale", "cancelled"]
AgeT = Literal["open", "U8", "U10", "U12", "U14", "U16", "U18", "U20", "U25", "Ü60", "Ü65"]

KIND = set(get_args(KindT))
VARIANT = set(get_args(VariantT))
TIME_CONTROL = set(get_args(TimeControlT))
PARTICIPATION = set(get_args(ParticipationT))
SCHEDULE_FORMAT = set(get_args(ScheduleFormatT))
REGION = set(get_args(RegionT))
STATUS = set(get_args(StatusT))
AGE = set(get_args(AgeT))

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class Tournament(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields rejected

    # identity
    id: str
    name: str
    kind: KindT
    start_date: str
    end_date: str
    # six filter axes
    variant: VariantT
    time_control: TimeControlT
    age_limit: list[AgeT]
    participation: ParticipationT
    schedule_format: ScheduleFormatT
    # placement & provenance
    region: RegionT
    status: StatusT
    sources: list[str]
    last_verified: str
    # optional / best-effort
    name_en: str | None = None
    edition: int | None = None
    rounds: list[str] | None = None
    organizer: str | None = None
    venue: str | None = None
    city: str | None = None
    source_url: str | None = None
    registration_deadline: str | None = None
    registration_url: str | None = None
    prize_pool: dict[str, Any] | None = None
    tagged_by: Literal["human", "auto"] = "human"
    notes: str | None = None

    # -- field validators ---------------------------------------------------

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError(f"id is not a clean slug: {v!r}")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name is empty")
        return v

    @field_validator("age_limit", "sources")
    @classmethod
    def _nonempty(cls, v: list) -> list:
        if not v:
            raise ValueError("must be a non-empty list")
        return v

    @field_validator("start_date", "end_date", "last_verified", "registration_deadline")
    @classmethod
    def _isodate(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _ISO.match(v):
            raise ValueError(f"must be ISO yyyy-mm-dd, got {v!r}")
        date.fromisoformat(v)  # raises on an impossible date
        return v

    @field_validator("edition")
    @classmethod
    def _edition(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError(f"edition must be a positive int, got {v!r}")
        return v

    @field_validator("rounds")
    @classmethod
    def _rounds(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("rounds, if present, must be a non-empty list of ISO dates")
        prev = None
        for r in v:
            if not _ISO.match(r):
                raise ValueError(f"round date not ISO: {r!r}")
            d = date.fromisoformat(r)
            if prev and d < prev:
                raise ValueError(f"rounds are not ascending near {r}")
            prev = d
        return v

    @field_validator("prize_pool")
    @classmethod
    def _prize(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if "amount" not in v or "currency" not in v:
            raise ValueError("prize_pool needs 'amount' and 'currency'")
        return v

    @model_validator(mode="after")
    def _order(self) -> "Tournament":
        if date.fromisoformat(self.end_date) < date.fromisoformat(self.start_date):
            raise ValueError(f"end_date {self.end_date} is before start_date {self.start_date}")
        return self

    # -- IO -----------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Tournament":
        try:
            return cls.model_validate(d)
        except _PydanticError as e:
            raise ValidationError(str(e)) from None

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json", exclude_none=True)
        if d.get("tagged_by") == "human":
            d.pop("tagged_by", None)
        return d


# ---- slug + IO (unchanged) -------------------------------------------------

_ORDINAL = re.compile(r"\b\d{1,3}\.\s*")
_ROMAN = re.compile(r"\b[IVXLC]+\.\s*")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def slugify(name: str, year: int | str) -> str:
    """Deterministic slug = normalized name + year. Stable across runs (it is the iCal UID)."""
    s = name
    s = _ORDINAL.sub("", s)
    s = _ROMAN.sub("", s)
    s = _YEAR.sub("", s)
    s = s.lower()
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


def save(tournaments: list[Tournament], path: str | Path) -> None:
    """Write tournaments.json sorted by start_date (stable, diffable)."""
    items = sorted(tournaments, key=lambda t: (t.start_date, t.id))
    payload = json.dumps([t.to_dict() for t in items], ensure_ascii=False, indent=2)
    Path(path).write_text(payload + "\n", encoding="utf-8")
