"""Tests for the typed workout-plan models and their own storage
(`dev/task-queue-modified.md`'s 5.1a), and — added for 5.1b — the shared
deterministic constraint validator and the one structured weekly generation
call (`dev/design-06-exercise-planning.md` §§4-5).

Three layers now: the 5.1a typed models' own validation and repository
round-trip; the 5.1b pure validator (`constraint_violations`,
`validate_workout_plan`) and the prompt-building helpers, both plain
functions with no I/O; and `generate_workout_week`'s no-model-call paths.
Nothing here touches the network, a model, or the clock — the two
preconditions that skip the model call are checked, and reaching the model
call at all (never actually placed) is checked by patching `build_client`
to raise rather than by mocking a real completion.

Manual feedback/progression proposals and the Today surfacing are later
subtasks (5.1c/5.1d) and are not exercised here.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from typing import get_args
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

import planner  # noqa: E402
from planner import MOVEMENT_PATTERNS  # noqa: E402
from repository import CONFIG_FILES, LocalJSONRepository, run_sync  # noqa: E402
from workout import (  # noqa: E402
    ExercisePrescription,
    ProgressionMethod,
    WeeklyWorkoutRecipes,
    WorkoutPlan,
    WorkoutSession,
    build_equipment_rule,
    build_exercise_history_rule,
    build_gym_program_rule,
    build_movement_constraints_rule,
    constraint_violations,
    generate_workout_week,
    validate_workout_plan,
    workout_plan_session_id,
)


def make_exercise(**overrides) -> dict:
    fields = dict(
        exercise_id="partial-range-squat",
        name="Partial-range back squat",
        movement_pattern="squat",
        role="compound",
        sets=3,
        rep_min=8,
        rep_max=12,
        target_rir=2,
        rest_seconds=90,
        execution_notes="Only to the established pain-free depth.",
        applied_constraint_ids=["hip-impingement-squat-depth"],
        progression_rule="double_progression",
    )
    fields.update(overrides)
    return fields


def make_session(**overrides) -> dict:
    fields = dict(
        date="2026-09-07",
        session_id="06:30:gym_hypertrophy",
        name="Monday full body",
        planned_duration_minutes=60,
        exercises=[make_exercise()],
    )
    fields.update(overrides)
    return fields


def make_plan(**overrides) -> dict:
    fields = dict(
        week_start_date="2026-09-07",
        gym_program="functional_hypertrophy",
        sessions=[make_session()],
    )
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# ExercisePrescription
# ---------------------------------------------------------------------------


class TestExercisePrescriptionValidation(unittest.TestCase):
    def test_a_minimal_exercise_is_accepted(self):
        exercise = ExercisePrescription(**make_exercise())
        self.assertEqual(exercise.movement_pattern, "squat")
        self.assertEqual(exercise.role, "compound")

    def test_target_load_kg_defaults_to_none(self):
        """design-06 §4: null unless matched from history — never an
        invented starting weight."""
        exercise = ExercisePrescription(**{k: v for k, v in make_exercise().items()})
        self.assertIsNone(exercise.target_load_kg)

    def test_target_load_kg_may_be_given_from_a_history_match(self):
        exercise = ExercisePrescription(**make_exercise(target_load_kg=42.5))
        self.assertEqual(exercise.target_load_kg, 42.5)

    def test_target_load_kg_may_not_be_negative(self):
        with self.assertRaises(ValidationError):
            ExercisePrescription(**make_exercise(target_load_kg=-5))

    def test_an_unknown_movement_pattern_is_rejected(self):
        with self.assertRaises(ValidationError):
            ExercisePrescription(**make_exercise(movement_pattern="lunge"))

    def test_every_shipped_movement_pattern_is_accepted(self):
        for pattern in MOVEMENT_PATTERNS:
            with self.subTest(pattern=pattern):
                ExercisePrescription(**make_exercise(movement_pattern=pattern))

    def test_an_unknown_role_is_rejected(self):
        with self.assertRaises(ValidationError):
            ExercisePrescription(**make_exercise(role="cardio"))

    def test_every_documented_role_is_accepted(self):
        for role in ("power", "compound", "accessory", "carry", "core"):
            with self.subTest(role=role):
                ExercisePrescription(**make_exercise(role=role))

    def test_rep_max_below_rep_min_is_rejected(self):
        with self.assertRaises(ValidationError):
            ExercisePrescription(**make_exercise(rep_min=12, rep_max=8))

    def test_rep_max_equal_to_rep_min_is_fine(self):
        exercise = ExercisePrescription(**make_exercise(rep_min=10, rep_max=10))
        self.assertEqual(exercise.rep_min, exercise.rep_max)

    def test_target_rir_must_be_in_bounds(self):
        with self.assertRaises(ValidationError):
            ExercisePrescription(**make_exercise(target_rir=-1))
        with self.assertRaises(ValidationError):
            ExercisePrescription(**make_exercise(target_rir=6))

    def test_an_unknown_progression_rule_is_rejected(self):
        with self.assertRaises(ValidationError):
            ExercisePrescription(**make_exercise(progression_rule="linear_progression"))

    def test_applied_constraint_ids_and_execution_notes_default_empty(self):
        fields = make_exercise()
        del fields["applied_constraint_ids"]
        del fields["execution_notes"]
        exercise = ExercisePrescription(**fields)
        self.assertEqual(exercise.applied_constraint_ids, [])
        self.assertEqual(exercise.execution_notes, "")

    def test_an_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            ExercisePrescription(**make_exercise(unexpected="nope"))

    def test_a_missing_required_field_is_rejected(self):
        fields = make_exercise()
        del fields["progression_rule"]
        with self.assertRaises(ValidationError):
            ExercisePrescription(**fields)


class TestProgressionRuleSharesGymProgramVocabulary(unittest.TestCase):
    """design-06 §6 implements exactly the two methods `GymProgram.progression`
    already declares (design-06 §2.2) — `ProgressionMethod` is read directly
    off that field so the two vocabularies cannot drift apart, the same
    reasoning `MovementPattern`/`MOVEMENT_PATTERNS` already applies to the
    pattern vocabulary."""

    def test_progression_method_is_double_progression_and_two_for_two(self):
        self.assertEqual(
            set(get_args(ProgressionMethod)),
            {"double_progression", "two_for_two"},
        )

    def test_every_progression_method_value_is_accepted(self):
        for method in get_args(ProgressionMethod):
            with self.subTest(method=method):
                ExercisePrescription(**make_exercise(progression_rule=method))


# ---------------------------------------------------------------------------
# WorkoutSession
# ---------------------------------------------------------------------------


class TestWorkoutSessionValidation(unittest.TestCase):
    def test_a_minimal_session_is_accepted(self):
        session = WorkoutSession(**make_session())
        self.assertEqual(len(session.exercises), 1)
        self.assertEqual(session.exercises[0].movement_pattern, "squat")

    def test_date_must_be_an_iso_date(self):
        with self.assertRaises(ValidationError):
            WorkoutSession(**make_session(date="07/09/2026"))

    def test_session_id_must_be_non_empty(self):
        with self.assertRaises(ValidationError):
            WorkoutSession(**make_session(session_id=""))

    def test_planned_duration_minutes_must_be_non_negative(self):
        with self.assertRaises(ValidationError):
            WorkoutSession(**make_session(planned_duration_minutes=-1))

    def test_an_empty_exercise_list_is_fine(self):
        session = WorkoutSession(**make_session(exercises=[]))
        self.assertEqual(session.exercises, [])

    def test_an_invalid_nested_exercise_fails_the_whole_session(self):
        bad_exercise = make_exercise(movement_pattern="lunge")
        with self.assertRaises(ValidationError):
            WorkoutSession(**make_session(exercises=[bad_exercise]))

    def test_an_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkoutSession(**make_session(unexpected="nope"))


# ---------------------------------------------------------------------------
# WorkoutPlan
# ---------------------------------------------------------------------------


class TestWorkoutPlanValidation(unittest.TestCase):
    def test_a_minimal_plan_is_accepted(self):
        plan = WorkoutPlan(**make_plan())
        self.assertEqual(plan.gym_program, "functional_hypertrophy")
        self.assertEqual(len(plan.sessions), 1)

    def test_week_start_date_must_be_an_iso_date(self):
        with self.assertRaises(ValidationError):
            WorkoutPlan(**make_plan(week_start_date="not-a-date"))

    def test_gym_program_must_be_non_empty(self):
        with self.assertRaises(ValidationError):
            WorkoutPlan(**make_plan(gym_program=""))

    def test_an_empty_session_list_is_fine(self):
        """The no-active-program/no-gym-sessions no-op (design-06 §4,
        5.1b) is a generation-path decision, not a model constraint — the
        model itself does not forbid an empty week."""
        plan = WorkoutPlan(**make_plan(sessions=[]))
        self.assertEqual(plan.sessions, [])

    def test_duplicate_session_ids_are_rejected(self):
        session = make_session()
        with self.assertRaises(ValidationError):
            WorkoutPlan(**make_plan(sessions=[session, dict(session)]))

    def test_two_sessions_with_distinct_ids_are_fine(self):
        first = make_session(date="2026-09-07", session_id="06:30:gym_hypertrophy")
        second = make_session(date="2026-09-10", session_id="06:30:gym_hypertrophy_b")
        plan = WorkoutPlan(**make_plan(sessions=[first, second]))
        self.assertEqual(len(plan.sessions), 2)

    def test_an_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkoutPlan(**make_plan(unexpected="nope"))


# ---------------------------------------------------------------------------
# Repository storage
# ---------------------------------------------------------------------------


class WorkoutPlanStorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(data_dir=self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


class TestWorkoutPlanRepositoryRoundTrip(WorkoutPlanStorageCase):
    def test_a_missing_file_reads_as_none(self):
        self.assertIsNone(run_sync(self.repo.load_workout_plan()))

    def test_a_missing_next_week_file_reads_as_none(self):
        self.assertIsNone(run_sync(self.repo.load_workout_plan("next")))

    def test_a_saved_plan_round_trips_under_current_by_default(self):
        plan = WorkoutPlan(**make_plan())
        run_sync(self.repo.save_workout_plan(plan.model_dump()))
        loaded = run_sync(self.repo.load_workout_plan())
        self.assertEqual(loaded["gym_program"], "functional_hypertrophy")
        self.assertEqual(len(loaded["sessions"]), 1)
        self.assertEqual(WorkoutPlan(**loaded), plan)

    def test_saving_again_under_the_same_identifier_replaces_rather_than_merges(self):
        first = WorkoutPlan(**make_plan())
        run_sync(self.repo.save_workout_plan(first.model_dump()))
        second = WorkoutPlan(**make_plan(gym_program="strength_block", sessions=[]))
        run_sync(self.repo.save_workout_plan(second.model_dump()))
        loaded = run_sync(self.repo.load_workout_plan())
        self.assertEqual(loaded["gym_program"], "strength_block")
        self.assertEqual(loaded["sessions"], [])

    def test_current_and_next_are_stored_and_read_independently(self):
        current = WorkoutPlan(**make_plan(gym_program="functional_hypertrophy"))
        next_week = WorkoutPlan(
            **make_plan(
                week_start_date="2026-09-14",
                gym_program="strength_block",
                sessions=[make_session(date="2026-09-14")],
            )
        )
        run_sync(self.repo.save_workout_plan(current.model_dump(), "current"))
        run_sync(self.repo.save_workout_plan(next_week.model_dump(), "next"))

        loaded_current = run_sync(self.repo.load_workout_plan("current"))
        loaded_next = run_sync(self.repo.load_workout_plan("next"))
        self.assertEqual(loaded_current["gym_program"], "functional_hypertrophy")
        self.assertEqual(loaded_next["gym_program"], "strength_block")


class TestWorkoutPlanFileLocation(WorkoutPlanStorageCase):
    def test_the_current_file_lives_under_data_dir_unsuffixed(self):
        plan = WorkoutPlan(**make_plan())
        run_sync(self.repo.save_workout_plan(plan.model_dump()))
        path = Path(self.repo.paths.workout_plans)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "workout_plans.json")
        self.assertEqual(path.parent, Path(self.tmp.name))

    def test_the_next_file_is_a_sibling_with_a_suffixed_name(self):
        plan = WorkoutPlan(**make_plan())
        run_sync(self.repo.save_workout_plan(plan.model_dump(), "next"))
        next_path = Path(self.tmp.name) / "workout_plans_next.json"
        self.assertTrue(next_path.exists())

    def test_it_is_not_a_config_file(self):
        """`data/workout_plans.json` is app-written generated state, never
        `config/` — it must not appear in the manifest that
        `save_config_keys` and preset overrides are checked against."""
        self.assertNotIn("workout_plans.json", CONFIG_FILES)


class TestFailedGenerationLeavesFileUntouched(WorkoutPlanStorageCase):
    """5.1a's own acceptance line: a failed generation or regeneration
    leaves the previous file byte-identical. `save_workout_plan` never
    validates its argument — a caller must construct (and thereby validate)
    a `WorkoutPlan` first, so a validation failure never reaches the
    repository at all, and the write it never makes cannot touch the file.
    """

    def setUp(self):
        super().setUp()
        self.plan = WorkoutPlan(**make_plan())
        run_sync(self.repo.save_workout_plan(self.plan.model_dump()))
        self.before = Path(self.repo.paths.workout_plans).read_bytes()

    def test_a_validation_failure_before_save_touches_nothing(self):
        broken = make_plan(sessions=[make_session(), make_session()])  # duplicate session_id
        with self.assertRaises(ValidationError):
            WorkoutPlan(**broken)
        # No save_workout_plan call was reachable above; the file on disk
        # must be exactly what it was before the failed attempt.
        after = Path(self.repo.paths.workout_plans).read_bytes()
        self.assertEqual(after, self.before)

    def test_reloading_after_the_failed_attempt_still_returns_the_old_plan(self):
        broken = make_plan(gym_program="")
        with self.assertRaises(ValidationError):
            WorkoutPlan(**broken)
        loaded = run_sync(self.repo.load_workout_plan())
        self.assertEqual(loaded["gym_program"], "functional_hypertrophy")


# ---------------------------------------------------------------------------
# 5.1b — the shared deterministic constraint validator (design-06 §5)
# ---------------------------------------------------------------------------

HIP_CONSTRAINT = planner.MovementConstraint(
    id="hip-impingement-squat-depth",
    scope="movement_pattern",
    target="squat",
    action="modify",
    # Matches `make_exercise()`'s default `execution_notes` exactly, so the
    # unmodified fixture already represents the compliant case — the same
    # 5.1a fixture this constraint's id (`applied_constraint_ids`) already
    # names.
    instruction="Only to the established pain-free depth.",
)

EXCLUDE_FULL_DEPTH_SQUAT = planner.MovementConstraint(
    id="no-full-depth-back-squat",
    scope="exercise",
    target="Full-depth back squat",
    action="exclude",
)

PREFER_GOBLET_SQUAT = planner.MovementConstraint(
    id="prefer-goblet-variation",
    scope="movement_pattern",
    target="squat",
    action="prefer",
    preferred_variations=["Goblet squat"],
)


def make_prescription(**overrides) -> ExercisePrescription:
    return ExercisePrescription(**make_exercise(**overrides))


FUNCTIONAL_HYPERTROPHY = {
    "label": "Functional hypertrophy",
    "primary_goal": "hypertrophy_and_function",
    "architecture": "full_body",
    "working_sets": 3,
    "compound_rep_range": [8, 12],
    "accessory_rep_range": [10, 15],
    "target_rir": 2,
    "progression": "double_progression",
    "include_power": True,
    "movement_patterns": ["squat", "hinge", "horizontal_push"],
    "notes": "Use low-impact power variations.",
}


class TestConstraintViolations(unittest.TestCase):
    """`constraint_violations` — design-06 §5 steps 3/4/6, over the shared
    pure function full generation, single-session regeneration, edits and
    storage validation all call."""

    def test_an_exercise_matching_no_constraint_is_fine(self):
        exercise = make_prescription(movement_pattern="hinge", applied_constraint_ids=[])
        self.assertEqual(constraint_violations([exercise], [HIP_CONSTRAINT]), [])

    def test_no_constraints_at_all_is_fine(self):
        exercise = make_prescription()
        self.assertEqual(constraint_violations([exercise], []), [])

    def test_an_exclude_match_is_always_a_violation(self):
        exercise = make_prescription(
            exercise_id="full-depth-squat", name="Full-depth back squat"
        )
        violations = constraint_violations([exercise], [EXCLUDE_FULL_DEPTH_SQUAT])
        self.assertEqual(len(violations), 1)
        self.assertIn("no-full-depth-back-squat", violations[0])

    def test_exclude_match_is_case_and_whitespace_insensitive(self):
        """design-06 §2.1/§5: normalized exact-title matching."""
        exercise = make_prescription(name="  FULL-DEPTH   Back Squat  ")
        violations = constraint_violations([exercise], [EXCLUDE_FULL_DEPTH_SQUAT])
        self.assertEqual(len(violations), 1)

    def test_a_modify_match_missing_from_applied_constraint_ids_is_rejected(self):
        exercise = make_prescription(applied_constraint_ids=[])
        violations = constraint_violations([exercise], [HIP_CONSTRAINT])
        self.assertEqual(len(violations), 1)
        self.assertIn("applied_constraint_ids", violations[0])

    def test_a_modify_match_missing_its_instruction_text_is_rejected(self):
        exercise = make_prescription(execution_notes="Standard squat cues.")
        violations = constraint_violations([exercise], [HIP_CONSTRAINT])
        self.assertEqual(len(violations), 1)
        self.assertIn(HIP_CONSTRAINT.instruction, violations[0])

    def test_a_correctly_modified_exercise_has_no_violations(self):
        exercise = make_prescription(
            applied_constraint_ids=[HIP_CONSTRAINT.id],
            execution_notes=f"Warm up normally. {HIP_CONSTRAINT.instruction}",
        )
        self.assertEqual(constraint_violations([exercise], [HIP_CONSTRAINT]), [])

    def test_a_modify_constraint_with_no_instruction_only_checks_the_id(self):
        bare_modify = planner.MovementConstraint(
            id="bare-modify", scope="movement_pattern", target="hinge", action="modify"
        )
        matching = make_prescription(
            movement_pattern="hinge", applied_constraint_ids=["bare-modify"]
        )
        self.assertEqual(constraint_violations([matching], [bare_modify]), [])
        not_listed = make_prescription(movement_pattern="hinge", applied_constraint_ids=[])
        self.assertEqual(len(constraint_violations([not_listed], [bare_modify])), 1)

    def test_a_prefer_constraint_never_rejects_a_plan(self):
        """design-06 §5 step 5: ranked in the prompt only, never enforced —
        a valid plan is not rejected merely because no preferred variation
        was used."""
        exercise = make_prescription(applied_constraint_ids=[])
        self.assertEqual(constraint_violations([exercise], [PREFER_GOBLET_SQUAT]), [])

    def test_multiple_exercises_each_report_their_own_violations(self):
        good = make_prescription()
        bad = make_prescription(
            exercise_id="full-depth-squat",
            name="Full-depth back squat",
            movement_pattern="hinge",
            applied_constraint_ids=[],
        )
        violations = constraint_violations(
            [good, bad], [HIP_CONSTRAINT, EXCLUDE_FULL_DEPTH_SQUAT]
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("full-depth-squat", violations[0])


class TestHipConstraintAcceptanceCases(unittest.TestCase):
    """design-06 §1's motivating example, directly — the four cases
    `dev/PROMPT-15.md`'s acceptance list names by name: full-depth
    rejection, missing-note rejection, valid partial-range acceptance, and
    range-progression rejection."""

    def test_full_depth_squat_is_rejected(self):
        full_depth = make_prescription(
            exercise_id="full-depth-squat",
            name="Full-depth back squat",
            applied_constraint_ids=[],
            execution_notes="",
        )
        violations = constraint_violations([full_depth], [EXCLUDE_FULL_DEPTH_SQUAT])
        self.assertTrue(violations)
        self.assertIn("excluded", violations[0])

    def test_a_missing_modification_note_is_rejected(self):
        unnoted = make_prescription(
            applied_constraint_ids=[HIP_CONSTRAINT.id], execution_notes=""
        )
        violations = constraint_violations([unnoted], [HIP_CONSTRAINT])
        self.assertTrue(violations)
        self.assertIn(HIP_CONSTRAINT.instruction, violations[0])

    def test_valid_user_approved_partial_range_is_accepted(self):
        approved = make_prescription(
            applied_constraint_ids=[HIP_CONSTRAINT.id],
            execution_notes=HIP_CONSTRAINT.instruction,
        )
        self.assertEqual(constraint_violations([approved], [HIP_CONSTRAINT]), [])

    def test_progression_that_drops_the_fixed_range_instruction_is_rejected(self):
        """design-06 §5 step 6/§6: depth is not a progression variable. A
        "progressed" exercise that still cites the constraint id but no
        longer carries its fixed-range instruction — the only way this
        schema can represent "progressed the range" at all, since nothing
        on `ExercisePrescription` names a range of motion numerically — must
        still be rejected."""
        progressed = make_prescription(
            sets=4,
            rep_min=10,
            rep_max=14,
            target_load_kg=45.0,
            applied_constraint_ids=[HIP_CONSTRAINT.id],
            execution_notes="Progress toward full depth over the coming weeks.",
        )
        violations = constraint_violations([progressed], [HIP_CONSTRAINT])
        self.assertTrue(violations)
        self.assertIn(HIP_CONSTRAINT.instruction, violations[0])


class TestValidateWorkoutPlan(unittest.TestCase):
    """`validate_workout_plan` — `constraint_violations` reused as design-06
    §5's "storage validation", over a whole assembled plan rather than one
    exercise list."""

    def test_a_fully_compliant_plan_has_no_violations(self):
        exercise = make_prescription(
            applied_constraint_ids=[HIP_CONSTRAINT.id],
            execution_notes=HIP_CONSTRAINT.instruction,
        )
        plan = WorkoutPlan(**make_plan(sessions=[make_session(exercises=[exercise.model_dump()])]))
        self.assertEqual(validate_workout_plan(plan, [HIP_CONSTRAINT]), [])

    def test_a_violation_names_its_session_id(self):
        exercise = make_prescription(execution_notes="")
        plan = WorkoutPlan(
            **make_plan(
                sessions=[
                    make_session(
                        session_id="05:30:gym_hypertrophy",
                        exercises=[exercise.model_dump()],
                    )
                ]
            )
        )
        violations = validate_workout_plan(plan, [HIP_CONSTRAINT])
        self.assertEqual(len(violations), 1)
        self.assertTrue(violations[0].startswith("05:30:gym_hypertrophy:"))


class TestWorkoutPlanSessionId(unittest.TestCase):
    def test_folds_date_type_and_time_into_one_string(self):
        self.assertEqual(
            workout_plan_session_id("2026-09-07", "05:30", "gym_hypertrophy"),
            "2026-09-07:gym_hypertrophy:05:30",
        )

    def test_two_declared_sessions_sharing_a_time_and_type_stay_distinct_by_date(self):
        """The exact real-world collision this format exists to avoid: the
        shipped config declares "05:30 gym_hypertrophy" on both Monday and
        Saturday, which `WorkoutPlan.session_ids_are_unique` (5.1a) would
        otherwise reject outright."""
        monday = workout_plan_session_id("2026-09-07", "05:30", "gym_hypertrophy")
        saturday = workout_plan_session_id("2026-09-12", "05:30", "gym_hypertrophy")
        self.assertNotEqual(monday, saturday)
        WorkoutPlan(
            **make_plan(
                sessions=[
                    make_session(date="2026-09-07", session_id=monday),
                    make_session(date="2026-09-12", session_id=saturday),
                ]
            )
        )


class TestWeeklyWorkoutRecipesValidator(unittest.TestCase):
    """The `model_validator` `generate_workout_week` relies on for its
    retry loop — exercised directly via `model_validate`, exactly as
    `planner.DayRecipes`/`MealTypeWeekRecipes` are tested elsewhere, so
    nothing here needs a real completion."""

    def test_a_compliant_response_validates_cleanly(self):
        exercise = make_exercise(
            applied_constraint_ids=[HIP_CONSTRAINT.id],
            execution_notes=HIP_CONSTRAINT.instruction,
        )
        data = {"sessions": {"2026-09-07:gym_hypertrophy:05:30": {
            "name": "Monday full body", "exercises": [exercise],
        }}}
        response = WeeklyWorkoutRecipes.model_validate(
            data, context={"movement_constraints": [HIP_CONSTRAINT]}
        )
        self.assertEqual(list(response.sessions), ["2026-09-07:gym_hypertrophy:05:30"])

    def test_a_violating_response_is_rejected_with_the_exact_failure(self):
        exercise = make_exercise(execution_notes="")
        data = {"sessions": {"2026-09-07:gym_hypertrophy:05:30": {
            "name": "Monday full body", "exercises": [exercise],
        }}}
        with self.assertRaises(ValidationError) as caught:
            WeeklyWorkoutRecipes.model_validate(
                data, context={"movement_constraints": [HIP_CONSTRAINT]}
            )
        message = str(caught.exception)
        self.assertIn("2026-09-07:gym_hypertrophy:05:30", message)
        self.assertIn(HIP_CONSTRAINT.instruction, message)

    def test_no_movement_constraints_in_context_validates_cleanly(self):
        exercise = make_exercise(execution_notes="", applied_constraint_ids=[])
        data = {"sessions": {"x": {"name": "Session", "exercises": [exercise]}}}
        WeeklyWorkoutRecipes.model_validate(data, context={})
        WeeklyWorkoutRecipes.model_validate(data, context=None)


class TestPromptBuilders(unittest.TestCase):
    """The pure string builders `generate_workout_week` assembles its
    prompt from — design-06 §4's "the selected program, available
    equipment, verbatim personal constraints, precedence rules, and the
    fixed-range statement"."""

    def test_no_constraints_means_no_rule_text(self):
        self.assertEqual(build_movement_constraints_rule([]), "")

    def test_an_exclude_constraint_is_stated_verbatim(self):
        rule = build_movement_constraints_rule([EXCLUDE_FULL_DEPTH_SQUAT])
        self.assertIn("Full-depth back squat", rule)
        self.assertIn("NEVER prescribe", rule)

    def test_a_modify_constraint_states_its_id_and_instruction_verbatim(self):
        rule = build_movement_constraints_rule([HIP_CONSTRAINT])
        self.assertIn(HIP_CONSTRAINT.id, rule)
        self.assertIn(HIP_CONSTRAINT.instruction, rule)

    def test_the_precedence_and_fixed_range_rules_are_always_included(self):
        rule = build_movement_constraints_rule([HIP_CONSTRAINT])
        self.assertIn("personal exclude > personal modify > personal prefer", rule)
        self.assertIn("not a progression target", rule)

    def test_a_prefer_constraint_lists_its_preferred_variations(self):
        rule = build_movement_constraints_rule([PREFER_GOBLET_SQUAT])
        self.assertIn("Goblet squat", rule)

    def test_a_prefer_constraint_with_no_variations_produces_no_line_for_it(self):
        """design-06 §5 step 5: only worth stating when there is a
        variation to name — an empty `preferred_variations` has nothing to
        rank."""
        bare_prefer = planner.MovementConstraint(
            id="bare-prefer", scope="movement_pattern", target="squat", action="prefer"
        )
        rule = build_movement_constraints_rule([bare_prefer])
        self.assertNotIn("bare-prefer", rule)

    def test_no_equipment_means_no_rule_text(self):
        self.assertEqual(build_equipment_rule([]), "")

    def test_equipment_is_listed_verbatim(self):
        rule = build_equipment_rule(["barbell", "adjustable bench"])
        self.assertIn("barbell", rule)
        self.assertIn("adjustable bench", rule)

    def test_no_history_means_no_rule_text(self):
        self.assertEqual(build_exercise_history_rule(None), "")
        self.assertEqual(build_exercise_history_rule([]), "")

    def test_history_rows_are_included_when_given(self):
        rule = build_exercise_history_rule([{"exercise": "Back squat", "load_kg": 60}])
        self.assertIn("Back squat", rule)

    def test_gym_program_rule_states_the_programs_own_numbers(self):
        program = planner.GymProgram(**FUNCTIONAL_HYPERTROPHY)
        rule = build_gym_program_rule(program)
        self.assertIn("Functional hypertrophy", rule)
        self.assertIn("8-12", rule)
        self.assertIn("10-15", rule)
        self.assertIn("double_progression", rule)
        self.assertIn("power-focused", rule)
        self.assertIn("squat, hinge, horizontal_push", rule)
        self.assertIn("Use low-impact power variations.", rule)

    def test_gym_program_rule_omits_power_line_when_program_has_none(self):
        program = planner.GymProgram(**dict(FUNCTIONAL_HYPERTROPHY, include_power=False))
        rule = build_gym_program_rule(program)
        self.assertNotIn("power-focused", rule)

    def test_gym_program_rule_omits_pattern_line_when_none_are_declared(self):
        program = planner.GymProgram(**dict(FUNCTIONAL_HYPERTROPHY, movement_patterns=[]))
        rule = build_gym_program_rule(program)
        self.assertNotIn("Distribute", rule)


