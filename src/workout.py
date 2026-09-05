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

5.1b adds the two pieces the paragraph above used to defer: `constraint_violations`
(design-06 §5's deterministic validator — normalizing titles, rejecting an
`exclude` match, requiring a `modify` instruction to travel with the
exercise) and `generate_workout_week` (design-06 §4's one structured weekly
call). Both live here, not in `planner.py`, because the import direction
only runs one way — this module already imports `GymProgram`/
`MovementConstraint`/`MovementPattern`/`TrainingProfile` from `planner.py`,
and `planner.py` never imports this module, so a generation function that
needs `WorkoutSession`/`ExercisePrescription` in scope has to sit on this
side of that line, not the other. `generate_workout_week` imports the
client/model/logging helpers it needs from `planner.py` instead (`build_client`,
`resolve_planner_model`, `reasoning_extra_body`, `log_completion`, `logger`),
the same one-way borrowing this module already does for the config types.
"""

import time
from datetime import date as date_type
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from planner import (
    FREE_MODEL_MAX_TOKENS,
    PAID_MODEL_MAX_TOKENS,
    WORKOUT_BREAKFAST_TYPES,
    GymProgram,
    MovementConstraint,
    MovementPattern,
    TrainingProfile,
    build_client,
    day_date,
    humanize,
    is_free_model,
    log_completion,
    logger,
    reasoning_extra_body,
    resolve_planner_model,
    week_days,
)

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


# ---------------------------------------------------------------------------
# design-06 §5: the shared, deterministic constraint validator
#
# One pure function, called from three places: `WeeklyWorkoutRecipes`'s own
# `model_validator` below (full generation, and single-session regeneration
# once 5.1c reuses the same response model for one session), and
# `validate_workout_plan` (storage validation — the last check a freshly
# generated plan passes before it is worth handing to
# `repository.save_workout_plan`). An edit (5.1c/5.1d) calls it the same way,
# over whatever single exercise changed. None of the three may drift, which
# is why the check itself is not a method on any of them.
# ---------------------------------------------------------------------------


def _normalized_exercise_title(value: str) -> str:
    """Case/whitespace-insensitive exact-title matching — design-06 §2.1/§5:
    "normalized exact-title matching... no fuzzy matching, no medical
    ontology." Folding case and collapsing internal whitespace *is* the
    normalization version one asks for; anything looser (stemming, partial
    matches) would be exactly the fuzzy matching it rules out.
    """
    return " ".join(value.strip().lower().split())


def _matching_personal_constraints(
    exercise: ExercisePrescription, constraints: List[MovementConstraint]
) -> List[MovementConstraint]:
    """Which personal constraints bind this exercise — design-06 §5 steps
    1-2. An `exercise`-scoped constraint matches by normalized exact title;
    a `movement_pattern`-scoped one matches by the same controlled
    vocabulary `exercise.movement_pattern` is already typed against, so no
    normalization is needed on that side — a `MovementPattern` literal is
    canonical by construction.
    """
    title = _normalized_exercise_title(exercise.name)
    return [
        constraint
        for constraint in constraints
        if (
            constraint.scope == "exercise"
            and _normalized_exercise_title(constraint.target) == title
        )
        or (
            constraint.scope == "movement_pattern"
            and constraint.target == exercise.movement_pattern
        )
    ]


def constraint_violations(
    exercises: List[ExercisePrescription], constraints: List[MovementConstraint]
) -> List[str]:
    """design-06 §5's deterministic validator. Pure: no I/O, no randomness,
    no clock, and no knowledge of instructor or the prompt that produced
    `exercises` — "the model prompt is never the validator" (design-06 §5).
    Returns one human-readable message per problem found; empty means every
    exercise satisfies every constraint that applies to it.

    Two of §5's six steps need no code here because they are already
    guaranteed by construction, not by a check this function performs:
    step 1's "controlled movement_pattern" is
    `ExercisePrescription.movement_pattern`'s own `Literal` type (an
    unrecognised pattern fails at construction, before this function ever
    runs), and "every stored exercise has a known pattern and a typed dose"
    is that same guarantee extended to `sets`/`rep_min`/`rep_max`/
    `target_rir`/`rest_seconds`. Step 5 (ranking `preferred_variations`) is
    prompt-only guidance design-06 explicitly says must never reject a valid
    plan, so it has no runtime check either.

    What remains, per exercise:

    - an `exclude` match (step 3) is always a violation — the exercise
      should never have been proposed at all, in any form;
    - a `modify` match (step 4) requires its id in `applied_constraint_ids`
      and, when the constraint states one, its `instruction` verbatim
      inside `execution_notes`;
    - step 6 — "reject progression text that changes a constrained range of
      motion" — has no separate field to check: nothing on
      `ExercisePrescription` represents a range of motion numerically, so
      the only way a regenerated or edited exercise could describe
      progressing one is by no longer carrying the fixed instruction its
      `modify` constraint requires. The `modify` check above **is** that
      check, re-run on every call this validator serves (full generation,
      regeneration, an edit, storage validation) — there is deliberately no
      second, textual "does this describe increasing depth" scan, which
      would be exactly the fuzzy biomechanical inference design-06 §5/§12
      rules out ("it cannot prove biomechanics from prose").
    """
    violations: List[str] = []
    for exercise in exercises:
        for constraint in _matching_personal_constraints(exercise, constraints):
            if constraint.action == "exclude":
                violations.append(
                    f"{exercise.exercise_id} ({exercise.name!r}) is excluded by "
                    f"personal constraint {constraint.id!r} and must not be "
                    "prescribed at all, in any form."
                )
            elif constraint.action == "modify":
                if constraint.id not in exercise.applied_constraint_ids:
                    violations.append(
                        f"{exercise.exercise_id} ({exercise.name!r}) matches "
                        f"personal constraint {constraint.id!r}, a required "
                        "modification, but does not list it in "
                        "applied_constraint_ids."
                    )
                if (
                    constraint.instruction
                    and constraint.instruction not in exercise.execution_notes
                ):
                    violations.append(
                        f"{exercise.exercise_id} ({exercise.name!r}) matches "
                        f"personal constraint {constraint.id!r} but its "
                        "execution_notes do not include the required "
                        f"instruction verbatim: {constraint.instruction!r}. A "
                        "constrained range of motion is fixed, not a "
                        "progression target, and the instruction must travel "
                        "with every prescription of this movement however it "
                        "was generated or edited."
                    )
    return violations


def validate_workout_plan(
    plan: WorkoutPlan, constraints: List[MovementConstraint]
) -> List[str]:
    """`constraint_violations` applied across a whole stored/generated plan —
    the "storage validation" design-06 §5 names alongside full generation,
    single-session regeneration and edits. Empty means every session's every
    exercise satisfies every constraint; a non-empty result names the
    session id with each message so a caller can report which session needs
    correcting rather than only that something, somewhere, does.
    """
    return [
        f"{session.session_id}: {message}"
        for session in plan.sessions
        for message in constraint_violations(session.exercises, constraints)
    ]


# ---------------------------------------------------------------------------
# design-06 §4: the one structured weekly generation call
# ---------------------------------------------------------------------------

# `training_schedule` entries are typed loosely (`planner.AppConfig` keeps
# the shape open — see CLAUDE.md's "config/ is eight files") so a session's
# own `type` string is the only signal for "this is a gym/resistance
# session." `morning_training_days` already treats a `type` starting with
# "gym" as exactly that signal (`WORKOUT_BREAKFAST_TYPES`); reused verbatim
# here, under a name that reads correctly on this axis, so a session judged
# a gym day for breakfast-pinning purposes can never silently disagree with
# one judged a gym day for workout generation.
GYM_SESSION_TYPES = WORKOUT_BREAKFAST_TYPES

# design-06 §3's precedence, stated verbatim in every call's prompt — soft
# guidance, not enforcement (`constraint_violations` is the enforcement).
PRECEDENCE_RULE = (
    "- Precedence when a personal constraint and the program disagree: an "
    "excluded exercise/pattern always wins over the program's own choice; a "
    "required modification always travels with the exercise even where the "
    "program would otherwise vary it; a preferred variation is used only "
    "where it does not conflict with either of those (personal exclude > "
    "personal modify > personal prefer > program preference).\n"
)

# design-06 §5/§6: a constrained range of motion is a fixed fact, never a
# progression variable. Stated in the prompt so the model is told the exact
# rule `constraint_violations` judges it against — the
# `build_long_cook_day_rule` lesson: a rejection for a rule never stated
# burns a retry to discover what one sentence would have said.
FIXED_RANGE_RULE = (
    "- A modification that fixes a range of motion (for example, a squat "
    "limited to a pain-free partial depth) is a permanent fact about this "
    "person, not a progression target: progress reps and then load within "
    "that fixed range, and never describe or plan progression by increasing "
    "the range itself.\n"
)


def build_gym_program_rule(program: GymProgram) -> str:
    """The selected program's own numbers, stated once for the whole call —
    design-06 §2.2's content, not a second calendar. Naming the rep ranges,
    RIR and progression method here is the same "tell the model the rule it
    is judged against" reasoning `build_long_cook_day_rule` already uses on
    the meal axis — `ExercisePrescription`'s own dosage fields are what a
    response is judged against, via ordinary schema bounds, not a
    `constraint_violations` check.
    """
    patterns = ", ".join(program.movement_patterns)
    return (
        f"Program: {program.label} ({humanize(program.architecture)} "
        f"architecture, primary goal: {humanize(program.primary_goal)}).\n"
        f"- {program.working_sets} working sets per exercise.\n"
        f"- Compound lifts: {program.compound_rep_range[0]}-"
        f"{program.compound_rep_range[1]} reps; accessories: "
        f"{program.accessory_rep_range[0]}-{program.accessory_rep_range[1]} "
        f"reps; target RIR {program.target_rir} on every exercise.\n"
        f"- progression_rule must be {program.progression!r} on every "
        "exercise — the program's own declared method; do not use a "
        "different one.\n"
        + (
            "- Include one power-focused exercise this week.\n"
            if program.include_power
            else ""
        )
        + (
            "- Distribute these movement patterns across the week's "
            "sessions so a pattern gets real recovery between repeats, and "
            f"vary exercise selection session to session rather than "
            f"repeating one verbatim: {patterns}.\n"
            if patterns
            else ""
        )
        + (f"- Program notes: {program.notes}\n" if program.notes else "")
    )


def build_movement_constraints_rule(constraints: List[MovementConstraint]) -> str:
    """Every personal constraint, stated verbatim, plus the precedence and
    fixed-range rules design-06 §3/§5 require in the prompt.

    Emits nothing when there are no constraints — the same "say nothing
    when there's nothing to say" convention every optional clause in
    `planner.build_generation_rules` follows, so an installation with an
    empty `training_profile` gets a call this feature does not change.

    This is guidance, not enforcement: `constraint_violations` is what
    actually rejects a response that ignores it.
    """
    if not constraints:
        return ""
    lines = []
    for constraint in constraints:
        subject = (
            f"the {constraint.target} movement pattern"
            if constraint.scope == "movement_pattern"
            else f'the exact exercise "{constraint.target}"'
        )
        if constraint.action == "exclude":
            lines.append(
                f"- NEVER prescribe {subject} (personal constraint "
                f"{constraint.id!r})."
            )
        elif constraint.action == "modify":
            instruction = constraint.instruction or "(no instruction recorded)"
            lines.append(
                f"- {subject[0].upper()}{subject[1:]} may be prescribed only "
                "with this exact instruction in execution_notes, and "
                f"{constraint.id!r} listed in applied_constraint_ids: "
                f"{instruction!r}"
            )
        elif constraint.action == "prefer" and constraint.preferred_variations:
            lines.append(
                f"- For {subject}, prefer one of: "
                f"{', '.join(constraint.preferred_variations)}, where it "
                "fits the session — a plan is not invalid if none of these "
                "can be used."
            )
    return (
        "Personal training constraints — must always be honoured, whatever "
        "the program or week says:\n" + "\n".join(lines) + "\n"
        + PRECEDENCE_RULE
        + FIXED_RANGE_RULE
    )


def build_equipment_rule(available_equipment: List[str]) -> str:
    """Empty when nothing is declared — an installation with no
    `available_equipment` gets a call unconstrained by this clause, the same
    absent-means-unchanged convention every optional rule here follows.
    """
    if not available_equipment:
        return ""
    return (
        "- Available equipment only — do not prescribe anything outside "
        f"this list: {', '.join(available_equipment)}.\n"
    )


def build_exercise_history_rule(recent_history: Optional[List[dict]]) -> str:
    """Recent completed-set evidence, when there is any to state.

    Always empty today: Hevy strength history (design-06 §6, Task 2.3) has
    not landed, so nothing yet calls `generate_workout_week` with real rows
    — but the call accepts the parameter now rather than needing a second
    signature change once it does. Emits nothing when absent, the same
    convention every optional clause here follows.
    """
    if not recent_history:
        return ""
    lines = "\n".join(f"  * {entry}" for entry in recent_history)
    return (
        "- Recent completed-set history for these exercises (context for "
        "the program's own progression method; it is evidence, not an "
        "instruction to change anything else):\n" + lines + "\n"
    )


def workout_plan_session_id(session_date: str, time_of_day: str, session_type: str) -> str:
    """A `WorkoutSession.session_id` unique across one `WorkoutPlan`.

    Deliberately not `planner.workout_session_id` (`"<time>:<type>"`): that
    identity is fine for `WorkoutCompletion`, which already carries its own
    `date` field alongside it as the other half of a `(date, session_id)`
    key — but `WorkoutPlan.session_ids_are_unique` checks `session_id` alone
    across every session in the plan, and a real `training_schedule` can
    genuinely repeat a time+type pair on two different days in one week (the
    shipped config declares "05:30 gym_hypertrophy" on both Monday and
    Saturday) — a real collision, not a hypothetical one. Folding the date
    in avoids it, and matches the `"<date>:<type>:<time>"` shape design-06
    §7's own `workout_feedback.json` example already uses for the same
    session.
    """
    return f"{session_date}:{session_type}:{time_of_day}"


class WorkoutSessionRecipe(BaseModel):
    """The model-authored part of one generated session.

    `date`, `session_id` and `planned_duration_minutes` are
    `training_schedule` facts Python already has before the call is made —
    the same reason `Recipe` itself carries no `day` field — so asking the
    model to restate them would only invite a mismatch Python then has to
    detect and reject: a retry spent on information it was never positioned
    to get right. `generate_workout_week` supplies those once the model
    returns and assembles the real `WorkoutSession`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    exercises: List[ExercisePrescription] = Field(default_factory=list)


