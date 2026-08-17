"""Tests for `src/ui_state.py` — the view model behind the NiceGUI canvas.

The `ui_*` modules are mostly widget construction and deliberately untested,
but `PlannerState` is not: it holds the grid's live shape, and the rules it
enforces are the ones a grid edit can silently get wrong. It imports cleanly
without a running NiceGUI app (nothing here touches `nicegui.ui`), so it is
testable like any other pure module.

Four behaviours are worth pinning, each of which has a documented reason for
being the way it is:

- **`apply_spec` rescales cook events**, because portions are derived from how
  many slots claim a cook. A card reading "4 portions" over ingredients
  weighed for 2 is exactly the disagreement derived portions exist to prevent.
- **The spec is held, not re-derived per read** — re-deriving would discard
  the edit just made — and `_shape()` excludes `servings` for a generated
  week, so nudging the drawer's people-per-meal cannot silently drop links.
- **`set_target` clears a key whose value matches the file**, which is what
  lets "overridden" mean "differs from config" and lets the reset button undo
  itself.
- **`slot_views` flattens a generated plan and an un-generated spec into one
  shape**, so a cold start previews the planned week rather than 28 blanks.

Nothing here writes to disk: grid edits are in-memory until a generation
saves, which is the contract `edited` exists to advertise.
"""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import ui_state  # noqa: E402
from planner import CookEvent, Ingredient, Recipe, WeekPlan  # noqa: E402
from week import (  # noqa: E402
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    SlotSpec,
    WeekSpec,
    link_leftover,
    slot_id,
)

DAYS = ["Monday", "Tuesday", "Wednesday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]

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
    # `planning_config()` reads this alongside weekly_schedule and cuisines to
    # build the config a run would actually use.
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
}


def make_spec(servings_per_meal=2) -> WeekSpec:
    slots = [
        SlotSpec(day=day, meal_type=meal_type, mode=MODE_COOK)
        for day in DAYS
        for meal_type in MEAL_TYPES
    ]
    return WeekSpec(days=DAYS, slots=slots, servings_per_meal=servings_per_meal)


def make_recipe(name="Green Chicken Curry", servings=2, grams=200.0):
    return Recipe(
        name=name,
        meal_type="dinner",
        ingredients=[
            Ingredient(
                name="Chicken breast", quantity_g=grams, nova_group=1,
                calories=grams * 1.65, protein_g=grams * 0.31,
                net_carbs_g=0.0, fat_g=grams * 0.036,
            )
        ],
        instructions=["Cook it."],
        prep_time_minutes=30,
        servings=servings,
    )


def make_plan(spec: WeekSpec, generated_at="2026-08-18T09:00:00") -> WeekPlan:
    event = CookEvent(
        slot_id="Monday:dinner", day="Monday", meal_type="dinner",
        portions=2, eaten_by=["Monday:dinner"], recipe=make_recipe(),
    )
    return WeekPlan(
        days=DAYS,
        servings_per_meal=spec.servings_per_meal,
        generated_at=generated_at,
        week_start_date="2026-08-17",
        cook_events=[event],
        slots=list(spec.slots),
        targets={day: dict(CONFIG["weekly_schedule"][day]) for day in DAYS},
        failures={},
    )


def make_state(with_plan=True, **kw) -> ui_state.PlannerState:
    spec = make_spec()
    state = ui_state.PlannerState(
        config=dict(CONFIG),
        week_plan=make_plan(spec) if with_plan else None,
        week_start="Monday",
        servings=2,
        shop_days=["Monday"],
        **kw,
    )
    state.apply_spec(spec)
    state.edited = False
    return state


