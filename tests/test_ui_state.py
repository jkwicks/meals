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
from ui_theme import format_day_label, training_icon  # noqa: E402
from planner import (  # noqa: E402
    LOCATION_RESTRICTION_PHRASES,
    SUNDAY_PREP_REHEAT_MINUTES,
    CookEvent,
    Ingredient,
    Recipe,
    SundayPrepSession,
    WeekPlan,
)
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


class TestUnlinkSlot(unittest.TestCase):
    """The inverse of `link_to_next_lunch`, and the only way to undo one —
    clicking the link button again hits `leftover_link_error`'s repeat-click
    guard rather than toggling. `ui_generation.generate_week`'s own "unlink
    one, or turn off one of the two toggles" warning named an action the UI
    did not offer until this existed.
    """

    def test_it_turns_a_leftover_back_into_a_cook(self):
        state = make_state()
        state.link_to_next_lunch("Monday:dinner")
        self.assertIsNone(state.unlink_slot("Tuesday:lunch"))
        self.assertEqual(state.spec.by_id()["Tuesday:lunch"].mode, MODE_COOK)

    def test_it_shrinks_the_source_batch_back(self):
        """Portions are derived, so dropping a claim rescales the cook event
        by the same linear arithmetic that grew it."""
        state = make_state()
        state.link_to_next_lunch("Monday:dinner")
        grown = state.week_plan.by_slot()["Monday:dinner"].portions
        state.unlink_slot("Tuesday:lunch")
        shrunk = state.week_plan.by_slot()["Monday:dinner"].portions
        self.assertLess(shrunk, grown)

    def test_unlinking_a_cook_slot_is_refused(self):
        state = make_state()
        self.assertIn("isn't a leftover", state.unlink_slot("Monday:dinner"))

    def test_an_unknown_slot_is_refused(self):
        self.assertIsNotNone(make_state().unlink_slot("Caturday:brunch"))

    def test_the_view_carries_the_raw_source_id(self):
        """The unlink notification reports what the source shrank to, and
        re-parsing the humanized `source_label` to recover it would be a
        second, lossy encoding of something already known."""
        state = make_state()
        state.link_to_next_lunch("Monday:dinner")
        self.assertEqual(state.slot_views()["Tuesday:lunch"].source_id, "Monday:dinner")
        self.assertEqual(state.slot_views()["Monday:dinner"].source_id, "")


class TestSundayPrepBadges(unittest.TestCase):
    """`is_sunday_prepped` covers the batch's own MODE_COOK anchor slot, not
    just the MODE_LEFTOVER slots eating it — see `planner.is_sunday_prepped`'s
    docstring for the live week this was written against, where a real
    long-cook anchor rendered as a plain "cook" card because the badge logic
    only ever looked at leftover slots. The shake candidate that rides along
    in the same session is the other case: it gets the badge too, but must
    keep its own prep time rather than collapsing to the dinner batches'
    reheat estimate, since a shake morning genuinely blends fresh — only its
    base was portioned ahead.
    """

    def make_state_with_session(self):
        state = make_state()
        state.link_to_next_lunch("Monday:dinner")
        dinner_event = CookEvent(
            slot_id="Monday:dinner", day="Monday", meal_type="dinner",
            portions=4, eaten_by=["Monday:dinner", "Tuesday:lunch"], recipe=make_recipe(),
        )
        shake_event = CookEvent(
            slot_id="Monday:breakfast", day="Monday", meal_type="breakfast",
            portions=2, eaten_by=["Monday:breakfast"],
            recipe=make_recipe(name="Protein Shake").model_copy(
                update={"meal_type": "breakfast", "prep_time_minutes": 5}
            ),
        )
        unrelated_event = CookEvent(
            slot_id="Wednesday:dinner", day="Wednesday", meal_type="dinner",
            portions=2, eaten_by=["Wednesday:dinner"],
            recipe=make_recipe(name="Ordinary Dinner"),
        )
        state.week_plan = state.week_plan.model_copy(
            update={
                "cook_events": [dinner_event, shake_event, unrelated_event],
                "sunday_prep_session": SundayPrepSession(
                    total_active_minutes=90,
                    candidate_slot_ids=["Monday:dinner", "Monday:breakfast"],
                ),
            }
        )
        return state

    def test_the_anchor_s_own_cook_slot_gets_the_badge(self):
        views = self.make_state_with_session().slot_views()
        self.assertEqual(views["Monday:dinner"].prep_badge, "fridge")

    def test_the_anchor_s_own_cook_slot_collapses_to_the_reheat_estimate(self):
        """Nothing is actually cooked fresh on the anchor's calendar day
        either — the whole batch was cooked Sunday — so its prep_minutes
        should read the same reheat estimate a downstream leftover gets."""
        views = self.make_state_with_session().slot_views()
        self.assertEqual(views["Monday:dinner"].prep_minutes, SUNDAY_PREP_REHEAT_MINUTES)

    def test_a_downstream_leftover_still_gets_the_badge(self):
        views = self.make_state_with_session().slot_views()
        self.assertEqual(views["Tuesday:lunch"].prep_badge, "fridge")

    def test_the_shake_gets_the_badge(self):
        views = self.make_state_with_session().slot_views()
        self.assertEqual(views["Monday:breakfast"].prep_badge, "fridge")

    def test_the_shake_keeps_its_own_prep_time(self):
        """Unlike the dinner batches, a shake morning genuinely blends fresh
        — only its base was portioned ahead — so it must not collapse to the
        reheat estimate the dinner anchor does."""
        views = self.make_state_with_session().slot_views()
        self.assertEqual(views["Monday:breakfast"].prep_minutes, 5)

    def test_an_unrelated_cook_gets_no_badge(self):
        views = self.make_state_with_session().slot_views()
        self.assertEqual(views["Wednesday:dinner"].prep_badge, "")


