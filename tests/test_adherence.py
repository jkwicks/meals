"""Tests for adherence tracking — whether the plan actually happened.

CHANGE-QUEUE.md's adherence item (`future-ideas.md` 5b). Three layers, in
the order the queue's own entry insisted on: storage first, then the pure
derived read, then the view models. Nothing here touches a network, a model
or a NiceGUI element tree — the write path goes through a real
`LocalJSONRepository` pointed at a temp directory, and the two read paths are
pure functions.

**The widget layer is deliberately absent**, per the `ui-work` skill: the
mark buttons in `ui_today.py` are element construction, and every decision
they make (is this slot markable, is this session already recorded, does a
second click clear) lives in `ui_state` or `nutrition_engine` precisely so it
can be pinned here instead.

Two of these tests exist because of a specific way this could break quietly:

- `TestSessionIdSpelling` pins `SessionMatch.session_id` equal to
  `planner.workout_session_id`. The two spell the same key in two modules on
  purpose — `nutrition_engine` imports nothing from `planner` — and a drift
  between them would file a manual mark under a key the button that wrote it
  never reads back, which renders as a tick that silently un-ticks itself.
- `TestClearIsNotAWrite` pins that clearing a mark nothing holds leaves no
  file at all. Absence and a status are different answers throughout this
  feature, and a repository that wrote `{"meals": [], "workouts": []}` on
  every stray double-click would make an untouched checkout indistinguishable
  from a marked-then-unmarked one on disk.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import ui_state  # noqa: E402
from nutrition_engine import match_recorded_sessions  # noqa: E402
from planner import (  # noqa: E402
    ADHERENCE_EATEN,
    ADHERENCE_SKIPPED,
    ADHERENCE_SWAPPED,
    CookEvent,
    Ingredient,
    Recipe,
    WeekPlan,
    workout_session_id,
)
from repository import LocalJSONRepository, run_sync  # noqa: E402
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, WeekSpec  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]

# Monday of the fixture week. Every date literal below is derived from it, so
# nothing here reads the clock — the standing rule the CLAUDE.md "Tests"
# section states after two weekday-dependent assertions were found by a date
# rollover mid-session.
WEEK_START = "2026-08-17"
MONDAY = "2026-08-17"
TUESDAY = "2026-08-18"

CONFIG = {
    "weekly_schedule": {
        day: {"calories": 2000, "protein_g": 144, "net_carbs_g": 120, "fat_g": 89}
        for day in DAYS
    },
    "meal_types": MEAL_TYPES,
    "meal_styles": {"dinner": {"curry": "..."}},
    "meal_weights": {"breakfast": 0.30, "lunch": 0.30, "dinner": 0.30, "snack": 0.10},
    "cuisines": ["thai"],
    "cuisine_meal_types": ["dinner"],
    "dietary_rules": {
        "allowed_nova_groups": [1, 2, 3],
        "banned_ingredients": [],
        "active_diet_styles": [],
    },
    "inventory_rules": {"fridge_safe_days": 4, "perishable_day_gap": 3},
    "week_start_day": "Monday",
    "training_schedule": [],
    "serving_rules": {"servings_per_meal": 2},
    "shopping": {"shop_days": ["Monday"]},
    "inventory_to_clear": [],
}


def make_spec(skip_snack=True) -> WeekSpec:
    slots = [
        SlotSpec(
            day=day,
            meal_type=meal_type,
            mode=MODE_SKIP if (skip_snack and meal_type == "snack") else MODE_COOK,
        )
        for day in DAYS
        for meal_type in MEAL_TYPES
    ]
    return WeekSpec(days=DAYS, slots=slots, servings_per_meal=2)


def make_plan(spec: WeekSpec, week_start_date=WEEK_START) -> WeekPlan:
    recipe = Recipe(
        name="Green Chicken Curry",
        meal_type="dinner",
        ingredients=[
            Ingredient(
                name="Chicken breast", quantity_g=200.0, nova_group=1,
                calories=330.0, protein_g=62.0, net_carbs_g=0.0, fat_g=7.2,
            )
        ],
        instructions=["Cook it."],
        prep_time_minutes=30,
        servings=2,
    )
    return WeekPlan(
        days=DAYS,
        servings_per_meal=2,
        generated_at="2026-08-17T09:00:00",
        week_start_date=week_start_date,
        cook_events=[
            CookEvent(
                slot_id="Monday:dinner", day="Monday", meal_type="dinner",
                portions=2, eaten_by=["Monday:dinner"], recipe=recipe,
            )
        ],
        slots=list(spec.slots),
        targets={day: dict(CONFIG["weekly_schedule"][day]) for day in DAYS},
        failures={},
    )


def make_state(week_start_date=WEEK_START, training=(), **kw) -> ui_state.PlannerState:
    spec = make_spec()
    config = dict(CONFIG, training_schedule=[dict(s) for s in training])
    state = ui_state.PlannerState(
        config=config,
        week_plan=make_plan(spec, week_start_date=week_start_date),
        week_start="Monday",
        servings=2,
        shop_days=["Monday"],
        training_schedule=[dict(s) for s in training],
        **kw,
    )
    state.apply_spec(spec)
    state.edited = False
    return state


GYM = {
    "day": "Monday",
    "time": "06:30",
    "type": "gym_hypertrophy",
    "duration_minutes": 60,
    "estimated_burn_kcal": 300,
}
WALK = {
    "day": "Monday",
    "time": "18:00",
    "type": "walk",
    "duration_minutes": 45,
    "estimated_burn_kcal": 150,
}


def activity(date, session_type, start_time, minutes=55.0, kcal=210.0) -> dict:
    return {
        "date": date,
        "session_type": session_type,
        "start_time": start_time,
        "duration_min": minutes,
        "net_calories": kcal,
        "source": "garmin",
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class AdherenceStorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(data_dir=self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def meal(self, slot_id, status=ADHERENCE_EATEN, date=MONDAY) -> dict:
        return {
            "date": date,
            "slot_id": slot_id,
            "status": status,
            "marked_at": f"{date}T20:00:00+00:00",
        }

    def workout(self, session_id, date=MONDAY, session_type="gym_hypertrophy") -> dict:
        return {
            "date": date,
            "session_id": session_id,
            "session_type": session_type,
            "completed": True,
            "source": "manual",
            "marked_at": f"{date}T20:00:00+00:00",
        }


class TestAdherenceRoundTrip(AdherenceStorageCase):
    """`data/adherence.json` — one file, two lists, keyed by date plus one
    more field. The shape decision itself, exercised."""

    def test_an_absent_file_reads_as_both_sections_empty(self):
        """The cold start: nothing is written until something is marked, so a
        checkout that never marks anything must read exactly as it did before
        this feature existed."""
        self.assertEqual(
            run_sync(self.repo.load_adherence()), {"meals": [], "workouts": []}
        )

    def test_a_meal_mark_round_trips(self):
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner")))
        loaded = run_sync(self.repo.load_adherence())
        self.assertEqual(len(loaded["meals"]), 1)
        self.assertEqual(loaded["meals"][0]["slot_id"], "Monday:dinner")
        self.assertEqual(loaded["meals"][0]["status"], ADHERENCE_EATEN)

    def test_re_marking_one_meal_updates_rather_than_appends(self):
        """The one thing separating this from `save_rejection_entry`'s append:
        a meal was eaten or it wasn't, and two rows disagreeing about
        Thursday's dinner leave nothing able to say which is current."""
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner")))
        run_sync(
            self.repo.save_meal_adherence(
                self.meal("Monday:dinner", status=ADHERENCE_SWAPPED)
            )
        )
        loaded = run_sync(self.repo.load_adherence())
        self.assertEqual(len(loaded["meals"]), 1)
        self.assertEqual(loaded["meals"][0]["status"], ADHERENCE_SWAPPED)

    def test_two_meals_on_one_date_are_two_rows(self):
        """The whole reason the key is `date` + `slot_id` rather than the bare
        `date` every biometric section uses: a date holds four meals."""
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:lunch")))
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner")))
        loaded = run_sync(self.repo.load_adherence())
        self.assertEqual(
            sorted(row["slot_id"] for row in loaded["meals"]),
            ["Monday:dinner", "Monday:lunch"],
        )

    def test_the_same_slot_on_two_dates_is_two_rows(self):
        """And the reason `slot_id` alone will not do: it is a weekday name,
        so it repeats every seven days."""
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner", date=MONDAY)))
        run_sync(
            self.repo.save_meal_adherence(
                self.meal("Monday:dinner", date="2026-08-24")
            )
        )
        loaded = run_sync(self.repo.load_adherence())
        self.assertEqual(len(loaded["meals"]), 2)

    def test_the_two_sections_never_touch_each_other(self):
        """One file, but separate lists — the part of the storage decision
        that actually matters, and the call this codebase has now made five
        times."""
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner")))
        run_sync(self.repo.save_workout_completion(self.workout("06:30:gym_hypertrophy")))
        loaded = run_sync(self.repo.load_adherence())
        self.assertEqual(len(loaded["meals"]), 1)
        self.assertEqual(len(loaded["workouts"]), 1)
        self.assertEqual(loaded["workouts"][0]["session_id"], "06:30:gym_hypertrophy")

    def test_rows_are_sorted_by_date_then_key(self):
        """So the file reads chronologically to a human and to a diff, the
        same property `_upsert_dated_entry`'s own sort buys."""
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:lunch", date=TUESDAY)))
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner", date=MONDAY)))
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:breakfast", date=MONDAY)))
        rows = run_sync(self.repo.load_adherence())["meals"]
        self.assertEqual(
            [(row["date"], row["slot_id"]) for row in rows],
            [
                (MONDAY, "Monday:breakfast"),
                (MONDAY, "Monday:dinner"),
                (TUESDAY, "Monday:lunch"),
            ],
        )

    def test_an_entry_without_both_key_halves_is_refused(self):
        """Loudly, rather than filed under a key nothing reads back — the same
        direction `_upsert_dated_entry` takes for a missing `date`."""
        with self.assertRaises(ValueError):
            run_sync(self.repo.save_meal_adherence({"date": MONDAY, "status": "eaten"}))
        with self.assertRaises(ValueError):
            run_sync(self.repo.save_meal_adherence({"slot_id": "Monday:dinner"}))


