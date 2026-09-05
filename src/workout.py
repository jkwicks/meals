"""Structured workout output — `dev/design-06-exercise-planning.md` §4.

`WorkoutPlan`/`WorkoutSession`/`ExercisePrescription` are a different
lifecycle and shape from `training_schedule` (day/time/type/duration/burn,
owned by `schedule.json`) and from `adherence.json` (whether a declared
session happened). This module is the third store design-06's overview
names: the schedule says *when*, adherence says *whether*, and this says
*what* — the generated exercise detail for a gym session, kept nowhere
else. `src/repository.py` persists it under `data/workout_plans.json`;
nothing here opens a file.

A non-UI module of its own, mirroring `freezer.py`: `planner.py` owns
`MovementConstraint`/`GymProgram` (design-06 §2) and this needs their
controlled vocabularies (a generated exercise's `movement_pattern` is
judged against the same list a personal constraint's `target` and a gym
program's `movement_patterns` already share, and `progression_rule` is the
program's own declared method) — imported here rather than re-declared, the
same one-way dependency `freezer.py` takes on `week.py`. `planner.py` never
imports this module, so the direction is safe.

Deliberately model-only: the deterministic constraint validator (design-06
§5 — normalizing titles, rejecting an `exclude` match, requiring a `modify`
instruction to travel with the exercise) and the generation call are a
later subtask (5.1b). This module only shapes what a stored exercise *can*
say, exactly as `MovementConstraint`'s docstring says about its own fields.
"""

from datetime import date as date_type
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from planner import GymProgram, MovementPattern

# The program's own declared progression method (design-06 §2.2, §6) is the
# vocabulary a generated exercise's `progression_rule` must agree with — read
# directly off `GymProgram`'s live field rather than hand-copied into a
# second `Literal["double_progression", "two_for_two"]`, which is exactly
# the drift `MovementPattern`/`MOVEMENT_PATTERNS` was split out to prevent
# for the pattern vocabulary. If `GymProgram.progression` ever grows a third
# method, this picks it up with no edit here.
ProgressionMethod = GymProgram.model_fields["progression"].annotation


def _parse_iso_date(value: str, field_name: str) -> date_type:
    """`date.fromisoformat`, with the field named in the error — same
    reasoning as `freezer._parse_iso_date`: a bare stdlib message doesn't
    say which of several date fields on a session/plan was wrong."""
    try:
        return date_type.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{field_name} must be an ISO date (YYYY-MM-DD), got {value!r}"
        )


class ExercisePrescription(BaseModel):
    """One exercise inside a generated `WorkoutSession` — design-06 §4.

    `movement_pattern` is typed directly as `planner.MovementPattern`, the
    same controlled vocabulary a personal constraint's `target` (when
    `scope="movement_pattern"`) and a gym program's `movement_patterns`
    already draw from — design-06 §5 step 1 ("validate its controlled
    movement_pattern") is a *generation-output* check, but there is no
    reason to accept an uncontrolled value here only to reject it later;
    Pydantic enforces the literal at construction the same way it already
    does for `GymProgram.movement_patterns`.

    `target_load_kg` is the one field design-06 §4 marks nullable
    (`target_load_kg | null`) and explains why: it is populated only from
    an unambiguous exercise-history match, and null otherwise, with the
    instruction to select a load that reaches `target_rir` — "the model
    must not invent a kilogram figure for a person it has never observed
    lifting that exercise." This model can only shape *that* a missing
    match is representable as `None` rather than some fabricated default;
    it cannot prove a non-null value really came from a real match rather
    than a guess dressed as one — that is the generation/validation path's
    job (5.1b), the same limit `MovementConstraint`'s docstring states for
    which action requires which field.

    `applied_constraint_ids` and `execution_notes` are what design-06 §5's
    validator (5.1b) will cross-check against a matching `modify`
    constraint's `instruction` — every id referenced here must exist on the
    exercise it names, and (once a required modification applies) its
    instruction text must appear in `execution_notes`. This model only
    shapes the two fields as an id list and free text; it is deliberately
    silent on whether they agree with each other or with any config, since
    it has no config to check against and design-06 §5 says the model
    prompt is never the validator.

    `progression_rule` is `planner`'s `GymProgram.progression` vocabulary,
    not a free-form description: design-06 §6 implements exactly two
    methods (`double_progression`, `two_for_two`), and a per-exercise
    proposal (5.1c) needs to know which one it is progressing under without
    re-reading the program the exercise was generated from — the program's
    catalog entry can change or be swapped out from under an already-stored
    plan.
    """

    model_config = ConfigDict(extra="forbid")

    exercise_id: str = Field(
        ..., min_length=1,
        description="Stable identity for this exercise, matched by normalized "
        "exact title against personal constraints (design-06 §5) — that "
        "normalization is the validator's job, not this model's.",
    )
    name: str = Field(..., min_length=1)
    movement_pattern: MovementPattern
    role: Literal["power", "compound", "accessory", "carry", "core"]
    sets: int = Field(..., ge=1, le=20)
    rep_min: int = Field(..., ge=1, le=100)
    rep_max: int = Field(..., ge=1, le=100)
    target_load_kg: Optional[float] = Field(
        default=None,
        ge=0,
        description="Populated only from an unambiguous exercise-history "
        "match; null means no match, never an invented starting weight "
        "(design-06 §4).",
    )
    target_rir: int = Field(..., ge=0, le=5)
    rest_seconds: int = Field(..., ge=0, le=1800)
    execution_notes: str = Field(
        default="",
        description="Form cues and, when a modify-class constraint applies, "
        "that constraint's required instruction text (design-06 §5).",
    )
    applied_constraint_ids: List[str] = Field(default_factory=list)
    progression_rule: ProgressionMethod

    @model_validator(mode="after")
    def rep_range_is_ordered(self) -> "ExercisePrescription":
        if self.rep_max < self.rep_min:
            raise ValueError(
                f"rep_max ({self.rep_max}) must be >= rep_min ({self.rep_min}) "
                f"for exercise {self.exercise_id!r}."
            )
        return self