# `base_schedule`/`location_rules` shaped after the shipped `schedule.json`,
# plus one unrecognised restriction tag that file doesn't have — a config
# token with no `LOCATION_RESTRICTION_PHRASES` entry is exactly what the
# tag/prose pairing has to survive.
LOCATION_CONFIG = dict(
    CONFIG,
    base_schedule={"Monday": "Office", "Tuesday": "Home"},
    location_rules={
        "Office": {
            "lunch_mode": "leftover",
            "restrictions": ["mystery_tag", "portable", "no_long_prep"],
            "max_prep_minutes": 0,
        },
    },
)


def make_context_state(training=(), config=LOCATION_CONFIG) -> ui_state.PlannerState:
    """A state whose config names locations, with `training` in the drawer.

    The schedule goes on the *state*, not the config, because that is where
    `day_context` reads it — the drawer's live copy, so an unsaved session
    shows on the Today tab the same way an unsaved target override already
    shows in the header.
    """
    state = make_state()
    state.config = dict(config)
    state.training_schedule = [dict(session) for session in training]
    return state


class TestDayContextLocation(unittest.TestCase):
    """Where a day is spent, as the Today tab reads it.

    Pinned because this mirrors `planner.build_location_note`'s scope rule
    rather than re-deciding it: a location constrains only the meals it
    declares a `<meal_type>_mode` for. Getting that wrong renders as an
    ordinary-looking card carrying a constraint the prompt never sent, which
    is not a failure anything else would catch.
    """

    def test_a_scheduled_day_reports_where_it_is_spent(self):
        location = ui_state.day_context(make_context_state(), "Monday").location
        self.assertEqual(location.name, "Office")
        self.assertEqual(location.max_prep_minutes, 0)

    def test_a_day_the_schedule_does_not_name_has_no_location(self):
        """The opt-in tolerance `week.location_for` already extends."""
        self.assertIsNone(
            ui_state.day_context(make_context_state(), "Wednesday").location
        )

    def test_a_config_without_base_schedule_has_no_location(self):
        state = make_context_state(config=CONFIG)
        self.assertIsNone(ui_state.day_context(state, "Monday").location)

    def test_a_location_with_no_rule_still_says_where_you_are(self):
        """"Home" is named by `base_schedule` and absent from `location_rules`.

        Still worth a chip: it answers the question the strip asks, and an
        empty rule simply constrains nothing.
        """
        location = ui_state.day_context(make_context_state(), "Tuesday").location
        self.assertEqual(location.name, "Home")
        self.assertEqual(location.meal_modes, {})
        self.assertEqual(location.phrase_pairs, [])

    def test_a_restriction_only_covers_the_meals_the_location_names(self):
        """Office names `lunch_mode` and nothing else, so breakfast — eaten at
        home before leaving — carries no "must travel in a container"."""
        location = ui_state.day_context(make_context_state(), "Monday").location
        self.assertTrue(location.constrains("lunch"))
        self.assertFalse(location.constrains("breakfast"))
        self.assertTrue(location.brief("lunch"))
        self.assertEqual(location.brief("breakfast"), "")

    def test_an_unrecognised_tag_does_not_shift_the_pairs(self):
        """The chip's label and its tooltip have to describe the same tag.

        `phrases` drops a tag with no prose, exactly as `build_location_note`
        does, so zipping it against `restrictions` would have paired
        "mystery_tag" with portable's sentence.
        """
        location = ui_state.day_context(make_context_state(), "Monday").location
        self.assertEqual(
            [tag for tag, _ in location.phrase_pairs], ["portable", "no_long_prep"]
        )
        for tag, phrase in location.phrase_pairs:
            self.assertEqual(phrase, LOCATION_RESTRICTION_PHRASES[tag])