class TestClearingAMark(AdherenceStorageCase):
    """Un-marking deletes the row. Absence and a status are different answers
    — "nobody has said" versus "somebody said" — so there is no fourth
    "unknown" status every reader would have to treat as absent anyway."""

    def test_clearing_removes_the_row(self):
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner")))
        run_sync(self.repo.clear_meal_adherence(MONDAY, "Monday:dinner"))
        self.assertEqual(run_sync(self.repo.load_adherence())["meals"], [])

    def test_clearing_leaves_its_neighbours_alone(self):
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:lunch")))
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner")))
        run_sync(self.repo.clear_meal_adherence(MONDAY, "Monday:dinner"))
        rows = run_sync(self.repo.load_adherence())["meals"]
        self.assertEqual([row["slot_id"] for row in rows], ["Monday:lunch"])

    def test_clearing_a_workout_leaves_the_meals_alone(self):
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:dinner")))
        run_sync(self.repo.save_workout_completion(self.workout("06:30:gym_hypertrophy")))
        run_sync(self.repo.clear_workout_completion(MONDAY, "06:30:gym_hypertrophy"))
        loaded = run_sync(self.repo.load_adherence())
        self.assertEqual(len(loaded["meals"]), 1)
        self.assertEqual(loaded["workouts"], [])


