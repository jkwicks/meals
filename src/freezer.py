"""The declared freezer ledger — `dev/design-04-freezer-and-prep.md` §2.1.

`FreezerItem` is a **confirmed observation**, not an inferred count. The user
states what is in the freezer; `repository.py` persists exactly that
statement under `data/freezer.json`; nothing here or anywhere else seeds,
decrements or auto-removes a row. That is what lets the file be hand-edited
and still trusted — see `PlanRepository.load_freezer`.

A non-UI module of its own, not a class on `week.py` or `planner.py`:
`week.py` cannot import `planner` (the dependency runs the other way), and
this model needs nothing from either — only the storage-class vocabulary and
macro keys `week.py` already owns, imported here rather than re-declared.
"""

import uuid
from datetime import date as date_type
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from week import MACRO_KEYS, STORAGE_CLASSES


def _parse_iso_date(value: str, field_name: str) -> date_type:
    """`date.fromisoformat`, with the field named in the error.

    A plain `ValueError` from the stdlib parser says "Invalid isoformat
    string: '13/08/2026'" and nothing about which of `cooked_on`/`frozen_on`
    was wrong — the two are validated in the same pass, so a bare message
    leaves the reader to guess.
    """
    try:
        return date_type.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{field_name} must be an ISO date (YYYY-MM-DD), got {value!r}"
        )


class FreezerItem(BaseModel):
    """One declared lot of frozen food.

    `id` is the only thing that tells two tubs of the same dish apart — they
    agree on every other field (same label, same recipe, frozen a fortnight
    apart), so a stable generated id is the one field with no natural
    candidate. Generated on construction and never recomputed from content,
    because editing a lot's label must not turn it into a different lot (the
    opposite of `repository.recipe_content_key`, which is *supposed* to
    change when what it names changes).

    `storage_class` and `per_serving` are **freeze-time snapshots**, not a
    live read of the catalog: `recipe_id` is kept only as provenance, so a
    later edit to that catalog recipe (a corrected macro, a
    reclassification) can never retroactively change what is already in the
    freezer. Both are optional — a hand-declared item with no matching
    recipe has neither; resolving an inferred fallback or an honest visible
    zero for a lot missing its snapshot is a later change than this one.

    `cooked_on` and `frozen_on` are both required (unlike everything else
    optional here, whose absence just means "less information") because a
    missing cook date degrades to "no idea how old this is", and there is no
    conservative number this model is entitled to guess in its place — see
    `week.freezer_quality_note` for the same reasoning stated at the reader
    end. `frozen_on` may never be earlier than `cooked_on`: freezing pauses
    quality decline, it does not predate the cook.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Stable, generated on add — nothing else identifies a lot.",
    )
    label: str = Field(..., min_length=1, description="User-facing free text, e.g. 'beef massaman'.")
    portions: int = Field(..., gt=0, description="Meal portions remaining, by declaration.")
    cooked_on: str = Field(
        ..., description="Required ISO date (YYYY-MM-DD) the dish was cooked — food safety."
    )
    frozen_on: str = Field(
        ..., description="Required ISO date (YYYY-MM-DD) it went into the freezer."
    )
    storage_class: Optional[str] = Field(
        default=None,
        description=(
            "Snapshot of the dish's Recipe.storage_class at freeze time. One "
            f"of: {', '.join(STORAGE_CLASSES)}. None means nobody classified "
            "it, resolved short by week.freezer_months like any other "
            "unclassified dish."
        ),
    )
    per_serving: Optional[Dict[str, float]] = Field(
        default=None,
        description=(
            "Snapshot of MACRO_KEYS (calories, protein_g, net_carbs_g, "
            "fat_g) per portion, at freeze time. None for a hand-declared "
            "item with no macro data."
        ),
    )
    recipe_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional provenance only — a catalog entry this lot came from, "
            "if any. Never the live source of this lot's macros or class."
        ),
    )

    @field_validator("cooked_on", "frozen_on")
    @classmethod
    def valid_iso_date(cls, v: str, info: ValidationInfo) -> str:
        _parse_iso_date(v, info.field_name)
        return v

    @field_validator("storage_class")
    @classmethod
    def known_storage_class(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in STORAGE_CLASSES:
            raise ValueError(f"storage_class {v!r} is not one of {STORAGE_CLASSES}")
        return v

    @field_validator("per_serving")
    @classmethod
    def known_macro_keys(cls, v: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
        if v is None:
            return v
        unknown = sorted(set(v) - set(MACRO_KEYS))
        if unknown:
            raise ValueError(f"per_serving has unrecognised keys: {unknown}")
        negative = {key: value for key, value in v.items() if value < 0}
        if negative:
            raise ValueError(f"per_serving values must be >= 0: {negative}")
        return v

    @model_validator(mode="after")
    def frozen_not_before_cooked(self) -> "FreezerItem":
        cooked = _parse_iso_date(self.cooked_on, "cooked_on")
        frozen = _parse_iso_date(self.frozen_on, "frozen_on")
        if frozen < cooked:
            raise ValueError(
                f"frozen_on ({self.frozen_on}) cannot be before cooked_on "
                f"({self.cooked_on}) — freezing pauses quality decline, it "
                "does not predate the cook."
            )
        return self