class TestDayContextTraining(unittest.TestCase):
    """A day's sessions, and which meal each one claims.

    The note badges classify `training_notes` by `TRAINING_NOTE_PREFIXES`, a
    constant `planner` owns and writes the notes with — these tests are what
    stop a reworded prompt from silently dropping the badge, since a note that
    fails to parse renders as no badge at all rather than as an error.
    """

    def test_sessions_are_ordered_by_clock_time(self):
        """The drawer appends a new session, so config order is not time order."""
        state = make_context_state(
            training=[
                {"day": "Monday", "time": "06:30", "type": "cardio_hiit",
                 "duration_minutes": 30, "estimated_burn_kcal": 320},
                {"day": "Monday", "time": "05:30", "type": "gym_hypertrophy",
                 "duration_minutes": 60, "estimated_burn_kcal": 350},
            ]
        )
        sessions = ui_state.day_context(state, "Monday").sessions
        self.assertEqual([s.time for s in sessions], ["05:30", "06:30"])

    def test_only_the_scheduled_day_is_reported(self):
        state = make_context_state(
            training=[{"day": "Monday", "time": "05:30", "type": "cardio_easy",
                       "duration_minutes": 25, "estimated_burn_kcal": 180}]
        )
        self.assertEqual(ui_state.day_context(state, "Tuesday").sessions, [])

    def test_the_days_burn_is_totalled(self):
        state = make_context_state(
            training=[
                {"day": "Monday", "time": "05:30", "type": "gym_hypertrophy",
                 "duration_minutes": 60, "estimated_burn_kcal": 350},
                {"day": "Monday", "time": "06:30", "type": "cardio_hiit",
                 "duration_minutes": 30, "estimated_burn_kcal": 320},
            ]
        )
        self.assertEqual(ui_state.day_context(state, "Monday").total_burn_kcal, 670)

    def test_a_rest_day_is_not_a_workout(self):
        """`apply_training_adjustments` skips a rest entry, so it buys no
        calories — the chip must not imply it did."""
        state = make_context_state(
            training=[{"day": "Monday", "time": "00:00", "type": "rest",
                       "duration_minutes": 0, "estimated_burn_kcal": 0}]
        )
        context = ui_state.day_context(state, "Monday")
        self.assertTrue(context.sessions[0].is_rest)
        self.assertEqual(context.active_sessions, [])
        self.assertEqual(context.total_burn_kcal, 0)

    def test_a_zero_burn_session_reads_as_rest_too(self):
        """Matches `apply_training_adjustments`' own filter, which drops a
        zero-burn session as surely as a typed rest day."""
        state = make_context_state(
            training=[{"day": "Monday", "time": "07:00", "type": "walk",
                       "duration_minutes": 20, "estimated_burn_kcal": 0}]
        )
        self.assertTrue(ui_state.day_context(state, "Monday").sessions[0].is_rest)

    def test_the_post_workout_meal_is_named(self):
        """An evening session pins the meal after it; the badge says which."""
        state = make_context_state(
            training=[{"day": "Monday", "time": "18:00", "type": "gym_hypertrophy",
                       "duration_minutes": 60, "estimated_burn_kcal": 400}]
        )
        notes = ui_state.day_context(state, "Monday").meal_notes
        self.assertEqual(notes["dinner"].kind, "post")
        # The prefix and brackets are stripped: the badge already says
        # POST-WORKOUT, so the tooltip carries only the reason.
        self.assertNotIn("POST-WORKOUT MEAL:", notes["dinner"].text)
        self.assertFalse(notes["dinner"].text.endswith("]"))
        self.assertIn("glycogen", notes["dinner"].text)

    def test_a_meal_within_digestion_range_is_flagged_pre_workout(self):
        """Lunch at 12:30 with a 14:00 session — inside the 120-minute rule."""
        state = make_context_state(
            training=[{"day": "Monday", "time": "14:00", "type": "cardio_hiit",
                       "duration_minutes": 30, "estimated_burn_kcal": 320}]
        )
        notes = ui_state.day_context(state, "Monday").meal_notes
        self.assertEqual(notes["lunch"].kind, "pre")
        self.assertNotIn("PRE-WORKOUT MEAL:", notes["lunch"].text)
        self.assertNotIn("breakfast", notes)

    def test_an_untrained_day_carries_no_notes(self):
        context = ui_state.day_context(make_context_state(), "Monday")
        self.assertEqual(context.sessions, [])
        self.assertEqual(context.meal_notes, {})

    def test_the_drawers_schedule_is_what_is_shown(self):
        """Not the file's — a session added in the drawer has to appear here,
        the same "live preview of the next run" contract `targets_for` keeps.
        """
        state = make_context_state()
        self.assertEqual(ui_state.day_context(state, "Monday").sessions, [])
        state.add_training_session()
        state.training_schedule[0]["day"] = "Monday"
        self.assertEqual(len(ui_state.day_context(state, "Monday").sessions), 1)