class WorkoutSession(BaseModel):
    """One gym session's generated detail — design-06 §4.

    `date` is a real calendar date, not a weekday name: `training_schedule`
    indexes its declared sessions by bare weekday (`session.get("day")`,
    e.g. `"Monday"`), which is exactly right for a *standing* week that
    repeats, and exactly wrong here — a generated plan belongs to one
    specific occurrence of that weekday, the same reason `WeekPlan` carries
    a real `week_start_date` rather than trusting `days[0]` alone.

    `session_id` plays the same role `slot_id`/`WorkoutCompletion.session_id`
    already play — design-06 §4's own schema comment calls it "the same
    identity vocabulary as schedule/adherence." This model does not
    recompute or validate it against `planner.workout_session_id`: it has
    no `time`/`type` fields of its own to check it against (nor does
    `WorkoutCompletion`, for the same reason), so agreeing on the spelling
    is the generation path's job, exactly as it already is for adherence.
    """

    model_config = ConfigDict(extra="forbid")

    date: str
    session_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    planned_duration_minutes: int = Field(..., ge=0, le=600)
    exercises: List[ExercisePrescription] = Field(default_factory=list)

    @field_validator("date")
    @classmethod
    def valid_iso_date(cls, v: str, info: ValidationInfo) -> str:
        _parse_iso_date(v, info.field_name)
        return v


class WorkoutPlan(BaseModel):
    """The week's generated exercise detail — design-06 §4.

    Stored under `data/workout_plans.json` (`src/repository.py`'s
    `load_workout_plan`/`save_workout_plan`), keyed by `week_identifier`
    ("current"/"next") the same way `WeekPlan` is — not by weekday, for the
    same reason `WorkoutSession.date` is a real date rather than a name.
    Absent means schedule-only behaviour: the declared session still shows
    with no generated detail, the same tolerance a missing `week_plan.json`
    already gets from every caller of `load_week_plan`.

    `gym_program` is the catalog id (`config["active_gym_program"]`) this
    plan was generated under, the `WeekPlan.preset` precedent applied here:
    "did that change work?" needs to know which program a stored plan
    reflects, not just what the catalog says today — a program's catalog
    entry can be edited or the active pick changed after a plan already
    exists.
    """

    model_config = ConfigDict(extra="forbid")

    week_start_date: str
    gym_program: str = Field(..., min_length=1)
    sessions: List[WorkoutSession] = Field(default_factory=list)

    @field_validator("week_start_date")
    @classmethod
    def valid_iso_date(cls, v: str, info: ValidationInfo) -> str:
        _parse_iso_date(v, info.field_name)
        return v

    @model_validator(mode="after")
    def session_ids_are_unique(self) -> "WorkoutPlan":
        seen = set()
        duplicates = set()
        for session in self.sessions:
            if session.session_id in seen:
                duplicates.add(session.session_id)
            seen.add(session.session_id)
        if duplicates:
            raise ValueError(
                f"WorkoutPlan.sessions session_ids must be unique; "
                f"duplicated: {sorted(duplicates)}."
            )
        return self
