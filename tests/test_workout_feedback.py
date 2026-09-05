"""Tests for 5.1c — manual feedback and progression proposals
(`dev/design-06-exercise-planning.md` §§6-7).

Three layers: `WorkoutFeedback`'s own validation and its repository
round-trip under its four-part key (`repository.WORKOUT_FEEDBACK_KEY_FIELDS`);
the pure feedback semantics (`feedback_blocks_progression`,
`feedback_flags_review`, `latest_feedback_for_exercise`); and
`propose_progression`/`apply_progression_proposal`, the pure proposal logic
for `double_progression` and `two_for_two`. Nothing here touches the network,
a model, or the clock, and nothing here calls `generate_workout_week` — that
is 5.1b's file.
"""

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from repository import (  # noqa: E402
    CONFIG_FILES,
    WORKOUT_FEEDBACK_KEY_FIELDS,
    LocalJSONRepository,
    run_sync,
)
from workout import (  # noqa: E402
    PROGRESSION_LOAD_INCREMENT_PCT,
    WORKOUT_FEEDBACK_RESPONSES,
    CompletedSetEvidence,
    ExercisePrescription,
    ProgressionProposal,
    WorkoutFeedback,
    apply_progression_proposal,
    feedback_blocks_progression,
    feedback_flags_review,
    latest_feedback_for_exercise,
    propose_progression,
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


def make_feedback(**overrides) -> dict:
    fields = dict(
        date="2026-09-07",
        session_id="2026-09-07:gym_hypertrophy:05:30",
        exercise_id="partial-range-squat",
        constraint_id="hip-impingement-squat-depth",
        response="no_issue",
        note=None,
    )
    fields.update(overrides)
    return fields


def make_set(**overrides) -> dict:
    fields = dict(
        date="2026-09-07",
        session_id="2026-09-07:gym_hypertrophy:05:30",
        exercise_id="partial-range-squat",
        set_number=1,
        reps=12,
        load_kg=40.0,
        rir=2,
    )
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# WorkoutFeedback validation
# ---------------------------------------------------------------------------


class TestWorkoutFeedbackValidation(unittest.TestCase):
    def test_a_minimal_feedback_row_is_accepted(self):
        feedback = WorkoutFeedback(**make_feedback())
        self.assertEqual(feedback.response, "no_issue")

    def test_date_must_be_an_iso_date(self):
        with self.assertRaises(ValidationError):
            WorkoutFeedback(**make_feedback(date="07/09/2026"))

    def test_an_unknown_response_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkoutFeedback(**make_feedback(response="excruciating"))

    def test_every_documented_response_is_accepted(self):
        for response in ("no_issue", "mild_irritation", "worse_than_usual"):
            with self.subTest(response=response):
                WorkoutFeedback(**make_feedback(response=response))

    def test_the_three_documented_responses_are_the_whole_vocabulary(self):
        """design-06 §7: "Three responses are enough initially.\""""
        self.assertEqual(
            set(WORKOUT_FEEDBACK_RESPONSES),
            {"no_issue", "mild_irritation", "worse_than_usual"},
        )

    def test_note_defaults_to_none(self):
        fields = make_feedback()
        del fields["note"]
        feedback = WorkoutFeedback(**fields)
        self.assertIsNone(feedback.note)

    def test_a_missing_key_field_is_rejected(self):
        for field in ("date", "session_id", "exercise_id", "constraint_id"):
            with self.subTest(field=field):
                fields = make_feedback()
                del fields[field]
                with self.assertRaises(ValidationError):
                    WorkoutFeedback(**fields)

    def test_an_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkoutFeedback(**make_feedback(unexpected="nope"))


# ---------------------------------------------------------------------------
# Repository round trip — the four-part key
# ---------------------------------------------------------------------------


class WorkoutFeedbackStorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(data_dir=self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


class TestWorkoutFeedbackRepositoryRoundTrip(WorkoutFeedbackStorageCase):
    def test_a_missing_file_reads_as_an_empty_list(self):
        self.assertEqual(run_sync(self.repo.load_workout_feedback()), [])

    def test_a_saved_entry_round_trips(self):
        entry = WorkoutFeedback(**make_feedback()).model_dump()
        run_sync(self.repo.save_workout_feedback(entry))
        loaded = run_sync(self.repo.load_workout_feedback())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(WorkoutFeedback(**loaded[0]), WorkoutFeedback(**entry))

    def test_re_saving_the_same_key_updates_rather_than_duplicates(self):
        """design-06 §7: "no_issue removes that feedback block" only makes
        sense if a later mark replaces an earlier one about the same
        exercise/constraint pairing, not appends beside it."""
        first = WorkoutFeedback(**make_feedback(response="worse_than_usual")).model_dump()
        run_sync(self.repo.save_workout_feedback(first))
        second = WorkoutFeedback(**make_feedback(response="no_issue")).model_dump()
        run_sync(self.repo.save_workout_feedback(second))
        loaded = run_sync(self.repo.load_workout_feedback())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["response"], "no_issue")

    def test_a_different_constraint_id_on_the_same_exercise_is_a_distinct_row(self):
        squat_hip = WorkoutFeedback(
            **make_feedback(constraint_id="hip-impingement-squat-depth", response="mild_irritation")
        ).model_dump()
        squat_knee = WorkoutFeedback(
            **make_feedback(constraint_id="knee-tracking-cue", response="no_issue")
        ).model_dump()
        run_sync(self.repo.save_workout_feedback(squat_hip))
        run_sync(self.repo.save_workout_feedback(squat_knee))
        loaded = run_sync(self.repo.load_workout_feedback())
        self.assertEqual(len(loaded), 2)
        responses = {row["constraint_id"]: row["response"] for row in loaded}
        self.assertEqual(responses["hip-impingement-squat-depth"], "mild_irritation")
        self.assertEqual(responses["knee-tracking-cue"], "no_issue")

    def test_a_different_exercise_is_a_distinct_row_even_with_the_same_constraint(self):
        squat = WorkoutFeedback(**make_feedback(exercise_id="partial-range-squat")).model_dump()
        lunge = WorkoutFeedback(**make_feedback(exercise_id="partial-range-lunge")).model_dump()
        run_sync(self.repo.save_workout_feedback(squat))
        run_sync(self.repo.save_workout_feedback(lunge))
        loaded = run_sync(self.repo.load_workout_feedback())
        self.assertEqual(len(loaded), 2)

    def test_a_different_date_is_a_distinct_row(self):
        monday = WorkoutFeedback(**make_feedback(date="2026-09-07")).model_dump()
        saturday = WorkoutFeedback(**make_feedback(date="2026-09-12")).model_dump()
        run_sync(self.repo.save_workout_feedback(monday))
        run_sync(self.repo.save_workout_feedback(saturday))
        loaded = run_sync(self.repo.load_workout_feedback())
        self.assertEqual(len(loaded), 2)

    def test_saving_preserves_unrelated_rows(self):
        first = WorkoutFeedback(**make_feedback(exercise_id="squat")).model_dump()
        second = WorkoutFeedback(**make_feedback(exercise_id="row")).model_dump()
        run_sync(self.repo.save_workout_feedback(first))
        run_sync(self.repo.save_workout_feedback(second))
        updated_first = WorkoutFeedback(
            **make_feedback(exercise_id="squat", response="worse_than_usual")
        ).model_dump()
        run_sync(self.repo.save_workout_feedback(updated_first))
        loaded = run_sync(self.repo.load_workout_feedback())
        self.assertEqual(len(loaded), 2)
        by_exercise = {row["exercise_id"]: row for row in loaded}
        self.assertEqual(by_exercise["squat"]["response"], "worse_than_usual")
        self.assertEqual(by_exercise["row"]["response"], "no_issue")

    def test_a_missing_key_field_is_rejected_before_any_write(self):
        entry = make_feedback()
        del entry["constraint_id"]
        with self.assertRaises(ValueError):
            run_sync(self.repo.save_workout_feedback(entry))
        self.assertEqual(run_sync(self.repo.load_workout_feedback()), [])

    def test_it_is_not_a_config_file(self):
        """`data/workout_feedback.json` is app-written observed state, never
        `config/` — see design-06 §7: "It does not change the persistent
        constraint automatically." It must not appear in the manifest
        `save_config_keys` and preset overrides are checked against."""
        self.assertNotIn("workout_feedback.json", CONFIG_FILES)

    def test_the_file_lives_under_data_dir(self):
        entry = WorkoutFeedback(**make_feedback()).model_dump()
        run_sync(self.repo.save_workout_feedback(entry))
        path = Path(self.repo.paths.workout_feedback)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "workout_feedback.json")
        self.assertEqual(path.parent, Path(self.tmp.name))

    def test_saving_never_touches_config_files(self):
        """design-06 §7: "It does not change the persistent constraint
        automatically." Recording feedback must leave every config file
        (which this test's isolated `data_dir` never even creates) alone —
        proven here by asserting no config directory materialises next to
        the data one."""
        entry = WorkoutFeedback(**make_feedback()).model_dump()
        run_sync(self.repo.save_workout_feedback(entry))
        for filename in CONFIG_FILES:
            self.assertFalse((Path(self.tmp.name) / filename).exists())