# A Monday five years back. `today_day()` reads the real clock — the only
# thing in this suite that does — so every test below pins the plan's dates
# rather than letting the fixture's happen to fall near the current date.
# Left to itself, `make_plan`'s 2026-08-17 week covers some real dates and not
# others, and these tests would pass or fail depending on the day they ran.
STALE_WEEK_START = "2020-01-06"


def make_stale_state() -> ui_state.PlannerState:
    """A plan dated to a week that is definitively not the current one."""
    state = make_state()
    state.week_plan = state.week_plan.model_copy(
        update={"week_start_date": STALE_WEEK_START}
    )
    return state


def make_current_state() -> ui_state.PlannerState:
    """A plan covering the real current week, whatever day the suite runs on.

    `days` is widened to all seven weekday names — `today_in_week` returns the
    real weekday, and the shared 3-day `CONFIG` could not contain it on four
    days out of seven.
    """
    from datetime import date, timedelta

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week = [(monday + timedelta(days=i)).strftime("%A") for i in range(7)]
    state = make_state()
    state.config = dict(
        CONFIG,
        weekly_schedule={day: dict(CONFIG["weekly_schedule"][DAYS[0]]) for day in week},
    )
    state.week_plan = state.week_plan.model_copy(
        update={"week_start_date": monday.isoformat(), "days": week}
    )
    return state


