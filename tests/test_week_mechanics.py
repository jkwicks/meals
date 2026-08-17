"""Tests for the deterministic week mechanics — the API-free half of planning.

`week.py` resolves the entire shape of a week before a single token is
generated: which slots cook, how many portions each cook must yield, which
shopping trip pays for it, and whether the grid is coherent at all. None of it
touches the network or a model, and all of it is load-bearing — `portions_for`
in particular is the derived-portions rule the whole cook-events architecture
rests on, and the reason there is deliberately no "batch multiplier" setting.

Covers what the end-to-end review found untested: `portions_for`,
`validate_week`, `shopping_windows`, `spread_batch`, `day_multiplicity`,
`carried_macros`, `storage_note`, `next_choice`, `meal_overrides_for`,
`training_pin_budget`, and the two shopping helpers `collect_unique_plants`
and `round_ingredient_quantity`.

`unittest` and the `sys.path` insert match `test_week_composition.py`; see its
docstring for why.
"""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import planner  # noqa: E402
import shopping  # noqa: E402
import week as wk  # noqa: E402
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, WeekSpec  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


def spec_with(modes=None, servings_per_meal=2, days=None, **kw) -> WeekSpec:
    """A full 7x4 grid of cook slots, with `modes` overriding named slot ids.

    Every slot cooks by default because that is the shape every rule below is
    stated against; the interesting cases are all departures from it.
    """
    days = days or DAYS
    modes = modes or {}
    slots = []
    for day in days:
        for meal_type in MEAL_TYPES:
            sid = wk.slot_id(day, meal_type)
            override = modes.get(sid, {})
            slots.append(
                SlotSpec(
                    day=day,
                    meal_type=meal_type,
                    mode=override.get("mode", MODE_COOK),
                    source=override.get("source"),
                    style=override.get("style"),
                    cuisine=override.get("cuisine"),
                    extra_portions=override.get("extra_portions", 0),
                )
            )
    return WeekSpec(days=days, slots=slots, servings_per_meal=servings_per_meal, **kw)