# ---------------------------------------------------------------------------
# Feedback semantics (design-06 §7)
# ---------------------------------------------------------------------------


class TestFeedbackBlocksProgression(unittest.TestCase):
    def test_no_feedback_at_all_does_not_block(self):
        self.assertFalse(feedback_blocks_progression(None))

    def test_no_issue_does_not_block(self):
        feedback = WorkoutFeedback(**make_feedback(response="no_issue"))
        self.assertFalse(feedback_blocks_progression(feedback))

    def test_mild_irritation_blocks(self):
        feedback = WorkoutFeedback(**make_feedback(response="mild_irritation"))
        self.assertTrue(feedback_blocks_progression(feedback))

    def test_worse_than_usual_blocks(self):
        feedback = WorkoutFeedback(**make_feedback(response="worse_than_usual"))
        self.assertTrue(feedback_blocks_progression(feedback))


class TestFeedbackFlagsReview(unittest.TestCase):
    def test_no_feedback_does_not_flag(self):
        self.assertFalse(feedback_flags_review(None))

    def test_no_issue_does_not_flag(self):
        feedback = WorkoutFeedback(**make_feedback(response="no_issue"))
        self.assertFalse(feedback_flags_review(feedback))

    def test_mild_irritation_does_not_flag(self):
        """design-06 §7: mild_irritation holds the prescription but does not
        itself propose reviewing/substituting the exercise — only
        worse_than_usual does."""
        feedback = WorkoutFeedback(**make_feedback(response="mild_irritation"))
        self.assertFalse(feedback_flags_review(feedback))

    def test_worse_than_usual_flags(self):
        feedback = WorkoutFeedback(**make_feedback(response="worse_than_usual"))
        self.assertTrue(feedback_flags_review(feedback))


