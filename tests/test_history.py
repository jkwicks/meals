"""Tests for dating meal_history.json entries and archiving their planned
targets (week.day_date, planner.record_week_history) — Phase 5a of the UI
roadmap in CLAUDE.md.

Real `LocalJSONRepository` on a temp directory, `run_sync` to bridge the
async call from a plain `unittest.TestCase` — same pattern
`test_sync_service.TestPersistence` already uses for a repository round trip.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from planner import CookEvent, Ingredient, Recipe, WeekPlan, record_week_history  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402
from week import day_date  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

TARGETS = {
    day: {"calories": 2200.0, "protein_g": 144.0, "net_carbs_g": 180.0, "fat_g": 70.0}
    for day in DAYS
}


def recipe(name: str, meal_type: str) -> Recipe:
    return Recipe(
        name=name,
        meal_type=meal_type,
        ingredients=[
            Ingredient(
                name="Chicken thigh",
                quantity_g=200,
                nova_group=1,
                calories=400,
                protein_g=40,
                net_carbs_g=0,
                fat_g=25,
            )
        ],
        instructions=["Cook it."],
        prep_time_minutes=20,
    )


def week_plan(**overrides) -> WeekPlan:
    defaults = dict(
        days=DAYS,
        servings_per_meal=2,
        generated_at="2026-08-16T20:31:21",
        week_start_date="2026-08-10",
        cook_events=[
            CookEvent(
                slot_id="Sunday:dinner",
                day="Sunday",
                meal_type="dinner",
                portions=2,
                style="grill",
                cuisine="middle_eastern",
                recipe=recipe("Lamb Sirloin", "dinner"),
            ),
        ],
        slots=[],
        targets=TARGETS,
    )
    defaults.update(overrides)
    return WeekPlan(**defaults)


class TestDayDate(unittest.TestCase):
    def test_first_day_is_the_start_date_itself(self):
        self.assertEqual(day_date("2026-08-10", DAYS, "Monday"), "2026-08-10")

    def test_last_day_is_six_days_later(self):
        self.assertEqual(day_date("2026-08-10", DAYS, "Sunday"), "2026-08-16")

    def test_a_middle_day_is_its_own_offset(self):
        self.assertEqual(day_date("2026-08-10", DAYS, "Wednesday"), "2026-08-12")

    def test_a_week_start_day_other_than_monday_still_offsets_by_rotation_position(self):
        rotated = ["Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Monday", "Tuesday"]
        self.assertEqual(day_date("2026-08-12", rotated, "Monday"), "2026-08-17")


class TestRecordWeekHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(data_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def entry_for(self, day: str) -> dict:
        history = run_sync(self.repo.load_history())
        return next(e for e in history if e["day_of_week"] == day)

    def test_a_cooked_day_is_dated_from_week_start_date(self):
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        self.assertEqual(self.entry_for("Sunday")["date"], "2026-08-16")

    def test_the_days_planned_targets_are_archived_verbatim(self):
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        self.assertEqual(self.entry_for("Sunday")["targets"], TARGETS["Sunday"])

    def test_a_plan_with_no_week_start_date_records_no_date(self):
        """Pre-Phase-4 plans (or a bare `regenerate_single_day` on one)
        carry no week_start_date — recording a guessed date for a *past*
        day would be worse than recording none, unlike today_in_week's
        live fallback for "does this cover today"."""
        run_sync(record_week_history(week_plan(week_start_date=None), self.repo, config=None))
        self.assertIsNone(self.entry_for("Sunday")["date"])

    def test_a_day_missing_from_targets_records_no_targets_rather_than_raising(self):
        sparse_targets = {k: v for k, v in TARGETS.items() if k != "Sunday"}
        run_sync(record_week_history(week_plan(targets=sparse_targets), self.repo, config=None))
        self.assertIsNone(self.entry_for("Sunday")["targets"])

    def test_existing_fields_are_unaffected_by_the_new_ones(self):
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        entry = self.entry_for("Sunday")
        self.assertEqual(entry["cuisine"], "middle_eastern")
        self.assertEqual(entry["styles"], {"dinner": "grill"})
        self.assertEqual(entry["recipe_names"], ["Lamb Sirloin"])

    def test_a_day_with_no_cook_events_is_not_recorded_at_all(self):
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        history = run_sync(self.repo.load_history())
        recorded_days = {e["day_of_week"] for e in history}
        self.assertEqual(recorded_days, {"Sunday"})

    def test_old_untouched_entries_keep_validating_with_no_date_or_targets_key(self):
        """Regression guard: entries written before this change have neither
        key at all (not even `None`) — history.json is a plain list of
        dicts, never Pydantic-validated (repository.py deals in plain
        dicts/lists), so nothing should choke on their absence."""
        pre_migration_entry = {
            "day_of_week": "Saturday",
            "generated_at": "2026-07-01T09:00:00",
            "cuisine": "thai",
            "styles": {},
            "main_proteins": [],
            "recipe_names": ["Old Dish"],
        }
        run_sync(self.repo.save_history([pre_migration_entry]))
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        history = run_sync(self.repo.load_history())
        self.assertEqual(history[0]["day_of_week"], "Saturday")
        self.assertNotIn("date", history[0])
        self.assertEqual(history[1]["date"], "2026-08-16")


if __name__ == "__main__":
    unittest.main()