class TestClearIsNotAWrite(AdherenceStorageCase):
    """Clearing what was never marked writes nothing at all.

    A double-click on a toggle is an ordinary interaction, and rewriting the
    file to record that nothing changed would make an untouched checkout
    indistinguishable on disk from a marked-then-unmarked one — the same
    waste `_replace_dated_entries` already declines for an empty day.
    """

    def test_no_file_is_created(self):
        run_sync(self.repo.clear_meal_adherence(MONDAY, "Monday:dinner"))
        self.assertFalse(os.path.exists(self.repo.paths.adherence))

    def test_an_existing_file_is_left_byte_identical(self):
        run_sync(self.repo.save_meal_adherence(self.meal("Monday:lunch")))
        before = Path(self.repo.paths.adherence).read_bytes()
        run_sync(self.repo.clear_meal_adherence(MONDAY, "Monday:dinner"))
        self.assertEqual(Path(self.repo.paths.adherence).read_bytes(), before)


class TestTolerantReads(AdherenceStorageCase):
    """A half-written or older file must read as a shape callers can index —
    the same guarantee `_read_biometrics` makes, and for the same reason."""

    def test_a_file_missing_a_section_reads_it_as_empty(self):
        Path(self.repo.paths.adherence).write_text(
            json.dumps({"meals": [{"date": MONDAY, "slot_id": "Monday:dinner"}]})
        )
        self.assertEqual(run_sync(self.repo.load_adherence())["workouts"], [])

    def test_a_null_where_a_list_belongs_reads_as_empty(self):
        Path(self.repo.paths.adherence).write_text(
            json.dumps({"meals": None, "workouts": None})
        )
        self.assertEqual(
            run_sync(self.repo.load_adherence()), {"meals": [], "workouts": []}
        )