class TestLatestFeedbackForExercise(unittest.TestCase):
    def test_no_entries_returns_none(self):
        self.assertIsNone(latest_feedback_for_exercise([], "partial-range-squat"))
        self.assertIsNone(latest_feedback_for_exercise(None, "partial-range-squat"))

    def test_entries_for_a_different_exercise_are_ignored(self):
        entries = [make_feedback(exercise_id="bench-press")]
        self.assertIsNone(latest_feedback_for_exercise(entries, "partial-range-squat"))

    def test_the_most_recent_by_date_wins(self):
        entries = [
            make_feedback(date="2026-09-07", response="worse_than_usual"),
            make_feedback(date="2026-09-12", response="no_issue"),
        ]
        latest = latest_feedback_for_exercise(entries, "partial-range-squat")
        self.assertEqual(latest.response, "no_issue")

    def test_a_later_no_issue_lifts_an_earlier_blocking_mark(self):
        """The concrete design-06 §7 scenario: "no_issue removes that
        feedback block" — verified end to end through
        `feedback_blocks_progression`."""
        entries = [
            make_feedback(date="2026-09-01", response="worse_than_usual"),
            make_feedback(date="2026-09-08", response="no_issue"),
        ]
        latest = latest_feedback_for_exercise(entries, "partial-range-squat")
        self.assertFalse(feedback_blocks_progression(latest))

    def test_two_different_constraints_on_one_exercise_the_most_recent_across_both_wins(self):
        entries = [
            make_feedback(
                date="2026-09-01",
                constraint_id="hip-impingement-squat-depth",
                response="no_issue",
            ),
            make_feedback(
                date="2026-09-10",
                constraint_id="knee-tracking-cue",
                response="worse_than_usual",
            ),
        ]
        latest = latest_feedback_for_exercise(entries, "partial-range-squat")
        self.assertEqual(latest.constraint_id, "knee-tracking-cue")
        self.assertEqual(latest.response, "worse_than_usual")


# ---------------------------------------------------------------------------
# propose_progression — double_progression
# ---------------------------------------------------------------------------