class TestPortionsAreDerived(unittest.TestCase):
    """The rule the architecture rests on: a batch size *is* the number of
    slots pointing at it x household size. Never entered by hand, so a batch
    can never silently disagree with the meals it has to cover."""

    def test_a_lone_cook_yields_one_meal_for_the_household(self):
        spec = spec_with()
        self.assertEqual(wk.portions_for(spec)["Monday:dinner"], 2)

    def test_each_claiming_slot_grows_the_batch(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(wk.portions_for(spec)["Monday:dinner"], 4)

    def test_extra_portions_are_added_on_top(self):
        """Deliberate extras to freeze, not a multiplier."""
        spec = spec_with({"Monday:dinner": {"mode": MODE_COOK, "extra_portions": 3}})
        self.assertEqual(wk.portions_for(spec)["Monday:dinner"], 5)

    def test_household_size_scales_every_cook(self):
        spec = spec_with(servings_per_meal=4)
        self.assertEqual(wk.portions_for(spec)["Monday:dinner"], 4)

    def test_a_leftover_slot_has_no_portions_of_its_own(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertNotIn("Tuesday:lunch", wk.portions_for(spec))

    def test_claim_counts_and_eaten_on_agree(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
            "Wednesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(wk.claim_counts(spec)["Monday:dinner"], 3)
        self.assertEqual(
            wk.eaten_on(spec)["Monday:dinner"],
            ["Monday:dinner", "Tuesday:lunch", "Wednesday:lunch"],
        )


class TestValidateWeek(unittest.TestCase):
    """The gate on every generation. Returns messages rather than raising so
    the UI can show all problems at once."""

    CONFIG = {
        "cuisines": ["thai", "greek"],
        "cuisine_meal_types": ["dinner"],
        "meal_types": MEAL_TYPES,
        "meal_styles": {"breakfast": {"eggs_salmon": "..."}, "dinner": {"curry": "..."}},
    }

    def errors_for(self, modes):
        return wk.validate_week(spec_with(modes), self.CONFIG)

    def test_a_plain_cooking_week_is_clean(self):
        self.assertEqual(wk.validate_week(spec_with(), self.CONFIG), [])

    def test_a_leftover_needs_a_source(self):
        errors = self.errors_for({"Tuesday:lunch": {"mode": MODE_LEFTOVER}})
        self.assertTrue(any("no source meal chosen" in e for e in errors))

    def test_a_leftover_cannot_point_at_a_later_day(self):
        """The ordering guarantee generation depends on — days are walked in
        week order, so a source must already exist when its macros are read."""
        errors = self.errors_for({
            "Monday:lunch": {"mode": MODE_LEFTOVER, "source": "Friday:dinner"},
        })
        self.assertTrue(any("later in the" in e for e in errors))

    def test_a_leftover_cannot_point_at_another_leftover(self):
        errors = self.errors_for({
            "Tuesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
            "Wednesday:lunch": {"mode": MODE_LEFTOVER, "source": "Tuesday:dinner"},
        })
        self.assertTrue(any("isn't a cooked meal" in e for e in errors))

    def test_a_leftover_cannot_point_at_a_missing_slot(self):
        errors = self.errors_for({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:brunch"},
        })
        self.assertTrue(any("not a slot in this week" in e for e in errors))

    def test_a_style_must_belong_to_its_meal_type(self):
        errors = self.errors_for({"Monday:dinner": {"mode": MODE_COOK, "style": "eggs_salmon"}})
        self.assertTrue(any("isn't a dinner style" in e for e in errors))

    def test_a_cuisine_must_be_known_and_apply_to_that_meal_type(self):
        unknown = self.errors_for({"Monday:dinner": {"mode": MODE_COOK, "cuisine": "martian"}})
        self.assertTrue(any("not in config cuisines" in e for e in unknown))

        wrong_type = self.errors_for({"Monday:lunch": {"mode": MODE_COOK, "cuisine": "thai"}})
        self.assertTrue(any("cuisine themes only apply" in e for e in wrong_type))

    def test_extra_portions_need_a_cooking_slot(self):
        errors = self.errors_for({
            "Monday:lunch": {"mode": MODE_SKIP, "extra_portions": 2},
        })
        self.assertTrue(any("extra portions only apply" in e for e in errors))

    def test_a_week_with_nothing_to_cook_is_rejected(self):
        spec = spec_with({
            wk.slot_id(day, meal_type): {"mode": MODE_SKIP}
            for day in DAYS for meal_type in MEAL_TYPES
        })
        errors = wk.validate_week(spec, self.CONFIG)
        self.assertTrue(any("Nothing to cook" in e for e in errors))

    def test_every_problem_is_reported_at_once(self):
        """Not fail-fast: the UI shows the whole list and keeps Generate
        disabled until the grid is coherent."""
        errors = self.errors_for({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER},
            "Monday:dinner": {"mode": MODE_COOK, "cuisine": "martian"},
        })
        self.assertGreaterEqual(len(errors), 2)


class TestShoppingWindows(unittest.TestCase):
    """Windows group by *cook* day. A Sunday batch eaten Wednesday belongs
    entirely to the Sunday trip — grouping by eating day would split one
    recipe's ingredients across two lists."""

    def test_one_shop_day_covers_the_whole_week(self):
        windows = wk.shopping_windows(DAYS, ["Monday"])
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].days, DAYS)

    def test_two_shop_days_split_the_week_between_them(self):
        windows = wk.shopping_windows(DAYS, ["Monday", "Thursday"])
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].days, ["Monday", "Tuesday", "Wednesday"])
        self.assertEqual(windows[1].days, ["Thursday", "Friday", "Saturday", "Sunday"])

    def test_every_day_lands_in_exactly_one_window(self):
        for shop_days in (["Monday"], ["Monday", "Friday"], ["Tuesday", "Thursday", "Sunday"]):
            with self.subTest(shop_days=shop_days):
                covered = [day for w in wk.shopping_windows(DAYS, shop_days) for day in w.days]
                self.assertEqual(sorted(covered), sorted(DAYS))