class TestViewedDay(unittest.TestCase):
    """The Today tab's day picker — which day it lands on, and where it stops.

    Before the picker existed, a week not covering today replaced the whole
    panel with a message. It now has to resolve to a browsable day instead,
    which is the behaviour change these pin.
    """

    def test_it_falls_back_to_the_first_day_when_today_is_not_in_the_week(self):
        state = make_stale_state()
        self.assertFalse(state.week_covers_today())
        self.assertEqual(state.viewed_day(), "Monday")
        self.assertFalse(state.viewing_today())

    def test_it_lands_on_today_when_the_week_covers_it(self):
        from datetime import date

        state = make_current_state()
        self.assertTrue(state.week_covers_today())
        self.assertEqual(state.viewed_day(), date.today().strftime("%A"))
        self.assertTrue(state.viewing_today())

    def test_a_selected_day_wins(self):
        state = make_stale_state()
        state.select_day("Wednesday")
        self.assertEqual(state.viewed_day(), "Wednesday")

    def test_browsing_away_from_today_stops_being_today(self):
        state = make_current_state()
        state.select_day(state.days[0])
        state.step_viewed_day(3)
        self.assertFalse(state.viewing_today())
        self.assertTrue(state.week_covers_today())

    def test_selecting_a_day_outside_the_week_falls_back_to_following_today(self):
        """A stale name — the drawer's week start moved under it, say — must
        not pin the tab to a day the grid has no column for."""
        state = make_stale_state()
        state.select_day("Friday")
        self.assertIsNone(state.selected_day)
        self.assertEqual(state.viewed_day(), "Monday")

    def test_no_plan_means_no_day(self):
        """The one case with genuinely nothing to show."""
        self.assertIsNone(make_state(with_plan=False).viewed_day())

    def test_stepping_moves_through_the_week(self):
        state = make_stale_state()
        state.step_viewed_day(1)
        self.assertEqual(state.viewed_day(), "Tuesday")
        state.step_viewed_day(1)
        self.assertEqual(state.viewed_day(), "Wednesday")
        state.step_viewed_day(-2)
        self.assertEqual(state.viewed_day(), "Monday")

    def test_stepping_clamps_at_both_ends(self):
        """Clamped, not wrapped: the loaded plan holds exactly these days, and
        wrapping the last day round to the first would pretend it is a loop."""
        state = make_stale_state()
        state.step_viewed_day(-1)
        self.assertEqual(state.viewed_day(), state.days[0])
        state.step_viewed_day(99)
        self.assertEqual(state.viewed_day(), state.days[-1])
        state.step_viewed_day(1)
        self.assertEqual(state.viewed_day(), state.days[-1])

    def test_the_reset_clears_rather_than_repoints(self):
        """`select_day(None)` restores "follow today" — storing today's *name*
        would pin the tab to whichever day the page happened to load on."""
        state = make_stale_state()
        state.select_day("Wednesday")
        state.select_day(None)
        self.assertIsNone(state.selected_day)

    def test_covering_today_is_about_the_columns_not_the_span(self):
        """A grid narrower than seven days has a span wider than its columns.

        `today_in_week` answers "is today inside this week's seven-day span",
        which here is true while the grid has no column for it — and it is the
        columns a picker can navigate to. Built so today's own weekday is
        deliberately excluded, so the assertion holds on any day of the week.
        """
        from datetime import date, timedelta

        today = date.today()
        elsewhere = [(today + timedelta(days=i)).strftime("%A") for i in (1, 2, 3)]
        monday = today - timedelta(days=today.weekday())
        state = make_state()
        state.config = dict(
            CONFIG,
            weekly_schedule={
                day: dict(CONFIG["weekly_schedule"][DAYS[0]]) for day in elsewhere
            },
        )
        state.week_plan = state.week_plan.model_copy(
            update={"week_start_date": monday.isoformat(), "days": elsewhere}
        )
        self.assertIsNotNone(state.today_day())
        self.assertFalse(state.week_covers_today())
        self.assertEqual(state.viewed_day(), elsewhere[0])


class TestDayDates(unittest.TestCase):
    """Dating a day, and refusing to when the plan can't support it."""

    def test_a_day_is_dated_from_the_weeks_real_start(self):
        state = make_state()
        self.assertEqual(state.day_date_iso("Monday"), "2026-08-17")
        self.assertEqual(state.day_date_iso("Wednesday"), "2026-08-19")

    def test_a_plan_without_a_start_date_is_not_dated(self):
        """Pre-migration tolerance: `week.day_date` refuses a `generated_at`
        anchor, because a plausible-looking wrong date in a tab title is worse
        than no date at all."""
        state = make_state()
        state.week_plan = state.week_plan.model_copy(update={"week_start_date": None})
        self.assertIsNone(state.day_date_iso("Monday"))

    def test_an_unknown_day_is_not_dated(self):
        self.assertIsNone(make_state().day_date_iso("Friday"))

    def test_the_label_degrades_to_the_bare_weekday(self):
        self.assertEqual(format_day_label("Thursday", None), "Thursday")
        self.assertEqual(format_day_label("Thursday", None, short=True), "Thu")

    def test_the_label_carries_the_date_when_there_is_one(self):
        self.assertEqual(format_day_label("Monday", "2026-08-17"), "Monday 17 August")
        self.assertEqual(
            format_day_label("Monday", "2026-08-17", short=True), "Mon 17 Aug"
        )

    def test_the_day_name_comes_from_the_rotation_not_the_date(self):
        """The long form labels with the plan's own weekday name, so a
        mis-dated plan reads oddly rather than silently renaming the day."""
        self.assertTrue(format_day_label("Monday", "2026-08-17").startswith("Monday"))