class TestDoubleProgressionProposal(unittest.TestCase):
    def make_prescription(self, **overrides):
        overrides.setdefault("target_load_kg", 40.0)
        return ExercisePrescription(**make_exercise(**overrides))

    def test_all_working_sets_at_top_reps_and_rir_proposes_an_increase(self):
        exercise = self.make_prescription(sets=3, rep_max=12, target_rir=2)
        sets = [make_set(set_number=n, reps=12, rir=2) for n in (1, 2, 3)]
        proposal = propose_progression(exercise, completed_sets=sets)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.rule, "double_progression")
        self.assertEqual(proposal.current_load_kg, 40.0)
        self.assertAlmostEqual(
            proposal.proposed_load_kg, round(40.0 * (1 + PROGRESSION_LOAD_INCREMENT_PCT), 2)
        )
        self.assertEqual(proposal.percentage_increment, PROGRESSION_LOAD_INCREMENT_PCT)
        self.assertEqual(len(proposal.qualifying_sets), 3)

    def test_the_proposal_names_its_evidence(self):
        """PROMPT-15's acceptance line: "cites the qualifying sets,
        percentage increment, and rule.\""""
        exercise = self.make_prescription(sets=3, rep_max=12, target_rir=2)
        sets = [make_set(set_number=n, reps=12, rir=2) for n in (1, 2, 3)]
        proposal = propose_progression(exercise, completed_sets=sets)
        self.assertIn("double_progression", proposal.evidence_summary)
        self.assertIn("2.5%", proposal.evidence_summary)
        self.assertIn("3", proposal.evidence_summary)

    def test_fewer_qualifying_sets_than_prescribed_proposes_nothing(self):
        exercise = self.make_prescription(sets=3, rep_max=12, target_rir=2)
        sets = [
            make_set(set_number=1, reps=12, rir=2),
            make_set(set_number=2, reps=12, rir=2),
            make_set(set_number=3, reps=9, rir=2),
        ]
        self.assertIsNone(propose_progression(exercise, completed_sets=sets))

    def test_reps_below_the_top_of_the_range_proposes_nothing(self):
        exercise = self.make_prescription(sets=1, rep_max=12, target_rir=2)
        sets = [make_set(set_number=1, reps=11, rir=2)]
        self.assertIsNone(propose_progression(exercise, completed_sets=sets))

    def test_rir_lower_than_prescribed_proposes_nothing(self):
        """This module's conservative, uniform reading: a set that required
        going harder than prescribed to reach the top of the range is not
        treated as qualifying evidence."""
        exercise = self.make_prescription(sets=1, rep_max=12, target_rir=2)
        sets = [make_set(set_number=1, reps=12, rir=1)]
        self.assertIsNone(propose_progression(exercise, completed_sets=sets))

    def test_a_null_rir_never_qualifies(self):
        """design-06 §6: "If Hevy is absent or its RPE is null... do not
        invent set performance.\""""
        exercise = self.make_prescription(sets=1, rep_max=12, target_rir=2)
        sets = [make_set(set_number=1, reps=12, rir=None)]
        self.assertIsNone(propose_progression(exercise, completed_sets=sets))

    def test_only_the_most_recent_session_is_considered(self):
        exercise = self.make_prescription(sets=1, rep_max=12, target_rir=2)
        old_qualifying = make_set(
            date="2026-08-01", session_id="old", set_number=1, reps=12, rir=2
        )
        recent_not_qualifying = make_set(
            date="2026-09-07", session_id="recent", set_number=1, reps=8, rir=2
        )
        proposal = propose_progression(
            exercise, completed_sets=[old_qualifying, recent_not_qualifying]
        )
        self.assertIsNone(proposal)

    def test_no_completed_sets_proposes_nothing(self):
        exercise = self.make_prescription()
        self.assertIsNone(propose_progression(exercise, completed_sets=None))
        self.assertIsNone(propose_progression(exercise, completed_sets=[]))

    def test_completed_sets_for_a_different_exercise_are_ignored(self):
        exercise = self.make_prescription(sets=1, rep_max=12, target_rir=2)
        sets = [make_set(exercise_id="bench-press", set_number=1, reps=12, rir=2)]
        self.assertIsNone(propose_progression(exercise, completed_sets=sets))


# ---------------------------------------------------------------------------
# propose_progression — two_for_two
# ---------------------------------------------------------------------------