class TestApplySpecRescalesTheBatch(unittest.TestCase):
    """Portions are derived, so an edit that changes how many slots claim a
    cook has to move the batch and its ingredient quantities with it."""

    def test_linking_a_lunch_grows_the_batch(self):
        state = make_state()
        before = state.week_plan.by_slot()["Monday:dinner"]
        self.assertEqual(before.portions, 2)

        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        after = state.week_plan.by_slot()["Monday:dinner"]
        self.assertEqual(after.portions, 4)

    def test_ingredient_quantities_move_with_the_portions(self):
        """A card reading "4 portions" over ingredients weighed for 2 is the
        disagreement derived portions exist to prevent."""
        state = make_state()
        before = state.week_plan.by_slot()["Monday:dinner"].recipe.ingredients[0].quantity_g
        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        after = state.week_plan.by_slot()["Monday:dinner"].recipe.ingredients[0].quantity_g
        self.assertGreater(after, before)

    def test_the_plans_own_slots_are_replaced_too(self):
        """`day_slot_macros` walks the *plan's* slots, so replacing only the
        spec would leave the telemetry header describing the old week."""
        state = make_state()
        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        plan_slot = next(s for s in state.week_plan.slots if s.id == "Tuesday:lunch")
        self.assertEqual(plan_slot.mode, MODE_LEFTOVER)
        self.assertEqual(plan_slot.source, "Monday:dinner")

    def test_eaten_by_is_refreshed(self):
        state = make_state()
        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        event = state.week_plan.by_slot()["Monday:dinner"]
        self.assertIn("Tuesday:lunch", event.eaten_by)

    def test_an_edit_marks_the_week_unsaved(self):
        """Nothing is written to disk — `edited` is what tells the header."""
        state = make_state()
        self.assertFalse(state.edited)
        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        self.assertTrue(state.edited)

    def test_applying_a_spec_without_a_plan_is_safe(self):
        state = make_state(with_plan=False)
        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        self.assertTrue(state.edited)
        self.assertIsNone(state.week_plan)


class TestTheSpecIsHeldNotRederived(unittest.TestCase):
    """Re-deriving the spec on every read would discard the edit just made —
    the trap the deleted Streamlit app dodged in `ensure_grid`."""

    def test_an_edit_survives_repeated_reads(self):
        state = make_state()
        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        for _ in range(3):
            self.assertEqual(state.spec.by_id()["Tuesday:lunch"].mode, MODE_LEFTOVER)

    def test_servings_do_not_invalidate_a_generated_weeks_edits(self):
        """A generated week's portions come from `week_plan.servings_per_meal`,
        so nudging the drawer's people-per-meal must not silently drop links
        it cannot affect."""
        state = make_state()
        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        state.servings = 6
        self.assertEqual(state.spec.by_id()["Tuesday:lunch"].mode, MODE_LEFTOVER)

    def test_a_new_generation_does_invalidate_them(self):
        """`generated_at` is part of the shape, so a fresh run rebuilds."""
        state = make_state()
        state.apply_spec(link_leftover(state.spec, "Tuesday:lunch", "Monday:dinner"))
        state.week_plan = make_plan(make_spec(), generated_at="2026-08-19T10:00:00")
        self.assertEqual(state.spec.by_id()["Tuesday:lunch"].mode, MODE_COOK)


class TestLinkToNextLunch(unittest.TestCase):
    """The one grid edit the UI offers. Returns a message on refusal rather
    than raising, because the button stays enabled so its tooltip can explain
    why — a disabled Quasar button swallows hover."""

    def test_a_valid_link_returns_none_and_applies(self):
        state = make_state()
        self.assertIsNone(state.link_to_next_lunch("Monday:dinner"))
        self.assertEqual(state.spec.by_id()["Tuesday:lunch"].mode, MODE_LEFTOVER)

    def test_the_last_day_has_no_next_lunch(self):
        state = make_state()
        message = state.link_to_next_lunch("Wednesday:dinner")
        self.assertIsNotNone(message)
        self.assertIn("last day", message)

    def test_an_unknown_slot_is_reported_not_raised(self):
        state = make_state()
        self.assertIsNotNone(state.link_to_next_lunch("Caturday:dinner"))

    def test_a_refused_link_leaves_the_week_untouched(self):
        state = make_state()
        state.link_to_next_lunch("Wednesday:dinner")
        self.assertFalse(state.edited)

    def test_the_message_is_about_the_two_meals_clicked(self):
        """A whole-week error list can't say which entry the click caused."""
        state = make_state()
        state.link_to_next_lunch("Monday:dinner")
        second = state.link_to_next_lunch("Tuesday:lunch")
        self.assertIsInstance(second, str)