# ---------------------------------------------------------------------------
# 5.1b — `generate_workout_week`'s no-model-call paths
#
# These are the only two states a call may reach with no active program or
# no gym sessions — both an explicit no-op, checked before `build_client` is
# ever reached, so neither needs network access or API credentials. What
# happens once a call *does* proceed past that check is exercised by
# patching `build_client` to raise rather than by mocking a real
# completion — this file makes no model call, ever.
# ---------------------------------------------------------------------------

BASE_WEEK_CONFIG = {
    "weekly_schedule": {
        day: {"calories": 1500, "protein_g": 120, "net_carbs_g": 130, "fat_g": 55}
        for day in (
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        )
    },
}

TRAINING_PROFILE_WITH_HIP_CONSTRAINT = {
    "movement_constraints": [
        {
            "id": "hip-impingement-squat-depth",
            "scope": "movement_pattern",
            "target": "squat",
            "action": "modify",
            "instruction": (
                "Do not prescribe full-depth squats; use only my user-approved "
                "partial range."
            ),
        }
    ],
    "available_equipment": ["barbell", "dumbbells"],
}

GYM_TRAINING_SCHEDULE = [
    {"day": "Monday", "time": "05:30", "type": "gym_hypertrophy", "duration_minutes": 60},
    {"day": "Saturday", "time": "05:30", "type": "gym_hypertrophy", "duration_minutes": 60},
]


