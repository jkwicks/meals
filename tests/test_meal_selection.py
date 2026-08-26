"""Tests for the meal-selection rules layered on top of the deterministic week.

Four features share this file because they share one idea: **more of the week
is decided before the model is called than used to be.** A location reshapes
the grid, a saved favourite claims a slot outright, a meal eaten out claims a
share of the day's budget without cooking anything, and fibre rides along
every recipe without ever entering the arithmetic those three affect.

Two of these were written against a specific failure and the tests say so —
`TestPinnedFavouritesReachGeneration.test_a_saved_batch_is_normalised_to_one_
serving` and `test_the_model_is_not_asked_for_a_pinned_slot`. Both bugs were
live in the first working version of the feature and neither is visible in
the UI: the first silently serves 20% over budget on every pinned favourite
(the trim clamp fires instead of the target), the second silently pays for a
recipe that is then thrown away. That is the shape to follow — when a test is
added because something broke, record the failure in the test, not just the
fix.

`unittest` and the `sys.path` insert match `test_week_mechanics.py`; see its
docstring for why.
"""

import asyncio
import sys
import unittest
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import planner  # noqa: E402
import week as wk  # noqa: E402
from planner import Ingredient, Recipe  # noqa: E402
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, WeekSpec  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


def spec_with(modes=None, days=None, servings_per_meal=2) -> WeekSpec:
    days = days or DAYS
    modes = modes or {}
    slots = []
    for day in days:
        for meal_type in MEAL_TYPES:
            override = modes.get(wk.slot_id(day, meal_type), {})
            slots.append(
                SlotSpec(
                    day=day,
                    meal_type=meal_type,
                    mode=override.get("mode", MODE_COOK),
                    source=override.get("source"),
                    style=override.get("style"),
                    cuisine=override.get("cuisine"),
                    skip_estimate=override.get("skip_estimate"),
                    recipe_id=override.get("recipe_id"),
                )
            )
    return WeekSpec(days=days, slots=slots, servings_per_meal=servings_per_meal)


def recipe(
    name, meal_type="breakfast", servings=1, fiber_g=0.0, long_oven_cook=False, **macros
) -> Recipe:
    """A one-ingredient recipe carrying exactly the macros asked for.

    One ingredient rather than several because every rule under test reads
    `total_macros`, which sums them — a second ingredient would only make the
    expected numbers harder to read.
    """
    return Recipe(
        name=name,
        meal_type=meal_type,
        servings=servings,
        prep_time_minutes=10,
        long_oven_cook=long_oven_cook,
        instructions=["Cook it."],
        ingredients=[
            Ingredient(
                name="test food",
                quantity_g=100.0,
                nova_group=1,
                calories=macros.get("calories", 400.0),
                protein_g=macros.get("protein_g", 30.0),
                net_carbs_g=macros.get("net_carbs_g", 20.0),
                fat_g=macros.get("fat_g", 15.0),
                fiber_g=fiber_g,
            )
        ],
    )


def favourite(
    name, meal_type, recipe_id=None, servings=1, long_oven_cook=False, **macros
) -> dict:
    """One `recipes_master.json` catalog record."""
    return {
        "id": recipe_id or f"id-{name}",
        "content_key": f"key-{name}",
        "recipe": recipe(
            name, meal_type, servings=servings, long_oven_cook=long_oven_cook, **macros
        ).model_dump(),
        "is_favorite": True,
        "source": "favorited",
    }