class TestTrainingIcons(unittest.TestCase):
    """Which glyph stands for a workout type.

    Icon rather than colour is what separates the types — every hue in this UI
    already means something — so this map is the whole distinction, and an
    unresolved type silently collapsing two workouts into one mark is the
    failure worth pinning.
    """

    def test_each_configured_type_has_its_own_icon(self):
        """The six real types must not collide, or the day picker would show
        the same mark for a gym session and a bike ride."""
        from planner import TRAINING_INTENSITY_SPLIT

        icons = {t: training_icon(t) for t in TRAINING_INTENSITY_SPLIT}
        self.assertEqual(len(set(icons.values())), len(icons), icons)

    def test_an_exact_match_beats_the_prefix(self):
        self.assertEqual(training_icon("cardio_ride"), "directions_bike")
        self.assertNotEqual(training_icon("cardio_ride"), training_icon("cardio"))

    def test_an_unknown_type_widens_to_its_longest_prefix(self):
        """A future `gym_strength`/`cardio_swim` should need no edit here."""
        self.assertEqual(training_icon("gym_strength"), training_icon("gym_hypertrophy"))
        self.assertEqual(training_icon("cardio_swim"), "monitor_heart")

    def test_a_type_matching_nothing_still_renders_a_workout(self):
        """A config typo shows a generic session rather than taking the picker
        down — the strip prints the humanized name beside it anyway."""
        self.assertEqual(training_icon("nonsense"), "fitness_center")
        self.assertEqual(training_icon(""), "fitness_center")


class TestTrainingForDay(unittest.TestCase):
    """The per-day session lookup the day picker calls seven times a repaint.

    Split out of `day_context` precisely so it costs a list scan: `day_context`
    needs `planning_config()` for its per-meal notes, and marking all seven
    pills through that would be seven `apply_training_adjustments` passes over
    the week.
    """

    def test_it_reads_only_the_drawer_schedule(self):
        """No config is consulted, so a state with an empty config still
        answers — which is what makes it safe to call per pill."""
        state = make_state()
        state.config = {"weekly_schedule": {}, "meal_types": []}
        state.training_schedule = [
            {"day": "Monday", "time": "05:30", "type": "gym_hypertrophy",
             "duration_minutes": 60, "estimated_burn_kcal": 350}
        ]
        self.assertEqual(len(state.training_for("Monday")), 1)

    def test_sessions_come_back_in_clock_order(self):
        state = make_state()
        state.training_schedule = [
            {"day": "Saturday", "time": "06:30", "type": "cardio_hiit",
             "duration_minutes": 30, "estimated_burn_kcal": 320},
            {"day": "Saturday", "time": "05:30", "type": "gym_hypertrophy",
             "duration_minutes": 60, "estimated_burn_kcal": 350},
        ]
        self.assertEqual(
            [s.type for s in state.training_for("Saturday")],
            ["gym_hypertrophy", "cardio_hiit"],
        )

    def test_two_sessions_on_one_day_keep_distinct_marks(self):
        """Saturday's gym-plus-HIIT is the case the dedupe must not collapse."""
        state = make_state()
        state.training_schedule = [
            {"day": "Saturday", "time": "05:30", "type": "gym_hypertrophy",
             "duration_minutes": 60, "estimated_burn_kcal": 350},
            {"day": "Saturday", "time": "06:30", "type": "cardio_hiit",
             "duration_minutes": 30, "estimated_burn_kcal": 320},
        ]
        marks = {training_icon(s.type) for s in state.training_for("Saturday")}
        self.assertEqual(len(marks), 2)

    def test_day_context_and_the_picker_agree(self):
        """Both read the same list, so the strip can never describe a day the
        pill above it says has no training."""
        state = make_context_state(
            training=[{"day": "Monday", "time": "05:30", "type": "cardio_ride",
                       "duration_minutes": 90, "estimated_burn_kcal": 650}]
        )
        self.assertEqual(
            ui_state.day_context(state, "Monday").sessions,
            state.training_for("Monday"),
        )


if __name__ == "__main__":
    unittest.main()