# ---------------------------------------------------------------------------
# The derived read
# ---------------------------------------------------------------------------


class TestMatchRecordedSessions(unittest.TestCase):
    """`nutrition_engine.match_recorded_sessions` — did the watch see it?

    Pure over two lists and a date, and this is the half that shrank the
    workout side of the queue's entry: v0.33.0's `activity_log` already holds
    what was done, so only what it *didn't* record needs storing.
    """

    def test_a_matching_activity_is_recorded_with_its_evidence(self):
        matches = match_recorded_sessions(
            [activity(MONDAY, "gym_hypertrophy", "06:38")], [GYM], MONDAY
        )
        self.assertTrue(matches[0].recorded)
        self.assertEqual(matches[0].recorded_start, "06:38")
        self.assertEqual(matches[0].recorded_minutes, 55.0)
        self.assertEqual(matches[0].recorded_kcal, 210.0)

    def test_a_declared_session_with_no_recording_is_unrecorded(self):
        matches = match_recorded_sessions([], [GYM], MONDAY)
        self.assertFalse(matches[0].recorded)
        self.assertIsNone(matches[0].recorded_start)

    def test_a_different_modality_does_not_answer_for_it(self):
        """A walk is not evidence that the declared lift happened."""
        matches = match_recorded_sessions(
            [activity(MONDAY, "walk", "06:30")], [GYM], MONDAY
        )
        self.assertFalse(matches[0].recorded)

    def test_another_days_recording_does_not_answer_for_it(self):
        matches = match_recorded_sessions(
            [activity(TUESDAY, "gym_hypertrophy", "06:30")], [GYM], MONDAY
        )
        self.assertFalse(matches[0].recorded)

    def test_an_unmapped_activity_answers_nothing(self):
        """`GARMIN_SESSION_TYPES` has no catch-all, so a yoga class arrives
        with `session_type: None` — skipped here exactly as
        `propose_training_schedule` skips it."""
        row = activity(MONDAY, "gym_hypertrophy", "06:30")
        row["session_type"] = None
        self.assertFalse(match_recorded_sessions([row], [GYM], MONDAY)[0].recorded)

    def test_time_chooses_between_candidates_but_never_rejects_one(self):
        """A 06:30 session started at 07:10 is the same session. A tolerance
        window would have to pick a number that is wrong for somebody."""
        matches = match_recorded_sessions(
            [activity(MONDAY, "gym_hypertrophy", "07:10")], [GYM], MONDAY
        )
        self.assertTrue(matches[0].recorded)

    def test_one_recording_cannot_answer_for_two_declared_sessions(self):
        """Each declared session claims the nearest *unclaimed* recording of
        its type, so the second is honestly unrecorded rather than silently
        confirmed."""
        second_gym = dict(GYM, time="18:00")
        matches = match_recorded_sessions(
            [activity(MONDAY, "gym_hypertrophy", "06:35")],
            [GYM, second_gym],
            MONDAY,
        )
        self.assertTrue(matches[0].recorded)
        self.assertFalse(matches[1].recorded)

    def test_the_nearest_recording_wins_not_the_first(self):
        matches = match_recorded_sessions(
            [
                activity(MONDAY, "gym_hypertrophy", "17:55", kcal=400.0),
                activity(MONDAY, "gym_hypertrophy", "06:35", kcal=210.0),
            ],
            [GYM],
            MONDAY,
        )
        self.assertEqual(matches[0].recorded_start, "06:35")