def make_workout_generation_config(**overrides) -> dict:
    raw = dict(BASE_WEEK_CONFIG)
    raw.update(overrides)
    return planner.load_app_config(raw)


class TestGenerateWorkoutWeekNoOp(unittest.TestCase):
    def test_no_active_program_is_an_explicit_no_op(self):
        config = make_workout_generation_config(training_schedule=GYM_TRAINING_SCHEDULE)
        self.assertIsNone(config["active_gym_program"])
        self.assertIsNone(generate_workout_week(config, "2026-09-07"))

    def test_an_active_program_with_an_empty_schedule_is_an_explicit_no_op(self):
        config = make_workout_generation_config(
            gym_programs={"functional_hypertrophy": FUNCTIONAL_HYPERTROPHY},
            active_gym_program="functional_hypertrophy",
        )
        self.assertIsNone(generate_workout_week(config, "2026-09-07"))

    def test_an_active_program_with_only_non_gym_sessions_is_an_explicit_no_op(self):
        config = make_workout_generation_config(
            gym_programs={"functional_hypertrophy": FUNCTIONAL_HYPERTROPHY},
            active_gym_program="functional_hypertrophy",
            training_schedule=[
                {"day": "Tuesday", "time": "05:30", "type": "cardio_hiit", "duration_minutes": 30},
                {"day": "Thursday", "time": "00:00", "type": "rest", "duration_minutes": 0},
            ],
        )
        self.assertIsNone(generate_workout_week(config, "2026-09-07"))

    def test_no_op_paths_never_need_api_credentials(self):
        """Both no-op checks run before `build_client`, which is the one
        place an API key is required — patching it to explode proves
        neither path reaches it."""
        config = make_workout_generation_config(training_schedule=GYM_TRAINING_SCHEDULE)
        with mock.patch("workout.build_client", side_effect=AssertionError("must not be called")):
            self.assertIsNone(generate_workout_week(config, "2026-09-07"))

    def test_an_active_program_with_a_declared_gym_session_proceeds_past_the_no_op_check(self):
        """The mirror image of the no-op tests above: once a program and a
        gym session both exist, generation must actually attempt a call
        rather than silently no-op a second time. Patching `build_client` to
        raise is how this is proven without ever reaching the network."""
        config = make_workout_generation_config(
            training_profile=TRAINING_PROFILE_WITH_HIP_CONSTRAINT,
            gym_programs={"functional_hypertrophy": FUNCTIONAL_HYPERTROPHY},
            active_gym_program="functional_hypertrophy",
            training_schedule=GYM_TRAINING_SCHEDULE,
        )
        marker = RuntimeError("would have called the model here")
        with mock.patch("workout.build_client", side_effect=marker):
            with self.assertRaises(RuntimeError) as caught:
                generate_workout_week(config, "2026-09-07")
        self.assertIs(caught.exception, marker)


if __name__ == "__main__":
    unittest.main()