class WeeklyWorkoutRecipes(BaseModel):
    """`generate_workout_week`'s `response_model` — design-06 §4's whole-week
    call, generating every declared gym session together so movement-pattern
    distribution and day-to-day variation are one call's decision, the same
    reason `planner.MealTypeWeekRecipes` spans its whole axis instead of
    looping per day.

    Keyed by the exact `workout_plan_session_id` the prompt names for each
    declared session — the model echoes it back as the dict key, precisely
    as `planner.MealTypeWeekRecipes.recipes` is keyed by the day names its
    own prompt states.
    """

    model_config = ConfigDict(extra="forbid")

    sessions: Dict[str, WorkoutSessionRecipe] = Field(default_factory=dict)

    @model_validator(mode="after")
    def constraints_are_respected(self, info: ValidationInfo) -> "WeeklyWorkoutRecipes":
        """design-06 §5's deterministic validator, run before instructor
        ever accepts a response — "the model prompt is never the
        validator." Raising here is load-bearing: instructor catches it and
        hands the model its own output back to retry, the same pattern
        `planner.reject_misplaced_long_cook`/`reject_short_storage_class`
        already use on the meal axis.
        """
        context = info.context or {}
        constraints: List[MovementConstraint] = context.get("movement_constraints") or []
        violations = [
            f"{session_id}: {message}"
            for session_id, session in self.sessions.items()
            for message in constraint_violations(session.exercises, constraints)
        ]
        if violations:
            raise ValueError(
                "The following exercises violate a personal training "
                "constraint and must be corrected, never dropped or "
                "substituted silently: " + " | ".join(violations)
            )
        return self


