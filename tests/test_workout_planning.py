"""Tests for the typed workout-plan models and their own storage —
`dev/design-06-exercise-planning.md` §4, `dev/task-queue-modified.md`'s
5.1a.

Two layers, like `test_freezer.py`/`test_blocks.py`: the typed models'
own validation, and a round-trip against a real `LocalJSONRepository`
pointed at a temp directory. Nothing here touches the network, a model, or
the clock.

Deliberately narrow, matching the subtask it covers — the model and its
storage only. The deterministic constraint validator, the generation call,
manual feedback/progression proposals and the Today surfacing are later
subtasks (5.1b/5.1c/5.1d) and are not exercised here.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from typing import get_args

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from planner import MOVEMENT_PATTERNS  # noqa: E402
from repository import CONFIG_FILES, LocalJSONRepository, run_sync  # noqa: E402
from workout import (  # noqa: E402
    ExercisePrescription,
    ProgressionMethod,
    WorkoutPlan,
    WorkoutSession,
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


if __name__ == "__main__":
    unittest.main()