class TestSessionIdSpelling(unittest.TestCase):
    """`SessionMatch.session_id` and `planner.workout_session_id` spell one
    key in two modules, because `nutrition_engine` imports nothing from
    `planner`. A drift files a manual mark under a key the button that wrote
    it never reads back — which renders as a tick that un-ticks itself and
    raises nothing anywhere."""

    def test_the_two_spellings_agree(self):
        match = match_recorded_sessions([], [GYM], MONDAY)[0]
        self.assertEqual(match.session_id, workout_session_id(GYM["time"], GYM["type"]))


# ---------------------------------------------------------------------------
# The view models
# ---------------------------------------------------------------------------


class TestMealAdherenceView(unittest.TestCase):
    def rows(self, **kw):
        base = {
            "date": MONDAY,
            "slot_id": "Monday:dinner",
            "status": ADHERENCE_EATEN,
            "marked_at": "x",
        }
        return [dict(base, **kw)]

    def test_a_day_with_no_calendar_date_is_not_markable(self):
        """A `slot_id` is a weekday name, so without the plan's
        `week_start_date` there is no key to file a mark under — the same
        pre-migration tolerance `logged_actuals_for` draws, costing the
        affordance rather than a readout."""
        view = ui_state.meal_adherence_view(self.rows(), None, ("Monday:dinner",))
        self.assertFalse(view.markable)
        self.assertEqual(view.statuses, {})

    def test_marks_are_matched_by_date_not_by_weekday(self):
        view = ui_state.meal_adherence_view(
            self.rows(date=TUESDAY), MONDAY, ("Monday:dinner",)
        )
        self.assertIsNone(view.status_for("Monday:dinner"))

    def test_a_mark_reads_back_with_its_label(self):
        view = ui_state.meal_adherence_view(self.rows(), MONDAY, ("Monday:dinner",))
        self.assertEqual(view.status_for("Monday:dinner"), ADHERENCE_EATEN)
        self.assertEqual(view.label_for("Monday:dinner"), "ate this")

    def test_the_summary_is_silent_until_something_is_marked(self):
        """The whole week is unmarked until somebody starts, and "0 of 3
        marked" on six days out of seven is a UI element announcing that a
        feature exists rather than reporting anything."""
        view = ui_state.meal_adherence_view([], MONDAY, ("Monday:dinner",))
        self.assertEqual(view.summary, "")

    def test_the_summary_counts_marked_against_planned(self):
        view = ui_state.meal_adherence_view(
            self.rows(), MONDAY, ("Monday:lunch", "Monday:dinner")
        )
        self.assertEqual(view.summary, "1 of 2 marked")

    def test_the_summary_names_the_split_only_when_it_differs(self):
        rows = self.rows() + [
            {
                "date": MONDAY,
                "slot_id": "Monday:lunch",
                "status": ADHERENCE_SKIPPED,
                "marked_at": "x",
            }
        ]
        view = ui_state.meal_adherence_view(
            rows, MONDAY, ("Monday:lunch", "Monday:dinner")
        )
        self.assertEqual(view.summary, "2 of 2 marked · 1 as planned")