def generate_workout_week(
    config: dict,
    week_start_date: str,
    recent_history: Optional[List[dict]] = None,
    progress_note=None,
) -> Optional[WorkoutPlan]:
    """Generate this week's whole gym-session detail in one structured call
    — design-06 §4, `dev/task-queue-modified.md`'s 5.1b.

    No model call at all when there is nothing to generate: no active
    program, or a `training_schedule` with no gym/resistance session this
    week, both return `None` — the explicit no-op design-06 §4/§10 asks
    for, checked before `build_client` is ever reached so neither case needs
    API credentials. `sessions` in the result is always exactly the
    declared gym sessions: Python assigns every session's `date`/
    `session_id`/`planned_duration_minutes` straight off `training_schedule`
    itself, never the model, so a session can never be added, removed or
    retimed by this call (design-06 §2.2, §10).

    `week_start_date` is the same real calendar anchor `WeekPlan.
    week_start_date` is (`week.week_date_range(...)[0].isoformat()`), passed
    in rather than derived here — this call has no `WeekSpec` of its own to
    read it off, and today's weekday-name-only `training_schedule` has no
    other way to reach a real date (`week.day_date`).
    """
    active_program_id = config.get("active_gym_program")
    programs = config.get("gym_programs") or {}
    # `config` is the plain dict `planner.load_app_config`/`AppConfig.
    # model_dump` produces, so `programs[id]` is a plain dict here too, not
    # already a `GymProgram` — re-validating it is cheap and gives
    # `build_gym_program_rule` typed attribute access (and turns its
    # `compound_rep_range`/`accessory_rep_range` JSON lists back into the
    # tuples the model actually declares).
    program = (
        GymProgram(**programs[active_program_id])
        if active_program_id and active_program_id in programs
        else None
    )

    days = week_days(config)
    gym_sessions = [
        session
        for session in (config.get("training_schedule") or [])
        if session.get("day") in days
        and str(session.get("type") or "").startswith(GYM_SESSION_TYPES)
    ]

    if program is None or not gym_sessions:
        return None

    training_profile = TrainingProfile(**(config.get("training_profile") or {}))
    constraints = training_profile.movement_constraints

    declared = []
    for session in gym_sessions:
        day = session["day"]
        time_of_day = str(session.get("time") or "00:00")
        session_type = str(session.get("type") or "")
        session_date = day_date(week_start_date, days, day)
        declared.append(
            {
                "session_id": workout_plan_session_id(session_date, time_of_day, session_type),
                "date": session_date,
                "day": day,
                "type": session_type,
                "duration_minutes": int(session.get("duration_minutes") or 0),
            }
        )

    session_briefs = "\n".join(
        f"- {item['session_id']}: {item['day']}, a {item['type']} session, "
        f"{item['duration_minutes']} minutes."
        for item in declared
    )

    system_prompt = (
        "You are a resistance-training assistant designing this week's "
        f"{len(declared)} gym session(s) from the athlete's selected "
        "program. Generate exactly one session per line below, keyed by "
        "its session_id exactly as given — do not add, drop or rename a "
        "session.\n\n"
        + build_gym_program_rule(program)
        + build_movement_constraints_rule(constraints)
        + build_equipment_rule(training_profile.available_equipment)
        + (f"- Trainee notes: {training_profile.notes}\n" if training_profile.notes else "")
        + build_exercise_history_rule(recent_history)
        + "- Do not show your work or narrate your process. Respond with "
        "the structured data only.\n"
    )
    user_prompt = (
        f"Generate exactly {len(declared)} session(s), one per session_id "
        f"below:\n{session_briefs}\n"
    )

    client = build_client(config.get("models"))
    model = resolve_planner_model(config)
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info("workout: requesting %d session(s) from %s", len(declared), model)
    started = time.monotonic()
    response, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=WeeklyWorkoutRecipes,
        max_retries=3,
        max_tokens=max_tokens,
        extra_body=reasoning_extra_body(model, config),
        context={"config": config, "movement_constraints": constraints},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    log_completion("workout", completion, started)

    requested_ids = {item["session_id"] for item in declared}
    missing = requested_ids - set(response.sessions)
    if missing:
        raise ValueError(
            f"workout: model returned no session for {', '.join(sorted(missing))}"
        )

    sessions = [
        WorkoutSession(
            date=item["date"],
            session_id=item["session_id"],
            name=response.sessions[item["session_id"]].name,
            planned_duration_minutes=item["duration_minutes"],
            exercises=response.sessions[item["session_id"]].exercises,
        )
        for item in declared
    ]

    plan = WorkoutPlan(
        week_start_date=week_start_date,
        gym_program=active_program_id,
        sessions=sessions,
    )

    # Storage validation: the same shared check the response validator above
    # already ran per session, reapplied once over the assembled plan — the
    # "full generation ... and storage validation" design-06 §5 asks one
    # validator to serve, so a plan is never left in a state the call that
    # produced it would itself have rejected.
    violations = validate_workout_plan(plan, constraints)
    if violations:
        raise ValueError(
            "workout: generated plan fails its own constraint validator: "
            + " | ".join(violations)
        )

    if progress_note:
        progress_note(f"workout: generated {len(sessions)} session(s)")

    return plan