class TestSpreadBatch(unittest.TestCase):
    """Anchor selection for the bulk-prep and long-cook toggles."""

    def test_it_links_forward_slots_to_one_anchor(self):
        spec, anchor = wk.spread_batch(spec_with(), "dinner", 6)
        self.assertEqual(anchor, "Monday:dinner")
        self.assertGreater(wk.claim_counts(spec)[anchor], 1)

    def test_the_earliest_eligible_day_wins(self):
        """Deterministic, and it leaves the most week left to spread across."""
        _, anchor = wk.spread_batch(spec_with(), "dinner", 6)
        self.assertEqual(wk.parse_slot_id(anchor)[0], "Monday")

    def test_excluded_days_are_skipped(self):
        """So a second call for the week's other toggle picks a different day."""
        _, anchor = wk.spread_batch(spec_with(), "dinner", 6, exclude_days={"Monday"})
        self.assertNotEqual(wk.parse_slot_id(anchor)[0], "Monday")

    def test_preferred_days_narrow_the_pool_first(self):
        _, anchor = wk.spread_batch(
            spec_with(), "dinner", 6, prefer_days=["Saturday", "Sunday"]
        )
        self.assertIn(wk.parse_slot_id(anchor)[0], {"Saturday", "Sunday"})

    def test_a_preference_nothing_satisfies_falls_back_to_the_whole_pool(self):
        _, anchor = wk.spread_batch(spec_with(), "dinner", 6, prefer_days=["Caturday"])
        self.assertIsNotNone(anchor)

    def test_no_eligible_anchor_returns_none_rather_than_raising(self):
        """Callers treat it as "nothing to do this run", not an error."""
        spec = spec_with({
            wk.slot_id(day, "dinner"): {"mode": MODE_SKIP} for day in DAYS
        })
        out, anchor = wk.spread_batch(spec, "dinner", 6)
        self.assertIsNone(anchor)
        self.assertIs(out, spec)

    def test_the_batch_never_exceeds_three_claims(self):
        """So a small household's arithmetic doesn't spread one dish across
        half the week."""
        spec, anchor = wk.spread_batch(spec_with(servings_per_meal=1), "dinner", 99)
        self.assertLessEqual(wk.claim_counts(spec)[anchor], 3)

    def test_links_are_valid_leftovers(self):
        """It works entirely through `link_leftover`, so the result must pass
        the same validation any hand-built grid does."""
        spec, _ = wk.spread_batch(spec_with(), "dinner", 6)
        self.assertEqual(
            wk.validate_week(spec, TestValidateWeek.CONFIG), []
        )