class TestWorkoutMarksView(unittest.TestCase):
    """Two independent ways a session can be done, and only one is stored."""

    def sessions(self, state, day="Monday"):
        return [s for s in state.training_for(day) if not s.is_rest]

    def test_a_recorded_session_reads_as_garmins(self):
        state = make_state(training=[GYM])
        marks = ui_state.workout_marks_view(
            self.sessions(state),
            [activity(MONDAY, "gym_hypertrophy", "06:38")],
            [],
            MONDAY,
        )
        self.assertTrue(marks[0].done)
        self.assertEqual(marks[0].source, "garmin")
        self.assertIn("06:38", marks[0].detail)

    def test_a_manual_mark_answers_for_a_session_the_watch_missed(self):
        state = make_state(training=[GYM])
        marks = ui_state.workout_marks_view(
            self.sessions(state),
            [],
            [
                {
                    "date": MONDAY,
                    "session_id": "06:30:gym_hypertrophy",
                    "completed": True,
                }
            ],
            MONDAY,
        )
        self.assertTrue(marks[0].done)
        self.assertEqual(marks[0].source, "manual")
        self.assertEqual(marks[0].detail, "")

    def test_garmin_wins_when_both_say_yes(self):
        """Only reachable when a later re-sync found a session already marked
        by hand — at which point the watch's record is the better evidence and
        the stale manual row is the one that should stop being cited."""
        state = make_state(training=[GYM])
        marks = ui_state.workout_marks_view(
            self.sessions(state),
            [activity(MONDAY, "gym_hypertrophy", "06:38")],
            [
                {
                    "date": MONDAY,
                    "session_id": "06:30:gym_hypertrophy",
                    "completed": True,
                }
            ],
            MONDAY,
        )
        self.assertEqual(marks[0].source, "garmin")

    def test_an_unanswered_session_is_neither(self):
        state = make_state(training=[GYM])
        marks = ui_state.workout_marks_view(self.sessions(state), [], [], MONDAY)
        self.assertFalse(marks[0].done)
        self.assertEqual(marks[0].source, "")
        self.assertTrue(marks[0].markable)

    def test_a_day_with_no_date_reports_nothing_as_checked(self):
        """Reporting them as "not done" would state as fact something never
        actually checked."""
        state = make_state(training=[GYM])
        marks = ui_state.workout_marks_view(
            self.sessions(state), [activity(MONDAY, "gym_hypertrophy", "06:38")], [], None
        )
        self.assertFalse(marks[0].recorded)
        self.assertFalse(marks[0].markable)

    def test_a_marked_row_that_is_not_completed_does_not_count(self):
        state = make_state(training=[GYM])
        marks = ui_state.workout_marks_view(
            self.sessions(state),
            [],
            [{"date": MONDAY, "session_id": "06:30:gym_hypertrophy", "completed": False}],
            MONDAY,
        )
        self.assertFalse(marks[0].done)


# ---------------------------------------------------------------------------
# PlannerState, against a real repository
# ---------------------------------------------------------------------------