BASE_CONFIG = {
    "cuisines": ["thai", "greek"],
    "cuisine_meal_types": ["dinner"],
    "meal_types": MEAL_TYPES,
    "meal_styles": {"breakfast": {"custom_shake": "..."}, "dinner": {"curry": "..."}},
    "meal_weights": {"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
    "planning_rules": dict(planner.DEFAULT_PLANNING_RULES),
    "week_defaults": {k: MODE_COOK for k in MEAL_TYPES},
    "serving_rules": {"servings_per_meal": 2},
    "week_start_day": "Monday",
    "weekly_schedule": {day: {} for day in DAYS},
    "inventory_rules": dict(wk.DEFAULT_INVENTORY_RULES),
    # Off, so the end-to-end tests below exercise the generation path
    # rather than the "a failed prep session must not fail the week" catch.
    "enable_sunday_prep": False,
    "max_prep_active_mins": 120,
}


# ---------------------------------------------------------------------------
# Fibre: reported, never budgeted
# ---------------------------------------------------------------------------


class TestFibreIsReportedNotBudgeted(unittest.TestCase):
    """`NUTRIENT_KEYS` exists to keep fibre out of arithmetic built on
    `calories ~= 4p + 4c + 9f`, while still scaling it with the portion."""

    def test_macro_keys_still_holds_exactly_the_budgeted_four(self):
        """The whole separation rests on this tuple not growing."""
        self.assertEqual(
            planner.MACRO_KEYS, ("calories", "protein_g", "net_carbs_g", "fat_g")
        )
        self.assertIn("fiber_g", planner.NUTRIENT_KEYS)
        self.assertNotIn("fiber_g", planner.MACRO_KEYS)

    def test_a_recipe_totals_its_fibre(self):
        self.assertEqual(recipe("r", fiber_g=9.0).total_macros["fiber_g"], 9.0)

    def test_the_portion_trim_scales_fibre_with_everything_else(self):
        """Fibre is linear in quantity even though nothing budgets it —
        leaving it out of `Ingredient.scaled` would inflate the reported
        figure relative to the food actually on the plate."""
        halved = recipe("r", fiber_g=10.0, calories=800.0).resize_by_factor(0.5)
        self.assertAlmostEqual(halved.total_macros["fiber_g"], 5.0, places=1)
        self.assertAlmostEqual(halved.total_macros["calories"], 400.0, places=1)

    def test_a_recipe_saved_before_fibre_existed_still_loads(self):
        """`fiber_g` defaults to 0.0 precisely so the eight favourites already
        in `recipes_master.json` stay loadable — the same pre-migration
        tolerance `history_styles` extends to old history entries."""
        legacy = {
            "name": "Old",
            "meal_type": "dinner",
            "prep_time_minutes": 10,
            "instructions": ["Cook."],
            "ingredients": [
                {
                    "name": "chicken",
                    "quantity_g": 100.0,
                    "nova_group": 1,
                    "calories": 200.0,
                    "protein_g": 30.0,
                    "net_carbs_g": 0.0,
                    "fat_g": 8.0,
                }
            ],
        }
        self.assertEqual(Recipe.model_validate(legacy).total_macros["fiber_g"], 0.0)

    def test_fitting_to_budget_ignores_fibre(self):
        """A budget carries only `MACRO_KEYS`, so the trim must not index a
        fifth key off it — this is the crash the split prevents."""
        fitted, factor = planner.fit_recipe_to_budget(
            recipe("r", calories=800.0, fiber_g=12.0),
            {"calories": 640.0, "protein_g": 30.0, "net_carbs_g": 20.0, "fat_g": 15.0},
            BASE_CONFIG,
        )
        self.assertAlmostEqual(factor, 0.8, places=2)
        self.assertAlmostEqual(fitted.total_macros["fiber_g"], 9.6, places=1)


# ---------------------------------------------------------------------------
# Meals eaten out
# ---------------------------------------------------------------------------


class TestSkipEstimates(unittest.TestCase):
    """A skipped meal that was actually eaten still costs the day. Without an
    estimate, its share is handed to the meals that *are* planned and they
    come back oversized."""

    ESTIMATE = {
        "calories": 900.0,
        "protein_g": 40.0,
        "net_carbs_g": 70.0,
        "fat_g": 45.0,
    }

    def test_a_plain_skip_contributes_nothing(self):
        spec = spec_with({"Monday:dinner": {"mode": MODE_SKIP}})
        self.assertEqual(
            wk.skip_estimate_totals(spec.slots, "Monday")["calories"], 0.0
        )

    def test_an_estimated_skip_is_totalled_for_its_day(self):
        spec = spec_with(
            {"Monday:dinner": {"mode": MODE_SKIP, "skip_estimate": self.ESTIMATE}}
        )
        self.assertEqual(
            wk.skip_estimate_totals(spec.slots, "Monday")["calories"], 900.0
        )

    def test_it_does_not_leak_into_another_day(self):
        spec = spec_with(
            {"Monday:dinner": {"mode": MODE_SKIP, "skip_estimate": self.ESTIMATE}}
        )
        self.assertEqual(
            wk.skip_estimate_totals(spec.slots, "Tuesday")["calories"], 0.0
        )

    def test_clearing_is_distinct_from_a_zero_estimate(self):
        """None means "not eaten", zeros mean "eaten, cost nothing". Both are
        legitimate and they brief the day differently, so `set_skip_estimate`
        has to be able to express each."""
        spec = spec_with({"Monday:dinner": {"mode": MODE_SKIP}})
        zeroed = wk.set_skip_estimate(
            spec, "Monday:dinner", {key: 0.0 for key in wk.MACRO_KEYS}
        )
        self.assertIsNotNone(zeroed.by_id()["Monday:dinner"].skip_estimate)
        cleared = wk.set_skip_estimate(zeroed, "Monday:dinner", None)
        self.assertIsNone(cleared.by_id()["Monday:dinner"].skip_estimate)

    def test_an_estimate_on_a_cooked_slot_is_rejected(self):
        spec = spec_with(
            {"Monday:dinner": {"mode": MODE_COOK, "skip_estimate": self.ESTIMATE}}
        )
        errors = wk.validate_week(spec, BASE_CONFIG)
        self.assertTrue(any("only applies to a skipped meal" in e for e in errors))

    def test_a_partial_estimate_is_rejected(self):
        """All four or none: a partial estimate would be subtracted from some
        macros and not others, leaving the day internally inconsistent in
        exactly the way `split_targets` assumes it never is."""
        spec = spec_with(
            {"Monday:dinner": {"mode": MODE_SKIP, "skip_estimate": {"calories": 900.0}}}
        )
        errors = wk.validate_week(spec, BASE_CONFIG)
        self.assertTrue(any("give all four macros or none" in e for e in errors))


# ---------------------------------------------------------------------------
# Location rules
# ---------------------------------------------------------------------------


class TestLocationShapesTheGrid(unittest.TestCase):
    """`base_schedule` and `location_rules` were config nothing read. They now
    shape the *default* grid only — never a week that already exists, whose
    slots carry the user's own structural edits."""

    CONFIG = dict(
        BASE_CONFIG,
        base_schedule={
            "Monday": "Office",
            "Tuesday": "WFH",
            "Wednesday": "Office",
            "Thursday": "Holiday",
        },
        location_rules={
            "Office": {"lunch_mode": "leftover", "restrictions": ["portable"]},
            "WFH": {"lunch_mode": "cook"},
            "Holiday": {
                "breakfast_mode": "skip",
                "lunch_mode": "skip",
                "dinner_mode": "skip",
                "snack_mode": "skip",
            },
        },
    )

    def test_an_office_lunch_inherits_the_previous_dinner(self):
        spec = wk.apply_location_modes(spec_with(), self.CONFIG)
        wednesday = spec.by_id()["Wednesday:lunch"]
        self.assertEqual(wednesday.mode, MODE_LEFTOVER)
        self.assertEqual(wednesday.source, "Tuesday:dinner")

    def test_an_office_lunch_on_day_one_falls_back_to_cooking(self):
        """A leftover with no source fails `validate_week` outright, and a
        grid that cannot be generated is a worse answer than one that cooks an
        extra lunch."""
        spec = wk.apply_location_modes(spec_with(), self.CONFIG)
        self.assertEqual(spec.by_id()["Monday:lunch"].mode, MODE_COOK)
        self.assertEqual(wk.validate_week(spec, self.CONFIG), [])

    def test_a_holiday_skips_every_meal_that_day(self):
        spec = wk.apply_location_modes(spec_with(), self.CONFIG)
        for meal_type in MEAL_TYPES:
            self.assertEqual(
                spec.by_id()[wk.slot_id("Thursday", meal_type)].mode, MODE_SKIP
            )

    def test_a_day_with_no_location_keeps_its_default(self):
        spec = wk.apply_location_modes(spec_with(), self.CONFIG)
        self.assertEqual(spec.by_id()["Friday:lunch"].mode, MODE_COOK)

    def test_a_config_without_base_schedule_is_untouched(self):
        """Every location feature is opt-in — an older config generates
        exactly as it did before any of this existed."""
        before = spec_with()
        after = wk.apply_location_modes(before, BASE_CONFIG)
        self.assertEqual(
            [slot.mode for slot in after.slots], [slot.mode for slot in before.slots]
        )

    def test_restrictions_reach_the_slot_brief(self):
        slot = SlotSpec(day="Wednesday", meal_type="lunch", mode=MODE_COOK)
        note = planner.build_location_note(slot, self.CONFIG)
        self.assertIn("Office", note)
        self.assertIn("travel in a container", note)

    def test_a_location_with_no_restrictions_says_nothing(self):
        """Silence, not an empty bracket — the prompt has to stay
        byte-identical when the feature isn't in use."""
        slot = SlotSpec(day="Tuesday", meal_type="lunch", mode=MODE_COOK)
        self.assertEqual(planner.build_location_note(slot, self.CONFIG), "")

    def test_an_office_day_does_not_constrain_breakfast(self):
        """Written against the first version, which put "must travel in a
        container" on a Monday breakfast. Being at the office all day says
        nothing about the meal eaten at home before leaving — and `Office`
        declaring only `lunch_mode` is already the honest statement of which
        meals it has an opinion about."""
        for meal_type in ("breakfast", "dinner", "snack"):
            slot = SlotSpec(day="Wednesday", meal_type=meal_type, mode=MODE_COOK)
            self.assertEqual(planner.build_location_note(slot, self.CONFIG), "")


class TestLocationSkipCarriesAnEstimate(unittest.TestCase):
    """A location that skips a meal (`Outing`'s lunch, on the shipped config)
    used to contribute nothing to the day at all — no flexible slot was left
    to absorb the gap between the weighted share a downstream leftover
    reserved and what its source day actually delivered, so a day like
    Saturday (breakfast pinned, lunch skipped, dinner a leftover, snack
    skipped by default) was structurally incapable of reconciling, every
    week, deterministically. `<meal_type>_skip_estimate` on the rule is what
    lets a location say what was actually eaten instead of nothing."""

    CONFIG = dict(
        BASE_CONFIG,
        base_schedule={"Saturday": "Outing"},
        location_rules={
            "Outing": {
                "lunch_mode": "skip",
                "lunch_skip_estimate": {
                    "calories": 795, "protein_g": 36, "net_carbs_g": 62, "fat_g": 44,
                },
                "dinner_mode": "leftover",
            },
        },
    )

    def test_a_skip_with_an_estimate_carries_it_onto_the_slot(self):
        spec = wk.apply_location_modes(spec_with(), self.CONFIG)
        slot = spec.by_id()["Saturday:lunch"]
        self.assertEqual(slot.mode, MODE_SKIP)
        self.assertEqual(
            slot.skip_estimate,
            {"calories": 795.0, "protein_g": 36.0, "net_carbs_g": 62.0, "fat_g": 44.0},
        )

    def test_a_skip_with_no_estimate_carries_none(self):
        """The common case (Holiday, or Outing before this feature) — a skip
        with nothing declared must still mean "nothing eaten", not a made-up
        number."""
        config = dict(
            self.CONFIG,
            location_rules={"Outing": {"lunch_mode": "skip", "dinner_mode": "leftover"}},
        )
        spec = wk.apply_location_modes(spec_with(), config)
        self.assertIsNone(spec.by_id()["Saturday:lunch"].skip_estimate)

    def test_a_leftover_mode_never_picks_up_a_skip_estimate(self):
        """Only a genuine MODE_SKIP transition reads `_skip_estimate` — a
        `lunch_skip_estimate` key sitting on a rule that also sets
        `dinner_mode: leftover` must not leak onto the leftover slot."""
        spec = wk.apply_location_modes(spec_with(), self.CONFIG)
        self.assertIsNone(spec.by_id()["Saturday:dinner"].skip_estimate)

    def test_the_estimate_reaches_the_days_plannable_budget(self):
        """The whole point: `skip_estimate_totals` (planner.py) is what feeds
        this into `generate_week_plan`'s plannable-target subtraction and
        `WeekPlan.day_slot_macros`'s numerator — so a day with an estimated
        skip is no longer structurally incapable of reconciling."""
        spec = wk.apply_location_modes(spec_with(), self.CONFIG)
        totals = wk.skip_estimate_totals(spec.slots, "Saturday")
        self.assertEqual(totals["calories"], 795.0)


# ---------------------------------------------------------------------------
# Fridge safety
# ---------------------------------------------------------------------------


class TestReconciliationGapIsReported(unittest.IsolatedAsyncioTestCase):
    """`generate_week_plan`'s post-loop check (H-1 part B): a day whose
    remaining slots are all skipped or fed by another day's cook has no
    flexible slot left to absorb the gap between what a cross-day leftover
    reserves during earlier stages and what its source day actually
    delivers — the mechanism that stranded a real Saturday at 55% of target,
    silently, on the shipped config. A stub model that returns exactly what
    it is briefed (no drift at all) still reproduces the shortfall, because
    the gap is structural, not a model error.
    """

    async def asyncSetUp(self):
        self._real = planner._generate_meal_type_events

        async def stub_exact_brief(
            meal_type, spec, config, day_budgets, portions, claims,
            carried_descriptions_by_day, pinned_days, avoid_proteins,
            avoid_recipe_names, note_callback=None, seafood_used=0,
        ):
            events = {}
            for day, budget in day_budgets.items():
                slot = spec.by_id()[wk.slot_id(day, meal_type)]
                events[slot.id] = planner.build_cook_event(
                    slot, recipe(f"{meal_type} {day}", meal_type, **budget),
                    spec, portions, claims, config,
                )
            return events

        planner._generate_meal_type_events = stub_exact_brief

    async def asyncTearDown(self):
        planner._generate_meal_type_events = self._real

    def _spec(self, tuesday_lunch_estimate=None):
        return spec_with(
            {
                "Tuesday:lunch": {"mode": MODE_SKIP, "skip_estimate": tuesday_lunch_estimate},
                "Tuesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
                "Tuesday:snack": {"mode": MODE_SKIP},
            },
            days=["Monday", "Tuesday"],
        )

    def _config(self):
        return dict(
            BASE_CONFIG,
            weekly_schedule={
                day: {"calories": 2000, "protein_g": 150, "net_carbs_g": 150, "fat_g": 78}
                for day in ["Monday", "Tuesday"]
            },
        )

    async def test_a_stranded_day_is_warned_about(self):
        """Tuesday: breakfast cooked, lunch skipped with no estimate, dinner a
        cross-day leftover of Monday's dinner, snack skipped — no flexible
        slot survives to close the gap between its share and the actual
        target, so the day lands far short and a note must say so."""
        notes = []
        with self.assertLogs("meals", level="WARNING"):
            plan = await planner.generate_week_plan(
                self._spec(), self._config(), history=[],
                note_callback=notes.append, repository=_FakeRepository([]),
            )
        planned = plan.day_slot_macros("Tuesday")["calories"]
        goal = plan.targets["Tuesday"]["calories"]
        self.assertLess(planned, goal * planner.UNDER_TARGET_NOTE_THRESHOLD)
        self.assertTrue(any("Tuesday" in n and "is planned" in n for n in notes))

    async def test_an_estimated_skip_closes_the_gap_and_the_note_stops(self):
        """Same grid, but the skipped lunch now carries what it would have
        been briefed at (what a location rule's `_skip_estimate`, or the
        card's "Eaten out?" button, supplies) — the day reconciles and the
        warning must not fire."""
        notes = []
        plan = await planner.generate_week_plan(
            self._spec(tuesday_lunch_estimate={
                "calories": 600.0, "protein_g": 45.0, "net_carbs_g": 45.0, "fat_g": 23.0,
            }),
            self._config(), history=[],
            note_callback=notes.append, repository=_FakeRepository([]),
        )
        planned = plan.day_slot_macros("Tuesday")["calories"]
        goal = plan.targets["Tuesday"]["calories"]
        self.assertGreaterEqual(planned, goal * planner.UNDER_TARGET_NOTE_THRESHOLD)
        self.assertFalse(any("Tuesday" in n and "is planned" in n for n in notes))


class TestFridgeSafety(unittest.TestCase):
    """Prevention in `spread_batch`, a backstop in `validate_week`. The batch
    toggles cannot create an unsafe chain; a hand-built one is still caught."""

    def test_spread_batch_stops_at_the_fridge_window(self):
        """Every lunch already claimed, so the only room left to grow into is
        further dinners — which is what pushes the walk past the window."""
        modes = {
            wk.slot_id(day, "lunch"): {"mode": MODE_SKIP} for day in DAYS
        }
        spec = spec_with(modes)
        bounded, anchor = wk.spread_batch(
            spec, "dinner", target_servings=6, max_span_days=2
        )
        self.assertIsNotNone(anchor)
        self.assertLessEqual(wk.span_days(bounded, anchor), 2)

    def test_unbounded_by_default(self):
        """None means no limit — every caller with no config in scope."""
        modes = {wk.slot_id(day, "lunch"): {"mode": MODE_SKIP} for day in DAYS}
        spec, anchor = wk.spread_batch(spec_with(modes), "dinner", target_servings=6)
        self.assertEqual(wk.span_days(spec, anchor), 2)

    def test_validate_week_catches_a_hand_built_overlong_chain(self):
        """A chain of "Link to next lunch" clicks never goes through
        `spread_batch`, so the bound there cannot see it."""
        spec = spec_with({
            "Saturday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        errors = wk.validate_week(spec, BASE_CONFIG)
        self.assertTrue(any("fridge limit" in e for e in errors))

    def test_a_chain_inside_the_window_is_clean(self):
        spec = spec_with({
            "Thursday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(wk.validate_week(spec, BASE_CONFIG), [])


# ---------------------------------------------------------------------------
# Prep day is a different day from the one at the end of the grid
# ---------------------------------------------------------------------------


class TestPrepDayStaleTargets(unittest.TestCase):
    """The batch-prep session runs the day *before* `spec.days[0]`, which puts
    the last day of the grid a full **7 days** after it — on a Monday-start
    week, the Sunday a batch is prepped on and the Sunday that eats it are not
    the same Sunday.

    Written against a live plan (`week_start_date: 2026-08-17`) whose long-cook
    anchor was Saturday dinner, spread into Sunday lunch and Sunday dinner, and
    then listed in `sunday_prep_session.meals_included` — i.e. lamb shanks
    braised on Aug 16 and eaten Aug 22-23. Nothing rejected it, because
    `span_days` measures from the anchor day (Sat -> Sun = 1) and never from
    prep day.
    """

    LAST_DAY = {"Sunday"}

    def test_a_batch_never_links_into_an_excluded_day(self):
        """`continue`, not `break` — the walk steps over the excluded day and
        keeps going, so the batch still reaches its target claim count."""
        modes = {wk.slot_id(day, "lunch"): {"mode": MODE_SKIP} for day in DAYS}
        spec, anchor = wk.spread_batch(
            spec_with(modes),
            "dinner",
            target_servings=6,
            exclude_target_days={"Wednesday"},
        )
        claims = wk.eaten_on(spec)[anchor]
        self.assertNotIn("Wednesday:dinner", claims)
        self.assertEqual(claims, ["Monday:dinner", "Tuesday:dinner", "Thursday:dinner"])

    def test_the_last_day_of_the_week_is_never_fed_by_a_batch(self):
        """The exact failure above: a Friday anchor may take Saturday and must
        stop there, rather than spreading into a Sunday 7 days past prep."""
        modes = {wk.slot_id(day, "lunch"): {"mode": MODE_SKIP} for day in DAYS}
        spec, anchor = wk.spread_batch(
            spec_with(modes),
            "dinner",
            target_servings=6,
            exclude_days={"Monday", "Tuesday", "Wednesday", "Thursday"},
            exclude_target_days=self.LAST_DAY,
        )
        self.assertEqual(anchor, "Friday:dinner")
        self.assertEqual(wk.eaten_on(spec)[anchor], ["Friday:dinner", "Saturday:dinner"])

    def test_an_unexcluded_week_is_unchanged(self):
        """Default is the empty set, so a caller with no prep session in scope
        gets byte-identical behaviour to before this existed."""
        modes = {wk.slot_id(day, "lunch"): {"mode": MODE_SKIP} for day in DAYS}
        before, anchor_before = wk.spread_batch(spec_with(modes), "dinner", 6)
        after, anchor_after = wk.spread_batch(
            spec_with(modes), "dinner", 6, exclude_target_days=set()
        )
        self.assertEqual(anchor_before, anchor_after)
        self.assertEqual(wk.eaten_on(before), wk.eaten_on(after))


# ---------------------------------------------------------------------------
# is_sunday_prepped matches by slot_id, not by the recipe's own flags
# ---------------------------------------------------------------------------


def cook_event(slot_id, day, meal_type, recipe_obj, portions=6) -> planner.CookEvent:
    return planner.CookEvent(
        slot_id=slot_id, day=day, meal_type=meal_type, portions=portions, recipe=recipe_obj
    )


def week_plan_with(cook_events, sunday_prep_session=None) -> planner.WeekPlan:
    """Minimal WeekPlan — only what `is_sunday_prepped` reads matters here."""
    slots = [
        SlotSpec(day=day, meal_type=meal_type, mode=MODE_COOK)
        for day in DAYS
        for meal_type in MEAL_TYPES
    ]
    return planner.WeekPlan(
        days=DAYS,
        servings_per_meal=2,
        generated_at="2026-08-24T00:00:00",
        cook_events=cook_events,
        slots=slots,
        targets={},
        sunday_prep_session=sunday_prep_session,
    )


class TestSundayPrepLabelling(unittest.TestCase):
    """Written against a live plan: a real "Korean Beef Bulgogi Rice Tray
    Bake" was `spread_batch`'s long-cook anchor — baked in the oven as part
    of the actual Sunday prep session — but the model came back with both
    `long_oven_cook` and `bulk_prep_friendly` False despite
    `LONG_COOK_ANCHOR_SLOT_DIRECTIVE` telling it to set one. `is_sunday_
    prepped` used to trust those flags alone, so the anchor's own leftover
    slots (its Friday dinner, its Saturday lunch) lost the "prepped on
    Sunday" badge and the 10-minute reheat estimate, and read as an ordinary
    from-scratch cook eaten late in the week instead.

    It now matches by slot_id against `SundayPrepSession.candidate_slot_ids`
    — the same ground truth `generate_sunday_prep_session` already used to
    pick its candidates before the model was ever called.
    """

    def test_the_anchor_is_recognised_even_with_both_flags_false(self):
        bulgogi = recipe("Korean Beef Bulgogi Rice Tray Bake", "dinner", servings=6)
        self.assertFalse(bulgogi.long_oven_cook)
        self.assertFalse(bulgogi.bulk_prep_friendly)
        event = cook_event("Thursday:dinner", "Thursday", "dinner", bulgogi)
        session = planner.SundayPrepSession(
            total_active_minutes=90,
            candidate_slot_ids=["Monday:dinner", "Thursday:dinner"],
        )
        plan = week_plan_with([event], sunday_prep_session=session)
        self.assertTrue(planner.is_sunday_prepped(event, plan))
        self.assertEqual(
            planner.weeknight_prep_minutes(event, plan), planner.SUNDAY_PREP_REHEAT_MINUTES
        )

    def test_a_stray_flag_off_the_session_is_not_credited(self):
        """The failure mode the flag-based check had in the other direction:
        an unrelated dinner the model happened to flag must not borrow the
        badge just because a session exists somewhere in the week."""
        stray = recipe("Stray Slow-Cooker Chilli", "dinner").model_copy(
            update={"long_oven_cook": True}
        )
        event = cook_event("Saturday:dinner", "Saturday", "dinner", stray)
        session = planner.SundayPrepSession(
            total_active_minutes=90, candidate_slot_ids=["Monday:dinner"]
        )
        plan = week_plan_with([event], sunday_prep_session=session)
        self.assertFalse(planner.is_sunday_prepped(event, plan))

    def test_a_pre_migration_session_falls_back_to_the_flags(self):
        """A session saved before `candidate_slot_ids` existed has an empty
        list — same tolerance `history_styles()` extends to old history."""
        bake = recipe("Old Saved Bake", "dinner", servings=6).model_copy(
            update={"long_oven_cook": True}
        )
        event = cook_event("Thursday:dinner", "Thursday", "dinner", bake)
        session = planner.SundayPrepSession(total_active_minutes=90)
        plan = week_plan_with([event], sunday_prep_session=session)
        self.assertTrue(planner.is_sunday_prepped(event, plan))

    def test_no_session_means_nothing_is_prepped(self):
        ordinary = recipe("Ordinary Dinner", "dinner")
        event = cook_event("Monday:dinner", "Monday", "dinner", ordinary)
        plan = week_plan_with([event], sunday_prep_session=None)
        self.assertFalse(planner.is_sunday_prepped(event, plan))


class TestSundayPrepStalenessGuardUsesGroundTruth(unittest.IsolatedAsyncioTestCase):
    """`regenerate_single_day`/`regenerate_single_meal` decide whether a saved
    `SundayPrepSession` is now stale by testing `event.recipe.prep_notes` for
    truthiness — the same proxy `is_sunday_prepped` (above) was rewritten to
    stop trusting. The bug that actually corrupts a plan: the shake candidate
    has `portions == 1`, so `storage_note()` returns "" and `prep_notes` is
    None even though its slot_id *is* in `candidate_slot_ids`. Regenerating
    that breakfast used to leave the stale session in place, still naming a
    "Berry Shake" that no longer exists on the grid.
    """

    async def asyncSetUp(self):
        self._real_generate_day = planner.generate_day

        def stub_generate_day(day, targets, cook_slots, **kwargs):
            # No LLM call: one recipe per requested slot, carrying no
            # prep_notes of its own — mirrors a freshly generated single
            # serving, same as the real shake this bug was found on.
            return {slot.meal_type: recipe(f"New {slot.meal_type}", slot.meal_type)
                    for slot in cook_slots}

        planner.generate_day = stub_generate_day

    async def asyncTearDown(self):
        planner.generate_day = self._real_generate_day

    def _config(self):
        return dict(
            BASE_CONFIG,
            weekly_schedule={
                day: {"calories": 2000, "protein_g": 150, "net_carbs_g": 150, "fat_g": 78}
                for day in DAYS
            },
        )

    def _week_plan_with_shake_session(self):
        shake = cook_event(
            "Monday:breakfast", "Monday", "breakfast",
            recipe("Berry Shake", "breakfast"), portions=1,
        )
        anchor = cook_event(
            "Monday:dinner", "Monday", "dinner",
            recipe("Beef Bulgogi", "dinner"), portions=6,
        )
        session = planner.SundayPrepSession(
            total_active_minutes=90,
            meals_included=["Beef Bulgogi", "Berry Shake"],
            candidate_slot_ids=["Monday:dinner", "Monday:breakfast"],
        )
        return week_plan_with([shake, anchor], sunday_prep_session=session)

    async def test_regenerating_the_shake_drops_the_stale_session(self):
        week_plan = self._week_plan_with_shake_session()
        shake_event = week_plan.by_slot()["Monday:breakfast"]
        # The proxy the old guard used says "not a candidate" — the whole bug.
        self.assertIsNone(shake_event.recipe.prep_notes)
        self.assertTrue(planner.is_sunday_prepped(shake_event, week_plan))

        result = await planner.regenerate_single_meal(
            "Monday:breakfast", spec_with(), self._config(), week_plan,
            history=[], repository=_FakeRepository([]),
        )
        self.assertIsNone(result.sunday_prep_session)

    async def test_regenerating_the_shakes_day_drops_the_stale_session(self):
        """Same fix, the `regenerate_single_day` call site."""
        week_plan = self._week_plan_with_shake_session()
        result = await planner.regenerate_single_day(
            "Monday", spec_with(), self._config(), week_plan,
            history=[], repository=_FakeRepository([]),
        )
        self.assertIsNone(result.sunday_prep_session)

    async def test_regenerating_an_unrelated_day_leaves_the_session(self):
        """Wednesday touches no candidate slot, so a session that never
        covered it must survive untouched."""
        week_plan = self._week_plan_with_shake_session()
        result = await planner.regenerate_single_day(
            "Wednesday", spec_with(), self._config(), week_plan,
            history=[], repository=_FakeRepository([]),
        )
        self.assertIsNotNone(result.sunday_prep_session)


# ---------------------------------------------------------------------------
# Saved favourites claiming slots
# ---------------------------------------------------------------------------


class TestFavouriteSelection(unittest.TestCase):
    """Which slots a saved favourite claims, and which it must never touch."""

    FAVOURITES = [
        favourite("Standing Scramble", "breakfast"),
        favourite("Second Breakfast", "breakfast"),
        favourite("Hummus Wrap", "lunch"),
        favourite("Big Roast", "dinner"),
        favourite("Second Roast", "dinner"),
        favourite("Third Roast", "dinner"),
    ]

    def pick(self, spec, history=None, favourites=None, today=date(2026, 8, 23)):
        return planner.select_favorite_assignments(
            spec,
            BASE_CONFIG,
            history or [],
            self.FAVOURITES if favourites is None else favourites,
            today=today,
        )

    def test_one_breakfast_favourite_covers_two_mornings(self):
        """The point of a standing breakfast is that it is the same one — and
        one shop covers both."""
        picks = self.pick(spec_with())
        breakfasts = {
            sid: rec["recipe"]["name"] for sid, rec in picks.items() if "breakfast" in sid
        }
        self.assertEqual(len(breakfasts), 2)
        self.assertEqual(len(set(breakfasts.values())), 1)

    def test_a_workout_shake_is_never_displaced(self):
        """The shake pin is a hard nutritional rule; a favourite is a
        preference, and a preference must not beat a rule."""
        spec = spec_with({
            "Monday:breakfast": {"style": planner.WORKOUT_BREAKFAST_STYLE},
            "Tuesday:breakfast": {"style": planner.WORKOUT_BREAKFAST_STYLE},
        })
        picks = self.pick(spec)
        self.assertNotIn("Monday:breakfast", picks)
        self.assertNotIn("Tuesday:breakfast", picks)

    def test_dinner_favourites_are_capped_not_one_per_slot(self):
        """Dinner used to be excluded outright. It is now capped instead, at
        `favorite_dinner_slots` — three eligible dinner favourites and seven
        open dinners still yield exactly two, because every pin blanks a
        cuisine and an uncapped week would have no block left."""
        dinners = {
            sid: rec["recipe"]["name"]
            for sid, rec in self.pick(spec_with()).items()
            if "dinner" in sid
        }
        self.assertEqual(len(dinners), 2)
        self.assertEqual(len(set(dinners.values())), 2, "must be distinct dishes")

    def test_a_dinner_pin_lands_on_the_end_of_a_cuisine_run(self):
        """Blanking a run's last day leaves the rest of it contiguous; taking
        a middle day would split one block into two with a hole between."""
        cuisines = dict(
            [(wk.slot_id(d, "dinner"), {"cuisine": "greek"})
             for d in DAYS[:4]]
            + [(wk.slot_id(d, "dinner"), {"cuisine": "thai"})
               for d in DAYS[4:]]
        )
        picks = self.pick(spec_with(cuisines))
        self.assertEqual(
            sorted(sid for sid in picks if "dinner" in sid),
            ["Sunday:dinner", "Thursday:dinner"],
        )

    def test_one_pin_per_run_rather_than_two_from_one_block(self):
        """Spreads the favourites across the week and damages each block
        equally, instead of halving one and leaving the other whole."""
        cuisines = dict(
            [(wk.slot_id(d, "dinner"), {"cuisine": "greek"}) for d in DAYS[:4]]
            + [(wk.slot_id(d, "dinner"), {"cuisine": "thai"}) for d in DAYS[4:]]
        )
        days = {sid.split(":")[0] for sid in self.pick(spec_with(cuisines)) if "dinner" in sid}
        self.assertEqual(days, {"Thursday", "Sunday"})

    def test_no_cuisines_degrades_to_earliest_first(self):
        """A week that never resolved its cuisines behaves like lunch rather
        than raising or picking nothing."""
        picks = self.pick(spec_with())
        self.assertEqual(
            sorted(sid for sid in picks if "dinner" in sid),
            ["Monday:dinner", "Tuesday:dinner"],
        )

    def test_snack_is_still_never_picked(self):
        """`week_defaults.snack` is skip in the shipped config, so there is
        usually no slot to claim — deliberately left alone."""
        picks = self.pick(spec_with())
        self.assertEqual([sid for sid in picks if "snack" in sid], [])

    def test_a_meal_type_never_borrows_another_type_s_favourite(self):
        lunches = {
            rec["recipe"]["name"] for sid, rec in self.pick(spec_with()).items()
            if "lunch" in sid
        }
        self.assertEqual(lunches, {"Hummus Wrap"})

    def test_a_recently_cooked_favourite_is_skipped(self):
        """`favorite_reuse_days` is 21 for lunch — a wrap cooked three days
        ago is not a break from rotation."""
        history = [{"date": "2026-08-20", "recipe_names": ["Hummus Wrap"]}]
        picks = self.pick(spec_with(), history=history)
        self.assertNotIn("Hummus Wrap", {r["recipe"]["name"] for r in picks.values()})

    def test_a_long_unused_favourite_is_eligible_again(self):
        history = [{"date": "2026-07-01", "recipe_names": ["Hummus Wrap"]}]
        picks = self.pick(spec_with(), history=history)
        self.assertIn("Hummus Wrap", {r["recipe"]["name"] for r in picks.values()})

    def test_least_recently_used_wins(self):
        """Same strict-LRU rule `next_choice` applies to styles, for the same
        reason: "unused in the last N" starves the tail of a short list."""
        history = [
            {"date": "2026-01-02", "recipe_names": ["Standing Scramble"]},
            {"date": "2026-01-01", "recipe_names": ["Second Breakfast"]},
        ]
        picks = self.pick(spec_with(), history=history)
        self.assertEqual(
            {r["recipe"]["name"] for sid, r in picks.items() if "breakfast" in sid},
            {"Second Breakfast"},
        )

    def test_an_undated_history_entry_does_not_block_anything(self):
        """Entries written before `week_start_date` existed carry no date, and
        "how long ago" is unanswerable for them."""
        history = [{"date": None, "recipe_names": ["Hummus Wrap"]}]
        picks = self.pick(spec_with(), history=history)
        self.assertIn("Hummus Wrap", {r["recipe"]["name"] for r in picks.values()})

    def test_a_hand_pinned_slot_is_left_alone(self):
        spec = spec_with({"Monday:breakfast": {"recipe_id": "chosen-by-hand"}})
        self.assertNotIn("Monday:breakfast", self.pick(spec))

    def test_no_favourites_means_no_assignments(self):
        self.assertEqual(self.pick(spec_with(), favourites=[]), {})


class TestALongCookNeedsADayWithTheHours(unittest.TestCase):
    """A `long_oven_cook` favourite may only claim a weekend slot.

    Written against a live failure: "Slow Cooked Beef Cheeks", an 8-10 hour
    braise imported from Google Keep, was pinned to a Thursday dinner. Every
    part of the app was correct by its own lights, which is why nothing
    caught it — `BATCH_ROAST_RULE` asks the *model* to put the week's long
    cook on a weekend and a favourite is never generated, so that rule never
    saw it; `select_favorite_assignments` placed it at a cuisine run end, a
    rule about protecting blocks that says nothing about the day; and
    `prep_limit_for`'s 30-minute weeknight ceiling counts active minutes,
    which a braise honestly reports as the 20 that are hands-on.
    """

    FAVOURITES = [
        favourite("Beef Cheeks", "dinner", long_oven_cook=True),
        favourite("Quick Stir Fry", "dinner"),
        favourite("Second Stir Fry", "dinner"),
    ]

    def pick(self, spec, favourites=None, today=date(2026, 8, 23)):
        return planner.select_favorite_assignments(
            spec,
            BASE_CONFIG,
            [],
            self.FAVOURITES if favourites is None else favourites,
            today=today,
        )

    def dinners(self, picks):
        return {
            sid: rec["recipe"]["name"] for sid, rec in picks.items() if "dinner" in sid
        }

    def test_a_long_cook_never_takes_a_weeknight(self):
        """The regression itself. Monday and Tuesday are the earliest run
        ends on a cuisine-less week, and neither may have the braise."""
        self.assertEqual(
            self.dinners(self.pick(spec_with())),
            {"Monday:dinner": "Quick Stir Fry", "Tuesday:dinner": "Second Stir Fry"},
        )

    def test_a_long_cook_waits_for_the_weekend_rather_than_being_dropped(self):
        """Passing it over on a weeknight is not the same as refusing to
        serve it: the weeknight takes an ordinary dish and the braise takes
        the run end that lands on a Sunday."""
        cuisines = dict(
            [(wk.slot_id(d, "dinner"), {"cuisine": "greek"}) for d in DAYS[:4]]
            + [(wk.slot_id(d, "dinner"), {"cuisine": "thai"}) for d in DAYS[4:]]
        )
        self.assertEqual(
            self.dinners(self.pick(spec_with(cuisines))),
            {"Thursday:dinner": "Quick Stir Fry", "Sunday:dinner": "Beef Cheeks"},
        )

    def test_a_declined_run_end_does_not_spend_the_cap(self):
        """`favorite_dinner_slots` counts pins made, not run ends looked at.
        Slicing the run ends first — which is what this used to do — spends
        the cap on Monday and Tuesday, both declined, and pins nothing at all
        on a week whose Saturday was available the whole time."""
        only_long = [favourite("Beef Cheeks", "dinner", long_oven_cook=True)]
        self.assertEqual(
            self.dinners(self.pick(spec_with(), favourites=only_long)),
            {"Saturday:dinner": "Beef Cheeks"},
        )

    def test_a_weeknight_lunch_is_skipped_without_ending_the_loop(self):
        """Lunch stops at the first slot with nothing eligible left, so the
        two cases have to stay distinct: "nothing suits today" continues to
        the next day, "the favourites are spent" breaks."""
        only_long = [favourite("All Day Ragu", "lunch", long_oven_cook=True)]
        self.assertEqual(
            list(self.pick(spec_with(), favourites=only_long)), ["Saturday:lunch"]
        )

    def test_a_breakfast_has_to_suit_both_mornings_it_covers(self):
        """One record covers `favorite_breakfast_slots` days at once, so
        there is no per-day choice left to make after the pick — a long cook
        is only eligible if every morning it claims can take one."""
        favourites = [
            favourite("Slow Baked Beans", "breakfast", long_oven_cook=True),
            favourite("Standing Scramble", "breakfast"),
        ]
        picks = self.pick(spec_with(), favourites=favourites)
        self.assertEqual(
            {rec["recipe"]["name"] for rec in picks.values()}, {"Standing Scramble"}
        )


class TestPinningClearsTheRolledStyle(unittest.TestCase):
    def test_a_pinned_recipe_drops_style_and_cuisine(self):
        """`resolve_auto_choices` has already rolled a style by the time a pin
        lands, so a scramble pinned onto a `yoghurt_bowl` slot would render as
        "YOGHURT BOWL" above a plate of eggs."""
        spec = spec_with({"Monday:breakfast": {"style": "yoghurt_bowl"}})
        pinned = wk.pin_recipe(spec, "Monday:breakfast", "fav-1")
        self.assertIsNone(pinned.by_id()["Monday:breakfast"].style)
        self.assertEqual(pinned.by_id()["Monday:breakfast"].recipe_id, "fav-1")

    def test_clearing_a_pin_does_not_reinstate_a_style(self):
        spec = wk.pin_recipe(spec_with(), "Monday:breakfast", "fav-1")
        cleared = wk.pin_recipe(spec, "Monday:breakfast", None)
        self.assertIsNone(cleared.by_id()["Monday:breakfast"].recipe_id)

    def test_clear_recipe_pins_empties_the_whole_week(self):
        """Called unconditionally on every full-week run: without it, week
        one's favourites would be re-served forever and the reuse window
        would never advance."""
        spec = wk.pin_recipe(spec_with(), "Monday:breakfast", "fav-1")
        self.assertTrue(
            all(slot.recipe_id is None for slot in wk.clear_recipe_pins(spec).slots)
        )

    def test_a_pin_on_a_leftover_is_rejected(self):
        spec = spec_with({
            "Tuesday:lunch": {
                "mode": MODE_LEFTOVER,
                "source": "Monday:dinner",
                "recipe_id": "fav-1",
            },
        })
        errors = wk.validate_week(spec, BASE_CONFIG)
        self.assertTrue(any("pinned recipe only applies" in e for e in errors))


class TestSingleServingNormalisation(unittest.TestCase):
    """A favourite is bookmarked off a card already scaled to its portions."""

    def test_a_two_serving_favourite_is_halved(self):
        normalised = planner.single_serving(recipe("r", servings=2, calories=800.0))
        self.assertEqual(normalised.servings, 1)
        self.assertAlmostEqual(normalised.total_macros["calories"], 400.0, places=1)

    def test_a_one_serving_recipe_is_returned_untouched(self):
        original = recipe("r", servings=1, calories=400.0)
        self.assertIs(planner.single_serving(original), original)


class TestPinnedFavouritesReachGeneration(unittest.IsolatedAsyncioTestCase):
    """The end-to-end path, with the provider stubbed out entirely — a pinned
    slot must be filled *without* one, which is the whole point."""

    async def asyncSetUp(self):
        self.asked = []
        self._real = planner._generate_meal_type_events

        async def never_called(meal_type, spec, config, day_budgets, *args, **kwargs):
            self.asked.append((meal_type, sorted(day_budgets)))
            return {}

        planner._generate_meal_type_events = never_called

    async def asyncTearDown(self):
        planner._generate_meal_type_events = self._real

    async def run_week(self, favourites, spec=None):
        repository = _FakeRepository(favourites)
        config = dict(
            BASE_CONFIG,
            weekly_schedule={
                day: {
                    "calories": 2000,
                    "protein_g": 150,
                    "net_carbs_g": 150,
                    "fat_g": 78,
                }
                for day in DAYS
            },
        )
        return await planner.generate_week_plan(
            spec or spec_with(), config, history=[], repository=repository
        )

    async def test_the_model_is_not_asked_for_a_pinned_slot(self):
        """The bug this was written for: `day_budgets` was built over every
        cook day and handed to the model whole, so a pinned favourite's day
        went back into the prompt and a second recipe was generated — and
        paid for — for a slot already filled."""
        await self.run_week([favourite("Standing Scramble", "breakfast")])
        breakfast_days = dict(self.asked)["breakfast"]
        self.assertNotIn("Monday", breakfast_days)
        self.assertNotIn("Tuesday", breakfast_days)
        self.assertEqual(len(breakfast_days), 5)

    async def test_a_pinned_slot_is_cooked_without_the_model(self):
        plan = await self.run_week([favourite("Standing Scramble", "breakfast")])
        self.assertEqual(
            {event.slot_id for event in plan.cook_events},
            {"Monday:breakfast", "Tuesday:breakfast"},
        )

    async def test_a_saved_batch_is_normalised_to_one_serving(self):
        """The other bug: a favourite saved at 2 servings needs a 0.5 factor,
        which is outside `portion_trim_limits` — so the clamp fired at 0.6 and
        every pinned favourite silently served 20% over budget."""
        plan = await self.run_week(
            [favourite("Big Breakfast", "breakfast", servings=2, calories=1200.0)]
        )
        event = next(e for e in plan.cook_events if e.slot_id == "Monday:breakfast")
        briefed = plan.targets["Monday"]["calories"] * BASE_CONFIG["meal_weights"][
            "breakfast"
        ]
        self.assertAlmostEqual(
            event.recipe.per_serving_macros["calories"], briefed, delta=briefed * 0.05
        )

    async def test_an_unusable_favourite_falls_back_to_generating(self):
        """A saved recipe that no longer validates must not take the slot down
        with it — the slot goes back to the model instead."""
        broken = favourite("Broken", "breakfast")
        broken["recipe"]["ingredients"] = []
        broken["recipe"]["prep_time_minutes"] = -5
        await self.run_week([broken])
        self.assertEqual(len(dict(self.asked)["breakfast"]), 7)


class _FakeRepository:
    """The one seam `generate_week_plan`/`regenerate_single_day`/
    `regenerate_single_meal` reach storage through in this suite.

    Only the methods those paths call — the suite substitutes at the seam
    rather than at the filesystem, same as `test_sync_service.py`.
    """

    def __init__(self, favourites):
        self._favourites = favourites

    async def get_favorites(self):
        return list(self._favourites)

    async def load_history(self):
        return []

    async def load_biometrics(self):
        return {"weigh_ins": [], "daily_actuals": []}

    async def get_latest_biometrics(self):
        return None

    async def load_whfoods(self):
        return []

    async def load_rejections(self):
        return []


class TestShakeMandatoryVegetables(unittest.TestCase):
    """The leafy green, the frozen vegetable and the fruit are mandatory in
    every shake, which only works if all three places that describe a shake
    agree."""

    def setUp(self):
        config = asyncio.run(_load_shipped_config())
        self.style = config["meal_styles"]["breakfast"][
            planner.WORKOUT_BREAKFAST_STYLE
        ]

    def test_the_style_names_all_three_as_mandatory_with_quantities(self):
        self.assertIn("MANDATORY BASE", self.style)
        self.assertIn("20-30g raw leafy green", self.style)
        self.assertIn("50-80g raw frozen vegetable", self.style)
        self.assertIn("one Fruit Fusion item", self.style)

    def test_the_rotation_rule_protects_them_from_being_dropped(self):
        """The interaction that makes this non-trivial: a rule whose job is to
        make two shakes differ will drop whatever it is allowed to drop, and
        the greens are the cheapest thing to lose. They have to be named as
        part of the base, not left in the 'secondary components' pool."""
        self.assertIn("leafy green", planner.SHAKE_ROTATION_RULE)
        self.assertIn("frozen vegetable", planner.SHAKE_ROTATION_RULE)
        self.assertIn("none of which may ever be dropped", planner.SHAKE_ROTATION_RULE)

    def test_the_rotation_rule_names_fruit_as_base_not_only_as_rotatable(self):
        """Fruit is the subtlest of the three, because the very next clause
        tells the model to rotate "the same combination of fruit, seeds, nuts
        and flavouring". Naming it as base is what makes that a rule about
        WHICH fruit rather than WHETHER one — otherwise dropping the fruit
        entirely is the easiest way to make two shakes differ, and a shake of
        protein powder, spinach and frozen broccoli is barely drinkable."""
        base_clause = planner.SHAKE_ROTATION_RULE.split("and rotate the secondary")[0]
        self.assertIn("fruit", base_clause)
        self.assertIn("omitting any of the three is not", planner.SHAKE_ROTATION_RULE)

    def test_the_per_slot_directive_says_it_too(self):
        """`generate_meal_type_week` sends the rotation rule, but a single
        regenerated shake only ever sees this."""
        self.assertIn("mandatory", planner.SHAKE_SLOT_DIRECTIVE)
        self.assertIn("leafy green", planner.SHAKE_SLOT_DIRECTIVE)
        self.assertIn("Fruit Fusion", planner.SHAKE_SLOT_DIRECTIVE)

    def test_none_of_the_named_vegetables_is_banned(self):
        """`mustard greens` is on the shipped blocklist and `Ingredient.
        reject_banned_ingredients` is a substring match, so a leafy-green list
        is exactly where a collision would land."""
        config = asyncio.run(_load_shipped_config())
        banned = [b.lower() for b in config["dietary_rules"]["banned_ingredients"]]
        greens = [
            "frozen spinach", "frozen kale", "fresh rocket", "fresh spinach",
            "frozen broccoli florets", "frozen cauliflower",
        ]
        for green in greens:
            for term in banned:
                self.assertNotIn(term, green, f"{green} matches banned '{term}'")


async def _load_shipped_config():
    """The real `config/` merge — these assertions are about the shipped
    template, not a fixture that could drift away from it."""
    from repository import LocalJSONRepository

    return await LocalJSONRepository().load_config()


if __name__ == "__main__":
    unittest.main()