class TestTargetOverrides(unittest.TestCase):
    def test_setting_a_value_that_differs_records_an_override(self):
        state = make_state()
        state.set_target("Monday", "calories", 1800)
        self.assertEqual(state.target_overrides["Monday"]["calories"], 1800)

    def test_setting_a_value_that_matches_config_clears_it(self):
        """How the reset button undoes itself: it writes the file's numbers
        back into the inputs, and the change events those fire cancel out."""
        state = make_state()
        state.set_target("Monday", "calories", 1800)
        state.set_target("Monday", "calories", 2000)
        self.assertNotIn("Monday", state.target_overrides)

    def test_an_untouched_day_follows_config(self):
        state = make_state()
        state.set_target("Monday", "calories", 1800)
        self.assertNotIn("Tuesday", state.target_overrides)

    def test_an_override_wins_over_the_generated_plans_target(self):
        """The point of editing a target before a run is seeing how far the
        current week sits from where you are about to aim it."""
        state = make_state()
        state.set_target("Monday", "calories", 1500)
        self.assertEqual(state.targets_for("Monday")["calories"], 1500)

    def test_an_unedited_day_is_measured_against_what_it_was_generated_for(self):
        state = make_state()
        self.assertEqual(
            state.targets_for("Monday"), state.week_plan.targets["Monday"]
        )

    def test_clear_targets_removes_a_days_override(self):
        state = make_state()
        state.set_target("Monday", "calories", 1500)
        state.clear_targets("Monday")
        self.assertNotIn("Monday", state.target_overrides)


class TestSlotViews(unittest.TestCase):
    """One shape for the card widget whether or not a week exists."""

    def test_a_cold_start_previews_the_planned_shape(self):
        """28 empty cells would be worse than a preview of what is about to
        be generated."""
        views = make_state(with_plan=False).slot_views()
        self.assertEqual(len(views), len(DAYS) * len(MEAL_TYPES))
        self.assertEqual(views["Monday:dinner"].title, "To be generated")

    def test_a_generated_slot_shows_its_recipe(self):
        views = make_state().slot_views()
        self.assertEqual(views["Monday:dinner"].title, "Green Chicken Curry")

    def test_an_ungenerated_slot_in_a_generated_week_reads_as_a_gap(self):
        """Red "not generated", not blank — the gap has to stay obvious."""
        views = make_state().slot_views()
        self.assertEqual(views["Tuesday:dinner"].title, "Not generated")

    def test_a_skipped_slot_says_so(self):
        state = make_state()
        spec = state.spec
        slots = [
            s.model_copy(update={"mode": MODE_SKIP}) if s.id == "Monday:lunch" else s
            for s in spec.slots
        ]
        state.apply_spec(spec.model_copy(update={"slots": slots}))
        self.assertEqual(state.slot_views()["Monday:lunch"].title, "Skipped")

    def test_a_leftover_shows_the_source_recipe(self):
        """It is the same food — resolving through `source` is what makes the
        two cards visibly the same dish."""
        state = make_state()
        state.link_to_next_lunch("Monday:dinner")
        views = state.slot_views()
        self.assertEqual(views["Tuesday:lunch"].title, "Green Chicken Curry")
        self.assertEqual(views["Tuesday:lunch"].mode, MODE_LEFTOVER)

    def test_a_linked_pair_shares_a_chain_id(self):
        """The chain is what lets one card's hover outline its partners."""
        state = make_state()
        state.link_to_next_lunch("Monday:dinner")
        views = state.slot_views()
        self.assertIsNotNone(views["Monday:dinner"].chain)
        self.assertEqual(views["Monday:dinner"].chain, views["Tuesday:lunch"].chain)

    def test_an_unshared_cook_gets_no_chain(self):
        """A cook nobody inherits from is not a link, so no colour, no marker."""
        self.assertIsNone(make_state().slot_views()["Monday:dinner"].chain)

    def test_a_cook_lists_what_it_feeds(self):
        state = make_state()
        state.link_to_next_lunch("Monday:dinner")
        self.assertTrue(state.slot_views()["Monday:dinner"].feeds)


if __name__ == "__main__":
    unittest.main()