class TestDayMultiplicityAndCarriedMacros(unittest.TestCase):
    def test_multiplicity_counts_same_day_claims(self):
        spec = spec_with({
            "Monday:dinner": {"mode": MODE_COOK},
            "Monday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(planner.day_multiplicity(spec, "Monday")["Monday:dinner"], 2)

    def test_a_later_days_claim_does_not_count_toward_today(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(planner.day_multiplicity(spec, "Monday")["Monday:dinner"], 1)

    def test_carried_macros_are_empty_without_a_generated_source(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        totals, descriptions = planner.carried_macros(spec, "Tuesday", {})
        self.assertEqual(descriptions, [])
        self.assertEqual(totals["calories"], 0.0)


class TestStorageNote(unittest.TestCase):
    def test_a_single_serving_eaten_today_says_nothing(self):
        self.assertEqual(planner.storage_note(1, 0), "")
        self.assertEqual(planner.storage_note(4, 0), "")

    def test_a_short_lived_batch_says_refrigerate(self):
        note = planner.storage_note(4, 2)
        self.assertIn("refrigerate in airtight containers", note)
        self.assertTrue(note.startswith(planner.STORAGE_NOTE_PREFIX))

    def test_a_long_lived_batch_says_freeze_the_rest(self):
        self.assertIn("freeze the rest", planner.storage_note(6, 30))

    def test_the_fridge_threshold_comes_from_config(self):
        config = {"inventory_rules": {"fridge_safe_days": 2}}
        self.assertIn("freeze the rest", planner.storage_note(4, 3, config))


class TestNextChoice(unittest.TestCase):
    """Strict LRU, not "unused in the last N" — the latter starves the tail of
    the list, cycling through the first 4 of 5 styles forever."""

    OPTIONS = ["a", "b", "c", "d", "e"]

    def test_an_unused_option_is_preferred(self):
        self.assertEqual(planner.next_choice(self.OPTIONS, ["a", "b"]), "c")

    def test_ties_break_on_config_order(self):
        self.assertEqual(planner.next_choice(self.OPTIONS, []), "a")

    def test_the_least_recently_used_wins_once_all_are_used(self):
        self.assertEqual(planner.next_choice(self.OPTIONS, ["a", "b", "c", "d", "e"]), "a")

    def test_repeated_picks_walk_the_whole_list_before_repeating(self):
        recent, picked = [], []
        for _ in range(len(self.OPTIONS)):
            choice = planner.next_choice(self.OPTIONS, recent)
            picked.append(choice)
            recent.append(choice)
        self.assertEqual(sorted(picked), sorted(self.OPTIONS))

    def test_no_options_is_none_rather_than_an_error(self):
        self.assertIsNone(planner.next_choice([], ["a"]))

    def test_history_naming_options_that_no_longer_exist_is_ignored(self):
        self.assertEqual(planner.next_choice(["a", "b"], ["deleted", "a"]), "b")


class TestMealOverridesFor(unittest.TestCase):
    def config(self, overrides):
        return {
            "weekly_schedule": {"Monday": {"meal_overrides": overrides}},
            "meal_types": MEAL_TYPES,
        }

    def test_fat_is_derived_when_not_given(self):
        out = planner.meal_overrides_for(
            "Monday", self.config({"breakfast": {"calories": 450, "protein_g": 45,
                                                 "net_carbs_g": 25}})
        )
        self.assertAlmostEqual(
            out["breakfast"]["fat_g"], planner.derive_fat_g(450, 45, 25), places=6
        )

    def test_an_explicit_fat_figure_is_kept(self):
        out = planner.meal_overrides_for(
            "Monday", self.config({"breakfast": {"calories": 450, "fat_g": 12}})
        )
        self.assertEqual(out["breakfast"]["fat_g"], 12)

    def test_an_unknown_meal_type_is_dropped_not_raised(self):
        """A config typo must not cost a day of generation."""
        out = planner.meal_overrides_for("Monday", self.config({"brunch": {"calories": 450}}))
        self.assertEqual(out, {})

    def test_an_override_without_calories_is_dropped(self):
        out = planner.meal_overrides_for("Monday", self.config({"breakfast": {"protein_g": 40}}))
        self.assertEqual(out, {})

    def test_a_day_with_no_overrides_is_empty(self):
        config = {"weekly_schedule": {"Monday": {}}, "meal_types": MEAL_TYPES}
        self.assertEqual(planner.meal_overrides_for("Monday", config), {})
        self.assertEqual(planner.meal_overrides_for("Friday", config), {})


class TestTrainingPinBudget(unittest.TestCase):
    """The post-workout pin, recomputed after hydration replaces the targets it
    was originally derived from — left alone it claimed 49 g of protein worked
    out from a day that no longer exists."""

    TARGETS = {"calories": 2000, "protein_g": 144, "net_carbs_g": 120, "fat_g": 89}
    WEIGHTS = {"breakfast": 0.30, "lunch": 0.30, "dinner": 0.30, "snack": 0.10}

    def test_the_pin_tracks_its_share_of_the_day(self):
        budget = planner.training_pin_budget(self.TARGETS, "snack", self.WEIGHTS)
        self.assertAlmostEqual(budget["protein_g"], 144 * 0.10, places=6)

    def test_a_bigger_meal_type_gets_a_bigger_pin(self):
        snack = planner.training_pin_budget(self.TARGETS, "snack", self.WEIGHTS)
        dinner = planner.training_pin_budget(self.TARGETS, "dinner", self.WEIGHTS)
        self.assertGreater(dinner["calories"], snack["calories"])

    def test_the_budget_carries_all_four_macros(self):
        budget = planner.training_pin_budget(self.TARGETS, "snack", self.WEIGHTS)
        for key in planner.MACRO_KEYS:
            self.assertIn(key, budget)


class TestShoppingHelpers(unittest.TestCase):
    def test_plants_are_counted_once_per_normalised_name(self):
        """Diversity, not quantity — and "Spinach" and "Baby spinach, washed"
        are one plant, keyed the same way shopping lines are combined."""
        self.assertEqual(
            shopping.normalize_name("Baby spinach, washed"),
            shopping.normalize_name("Baby spinach"),
        )

    def test_a_non_plant_department_is_not_counted(self):
        self.assertNotIn(
            shopping.categorize_department("Chicken breast"), shopping.PLANT_DEPARTMENTS
        )
        self.assertIn(shopping.categorize_department("Spinach"), shopping.PLANT_DEPARTMENTS)

    def test_count_unit_ingredients_round_to_whole_items(self):
        """You cannot buy 1.4 eggs."""
        rounded = shopping.round_ingredient_quantity("Eggs", 132.0, "Dairy & Eggs")
        self.assertEqual(rounded, round(rounded))

    def test_rounding_never_returns_zero_for_a_real_quantity(self):
        for name, grams, dept in [
            ("Olive oil", 3.0, "Pantry"),
            ("Salt", 0.4, "Herbs & Spices"),
            ("Chicken breast", 7.0, "Meat & Poultry"),
        ]:
            with self.subTest(name=name):
                self.assertGreater(
                    shopping.round_ingredient_quantity(name, grams, dept), 0
                )


def cook_event(day, meal_type, ingredients, name=None, portions=2):
    """A `CookEvent` carrying the named ingredients at batch quantities.

    `ingredients` is `[(name, grams), ...]`; macros are filler, since every
    assertion below is about which lines appear, how they combine, and which
    trip pays for them — never about energy.
    """
    return planner.CookEvent(
        slot_id=wk.slot_id(day, meal_type),
        day=day,
        meal_type=meal_type,
        portions=portions,
        recipe=planner.Recipe(
            name=name or f"{day} {meal_type}",
            meal_type=meal_type,
            ingredients=[
                planner.Ingredient(
                    name=n, quantity_g=g, nova_group=1,
                    calories=g * 1.2, protein_g=g * 0.1,
                    net_carbs_g=g * 0.05, fat_g=g * 0.03,
                )
                for n, g in ingredients
            ],
            instructions=["Cook it."],
            prep_time_minutes=20,
            servings=portions,
        ),
    )


class TestAggregateCookEvents(unittest.TestCase):
    """The shopping list proper: cook events in, departments out.

    Aggregation is over **cook events, not days**, so a batch's full quantity
    lands on the trip that pays for it rather than being split across the days
    that eat it.
    """

    def lines(self, shopping_list):
        return {item.name: item for item in shopping_list.items()}

    def test_the_same_ingredient_across_two_cooks_becomes_one_line(self):
        events = [
            cook_event("Monday", "dinner", [("Chicken breast", 400)]),
            cook_event("Tuesday", "dinner", [("Chicken breast", 300)]),
        ]
        lines = self.lines(shopping.aggregate_cook_events(events, DAYS))
        chicken = [item for name, item in lines.items() if "hicken" in name]
        self.assertEqual(len(chicken), 1)
        self.assertEqual(chicken[0].total_amount_g, 700)

    def test_prep_variants_combine_but_state_variants_do_not(self):
        """"Cucumber, diced" and "Cucumber, sliced" are one purchase; quinoa
        dry and cooked are not, because state changes what a gram means."""
        diced = shopping.aggregate_cook_events(
            [cook_event("Monday", "lunch", [("Cucumber, diced", 100),
                                            ("Cucumber, sliced", 50)])], DAYS
        )
        self.assertEqual(len(self.lines(diced)), 1)

        quinoa = shopping.aggregate_cook_events(
            [cook_event("Monday", "lunch", [("Quinoa, dry", 100),
                                            ("Quinoa, cooked", 200)])], DAYS
        )
        self.assertEqual(len(self.lines(quinoa)), 2)

    def test_water_never_reaches_the_list(self):
        """A "Water: 300g" line makes the rest of the list look untrustworthy."""
        out = shopping.aggregate_cook_events(
            [cook_event("Monday", "dinner", [("Water", 300), ("Chicken breast", 200)])], DAYS
        )
        self.assertEqual(len(self.lines(out)), 1)

    def test_items_are_grouped_into_departments(self):
        out = shopping.aggregate_cook_events(
            [cook_event("Monday", "dinner", [("Chicken breast", 400), ("Spinach", 100)])], DAYS
        )
        self.assertGreaterEqual(len(out.categories), 2)

    def test_the_offset_records_how_late_an_item_is_needed(self):
        """`buy_late` reads this to flag a perishable bought on day 1 for a
        day 5 cook. It annotates only — moving it to another trip is the
        shopper's call."""
        events = [cook_event("Friday", "dinner", [("Salmon fillet", 300)])]
        item = self.lines(shopping.aggregate_cook_events(events, DAYS))
        self.assertEqual(next(iter(item.values())).latest_cook_offset, DAYS.index("Friday"))

    def test_a_cook_outside_the_window_is_offset_zero(self):
        events = [cook_event("Friday", "dinner", [("Salmon fillet", 300)])]
        item = self.lines(shopping.aggregate_cook_events(events, ["Monday", "Tuesday"]))
        self.assertEqual(next(iter(item.values())).latest_cook_offset, 0)

    def test_no_events_is_an_empty_list_not_an_error(self):
        self.assertEqual(shopping.aggregate_cook_events([], DAYS).items(), [])


class TestCollectUniquePlants(unittest.TestCase):
    """The telemetry header's plant count — diversity, not quantity."""

    def test_a_plant_used_twice_counts_once(self):
        events = [
            cook_event("Monday", "dinner", [("Spinach", 100)]),
            cook_event("Tuesday", "dinner", [("Baby spinach, washed", 80)]),
        ]
        self.assertEqual(len(shopping.collect_unique_plants(events)), 1)

    def test_distinct_plants_each_count(self):
        events = [cook_event("Monday", "dinner",
                             [("Spinach", 100), ("Carrot", 80), ("Walnuts", 20)])]
        self.assertEqual(len(shopping.collect_unique_plants(events)), 3)

    def test_meat_and_dairy_are_not_plants(self):
        events = [cook_event("Monday", "dinner",
                             [("Chicken breast", 300), ("Greek yoghurt", 100),
                              ("Spinach", 50)])]
        self.assertEqual(shopping.collect_unique_plants(events), ["Spinach"])

    def test_the_result_is_sorted_and_stable(self):
        events = [cook_event("Monday", "dinner",
                             [("Walnuts", 20), ("Carrot", 80), ("Spinach", 100)])]
        plants = shopping.collect_unique_plants(events)
        self.assertEqual(plants, sorted(plants))

    def test_no_events_is_an_empty_list(self):
        self.assertEqual(shopping.collect_unique_plants([]), [])


if __name__ == "__main__":
    unittest.main()