class TestTwoForTwoProposal(unittest.TestCase):
    def make_prescription(self, **overrides):
        overrides.setdefault("progression_rule", "two_for_two")
        overrides.setdefault("target_load_kg", 40.0)
        return ExercisePrescription(**make_exercise(**overrides))

    def test_two_consecutive_qualifying_sessions_propose_an_increase(self):
        exercise = self.make_prescription(rep_max=12, target_rir=2)
        sets = [
            make_set(date="2026-09-01", session_id="s1", set_number=3, reps=14, rir=2),
            make_set(date="2026-09-08", session_id="s2", set_number=3, reps=14, rir=2),
        ]
        proposal = propose_progression(exercise, completed_sets=sets)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.rule, "two_for_two")
        self.assertEqual(set(proposal.session_ids), {"s1", "s2"})

    def test_only_the_final_set_of_each_session_is_checked(self):
        exercise = self.make_prescription(rep_max=12, target_rir=2)
        sets = [
            make_set(date="2026-09-01", session_id="s1", set_number=1, reps=8, rir=2),
            make_set(date="2026-09-01", session_id="s1", set_number=2, reps=14, rir=2),
            make_set(date="2026-09-08", session_id="s2", set_number=1, reps=8, rir=2),
            make_set(date="2026-09-08", session_id="s2", set_number=2, reps=14, rir=2),
        ]
        proposal = propose_progression(exercise, completed_sets=sets)
        self.assertIsNotNone(proposal)

    def test_only_one_qualifying_session_proposes_nothing(self):
        exercise = self.make_prescription(rep_max=12, target_rir=2)
        sets = [
            make_set(date="2026-09-01", session_id="s1", set_number=1, reps=12, rir=2),
            make_set(date="2026-09-08", session_id="s2", set_number=1, reps=14, rir=2),
        ]
        self.assertIsNone(propose_progression(exercise, completed_sets=sets))

    def test_the_most_recent_session_must_qualify(self):
        """An older qualifying session does not extend a streak once a more
        recent non-qualifying one exists."""
        exercise = self.make_prescription(rep_max=12, target_rir=2)
        sets = [
            make_set(date="2026-08-25", session_id="s0", set_number=1, reps=14, rir=2),
            make_set(date="2026-09-01", session_id="s1", set_number=1, reps=14, rir=2),
            make_set(date="2026-09-08", session_id="s2", set_number=1, reps=12, rir=2),
        ]
        self.assertIsNone(propose_progression(exercise, completed_sets=sets))

    def test_a_single_session_is_not_enough(self):
        exercise = self.make_prescription(rep_max=12, target_rir=2)
        sets = [make_set(date="2026-09-08", session_id="s2", set_number=1, reps=14, rir=2)]
        self.assertIsNone(propose_progression(exercise, completed_sets=sets))


# ---------------------------------------------------------------------------
# Feedback and the missing-load precondition, both gate propose_progression
# ---------------------------------------------------------------------------


class TestProgressionPreconditions(unittest.TestCase):
    def qualifying_sets(self):
        return [make_set(set_number=n, reps=12, rir=2) for n in (1, 2, 3)]

    def test_mild_irritation_suppresses_an_otherwise_qualifying_proposal(self):
        exercise = ExercisePrescription(**make_exercise(target_load_kg=40.0))
        feedback = WorkoutFeedback(**make_feedback(response="mild_irritation"))
        proposal = propose_progression(
            exercise, completed_sets=self.qualifying_sets(), feedback=feedback
        )
        self.assertIsNone(proposal)

    def test_worse_than_usual_suppresses_an_otherwise_qualifying_proposal(self):
        exercise = ExercisePrescription(**make_exercise(target_load_kg=40.0))
        feedback = WorkoutFeedback(**make_feedback(response="worse_than_usual"))
        proposal = propose_progression(
            exercise, completed_sets=self.qualifying_sets(), feedback=feedback
        )
        self.assertIsNone(proposal)

    def test_no_issue_does_not_suppress_a_qualifying_proposal(self):
        exercise = ExercisePrescription(**make_exercise(target_load_kg=40.0))
        feedback = WorkoutFeedback(**make_feedback(response="no_issue"))
        proposal = propose_progression(
            exercise, completed_sets=self.qualifying_sets(), feedback=feedback
        )
        self.assertIsNotNone(proposal)

    def test_no_issue_alone_cannot_fabricate_performance_evidence(self):
        """design-06 §7: "no_issue... is never itself proof reps were
        completed." With no completed-set evidence at all, no_issue feedback
        must not manufacture a proposal."""
        exercise = ExercisePrescription(**make_exercise(target_load_kg=40.0))
        feedback = WorkoutFeedback(**make_feedback(response="no_issue"))
        proposal = propose_progression(exercise, completed_sets=None, feedback=feedback)
        self.assertIsNone(proposal)

    def test_a_load_increase_proposal_requires_a_history_established_load(self):
        """design-06 §4/§6: "The model must not invent a kilogram figure...
        Progression proposals require a non-null evidenced load." No
        `target_load_kg` at all means there is nothing to scale by 2.5%,
        however good the set evidence looks."""
        exercise = ExercisePrescription(**make_exercise(target_load_kg=None))
        self.assertIsNone(exercise.target_load_kg)
        proposal = propose_progression(exercise, completed_sets=self.qualifying_sets())
        self.assertIsNone(proposal)