class TestMarkingThroughState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(data_dir=self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_a_skipped_slot_is_not_in_the_planned_set(self):
        """Nothing was planned to be eaten there, so "did you eat it" has no
        answer — and counting it would make every week with a skipped snack
        read as permanently 3-of-4 adhered."""
        state = make_state()
        planned = state.meal_adherence_for("Monday").planned
        self.assertIn("Monday:dinner", planned)
        self.assertNotIn("Monday:snack", planned)

    def test_a_leftover_slot_is_still_planned(self):
        """It is a meal the week intends you to eat, whoever cooked it."""
        spec = make_spec()
        spec = spec.model_copy(
            update={
                "slots": [
                    slot.model_copy(update={"mode": MODE_LEFTOVER, "source": "Monday:dinner"})
                    if slot.id == "Tuesday:lunch"
                    else slot
                    for slot in spec.slots
                ]
            }
        )
        state = make_state()
        state.apply_spec(spec)
        self.assertIn("Tuesday:lunch", state.meal_adherence_for("Tuesday").planned)

    def test_marking_persists_and_shows_immediately(self):
        """Both halves: the file, and the in-memory copy the repaint reads —
        there is no reload between a click and the paint that has to show it."""
        state = make_state()
        run_sync(state.mark_meal(self.repo, "Monday", "dinner", ADHERENCE_EATEN))

        self.assertEqual(
            state.meal_adherence_for("Monday").status_for("Monday:dinner"),
            ADHERENCE_EATEN,
        )
        stored = run_sync(self.repo.load_adherence())["meals"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["date"], MONDAY)
        self.assertEqual(stored[0]["status"], ADHERENCE_EATEN)

    def test_marking_a_different_status_replaces_the_first(self):
        state = make_state()
        run_sync(state.mark_meal(self.repo, "Monday", "dinner", ADHERENCE_EATEN))
        run_sync(state.mark_meal(self.repo, "Monday", "dinner", ADHERENCE_SWAPPED))
        self.assertEqual(
            state.meal_adherence_for("Monday").status_for("Monday:dinner"),
            ADHERENCE_SWAPPED,
        )
        self.assertEqual(len(run_sync(self.repo.load_adherence())["meals"]), 1)

    def test_clicking_the_status_a_slot_already_carries_clears_it(self):
        """What makes three buttons a complete control rather than three
        one-way doors."""
        state = make_state()
        run_sync(state.mark_meal(self.repo, "Monday", "dinner", ADHERENCE_EATEN))
        run_sync(state.mark_meal(self.repo, "Monday", "dinner", ADHERENCE_EATEN))
        self.assertIsNone(
            state.meal_adherence_for("Monday").status_for("Monday:dinner")
        )
        self.assertEqual(run_sync(self.repo.load_adherence())["meals"], [])

    def test_a_day_with_no_calendar_date_cannot_be_marked(self):
        """A plan generated before `week_start_date` existed. The UI does not
        offer the buttons; this refuses rather than filing the mark under a
        key nothing will read back."""
        state = make_state(week_start_date=None)
        run_sync(state.mark_meal(self.repo, "Monday", "dinner", ADHERENCE_EATEN))
        self.assertEqual(run_sync(self.repo.load_adherence())["meals"], [])

    def test_marking_a_workout_persists_and_toggles(self):
        state = make_state(training=[GYM, WALK])
        state.biometrics = {"activity_log": []}
        mark = state.workout_marks_for("Monday")[0]
        run_sync(state.mark_workout(self.repo, "Monday", mark))
        self.assertTrue(state.workout_marks_for("Monday")[0].marked)

        again = state.workout_marks_for("Monday")[0]
        run_sync(state.mark_workout(self.repo, "Monday", again))
        self.assertFalse(state.workout_marks_for("Monday")[0].marked)
        self.assertEqual(run_sync(self.repo.load_adherence())["workouts"], [])

    def test_a_session_garmin_recorded_is_never_stored(self):
        """`activity_log` is the answer for those, and a stored `completed`
        row beside it would be a second answer free to disagree the moment a
        re-sync changed one of them."""
        state = make_state(training=[GYM])
        state.biometrics = {"activity_log": [activity(MONDAY, "gym_hypertrophy", "06:38")]}
        mark = state.workout_marks_for("Monday")[0]
        self.assertTrue(mark.recorded)
        run_sync(state.mark_workout(self.repo, "Monday", mark))
        self.assertEqual(run_sync(self.repo.load_adherence())["workouts"], [])

    def test_a_rest_day_offers_nothing_to_complete(self):
        """`TrainingView.is_rest` folds a typed rest and a zero-burn session
        together, and neither is a session that could have been missed."""
        rest = {"day": "Monday", "time": "00:00", "type": "rest", "estimated_burn_kcal": 0}
        state = make_state(training=[rest])
        self.assertEqual(state.workout_marks_for("Monday"), [])


if __name__ == "__main__":
    unittest.main()