# ---------------------------------------------------------------------------
# apply_progression_proposal — the only writer of the changed prescription
# ---------------------------------------------------------------------------


class TestApplyProgressionProposal(unittest.TestCase):
    def test_only_target_load_kg_changes(self):
        exercise = ExercisePrescription(**make_exercise(target_load_kg=40.0))
        proposal = ProgressionProposal(
            exercise_id=exercise.exercise_id,
            rule="double_progression",
            session_ids=["s1"],
            current_load_kg=40.0,
            proposed_load_kg=41.0,
            percentage_increment=0.025,
            qualifying_sets=[CompletedSetEvidence(**make_set(reps=12, rir=2))],
            evidence_summary="3 of 3 sets qualified; propose a 2.5% increase.",
        )
        updated = apply_progression_proposal(exercise, proposal)
        self.assertEqual(updated.target_load_kg, 41.0)
        before = exercise.model_dump(exclude={"target_load_kg"})
        after = updated.model_dump(exclude={"target_load_kg"})
        self.assertEqual(before, after)

    def test_range_of_motion_stays_fixed_for_a_modified_movement(self):
        """design-06 §6: "range of motion stays fixed for a modified
        movement" — the fields a `modify` constraint requires
        (`applied_constraint_ids`, `execution_notes`) must survive an
        accepted progression untouched."""
        exercise = ExercisePrescription(
            **make_exercise(
                target_load_kg=40.0,
                applied_constraint_ids=["hip-impingement-squat-depth"],
                execution_notes="Only to the established pain-free depth.",
            )
        )
        proposal = ProgressionProposal(
            exercise_id=exercise.exercise_id,
            rule="double_progression",
            session_ids=["s1"],
            current_load_kg=40.0,
            proposed_load_kg=41.0,
            percentage_increment=0.025,
            qualifying_sets=[CompletedSetEvidence(**make_set(reps=12, rir=2))],
            evidence_summary="3 of 3 sets qualified; propose a 2.5% increase.",
        )
        updated = apply_progression_proposal(exercise, proposal)
        self.assertEqual(updated.applied_constraint_ids, ["hip-impingement-squat-depth"])
        self.assertEqual(updated.execution_notes, "Only to the established pain-free depth.")

    def test_the_original_exercise_is_never_mutated(self):
        exercise = ExercisePrescription(**make_exercise(target_load_kg=40.0))
        proposal = ProgressionProposal(
            exercise_id=exercise.exercise_id,
            rule="double_progression",
            session_ids=["s1"],
            current_load_kg=40.0,
            proposed_load_kg=41.0,
            percentage_increment=0.025,
            qualifying_sets=[CompletedSetEvidence(**make_set(reps=12, rir=2))],
            evidence_summary="3 of 3 sets qualified; propose a 2.5% increase.",
        )
        apply_progression_proposal(exercise, proposal)
        self.assertEqual(exercise.target_load_kg, 40.0)

    def test_a_mismatched_exercise_id_is_rejected(self):
        exercise = ExercisePrescription(**make_exercise(target_load_kg=40.0))
        proposal = ProgressionProposal(
            exercise_id="a-different-exercise",
            rule="double_progression",
            session_ids=["s1"],
            current_load_kg=40.0,
            proposed_load_kg=41.0,
            percentage_increment=0.025,
            qualifying_sets=[CompletedSetEvidence(**make_set(reps=12, rir=2))],
            evidence_summary="3 of 3 sets qualified; propose a 2.5% increase.",
        )
        with self.assertRaises(ValueError):
            apply_progression_proposal(exercise, proposal)


if __name__ == "__main__":
    unittest.main()
